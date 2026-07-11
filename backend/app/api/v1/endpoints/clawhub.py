from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.deps import (
    DB,
    CurrentUser,
    OptionalCurrentUser,
    get_robot_account,
    require_namespace_writer,
)
from app.core.identifiers import normalize_resource_identifier
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.public_release import PublicReleaseApprovalStatus, PublicSkillRelease
from app.models.scan import ScanResult, ScanStatus
from app.models.skill import Skill, SkillVersion, SkillVersionStatus
from app.models.user import User
from app.schemas.skill import (
    PublishVersionRequest,
    normalize_package_path,
    validate_skill_package,
)
from app.services.artifact_service import ArtifactStorageError, artifact_service
from app.services.audit_service import audit
from app.services.git_service import GitServiceError, git_service
from app.services.iam_service import namespace_access_map
from app.services.replication_service import run_on_publish_replication
from app.services.sync_event_service import (
    derive_sync_state,
    record_skill_restore_events,
    record_skill_tombstone_event,
    record_version_sync_event,
)
from app.services.webhook_service import dispatch_event
from app.workers.scan_tasks import trigger_scan
from .skills import (
    _active_namespace_clause,
    _active_skill_clause,
    _enforce_skill_creation_quota,
    _enforce_version_publish_quota,
)

router = APIRouter(tags=["clawhub-compat"])


def _to_millis(value: datetime | None) -> int | None:
    if value is None:
        return None
    return int(value.timestamp() * 1000)


def _tag_to_version(tag: str) -> str:
    return tag[1:] if tag.startswith("v") else tag


def _version_to_tag(version: str) -> str:
    return version if version.startswith("v") else f"v{version}"


def _slug_parts(raw_slug: str) -> tuple[str | None, str]:
    slug = raw_slug.strip().lower()
    if not slug:
        raise HTTPException(status_code=400, detail="Slug required")
    if "--" in slug:
        namespace, skill = slug.split("--", 1)
        if namespace and skill:
            try:
                return (
                    normalize_resource_identifier(namespace, "Namespace name"),
                    normalize_resource_identifier(skill, "Skill name"),
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        return None, normalize_resource_identifier(slug, "Skill name")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _display_slug(namespace_name: str, skill_name: str, *, single_namespace: bool) -> str:
    if single_namespace:
        return skill_name
    return f"{namespace_name}--{skill_name}"


def _latest_version(versions: list[SkillVersion]) -> SkillVersion | None:
    if not versions:
        return None
    return sorted(versions, key=lambda item: item.created_at, reverse=True)[0]


def _visible_versions(
    versions: list[SkillVersion],
    *,
    role: NamespaceRole | None,
    public_version_ids: set[int] | None = None,
) -> list[SkillVersion]:
    ordered = sorted(versions, key=lambda item: item.created_at, reverse=True)
    if role is None:
        allowed = public_version_ids or set()
        return [
            item for item in ordered
            if item.id in allowed and item.status == SkillVersionStatus.PRODUCTION
        ]
    if role == NamespaceRole.READONLY:
        return [item for item in ordered if item.status == SkillVersionStatus.PRODUCTION]
    return ordered


async def _visible_namespaces(current_user: User, db: DB) -> list[tuple[Namespace, NamespaceRole]]:
    robot = get_robot_account(current_user)
    if robot is not None:
        result = await db.execute(
            select(Namespace).where(Namespace.id == robot.namespace_id, _active_namespace_clause())
        )
        namespace = result.scalar_one_or_none()
        return [(namespace, robot.role)] if namespace is not None else []

    membership_rows = (
        await db.execute(
        select(Namespace, NamespaceMember.role)
        .join(NamespaceMember, NamespaceMember.namespace_id == Namespace.id)
        .where(NamespaceMember.user_id == current_user.id, _active_namespace_clause())
        .order_by(Namespace.name)
        )
    ).all()
    access_map = await namespace_access_map(db, user=current_user)
    bound_rows: list[tuple[Namespace, NamespaceRole]] = []
    if access_map:
        result = await db.execute(
            select(Namespace)
            .where(Namespace.id.in_(list(access_map.keys())), _active_namespace_clause())
            .order_by(Namespace.name)
        )
        bound_rows = [
            (row, access_map[row.id])
            for row in result.scalars().all()
        ]
    merged: dict[int, tuple[Namespace, NamespaceRole]] = {}
    priority = {
        NamespaceRole.READONLY: 1,
        NamespaceRole.DEVELOPER: 2,
        NamespaceRole.ADMIN: 3,
    }
    for namespace, role in [*membership_rows, *bound_rows]:
        current = merged.get(namespace.id)
        if current is None or priority[role] > priority[current[1]]:
            merged[namespace.id] = (namespace, role)
    return sorted(merged.values(), key=lambda item: item[0].name)


async def _public_release_map(
    db: DB,
    *,
    skill_ids: list[int] | None = None,
) -> dict[int, set[int]]:
    query = (
        select(SkillVersion.skill_id, PublicSkillRelease.version_id)
        .join(Skill, Skill.id == SkillVersion.skill_id)
        .join(Namespace, Namespace.id == Skill.namespace_id)
        .join(PublicSkillRelease, PublicSkillRelease.version_id == SkillVersion.id)
        .where(
            SkillVersion.status == SkillVersionStatus.PRODUCTION,
            PublicSkillRelease.approval_status == PublicReleaseApprovalStatus.APPROVED,
            (PublicSkillRelease.expires_at.is_(None) | (PublicSkillRelease.expires_at > datetime.now(timezone.utc))),
            _active_skill_clause(),
            _active_namespace_clause(),
        )
    )
    if skill_ids:
        query = query.where(SkillVersion.skill_id.in_(skill_ids))
    rows = (await db.execute(query)).all()
    mapping: dict[int, set[int]] = {}
    for skill_id, version_id in rows:
        mapping.setdefault(skill_id, set()).add(version_id)
    return mapping


async def _public_skills(
    db: DB,
    *,
    namespace_name: str | None = None,
    skill_name: str | None = None,
) -> tuple[list[Skill], dict[int, set[int]]]:
    query = (
        select(Skill)
        .join(SkillVersion, SkillVersion.skill_id == Skill.id)
        .join(PublicSkillRelease, PublicSkillRelease.version_id == SkillVersion.id)
        .join(Namespace, Namespace.id == Skill.namespace_id)
        .where(
            SkillVersion.status == SkillVersionStatus.PRODUCTION,
            PublicSkillRelease.approval_status == PublicReleaseApprovalStatus.APPROVED,
            (PublicSkillRelease.expires_at.is_(None) | (PublicSkillRelease.expires_at > datetime.now(timezone.utc))),
            _active_skill_clause(),
            _active_namespace_clause(),
        )
        .options(selectinload(Skill.versions), selectinload(Skill.namespace))
        .order_by(Namespace.name, Skill.name)
    )
    if namespace_name:
        query = query.where(Namespace.name == namespace_name)
    if skill_name:
        query = query.where(Skill.name == skill_name)
    skills = (await db.execute(query)).scalars().unique().all()
    release_map = await _public_release_map(db, skill_ids=[skill.id for skill in skills])
    return skills, release_map


async def _load_owner_map(namespaces: list[Namespace], db: DB) -> dict[int, User]:
    owner_ids = sorted({namespace.owner_id for namespace in namespaces})
    if not owner_ids:
        return {}
    result = await db.execute(select(User).where(User.id.in_(owner_ids)))
    users = result.scalars().all()
    return {user.id: user for user in users}


async def _load_scan_map(versions: list[SkillVersion], db: DB) -> dict[int, ScanResult]:
    if not versions:
        return {}
    result = await db.execute(select(ScanResult).where(ScanResult.version_id.in_([item.id for item in versions])))
    scans = result.scalars().all()
    return {scan.version_id: scan for scan in scans}


def _moderation_payload(version: SkillVersion | None, scan: ScanResult | None) -> dict[str, Any] | None:
    if version is None:
        return None

    updated_at = None
    if scan is not None:
        updated_at = _to_millis(scan.completed_at or scan.started_at or scan.created_at)

    if scan is None:
        if version.status == SkillVersionStatus.PRODUCTION:
            return None
        return {
            "isSuspicious": True,
            "isMalwareBlocked": version.status == SkillVersionStatus.REJECTED,
            "verdict": "malicious" if version.status == SkillVersionStatus.REJECTED else "suspicious",
            "reasonCodes": ["SCAN_PENDING"],
            "updatedAt": updated_at,
            "engineVersion": None,
            "summary": "Verification is pending.",
        }

    if scan.status == ScanStatus.PASSED:
        return {
            "isSuspicious": False,
            "isMalwareBlocked": False,
            "verdict": "clean",
            "reasonCodes": [],
            "updatedAt": updated_at,
            "engineVersion": scan.scanner_version,
            "summary": "Verification passed.",
        }

    if scan.status == ScanStatus.WARNED:
        return {
            "isSuspicious": True,
            "isMalwareBlocked": False,
            "verdict": "suspicious",
            "reasonCodes": ["SCAN_WARNED"],
            "updatedAt": updated_at,
            "engineVersion": scan.scanner_version,
            "summary": "Verification passed with warnings.",
        }

    if scan.status == ScanStatus.FAILED:
        return {
            "isSuspicious": True,
            "isMalwareBlocked": True,
            "verdict": "malicious",
            "reasonCodes": ["SCAN_FAILED"],
            "updatedAt": updated_at,
            "engineVersion": scan.scanner_version,
            "summary": "Verification failed.",
        }

    return {
        "isSuspicious": True,
        "isMalwareBlocked": False,
        "verdict": "suspicious",
        "reasonCodes": ["SCAN_PENDING"],
        "updatedAt": updated_at,
        "engineVersion": scan.scanner_version,
        "summary": "Verification is pending.",
    }


def _skill_summary(skill: Skill, latest: SkillVersion | None) -> str | None:
    metadata = latest.skill_metadata if latest else None
    if metadata and metadata.get("description"):
        return str(metadata["description"])
    return skill.description


def _skill_display_name(skill: Skill, latest: SkillVersion | None) -> str:
    metadata = latest.skill_metadata if latest else None
    if metadata and metadata.get("name"):
        return str(metadata["name"])
    return skill.name.replace("-", " ").replace("_", " ").title()


def _version_entry(version: SkillVersion) -> dict[str, Any]:
    return {
        "version": _tag_to_version(version.tag),
        "createdAt": _to_millis(version.created_at),
        "changelog": version.changelog or "",
        "changelogSource": None,
        "license": None,
        "tags": version.publish_tags or [],
        "fingerprint": version.content_fingerprint,
        "fileCount": version.file_count,
    }


def _visibility_label(
    *,
    role: NamespaceRole | None,
    public_version_ids: set[int] | None,
) -> str:
    if role is None:
        return "public"
    return "public" if public_version_ids else "private"


def _score_skill(skill: Skill, query: str) -> float:
    q = query.strip().lower()
    if not q:
        return 1.0
    name = skill.name.lower()
    desc = (skill.description or "").lower()
    if name == q:
        return 1.0
    if name.startswith(q):
        return 0.9
    if q in name:
        return 0.75
    if q in desc:
        return 0.5
    return 0.0


def _select_version_by_reference(
    versions: list[SkillVersion],
    ref: str | None,
) -> SkillVersion | None:
    if not versions:
        return None
    if not ref or ref.strip().lower() == "latest":
        return _latest_version(versions)
    tag = _version_to_tag(ref)
    return next((item for item in versions if item.tag == tag), None)


async def _resolve_visible_skill(
    raw_slug: str,
    current_user: User | None,
    db: DB,
) -> tuple[Namespace, NamespaceRole | None, Skill, set[int] | None] | None:
    namespace_hint, skill_name = _slug_parts(raw_slug)
    if current_user is None:
        skills, release_map = await _public_skills(
            db,
            namespace_name=namespace_hint,
            skill_name=skill_name,
        )
        if not skills:
            return None
        if len(skills) > 1 and not namespace_hint:
            raise HTTPException(status_code=409, detail=f"Ambiguous slug '{raw_slug}'. Use namespace--skill.")
        skill = skills[0]
        return skill.namespace, None, skill, release_map.get(skill.id, set())

    visible = await _visible_namespaces(current_user, db)
    if not visible:
        return None

    namespace_map = {namespace.name: (namespace, role) for namespace, role in visible}
    if namespace_hint:
        namespace_role = namespace_map.get(namespace_hint)
        if namespace_role is None:
            return None
        namespace, role = namespace_role
        result = await db.execute(
            select(Skill)
            .where(Skill.namespace_id == namespace.id, Skill.name == skill_name, _active_skill_clause())
            .options(selectinload(Skill.versions), selectinload(Skill.namespace))
        )
        skill = result.scalar_one_or_none()
        if skill is None:
            return None
        return namespace, role, skill, None

    if len(visible) == 1:
        namespace, role = visible[0]
        result = await db.execute(
            select(Skill)
            .where(Skill.namespace_id == namespace.id, Skill.name == skill_name, _active_skill_clause())
            .options(selectinload(Skill.versions), selectinload(Skill.namespace))
        )
        skill = result.scalar_one_or_none()
        if skill is None:
            return None
        return namespace, role, skill, None

    namespace_ids = [namespace.id for namespace, _ in visible]
    result = await db.execute(
        select(Skill)
        .where(Skill.namespace_id.in_(namespace_ids), Skill.name == skill_name, _active_skill_clause())
        .options(selectinload(Skill.versions), selectinload(Skill.namespace))
    )
    matches = result.scalars().unique().all()
    if not matches:
        return None
    if len(matches) > 1:
        raise HTTPException(
            status_code=409,
            detail=f"Ambiguous slug '{raw_slug}'. Use namespace--skill.",
        )
    skill = matches[0]
    namespace, role = namespace_map[skill.namespace.name]
    return namespace, role, skill, None


async def _resolve_publish_target(
    raw_slug: str,
    current_user: User,
    db: DB,
) -> tuple[Namespace, str]:
    namespace_hint, skill_name = _slug_parts(raw_slug)
    visible = await _visible_namespaces(current_user, db)
    writable = [(namespace, role) for namespace, role in visible if role != NamespaceRole.READONLY]
    if not writable:
        raise HTTPException(status_code=403, detail="No writable namespace available")

    if namespace_hint:
        for namespace, _ in writable:
            if namespace.name == namespace_hint:
                return namespace, skill_name
        raise HTTPException(status_code=403, detail="No write access to namespace")

    if len(writable) == 1:
        namespace, _ = writable[0]
        return namespace, skill_name

    raise HTTPException(
        status_code=409,
        detail=f"Ambiguous slug '{raw_slug}'. Use namespace--skill when you can write to multiple namespaces.",
    )


async def _resolve_deleted_skill(
    raw_slug: str,
    current_user: User,
    db: DB,
) -> tuple[Namespace, NamespaceRole, Skill] | None:
    namespace_hint, skill_name = _slug_parts(raw_slug)
    visible = await _visible_namespaces(current_user, db)
    if not visible:
        return None
    namespace_map = {namespace.name: (namespace, role) for namespace, role in visible}

    if namespace_hint:
        namespace_role = namespace_map.get(namespace_hint)
        if namespace_role is None:
            return None
        namespace, role = namespace_role
        result = await db.execute(
            select(Skill)
            .where(
                Skill.namespace_id == namespace.id,
                Skill.name == skill_name,
                Skill.deleted_at.is_not(None),
            )
            .options(selectinload(Skill.versions), selectinload(Skill.namespace))
        )
        skill = result.scalar_one_or_none()
        if skill is None:
            return None
        return namespace, role, skill

    if len(visible) == 1:
        namespace, role = visible[0]
        result = await db.execute(
            select(Skill)
            .where(
                Skill.namespace_id == namespace.id,
                Skill.name == skill_name,
                Skill.deleted_at.is_not(None),
            )
            .options(selectinload(Skill.versions), selectinload(Skill.namespace))
        )
        skill = result.scalar_one_or_none()
        if skill is None:
            return None
        return namespace, role, skill

    namespace_ids = [namespace.id for namespace, _ in visible]
    result = await db.execute(
        select(Skill)
        .where(
            Skill.namespace_id.in_(namespace_ids),
            Skill.name == skill_name,
            Skill.deleted_at.is_not(None),
        )
        .options(selectinload(Skill.versions), selectinload(Skill.namespace))
    )
    matches = result.scalars().unique().all()
    if not matches:
        return None
    if len(matches) > 1:
        raise HTTPException(
            status_code=409,
            detail=f"Ambiguous slug '{raw_slug}'. Use namespace--skill.",
        )
    skill = matches[0]
    namespace, role = namespace_map[skill.namespace.name]
    return namespace, role, skill


async def _publish_compat_skill(
    *,
    namespace: Namespace,
    skill_name: str,
    display_name: str,
    version: str,
    changelog: str,
    publish_tags: list[str],
    share_public: bool,
    uploaded_files: list[UploadFile],
    db: DB,
    current_user: User,
) -> dict[str, str]:
    await require_namespace_writer(current_user, namespace.id, db)
    robot = get_robot_account(current_user)
    tag = _version_to_tag(version)
    files: dict[str, str] = {}
    raw_files: dict[str, bytes] = {}
    for upload in uploaded_files:
        try:
            rel_path = normalize_package_path(upload.filename or "")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not rel_path:
            raise HTTPException(status_code=422, detail="Uploaded file is missing a filename")
        data = await upload.read()
        if rel_path in raw_files:
            raise HTTPException(status_code=422, detail=f"Duplicate uploaded file '{rel_path}'")
        raw_files[rel_path] = data
        try:
            files[rel_path] = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=422, detail=f"File '{rel_path}' must be UTF-8 text") from exc

    try:
        package_validation = validate_skill_package(
            files,
            expected_version=tag,
            require_version=False,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": "Skill package validation failed", "errors": [str(exc)]},
        ) from exc
    metadata = package_validation.metadata
    files = package_validation.files
    raw_files = {path: raw_files[path] for path in files}

    skill_result = await db.execute(
        select(Skill).where(Skill.namespace_id == namespace.id, Skill.name == skill_name)
    )
    skill = skill_result.scalar_one_or_none()
    if skill is not None and skill.deleted_at is not None:
        raise HTTPException(status_code=409, detail="Skill is deleted. Restore it before publishing.")
    if skill is None:
        await _enforce_skill_creation_quota(namespace.id, db)
        try:
            repo_path = git_service.create_repo(namespace.name, skill_name)
        except GitServiceError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        skill = Skill(
            namespace_id=namespace.id,
            name=skill_name,
            description=str(metadata.get("description") or display_name or skill_name),
            git_repo_path=str(repo_path),
        )
        db.add(skill)
        await db.flush()
        await audit(
            db,
            user=current_user,
            action="skill.created",
            resource_type="skill",
            resource_id=skill.id,
            namespace_id=namespace.id,
            details={"skill": skill_name, "source": "clawhub"},
        )
    else:
        await _enforce_version_publish_quota(namespace, skill, db)

    existing_tag = await db.execute(
        select(SkillVersion).where(SkillVersion.skill_id == skill.id, SkillVersion.tag == tag)
    )
    if existing_tag.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Version '{version}' already exists")

    publish_body = PublishVersionRequest(
        tag=tag,
        skill_md=files["SKILL.md"],
        system_prompt=files.pop("system_prompt.md", None),
        extra_files={path: content for path, content in files.items() if path != "SKILL.md"} or None,
        message=changelog or f"chore: publish {tag}",
    )

    publish_files: dict[str, str] = {"SKILL.md": publish_body.skill_md}
    if publish_body.system_prompt:
        publish_files["system_prompt.md"] = publish_body.system_prompt
    if publish_body.extra_files:
        publish_files.update(publish_body.extra_files)

    author_email = current_user.email if not robot else f"{robot.name}@robots.duckdock.local"
    try:
        raw_publish_files = {path: raw_files[path] for path in publish_files}
        commit_sha = git_service.publish_version_bytes(
            namespace=namespace.name,
            skill_name=skill_name,
            files=raw_publish_files,
            tag=publish_body.tag,
            author_name=current_user.username,
            author_email=author_email,
            message=publish_body.message or f"chore: publish {publish_body.tag}",
        )
    except GitServiceError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    try:
        content_fingerprint = git_service.version_fingerprint(namespace.name, skill_name, publish_body.tag)
    except GitServiceError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    version_row = SkillVersion(
        skill_id=skill.id,
        tag=publish_body.tag,
        commit_sha=commit_sha,
        status=SkillVersionStatus.QUARANTINE,
        skill_metadata=metadata,
        changelog=changelog or None,
        publish_tags=publish_tags,
        content_fingerprint=content_fingerprint,
        file_count=len(publish_files),
        published_by=current_user.id if not robot else None,
    )
    db.add(version_row)
    await db.flush()
    previous_sync_state = derive_sync_state(version_row, None)
    if share_public:
        from app.services.public_release_service import set_public_release
        public_release = await set_public_release(
            db,
            version=version_row,
            shared=True,
            shared_by=current_user.id if not robot else None,
        )
        await record_version_sync_event(
            db,
            namespace_id=namespace.id,
            namespace_name=namespace.name,
            skill_name=skill_name,
            description=str(metadata.get("description") or skill.description),
            version=version_row,
            public_release=public_release,
            previous_state=previous_sync_state,
            reason="published",
        )

    try:
        artifact_service.publish_version_artifacts(
            namespace=namespace.name,
            skill_name=skill_name,
            tag=publish_body.tag,
            commit_sha=commit_sha,
            description=str(metadata.get("description") or skill.description),
            status=version_row.status.value,
            skill_metadata=metadata,
            changelog=version_row.changelog,
            publish_tags=version_row.publish_tags,
            content_fingerprint=version_row.content_fingerprint,
            file_count=version_row.file_count,
            files=publish_files,
        )
    except ArtifactStorageError as exc:
        try:
            git_service.delete_tag(namespace.name, skill_name, publish_body.tag)
        except GitServiceError:
            pass
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    scan = ScanResult(version_id=version_row.id, status=ScanStatus.PENDING)
    db.add(scan)
    await db.flush()
    await db.commit()

    trigger_scan.delay(version_row.id)

    await audit(
        db,
        user=current_user,
        action="skill.version.published",
        resource_type="skill_version",
        resource_id=version_row.id,
        namespace_id=namespace.id,
        details={"skill": skill_name, "tag": publish_body.tag, "source": "clawhub"},
    )
    await dispatch_event(
        db,
        namespace_id=namespace.id,
        event="skill.published",
        payload={
            "namespace": namespace.name,
            "skill": skill_name,
            "tag": publish_body.tag,
            "version_id": version_row.id,
            "source": "clawhub",
        },
    )
    await run_on_publish_replication(
        db,
        namespace_id=namespace.id,
        skill_name=skill_name,
        tag=publish_body.tag,
        triggered_by=current_user.id if not robot else None,
    )

    return {"skillId": str(skill.id), "versionId": str(version_row.id)}


@router.get("/whoami")
async def clawhub_whoami(current_user: CurrentUser):
    robot = get_robot_account(current_user)
    if robot is not None:
        return {
            "user": {
                "handle": f"robot-{robot.name}",
                "displayName": f"{robot.name} ({robot.namespace_id})",
                "image": None,
            }
        }
    return {
        "user": {
            "handle": current_user.username,
            "displayName": current_user.username,
            "image": None,
        }
    }


@router.get("/search")
async def clawhub_search(
    db: DB,
    current_user: OptionalCurrentUser,
    q: str = Query(..., min_length=1),
    limit: int = Query(default=10, ge=1, le=200),
):
    if current_user is None:
        skills, public_map = await _public_skills(db)
        single_namespace = False
        role_map: dict[int, NamespaceRole | None] = {skill.id: None for skill in skills}
    else:
        visible = await _visible_namespaces(current_user, db)
        namespace_ids = [namespace.id for namespace, _ in visible]
        if not namespace_ids:
            return {"results": []}
        result = await db.execute(
            select(Skill)
            .where(Skill.namespace_id.in_(namespace_ids), _active_skill_clause())
            .options(selectinload(Skill.versions), selectinload(Skill.namespace))
        )
        skills = result.scalars().unique().all()
        public_map = {}
        single_namespace = len(visible) == 1
        role_map = {namespace.id: role for namespace, role in visible}
    matches = []
    for skill in skills:
        score = _score_skill(skill, q)
        if score <= 0:
            continue
        role = role_map.get(skill.namespace_id) if current_user is not None else None
        latest = _latest_version(
            _visible_versions(
                list(skill.versions),
                role=role,
                public_version_ids=public_map.get(skill.id),
            )
        )
        if latest is None:
            continue
        matches.append(
            {
                "slug": _display_slug(skill.namespace.name, skill.name, single_namespace=single_namespace),
                "displayName": _skill_display_name(skill, latest),
                "summary": _skill_summary(skill, latest),
                "version": _tag_to_version(latest.tag) if latest else None,
                "score": score,
                "updatedAt": _to_millis(latest.created_at if latest else skill.created_at),
                "visibility": _visibility_label(role=role, public_version_ids=public_map.get(skill.id)),
                "deleted": False,
                "fingerprint": latest.content_fingerprint,
            }
        )
    matches.sort(key=lambda item: (-item["score"], -(item["updatedAt"] or 0), item["slug"]))
    return {"results": matches[:limit]}


@router.get("/skills/deleted")
async def clawhub_list_deleted_skills(
    db: DB,
    current_user: CurrentUser,
    limit: int = Query(default=25, ge=1, le=200),
):
    visible = await _visible_namespaces(current_user, db)
    namespace_ids = [namespace.id for namespace, _ in visible]
    if not namespace_ids:
        return {"items": [], "nextCursor": None}

    result = await db.execute(
        select(Skill)
        .where(Skill.namespace_id.in_(namespace_ids), Skill.deleted_at.is_not(None))
        .options(selectinload(Skill.versions), selectinload(Skill.namespace))
        .order_by(Skill.deleted_at.desc(), Skill.name)
    )
    skills = result.scalars().unique().all()
    role_map = {namespace.id: role for namespace, role in visible}
    items = []
    for skill in skills[:limit]:
        role = role_map[skill.namespace_id]
        latest = _latest_version(list(skill.versions))
        items.append(
            {
                "slug": _display_slug(skill.namespace.name, skill.name, single_namespace=len(visible) == 1),
                "displayName": _skill_display_name(skill, latest),
                "summary": _skill_summary(skill, latest),
                "deleted": True,
                "deletedAt": _to_millis(skill.deleted_at),
                "visibility": _visibility_label(role=role, public_version_ids=None),
                "tags": {"latest": _tag_to_version(latest.tag)} if latest else {},
            }
        )
    return {"items": items, "nextCursor": None}


@router.get("/skills")
async def clawhub_list_skills(
    db: DB,
    current_user: OptionalCurrentUser,
    limit: int = Query(default=25, ge=1, le=200),
    sort: str = Query(default="updated"),
):
    if current_user is None:
        skills, public_map = await _public_skills(db)
        single_namespace = False
        role_map: dict[int, NamespaceRole | None] = {skill.id: None for skill in skills}
    else:
        visible = await _visible_namespaces(current_user, db)
        namespace_ids = [namespace.id for namespace, _ in visible]
        if not namespace_ids:
            return {"items": [], "nextCursor": None}
        result = await db.execute(
            select(Skill)
            .where(Skill.namespace_id.in_(namespace_ids), _active_skill_clause())
            .options(selectinload(Skill.versions), selectinload(Skill.namespace))
        )
        skills = result.scalars().unique().all()
        public_map = {}
        single_namespace = len(visible) == 1
        role_map = {namespace.id: role for namespace, role in visible}
    items = []
    for skill in skills:
        role = role_map.get(skill.namespace_id) if current_user is not None else None
        latest = _latest_version(
            _visible_versions(
                list(skill.versions),
                role=role,
                public_version_ids=public_map.get(skill.id),
            )
        )
        if latest is None:
            continue
        updated_at = latest.created_at if latest else skill.created_at
        items.append(
            {
                "slug": _display_slug(skill.namespace.name, skill.name, single_namespace=single_namespace),
                "displayName": _skill_display_name(skill, latest),
                "summary": _skill_summary(skill, latest),
                "tags": {"latest": _tag_to_version(latest.tag)} if latest else {},
                "stats": {"versions": len(skill.versions), "files": latest.file_count if latest else 0},
                "createdAt": _to_millis(skill.created_at),
                "updatedAt": _to_millis(updated_at),
                "latestVersion": _version_entry(latest) if latest else None,
                "visibility": _visibility_label(role=role, public_version_ids=public_map.get(skill.id)),
                "deleted": False,
            }
        )

    normalized_sort = sort.strip().lower()
    items.sort(
        key=lambda item: (
            -(item["createdAt"] if normalized_sort == "new" else item["updatedAt"]),
            item["slug"],
        )
    )
    return {"items": items[:limit], "nextCursor": None}


@router.post("/skills", status_code=status.HTTP_201_CREATED)
async def clawhub_publish_skill(
    db: DB,
    current_user: CurrentUser,
    payload: str = Form(...),
    files: list[UploadFile] = File(...),
):
    try:
        body = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="Invalid payload JSON") from exc

    slug = str(body.get("slug") or "").strip().lower()
    display_name = str(body.get("displayName") or slug).strip()
    version = str(body.get("version") or "").strip()
    changelog = str(body.get("changelog") or "").strip()
    tags = [str(value).strip().lower() for value in (body.get("tags") or []) if str(value).strip()]
    if not slug:
        raise HTTPException(status_code=422, detail="slug required")
    if not version:
        raise HTTPException(status_code=422, detail="version required")

    namespace, skill_name = await _resolve_publish_target(slug, current_user, db)
    result = await _publish_compat_skill(
        namespace=namespace,
        skill_name=skill_name,
        display_name=display_name,
        version=version,
        changelog=changelog,
        publish_tags=sorted(set(tags or ["latest"])),
        share_public=("public" in tags or "shared" in tags),
        uploaded_files=files,
        db=db,
        current_user=current_user,
    )
    return {"ok": True, **result}


@router.get("/skills/{slug}")
async def clawhub_get_skill(
    slug: str,
    db: DB,
    current_user: OptionalCurrentUser,
):
    resolved = await _resolve_visible_skill(slug, current_user, db)
    if resolved is None:
        if current_user is not None:
            deleted = await _resolve_deleted_skill(slug, current_user, db)
            if deleted is not None:
                namespace, role, skill = deleted
                latest = _latest_version(list(skill.versions))
                return {
                    "skill": {
                        "slug": _display_slug(
                            namespace.name,
                            skill.name,
                            single_namespace=len(await _visible_namespaces(current_user, db)) == 1,
                        ),
                        "displayName": _skill_display_name(skill, latest),
                        "summary": _skill_summary(skill, latest),
                        "tags": {"latest": _tag_to_version(latest.tag)} if latest else {},
                        "stats": {"versions": len(skill.versions), "files": latest.file_count if latest else 0},
                        "createdAt": _to_millis(skill.created_at),
                        "updatedAt": _to_millis(skill.deleted_at or skill.created_at),
                        "deleted": True,
                        "deletedAt": _to_millis(skill.deleted_at),
                        "visibility": _visibility_label(role=role, public_version_ids=None),
                    },
                    "latestVersion": None,
                    "owner": None,
                    "moderation": None,
                }
        return {"skill": None, "latestVersion": None, "owner": None, "moderation": None}

    namespace, role, skill, public_version_ids = resolved
    latest = _latest_version(
        _visible_versions(list(skill.versions), role=role, public_version_ids=public_version_ids)
    )
    scan_map = await _load_scan_map([latest] if latest else [], db)
    owner = None
    if current_user is not None:
        owner_map = await _load_owner_map([namespace], db)
        owner = owner_map.get(namespace.owner_id)
    single_namespace = current_user is not None and len(await _visible_namespaces(current_user, db)) == 1
    return {
        "skill": {
            "slug": _display_slug(namespace.name, skill.name, single_namespace=single_namespace),
            "displayName": _skill_display_name(skill, latest),
            "summary": _skill_summary(skill, latest),
            "tags": {"latest": _tag_to_version(latest.tag)} if latest else {},
            "stats": {"versions": len(skill.versions), "files": latest.file_count if latest else 0},
            "createdAt": _to_millis(skill.created_at),
            "updatedAt": _to_millis(latest.created_at if latest else skill.created_at),
            "deleted": False,
            "deletedAt": None,
            "visibility": _visibility_label(role=role, public_version_ids=public_version_ids),
        },
        "latestVersion": _version_entry(latest) if latest else None,
        "owner": (
            {
                "handle": owner.username,
                "displayName": owner.username,
                "image": None,
            }
            if owner is not None
            else None
        ),
        "moderation": _moderation_payload(latest, scan_map.get(latest.id) if latest else None),
    }


@router.delete("/skills/{slug}")
async def clawhub_delete_skill(
    slug: str,
    db: DB,
    current_user: CurrentUser,
):
    resolved = await _resolve_visible_skill(slug, current_user, db)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    namespace, _, skill, _ = resolved
    await require_namespace_writer(current_user, namespace.id, db)
    versions = (
        await db.execute(select(SkillVersion).where(SkillVersion.skill_id == skill.id))
    ).scalars().all()
    had_public_visibility = False
    for version in versions:
        result = await db.execute(
            select(PublicSkillRelease).where(PublicSkillRelease.version_id == version.id)
        )
        if result.scalar_one_or_none() is not None:
            had_public_visibility = True
            break
    await record_skill_tombstone_event(
        db,
        namespace_id=namespace.id,
        namespace_name=namespace.name,
        skill_name=skill.name,
        had_public_visibility=had_public_visibility,
        reason="skill_deleted",
    )
    skill.deleted_at = datetime.now().astimezone()
    skill.deleted_by = current_user.id
    await audit(
        db,
        user=current_user,
        action="skill.deleted",
        resource_type="skill",
        resource_id=skill.id,
        namespace_id=namespace.id,
        details={"skill": skill.name, "source": "clawhub"},
    )
    await dispatch_event(
        db,
        namespace_id=namespace.id,
        event="skill.deleted",
        payload={"namespace": namespace.name, "skill": skill.name, "source": "clawhub"},
    )
    await db.flush()
    await db.commit()
    return {"ok": True}


@router.post("/skills/{slug}/restore")
async def clawhub_restore_skill(
    slug: str,
    db: DB,
    current_user: CurrentUser,
):
    resolved = await _resolve_deleted_skill(slug, current_user, db)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Deleted skill not found")
    namespace, role, skill = resolved
    if role == NamespaceRole.READONLY:
        raise HTTPException(status_code=403, detail="Read-only members cannot restore this skill")

    skill.deleted_at = None
    skill.deleted_by = None
    await record_skill_restore_events(
        db,
        namespace_id=namespace.id,
        namespace_name=namespace.name,
        skill=skill,
        reason="skill_restored",
    )
    await audit(
        db,
        user=current_user,
        action="skill.restored",
        resource_type="skill",
        resource_id=skill.id,
        namespace_id=namespace.id,
        details={"skill": skill.name, "source": "clawhub"},
    )
    await dispatch_event(
        db,
        namespace_id=namespace.id,
        event="skill.restored",
        payload={"namespace": namespace.name, "skill": skill.name, "source": "clawhub"},
    )
    await db.flush()
    await db.commit()
    return {"ok": True, "slug": _display_slug(namespace.name, skill.name, single_namespace=False)}


@router.get("/skills/{slug}/versions")
async def clawhub_list_versions(
    slug: str,
    db: DB,
    current_user: OptionalCurrentUser,
    limit: int = Query(default=25, ge=1, le=200),
):
    resolved = await _resolve_visible_skill(slug, current_user, db)
    if resolved is None:
        return {"items": [], "nextCursor": None}
    _, role, skill, public_version_ids = resolved
    items = [
        _version_entry(version)
        for version in _visible_versions(
            list(skill.versions),
            role=role,
            public_version_ids=public_version_ids,
        )[:limit]
    ]
    return {"items": items, "nextCursor": None}


@router.get("/skills/{slug}/versions/{version}")
async def clawhub_get_version(
    slug: str,
    version: str,
    db: DB,
    current_user: OptionalCurrentUser,
):
    resolved = await _resolve_visible_skill(slug, current_user, db)
    if resolved is None:
        return {"version": None, "skill": None}

    namespace, role, skill, public_version_ids = resolved
    visible_versions = _visible_versions(
        list(skill.versions),
        role=role,
        public_version_ids=public_version_ids,
    )
    selected = _select_version_by_reference(visible_versions, version)
    if selected is None:
        return {
            "version": None,
            "skill": {
                "slug": slug,
                "displayName": _skill_display_name(skill, _latest_version(visible_versions)),
            },
        }

    files = git_service.list_version_file_metadata(namespace.name, skill.name, selected.tag)
    return {
        "version": {
            **_version_entry(selected),
            "files": files,
            "metadata": selected.skill_metadata or {},
            "status": selected.status.value,
        },
        "skill": {
            "slug": slug,
            "displayName": _skill_display_name(skill, selected),
        },
    }


@router.get("/skills/{slug}/file", response_class=PlainTextResponse)
async def clawhub_get_file(
    slug: str,
    path: str,
    db: DB,
    current_user: OptionalCurrentUser,
    version: str | None = None,
    tag: str | None = None,
):
    resolved = await _resolve_visible_skill(slug, current_user, db)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    namespace, role, skill, public_version_ids = resolved
    visible_versions = _visible_versions(
        list(skill.versions),
        role=role,
        public_version_ids=public_version_ids,
    )
    selected = _select_version_by_reference(visible_versions, version or tag)
    if selected is None:
        raise HTTPException(status_code=404, detail="Version not found")
    files = git_service.get_version_files(namespace.name, skill.name, selected.tag)
    content = files.get(path)
    if content is None:
        raise HTTPException(status_code=404, detail="File not found")
    await audit(
        db,
        user=current_user,
        username="anonymous" if current_user is None else None,
        action="clawhub.file.read",
        resource_type="skill_version",
        resource_id=selected.id,
        namespace_id=namespace.id,
        details={
            "namespace": namespace.name,
            "skill": skill.name,
            "tag": selected.tag,
            "path": path,
            "anonymous": current_user is None,
        },
    )
    return content


@router.get("/download")
async def clawhub_download(
    slug: str,
    db: DB,
    current_user: OptionalCurrentUser,
    version: str | None = None,
):
    resolved = await _resolve_visible_skill(slug, current_user, db)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    namespace, role, skill, public_version_ids = resolved
    visible_versions = _visible_versions(
        list(skill.versions),
        role=role,
        public_version_ids=public_version_ids,
    )
    selected = None
    if version:
        selected = _select_version_by_reference(visible_versions, version)
    if selected is None:
        selected = _latest_version(visible_versions)
    if selected is None:
        raise HTTPException(status_code=404, detail="Version not found")
    archive = git_service.clone_to_zip(namespace.name, skill.name, selected.tag)
    await audit(
        db,
        user=current_user,
        username="anonymous" if current_user is None else None,
        action="clawhub.skill.downloaded",
        resource_type="skill_version",
        resource_id=selected.id,
        namespace_id=namespace.id,
        details={
            "namespace": namespace.name,
            "skill": skill.name,
            "tag": selected.tag,
            "format": "zip",
            "anonymous": current_user is None,
        },
    )
    return Response(
        content=archive,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{skill.name}-{_tag_to_version(selected.tag)}.zip"',
            "ETag": f'"{selected.content_fingerprint or git_service.version_fingerprint(namespace.name, skill.name, selected.tag)}"',
            "X-DuckDock-Fingerprint": selected.content_fingerprint or git_service.version_fingerprint(namespace.name, skill.name, selected.tag),
            "X-DuckDock-Version": _tag_to_version(selected.tag),
        },
    )


@router.get("/resolve")
async def clawhub_resolve(
    slug: str,
    hash: str,
    db: DB,
    current_user: OptionalCurrentUser,
):
    resolved = await _resolve_visible_skill(slug, current_user, db)
    if resolved is None:
        return {"match": None, "latestVersion": None}
    namespace, role, skill, public_version_ids = resolved
    visible_versions = _visible_versions(
        list(skill.versions),
        role=role,
        public_version_ids=public_version_ids,
    )
    latest = _latest_version(visible_versions)
    match = None
    for version in visible_versions:
        if git_service.version_fingerprint(namespace.name, skill.name, version.tag) == hash:
            match = {"version": _tag_to_version(version.tag)}
            break
    return {
        "match": match,
        "latestVersion": {"version": _tag_to_version(latest.tag)} if latest else None,
    }
