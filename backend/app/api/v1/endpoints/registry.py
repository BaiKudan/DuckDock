from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.deps import (
    DB,
    OptionalCurrentUser,
    get_robot_account,
    require_namespace_member,
)
from app.models.namespace import Namespace, NamespaceMember
from app.models.public_release import PublicReleaseApprovalStatus, PublicSkillRelease
from app.models.scan import ScanResult
from app.models.skill import Skill, SkillVersion, SkillVersionStatus
from app.models.sync_event import RegistrySyncEvent, SyncEventOperation
from app.schemas.registry import (
    RegistrySyncEventEntry,
    RegistrySyncEventsResponse,
    RegistryIndexResponse,
    RegistryManifestResponse,
    RegistrySignedUrlResponse,
    RegistryVersionEntry,
)
from app.services.artifact_service import ArtifactStorageError, artifact_service
from app.services.audit_service import audit
from app.services.iam_service import namespace_access_map
from app.services.public_release_service import is_publicly_available

router = APIRouter(prefix="/registry", tags=["registry"])


@router.get("/skill.md", response_class=PlainTextResponse)
async def skillhub_registry_markdown(request: Request):
    base = str(request.base_url).rstrip("/")
    api_base = f"{base}{settings.API_V1_PREFIX}"
    return f"""# DuckDock Private Skills Registry

Use this endpoint as the SkillHub Skills Registry entry for DuckDock-managed runtimes.

Registry URL:
{api_base}/registry/skill.md

Machine-readable index:
{api_base}/registry/index

Sync events:
{api_base}/registry/events

Required probe skill:
duckdock/duckdock-reporter

Reporter upload API:
{api_base}/reports/upload-sessions

Notes:
- Install `duckdock/duckdock-reporter` from this registry.
- Use a DuckDock runtime report token for uploads.
- Run one dry-run upload check after installation.
- Do not upload raw private conversation content unless your company policy explicitly allows it.
"""


def _active_namespace_clause():
    return Namespace.deleted_at.is_(None)


def _active_skill_clause():
    return Skill.deleted_at.is_(None)


async def _get_namespace(name: str, db: DB) -> Namespace:
    result = await db.execute(select(Namespace).where(Namespace.name == name, _active_namespace_clause()))
    namespace = result.scalar_one_or_none()
    if namespace is None:
        raise HTTPException(status_code=404, detail="Namespace not found")
    return namespace


async def _get_skill(namespace_id: int, skill_name: str, db: DB) -> Skill:
    result = await db.execute(
        select(Skill)
        .where(Skill.namespace_id == namespace_id, Skill.name == skill_name, _active_skill_clause())
        .options(selectinload(Skill.versions))
    )
    skill = result.scalar_one_or_none()
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return skill


async def _get_public_skill(namespace_name: str, skill_name: str, db: DB) -> Skill:
    result = await db.execute(
        select(Skill)
        .join(Namespace, Namespace.id == Skill.namespace_id)
        .join(SkillVersion, SkillVersion.skill_id == Skill.id)
        .join(PublicSkillRelease, PublicSkillRelease.version_id == SkillVersion.id)
        .where(
            Namespace.name == namespace_name,
            Skill.name == skill_name,
            SkillVersion.status == SkillVersionStatus.PRODUCTION,
            _active_namespace_clause(),
            _active_skill_clause(),
        )
        .options(selectinload(Skill.versions), selectinload(Skill.namespace))
    )
    skill = result.scalars().unique().first()
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return skill


async def _public_release_for_version(db: DB, version_id: int) -> PublicSkillRelease | None:
    result = await db.execute(
        select(PublicSkillRelease).where(PublicSkillRelease.version_id == version_id)
    )
    return result.scalar_one_or_none()


async def _public_release_map(db: DB, skill_ids: list[int]) -> dict[int, set[int]]:
    if not skill_ids:
        return {}
    rows = (
        await db.execute(
            select(SkillVersion.skill_id, PublicSkillRelease.version_id)
            .join(Skill, Skill.id == SkillVersion.skill_id)
            .join(Namespace, Namespace.id == Skill.namespace_id)
            .join(PublicSkillRelease, PublicSkillRelease.version_id == SkillVersion.id)
            .where(
                SkillVersion.skill_id.in_(skill_ids),
                SkillVersion.status == SkillVersionStatus.PRODUCTION,
                PublicSkillRelease.approval_status == PublicReleaseApprovalStatus.APPROVED,
                (PublicSkillRelease.expires_at.is_(None) | (PublicSkillRelease.expires_at > datetime.now(timezone.utc))),
                _active_skill_clause(),
                _active_namespace_clause(),
            )
        )
    ).all()
    mapping: dict[int, set[int]] = {}
    for skill_id, version_id in rows:
        mapping.setdefault(skill_id, set()).add(version_id)
    return mapping


def _select_versions(
    versions: list[SkillVersion],
    *,
    include_non_production: bool,
    latest_only: bool,
) -> list[SkillVersion]:
    ordered = sorted(versions, key=lambda item: item.created_at, reverse=True)
    if not include_non_production:
        ordered = [item for item in ordered if item.status == SkillVersionStatus.PRODUCTION]
    if latest_only:
        return ordered[:1]
    return ordered


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _version_updated_at(version: SkillVersion, scan: ScanResult | None) -> datetime:
    candidates = [_as_utc(version.created_at)]
    if scan is not None:
        if scan.completed_at is not None:
            candidates.append(_as_utc(scan.completed_at))
        elif scan.started_at is not None:
            candidates.append(_as_utc(scan.started_at))
        else:
            candidates.append(_as_utc(scan.created_at))
    return max(candidates)


def _build_entry(
    *,
    namespace: str,
    skill: Skill,
    version: SkillVersion,
    updated_at: datetime,
    include_urls: bool,
    expires_in: int | None,
) -> RegistryVersionEntry:
    manifest_path = artifact_service.manifest_object_key(namespace, skill.name, version.tag)
    artifact_path = artifact_service.bundle_object_key(namespace, skill.name, version.tag)

    distribution_ready = True
    manifest_url = None
    artifact_url = None
    expires_at = None
    artifact_sha256 = None
    artifact_size_bytes = None

    try:
        manifest = artifact_service.ensure_version_artifacts(
            namespace=namespace,
            skill_name=skill.name,
            tag=version.tag,
            commit_sha=version.commit_sha,
            description=(version.skill_metadata or {}).get("description") or skill.description,
            status=version.status.value,
            skill_metadata=version.skill_metadata or {},
            changelog=version.changelog,
            publish_tags=version.publish_tags,
            content_fingerprint=version.content_fingerprint,
            file_count=version.file_count,
        )
        artifact = manifest.get("artifact", {})
        artifact_sha256 = artifact.get("sha256")
        artifact_size_bytes = artifact.get("size_bytes")
        if include_urls:
            signed = artifact_service.generate_signed_urls(
                namespace=namespace,
                skill_name=skill.name,
                tag=version.tag,
                expires_in=expires_in,
            )
            manifest_url = signed["manifest_url"]
            artifact_url = signed["artifact_url"]
            expires_at = signed["expires_at"]
    except ArtifactStorageError:
        distribution_ready = False

    return RegistryVersionEntry(
        namespace=namespace,
        skill=skill.name,
        description=skill.description,
        tag=version.tag,
        commit_sha=version.commit_sha,
        status=version.status,
        created_at=version.created_at,
        updated_at=updated_at,
        sync_cursor=updated_at,
        distribution_ready=distribution_ready,
        manifest_path=manifest_path,
        artifact_path=artifact_path,
        artifact_sha256=artifact_sha256,
        artifact_size_bytes=artifact_size_bytes,
        manifest_url=manifest_url,
        artifact_url=artifact_url,
        urls_expire_at=expires_at,
        skill_metadata=version.skill_metadata,
    )


@router.get("/events", response_model=RegistrySyncEventsResponse)
async def registry_events(
    db: DB,
    current_user: OptionalCurrentUser,
    namespace: str | None = None,
    since_cursor: int | None = Query(default=None, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    include_state_events: bool = False,
):
    query = select(RegistrySyncEvent).order_by(RegistrySyncEvent.id.asc())
    if since_cursor is not None:
        query = query.where(RegistrySyncEvent.id > since_cursor)
    if namespace:
        query = query.where(RegistrySyncEvent.namespace_name == namespace)

    if current_user is None:
        query = query.where(RegistrySyncEvent.is_public == True)
        if not include_state_events:
            query = query.where(
                RegistrySyncEvent.operation.in_(
                    [SyncEventOperation.UPSERT.value, SyncEventOperation.TOMBSTONE.value]
                )
            )
    else:
        robot = get_robot_account(current_user)
        if robot is not None:
            query = query.where(RegistrySyncEvent.namespace_id == robot.namespace_id)
        else:
            member_namespace_ids = set(
                (
                    await db.execute(
                        select(NamespaceMember.namespace_id).where(NamespaceMember.user_id == current_user.id)
                    )
                ).scalars().all()
            )
            member_namespace_ids.update((await namespace_access_map(db, user=current_user)).keys())
            if not member_namespace_ids:
                return RegistrySyncEventsResponse(
                    schema_version="duckdock-registry-events/v1",
                    generated_at=datetime.now(timezone.utc),
                    since_cursor=since_cursor,
                    next_cursor=since_cursor or 0,
                    items=[],
                )
            query = query.where(RegistrySyncEvent.namespace_id.in_(sorted(member_namespace_ids)))
        if not include_state_events:
            query = query.where(RegistrySyncEvent.operation != SyncEventOperation.STATE.value)

    rows = (await db.execute(query.limit(limit))).scalars().all()
    items = [
        RegistrySyncEventEntry(
            cursor=row.id,
            namespace=row.namespace_name,
            skill=row.skill_name,
            tag=row.tag,
            entity=row.entity,
            operation=row.operation,
            sync_state=row.sync_state,
            event=row.event,
            is_public=row.is_public,
            version_status=row.version_status,
            commit_sha=row.commit_sha,
            reason=row.reason,
            payload=row.payload,
            created_at=row.created_at,
        )
        for row in rows
    ]
    next_cursor = rows[-1].id if rows else (since_cursor or 0)
    return RegistrySyncEventsResponse(
        schema_version="duckdock-registry-events/v1",
        generated_at=datetime.now(timezone.utc),
        since_cursor=since_cursor,
        next_cursor=next_cursor,
        limit=limit,
        include_state_events=include_state_events,
        items=items,
    )


@router.get("/index", response_model=RegistryIndexResponse)
async def registry_index(
    db: DB,
    current_user: OptionalCurrentUser,
    namespace: str | None = None,
    latest_only: bool = True,
    include_non_production: bool = False,
    include_urls: bool = True,
    changed_since: datetime | None = None,
    expires_in: int | None = Query(
        default=None,
        ge=60,
        le=settings.REGISTRY_SIGNED_URL_MAX_SECONDS,
    ),
):
    if current_user is None:
        include_non_production = False
        query = (
            select(Skill)
            .join(Namespace, Namespace.id == Skill.namespace_id)
            .join(SkillVersion, SkillVersion.skill_id == Skill.id)
            .join(PublicSkillRelease, PublicSkillRelease.version_id == SkillVersion.id)
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
        if namespace:
            query = query.where(Namespace.name == namespace)
        skills = (await db.execute(query)).scalars().unique().all()
        public_release_map = await _public_release_map(db, [skill.id for skill in skills])
    else:
        robot = get_robot_account(current_user)
        query = (
            select(Skill)
            .join(Namespace, Namespace.id == Skill.namespace_id)
            .where(_active_skill_clause(), _active_namespace_clause())
            .options(selectinload(Skill.versions), selectinload(Skill.namespace))
            .order_by(Namespace.name, Skill.name)
        )

        if robot is not None:
            query = query.where(Skill.namespace_id == robot.namespace_id)
        else:
            query = query.join(
                NamespaceMember,
                NamespaceMember.namespace_id == Namespace.id,
            ).where(NamespaceMember.user_id == current_user.id)

        if namespace:
            query = query.where(Namespace.name == namespace)

        skills = (await db.execute(query)).scalars().unique().all()
        public_release_map = {}
    selected_versions: list[SkillVersion] = []
    for skill in skills:
        selected_versions.extend(
            (
                [
                    version for version in _select_versions(
                        list(skill.versions),
                        include_non_production=False,
                        latest_only=latest_only,
                    )
                    if version.id in public_release_map.get(skill.id, set())
                ]
                if current_user is None
                else _select_versions(
                    list(skill.versions),
                    include_non_production=include_non_production,
                    latest_only=latest_only,
                )
            )
        )

    scan_map: dict[int, ScanResult] = {}
    if selected_versions:
        version_ids = [version.id for version in selected_versions]
        scans = (
            await db.execute(select(ScanResult).where(ScanResult.version_id.in_(version_ids)))
        ).scalars().all()
        scan_map = {scan.version_id: scan for scan in scans}

    items: list[RegistryVersionEntry] = []
    actual_expires = artifact_service._expires_in(expires_in) if include_urls else None
    changed_since_cursor = _as_utc(changed_since) if changed_since is not None else None
    next_cursor = changed_since_cursor or datetime.now(timezone.utc)

    for skill in skills:
        versions_for_skill = _select_versions(
            list(skill.versions),
            include_non_production=False if current_user is None else include_non_production,
            latest_only=latest_only,
        )
        if current_user is None:
            versions_for_skill = [
                version for version in versions_for_skill
                if version.id in public_release_map.get(skill.id, set())
            ]
        for version in versions_for_skill:
            updated_at = _version_updated_at(version, scan_map.get(version.id))
            if changed_since_cursor is not None and updated_at <= changed_since_cursor:
                continue
            next_cursor = max(next_cursor, updated_at)
            items.append(
                _build_entry(
                    namespace=skill.namespace.name,
                    skill=skill,
                    version=version,
                    updated_at=updated_at,
                    include_urls=include_urls,
                    expires_in=actual_expires,
                )
            )

    return RegistryIndexResponse(
        schema_version="duckdock-registry-index/v1",
        generated_at=datetime.now(timezone.utc),
        changed_since=changed_since,
        next_cursor=next_cursor,
        include_urls=include_urls,
        expires_in=actual_expires,
        latest_only=latest_only,
        include_non_production=include_non_production,
        items=items,
    )


@router.get(
    "/namespaces/{ns_name}/skills/{skill_name}/versions/{tag}/manifest",
    response_model=RegistryManifestResponse,
)
async def registry_manifest(
    ns_name: str,
    skill_name: str,
    tag: str,
    db: DB,
    current_user: OptionalCurrentUser,
    expires_in: int | None = Query(
        default=None,
        ge=60,
        le=settings.REGISTRY_SIGNED_URL_MAX_SECONDS,
    ),
):
    if current_user is None:
        skill = await _get_public_skill(ns_name, skill_name, db)
        namespace = skill.namespace
    else:
        namespace = await _get_namespace(ns_name, db)
        await require_namespace_member(current_user, namespace.id, db)
        skill = await _get_skill(namespace.id, skill_name, db)
    version = next((candidate for candidate in skill.versions if candidate.tag == tag), None)
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")
    if current_user is None and (
        version.status != SkillVersionStatus.PRODUCTION
        or not is_publicly_available(version, await _public_release_for_version(db, version.id))
    ):
        raise HTTPException(status_code=404, detail="Version not found")

    try:
        manifest = artifact_service.ensure_version_artifacts(
            namespace=ns_name,
            skill_name=skill_name,
            tag=tag,
            commit_sha=version.commit_sha,
            description=(version.skill_metadata or {}).get("description") or skill.description,
            status=version.status.value,
            skill_metadata=version.skill_metadata or {},
            changelog=version.changelog,
            publish_tags=version.publish_tags,
            content_fingerprint=version.content_fingerprint,
            file_count=version.file_count,
        )
        signed = artifact_service.generate_signed_urls(
            namespace=ns_name,
            skill_name=skill_name,
            tag=tag,
            expires_in=expires_in,
        )
    except ArtifactStorageError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    await audit(
        db,
        user=current_user,
        username="anonymous" if current_user is None else None,
        action="artifact.manifest.issued",
        resource_type="skill_version",
        resource_id=version.id,
        namespace_id=namespace.id,
        details={
            "namespace": ns_name,
            "skill": skill_name,
            "tag": tag,
            "anonymous": current_user is None,
            "expires_in": signed["expires_in"],
        },
    )

    return RegistryManifestResponse(
        manifest=manifest,
        manifest_url=signed["manifest_url"],
        artifact_url=signed["artifact_url"],
        expires_at=signed["expires_at"],
        expires_in=signed["expires_in"],
    )


@router.get(
    "/namespaces/{ns_name}/skills/{skill_name}/versions/{tag}/download-link",
    response_model=RegistrySignedUrlResponse,
)
async def registry_download_link(
    ns_name: str,
    skill_name: str,
    tag: str,
    db: DB,
    current_user: OptionalCurrentUser,
    expires_in: int | None = Query(
        default=None,
        ge=60,
        le=settings.REGISTRY_SIGNED_URL_MAX_SECONDS,
    ),
):
    if current_user is None:
        skill = await _get_public_skill(ns_name, skill_name, db)
        namespace = skill.namespace
    else:
        namespace = await _get_namespace(ns_name, db)
        await require_namespace_member(current_user, namespace.id, db)
        skill = await _get_skill(namespace.id, skill_name, db)
    version = next((candidate for candidate in skill.versions if candidate.tag == tag), None)
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")
    if current_user is None and (
        version.status != SkillVersionStatus.PRODUCTION
        or not is_publicly_available(version, await _public_release_for_version(db, version.id))
    ):
        raise HTTPException(status_code=404, detail="Version not found")

    try:
        artifact_service.ensure_version_artifacts(
            namespace=ns_name,
            skill_name=skill_name,
            tag=tag,
            commit_sha=version.commit_sha,
            description=(version.skill_metadata or {}).get("description") or skill.description,
            status=version.status.value,
            skill_metadata=version.skill_metadata or {},
            changelog=version.changelog,
            publish_tags=version.publish_tags,
            content_fingerprint=version.content_fingerprint,
            file_count=version.file_count,
        )
        signed = artifact_service.generate_signed_urls(
            namespace=ns_name,
            skill_name=skill_name,
            tag=tag,
            expires_in=expires_in,
        )
    except ArtifactStorageError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    await audit(
        db,
        user=current_user,
        username="anonymous" if current_user is None else None,
        action="artifact.download_link.issued",
        resource_type="skill_version",
        resource_id=version.id,
        namespace_id=namespace.id,
        details={
            "namespace": ns_name,
            "skill": skill_name,
            "tag": tag,
            "anonymous": current_user is None,
            "expires_in": signed["expires_in"],
        },
    )

    return RegistrySignedUrlResponse(
        namespace=ns_name,
        skill=skill_name,
        tag=tag,
        artifact_url=signed["artifact_url"],
        manifest_url=signed["manifest_url"],
        expires_at=signed["expires_at"],
        expires_in=signed["expires_in"],
    )
