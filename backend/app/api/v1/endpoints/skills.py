from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy import select, func, or_
from sqlalchemy.orm import selectinload

from app.core.deps import (
    DB,
    CurrentUser,
    get_robot_account,
    require_namespace_admin,
    require_namespace_member,
    require_namespace_writer,
)
from app.services.audit_service import audit
from app.models.namespace import Namespace, NamespaceMember
from app.models.lifecycle import NamespaceQuota
from app.models.public_release import PublicReleaseApprovalStatus, PublicSkillRelease
from app.models.scan import ScanResult, ScanStatus
from app.models.sandbox_validation import SandboxValidationRun, SandboxValidationStatus
from app.models.skill import Skill, SkillVersion, SkillVersionReviewStatus, SkillVersionStatus
from app.schemas.governance import (
    PackageValidationRequest,
    PackageValidationResponse,
    SkillPackageTemplateOut,
)
from app.schemas.skill import (
    DraftValidationIssue,
    SkillCreate, SkillUpdate, SkillGenerateDraftRequest, SkillGenerateDraftResponse, SkillOut, PublishVersionRequest,
    SkillVersionOut, SkillVersionDetail, DiffResponse, SkillVersionSharingState, SkillVersionSharingUpdate,
    SearchResult, SkillVersionReviewUpdate, SkillVersionSharingApprovalUpdate, parse_skill_front_matter, validate_skill_package,
)
from app.services.git_service import git_service, GitServiceError
from app.services.artifact_service import artifact_service, ArtifactStorageError
from app.services.governance_service import get_or_create_namespace_governance, is_public_license_allowed
from app.services.public_release_service import (
    get_public_release,
    is_publicly_available,
    set_public_release,
)
from app.services.release_gate_service import release_gate_service
from app.models.scanner_suppression import ScannerRuleSuppression
from app.services.scanner_service import aggregate_issues, apply_suppressions, scanner_service
from app.services.skill_package_template_service import list_skill_package_templates
from app.services.skill_generation_service import skill_generation_service, SkillGenerationError
from app.services.sync_event_service import (
    derive_sync_state,
    record_skill_restore_events,
    record_skill_tombstone_event,
    record_version_sync_event,
)
from app.services.webhook_service import dispatch_event
from app.services.replication_service import run_on_publish_replication

router = APIRouter(tags=["skills"])


def _active_namespace_clause():
    return Namespace.deleted_at.is_(None)


def _active_skill_clause():
    return Skill.deleted_at.is_(None)


# ── Helpers ────────────────────────────────────────────────────────

async def _get_namespace(name: str, db: DB) -> Namespace:
    result = await db.execute(
        select(Namespace).where(Namespace.name == name, _active_namespace_clause())
    )
    ns = result.scalar_one_or_none()
    if not ns:
        raise HTTPException(status_code=404, detail="Namespace not found")
    return ns


async def _get_skill(ns_id: int, skill_name: str, db: DB) -> Skill:
    result = await db.execute(
        select(Skill).where(
            Skill.namespace_id == ns_id,
            Skill.name == skill_name,
            _active_skill_clause(),
        )
    )
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return skill


def _dir_size_bytes(path_str: str) -> int:
    import os

    total = 0
    for root, _, files in os.walk(path_str):
        for name in files:
            file_path = os.path.join(root, name)
            try:
                total += os.path.getsize(file_path)
            except OSError:
                continue
    return total


async def _get_quota(ns_id: int, db: DB) -> NamespaceQuota:
    result = await db.execute(
        select(NamespaceQuota).where(NamespaceQuota.namespace_id == ns_id)
    )
    quota = result.scalar_one_or_none()
    if quota:
        return quota
    return NamespaceQuota(
        namespace_id=ns_id,
        max_skills=100,
        max_versions_per_skill=50,
        max_total_versions=2000,
        max_storage_bytes=536870912,
    )


async def _enforce_skill_creation_quota(ns_id: int, db: DB) -> None:
    quota = await _get_quota(ns_id, db)
    current_skills = (
        await db.execute(
            select(func.count())
            .select_from(Skill)
            .where(Skill.namespace_id == ns_id, _active_skill_clause())
        )
    ).scalar_one()
    if current_skills >= quota.max_skills:
        raise HTTPException(status_code=409, detail="Namespace skill quota exceeded")


async def _enforce_version_publish_quota(ns: Namespace, skill: Skill, db: DB) -> None:
    quota = await _get_quota(ns.id, db)
    versions_for_skill = (
        await db.execute(
            select(func.count()).select_from(SkillVersion).where(SkillVersion.skill_id == skill.id)
        )
    ).scalar_one()
    if versions_for_skill >= quota.max_versions_per_skill:
        raise HTTPException(status_code=409, detail="Per-skill version quota exceeded")

    total_versions = (
        await db.execute(
            select(func.count())
            .select_from(SkillVersion)
            .join(Skill, Skill.id == SkillVersion.skill_id)
            .where(Skill.namespace_id == ns.id, _active_skill_clause())
        )
    ).scalar_one()
    if total_versions >= quota.max_total_versions:
        raise HTTPException(status_code=409, detail="Namespace total version quota exceeded")

    current_bytes = sum(_dir_size_bytes(s.git_repo_path) for s in (await db.execute(
        select(Skill).where(Skill.namespace_id == ns.id, _active_skill_clause())
    )).scalars().all())
    if current_bytes >= quota.max_storage_bytes:
        raise HTTPException(status_code=409, detail="Namespace storage quota exceeded")


def _skill_out(skill: Skill, versions: list[SkillVersion]) -> SkillOut:
    production = [v for v in versions if v.status == SkillVersionStatus.PRODUCTION]
    latest = production[0] if production else (versions[0] if versions else None)
    return SkillOut(
        id=skill.id,
        namespace_id=skill.namespace_id,
        name=skill.name,
        description=skill.description,
        git_repo_path=skill.git_repo_path,
        created_at=skill.created_at,
        latest_tag=latest.tag if latest else None,
        version_count=len(versions),
    )


class _Unset:
    """Sentinel marking an unsupplied ``public_release`` argument."""


_UNSET = _Unset()


async def _version_out(
    db: DB,
    namespace_name: str,
    skill_name: str,
    version: SkillVersion,
    *,
    public_release: PublicSkillRelease | None | _Unset = _UNSET,
) -> SkillVersionOut:
    # ``public_release`` may be supplied by batch-loading callers (avoiding an
    # N+1 PublicSkillRelease query per version); ``_UNSET`` means "fetch it".
    if isinstance(public_release, _Unset):
        public_release = await get_public_release(db, version_id=version.id)
    return SkillVersionOut(
        id=version.id,
        skill_id=version.skill_id,
        tag=version.tag,
        commit_sha=version.commit_sha,
        status=version.status,
        review_status=version.review_status,
        review_required=version.review_required,
        review_notes=version.review_notes,
        review_requested_at=version.review_requested_at,
        reviewed_at=version.reviewed_at,
        gate_result=version.gate_result,
        skill_metadata=version.skill_metadata,
        changelog=version.changelog,
        publish_tags=version.publish_tags,
        content_fingerprint=version.content_fingerprint,
        file_count=version.file_count,
        created_at=version.created_at,
        is_public_shared=public_release is not None,
        public_shared_at=public_release.created_at if public_release else None,
    )


def _public_slug(namespace_name: str, skill_name: str) -> str:
    return f"{namespace_name}--{skill_name}"


def _sharing_state(
    request: Request,
    namespace_name: str,
    skill_name: str,
    version: SkillVersion,
    *,
    public_release,
    requires_approval: bool,
) -> SkillVersionSharingState:
    base = str(request.base_url).rstrip("/")
    slug = _public_slug(namespace_name, skill_name)
    version_value = version.tag[1:] if version.tag.startswith("v") else version.tag
    public_ready = is_publicly_available(version, public_release)
    return SkillVersionSharingState(
        namespace=namespace_name,
        skill=skill_name,
        tag=version.tag,
        is_public_shared=public_release is not None,
        public_shared_at=public_release.created_at if public_release else None,
        public_ready=public_ready,
        approval_status=public_release.approval_status if public_release else None,
        approved_at=public_release.approved_at if public_release else None,
        expires_at=public_release.expires_at if public_release else None,
        requires_approval=requires_approval,
        license_name=public_release.license_name if public_release else None,
        license_attested=public_release.license_attested if public_release else False,
        risk_acknowledged=public_release.risk_acknowledged if public_release else False,
        public_slug=slug,
        public_download_url=f"{base}/api/v1/download?slug={slug}&version={version_value}",
        public_inspect_url=f"{base}/api/v1/skills/{slug}",
    )


@router.get("/skills/package-templates", response_model=list[SkillPackageTemplateOut])
async def list_package_templates():
    return [
        SkillPackageTemplateOut(
            key=item["key"],
            name=item["name"],
            description=item["description"],
            recommended_files=item["recommended_files"],
            package_files=item["builder"]("my_skill"),
        )
        for item in list_skill_package_templates()
    ]


@router.post("/skills/package-validate", response_model=PackageValidationResponse)
async def validate_package(body: PackageValidationRequest):
    try:
        result = validate_skill_package(
            body.package_files,
            expected_version=body.expected_tag,
            require_version=True,
        )
        return PackageValidationResponse(
            ok=True,
            metadata=result.metadata,
            warnings=result.warnings,
            errors=[],
            validation_spec_present=result.validation_spec is not None,
            example_file_count=sum(1 for path in result.files if path.startswith("examples/")),
            file_count=len(result.files),
        )
    except ValueError as exc:
        return PackageValidationResponse(
            ok=False,
            metadata=None,
            warnings=[],
            errors=[str(exc)],
            validation_spec_present=".duckdock/validation.yaml" in body.package_files,
            example_file_count=sum(1 for path in body.package_files if path.replace("\\", "/").startswith("examples/")),
            file_count=len(body.package_files),
        )


# ── Skills CRUD ─────────────────────────────────────────────────────

@router.post(
    "/namespaces/{ns_name}/skills",
    response_model=SkillOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_skill(
    ns_name: str,
    body: SkillCreate,
    db: DB,
    current_user: CurrentUser,
):
    ns = await _get_namespace(ns_name, db)
    await require_namespace_writer(current_user, ns.id, db)
    await _enforce_skill_creation_quota(ns.id, db)

    # Check name unique in namespace
    existing = await db.execute(
        select(Skill).where(Skill.namespace_id == ns.id, Skill.name == body.name)
    )
    existing_skill = existing.scalar_one_or_none()
    if existing_skill:
        detail = "Skill name already exists in this namespace"
        if existing_skill.deleted_at is not None:
            detail = "Skill already exists but is deleted. Restore it instead of recreating."
        raise HTTPException(status_code=409, detail=detail)

    # Create Git bare repo
    try:
        repo_path = git_service.create_repo(ns_name, body.name)
    except GitServiceError as e:
        raise HTTPException(status_code=500, detail=str(e))

    skill = Skill(
        namespace_id=ns.id,
        name=body.name,
        description=body.description,
        git_repo_path=str(repo_path),
    )
    db.add(skill)
    await db.flush()
    await db.refresh(skill)
    await audit(
        db,
        user=current_user,
        action="skill.created",
        resource_type="skill",
        resource_id=skill.id,
        namespace_id=ns.id,
        details={"skill": body.name},
    )
    return _skill_out(skill, [])


@router.post(
    "/namespaces/{ns_name}/skills/generate-draft",
    response_model=SkillGenerateDraftResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_skill_draft(
    ns_name: str,
    body: SkillGenerateDraftRequest,
    db: DB,
    current_user: CurrentUser,
):
    ns = await _get_namespace(ns_name, db)
    await require_namespace_writer(current_user, ns.id, db)

    skill_result = await db.execute(
        select(Skill).where(Skill.namespace_id == ns.id, Skill.name == body.name)
    )
    skill = skill_result.scalar_one_or_none()
    created_skill = False

    if skill is not None and skill.deleted_at is not None:
        raise HTTPException(status_code=409, detail="Skill is deleted. Restore it before generating a draft.")

    if skill is None and not body.create_if_missing:
        raise HTTPException(status_code=404, detail="Skill not found")

    if skill is None and body.create_if_missing:
        await _enforce_skill_creation_quota(ns.id, db)

    if skill is not None:
        existing_tag = await db.execute(
            select(SkillVersion).where(
                SkillVersion.skill_id == skill.id,
                SkillVersion.tag == body.tag,
            )
        )
        if existing_tag.scalar_one_or_none():
            raise HTTPException(status_code=409, detail=f"Tag '{body.tag}' already exists")

    try:
        draft = await skill_generation_service.generate_skill_draft(
            namespace=ns_name,
            skill_name=body.name,
            description=body.description,
            tag=body.tag,
            user_prompt=body.prompt,
            template_key=body.template_key,
        )
    except SkillGenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    files = dict(draft.package_files)
    try:
        package_validation = validate_skill_package(
            files,
            expected_version=body.tag,
            require_version=True,
        )
        metadata = package_validation.metadata
        files = package_validation.files
        validation_errors = []
    except ValueError as exc:
        metadata = parse_skill_front_matter(files.get("SKILL.md", ""))
        validation_errors = [str(exc)]

    known_names = set(
        (
            await db.execute(
                select(Skill.name).where(Skill.namespace_id == ns.id, _active_skill_clause())
            )
        ).scalars().all()
    )
    known_names.add(body.name)

    issues = scanner_service.scan_files(
        files=files,
        metadata=metadata,
        known_skill_names=known_names,
    )
    suppression_rows = (
        await db.execute(
            select(ScannerRuleSuppression.rule_id, ScannerRuleSuppression.file_pattern)
            .where(ScannerRuleSuppression.namespace_id == ns.id)
        )
    ).all()
    issues, _ = apply_suppressions(issues, [(row[0], row[1]) for row in suppression_rows])
    aggregate = aggregate_issues(issues)
    ready_to_publish = not validation_errors and aggregate["status"] != ScanStatus.FAILED

    if skill is None and body.create_if_missing:
        try:
            repo_path = git_service.create_repo(ns_name, body.name)
        except GitServiceError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        skill = Skill(
            namespace_id=ns.id,
            name=body.name,
            description=draft.description or body.description,
            git_repo_path=str(repo_path),
        )
        db.add(skill)
        await db.flush()
        created_skill = True
        await audit(
            db,
            user=current_user,
            action="skill.created",
            resource_type="skill",
            resource_id=skill.id,
            namespace_id=ns.id,
            details={"skill": body.name, "source": "ai_generate"},
        )

    await audit(
        db,
        user=current_user,
        action="skill.ai_generated",
        resource_type="skill",
        resource_id=skill.id if skill else None,
        namespace_id=ns.id,
        details={
            "skill": body.name,
            "tag": body.tag,
            "model": draft.model,
            "ready_to_publish": ready_to_publish,
            "validation_status": aggregate["status"].value,
        },
    )

    return SkillGenerateDraftResponse(
        skill_name=body.name,
        description=draft.description or body.description,
        tag=body.tag,
        skill_md=files.get("SKILL.md", ""),
        system_prompt=files.get("system_prompt.md"),
        package_files=files,
        changelog=draft.changelog,
        publish_tags=draft.publish_tags,
        created_skill=created_skill,
        validation_status=aggregate["status"],
        validation_errors=validation_errors,
        validation_issues=[DraftValidationIssue(**issue.to_dict()) for issue in issues],
        critical_count=aggregate["critical_count"],
        high_count=aggregate["high_count"],
        medium_count=aggregate["medium_count"],
        low_count=aggregate["low_count"],
        ready_to_publish=ready_to_publish,
        model=draft.model,
    )


@router.get("/namespaces/{ns_name}/skills", response_model=list[SkillOut])
async def list_skills(ns_name: str, db: DB, current_user: CurrentUser):
    ns = await _get_namespace(ns_name, db)
    await require_namespace_member(current_user, ns.id, db)

    result = await db.execute(
        select(Skill)
        .where(Skill.namespace_id == ns.id, _active_skill_clause())
        .options(selectinload(Skill.versions))
        .order_by(Skill.name)
    )
    skills = result.scalars().all()
    return [_skill_out(s, list(s.versions)) for s in skills]


@router.get("/namespaces/{ns_name}/skills/deleted", response_model=list[SkillOut])
async def list_deleted_skills(ns_name: str, db: DB, current_user: CurrentUser):
    ns = await _get_namespace(ns_name, db)
    await require_namespace_member(current_user, ns.id, db)

    result = await db.execute(
        select(Skill)
        .where(Skill.namespace_id == ns.id, Skill.deleted_at.is_not(None))
        .options(selectinload(Skill.versions))
        .order_by(Skill.name)
    )
    skills = result.scalars().all()
    return [_skill_out(s, list(s.versions)) for s in skills]


@router.get("/namespaces/{ns_name}/skills/{skill_name}", response_model=SkillOut)
async def get_skill(ns_name: str, skill_name: str, db: DB, current_user: CurrentUser):
    ns = await _get_namespace(ns_name, db)
    await require_namespace_member(current_user, ns.id, db)

    result = await db.execute(
        select(Skill)
        .where(Skill.namespace_id == ns.id, Skill.name == skill_name, _active_skill_clause())
        .options(selectinload(Skill.versions))
    )
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return _skill_out(skill, list(skill.versions))


@router.patch("/namespaces/{ns_name}/skills/{skill_name}", response_model=SkillOut)
async def update_skill(
    ns_name: str,
    skill_name: str,
    body: SkillUpdate,
    db: DB,
    current_user: CurrentUser,
):
    ns = await _get_namespace(ns_name, db)
    await require_namespace_writer(current_user, ns.id, db)
    skill = await _get_skill(ns.id, skill_name, db)

    skill.description = body.description
    await db.flush()
    await audit(
        db,
        user=current_user,
        action="skill.updated",
        resource_type="skill",
        resource_id=skill.id,
        namespace_id=ns.id,
        details={"skill": skill.name, "description": skill.description},
    )
    await dispatch_event(
        db,
        namespace_id=ns.id,
        event="skill.updated",
        payload={"namespace": ns_name, "skill": skill.name, "description": skill.description},
    )
    await db.refresh(skill)
    result = await db.execute(
        select(SkillVersion)
        .where(SkillVersion.skill_id == skill.id)
        .order_by(SkillVersion.created_at.desc())
    )
    versions = result.scalars().all()
    return _skill_out(skill, versions)


@router.delete("/namespaces/{ns_name}/skills/{skill_name}", status_code=204)
async def delete_skill(ns_name: str, skill_name: str, db: DB, current_user: CurrentUser):
    ns = await _get_namespace(ns_name, db)
    await require_namespace_writer(current_user, ns.id, db)
    skill = await _get_skill(ns.id, skill_name, db)
    versions = (
        await db.execute(
            select(SkillVersion).where(SkillVersion.skill_id == skill.id)
        )
    ).scalars().all()
    had_public_visibility = False
    for version in versions:
        if await get_public_release(db, version_id=version.id) is not None:
            had_public_visibility = True
            break
    await record_skill_tombstone_event(
        db,
        namespace_id=ns.id,
        namespace_name=ns_name,
        skill_name=skill_name,
        had_public_visibility=had_public_visibility,
        reason="skill_deleted",
    )
    skill.deleted_at = datetime.now(timezone.utc)
    skill.deleted_by = current_user.id
    await audit(
        db,
        user=current_user,
        action="skill.deleted",
        resource_type="skill",
        resource_id=skill.id,
        namespace_id=ns.id,
        details={"skill": skill_name},
    )
    await dispatch_event(
        db,
        namespace_id=ns.id,
        event="skill.deleted",
        payload={"namespace": ns_name, "skill": skill_name},
    )
    await db.flush()


@router.post("/namespaces/{ns_name}/skills/{skill_name}/restore", response_model=SkillOut)
async def restore_skill(ns_name: str, skill_name: str, db: DB, current_user: CurrentUser):
    ns = await _get_namespace(ns_name, db)
    await require_namespace_writer(current_user, ns.id, db)
    result = await db.execute(
        select(Skill)
        .where(Skill.namespace_id == ns.id, Skill.name == skill_name)
        .options(selectinload(Skill.versions))
    )
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    if skill.deleted_at is None:
        raise HTTPException(status_code=409, detail="Skill is not deleted")

    skill.deleted_at = None
    skill.deleted_by = None
    await audit(
        db,
        user=current_user,
        action="skill.restored",
        resource_type="skill",
        resource_id=skill.id,
        namespace_id=ns.id,
        details={"skill": skill_name},
    )
    await dispatch_event(
        db,
        namespace_id=ns.id,
        event="skill.restored",
        payload={"namespace": ns_name, "skill": skill_name},
    )
    await record_skill_restore_events(
        db,
        namespace_id=ns.id,
        namespace_name=ns_name,
        skill=skill,
        reason="skill_restored",
    )
    versions = (
        await db.execute(
            select(SkillVersion)
            .where(SkillVersion.skill_id == skill.id)
            .order_by(SkillVersion.created_at.desc())
        )
    ).scalars().all()
    await db.flush()
    return _skill_out(skill, versions)


@router.delete("/namespaces/{ns_name}/skills/{skill_name}/purge", status_code=204)
async def purge_skill(ns_name: str, skill_name: str, db: DB, current_user: CurrentUser):
    ns = await _get_namespace(ns_name, db)
    await require_namespace_writer(current_user, ns.id, db)
    result = await db.execute(
        select(Skill)
        .where(Skill.namespace_id == ns.id, Skill.name == skill_name)
        .options(selectinload(Skill.versions))
    )
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    if skill.deleted_at is None:
        raise HTTPException(status_code=409, detail="Skill must be deleted before permanent removal")

    try:
        artifact_service.delete_skill_artifacts(namespace=ns_name, skill_name=skill_name)
    except ArtifactStorageError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    try:
        git_service.delete_repo_path(skill.git_repo_path)
    except GitServiceError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    skill_id = skill.id
    await db.delete(skill)
    await audit(
        db,
        user=current_user,
        action="skill.purged",
        resource_type="skill",
        resource_id=skill_id,
        namespace_id=ns.id,
        details={"skill": skill_name},
    )
    await db.flush()
    return Response(status_code=204)


# ── Versions ────────────────────────────────────────────────────────

@router.post(
    "/namespaces/{ns_name}/skills/{skill_name}/versions",
    response_model=SkillVersionOut,
    status_code=status.HTTP_201_CREATED,
)
async def publish_version(
    ns_name: str,
    skill_name: str,
    body: PublishVersionRequest,
    db: DB,
    current_user: CurrentUser,
):
    ns = await _get_namespace(ns_name, db)
    await require_namespace_writer(current_user, ns.id, db)
    robot = get_robot_account(current_user)

    skill = await _get_skill(ns.id, skill_name, db)
    skill = (
        await db.execute(select(Skill).where(Skill.id == skill.id).with_for_update())
    ).scalar_one()
    await _enforce_version_publish_quota(ns, skill, db)
    policy = await get_or_create_namespace_governance(db, namespace_id=ns.id)

    # Check tag not already used
    existing_tag = await db.execute(
        select(SkillVersion).where(
            SkillVersion.skill_id == skill.id, SkillVersion.tag == body.tag
        )
    )
    if existing_tag.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Tag '{body.tag}' already exists")

    try:
        package_validation = validate_skill_package(
            body.to_package_files(),
            expected_version=body.tag,
            require_version=True,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": "Skill package validation failed", "errors": [str(exc)]},
        )
    metadata = package_validation.metadata

    # Build files dict
    files = package_validation.files
    changelog = body.changelog or body.message
    publish_tags = body.publish_tags or ["latest"]

    if body.share_public:
        if policy.require_license_attestation and not body.license_attested:
            raise HTTPException(status_code=422, detail="License attestation is required for public sharing")
        if not body.license_name:
            raise HTTPException(status_code=422, detail="license_name is required for public sharing")
        if not is_public_license_allowed(policy, body.license_name):
            raise HTTPException(status_code=422, detail="The selected public license is not allowed by namespace policy")
        if not body.risk_acknowledged:
            raise HTTPException(status_code=422, detail="Risk acknowledgment is required for public sharing")

    # Commit to Git
    author_email = current_user.email if not robot else f"{robot.name}@robots.duckdock.local"
    try:
        commit_sha = git_service.publish_version(
            namespace=ns_name,
            skill_name=skill_name,
            files=files,
            tag=body.tag,
            author_name=current_user.username,
            author_email=author_email,
            message=body.message or f"chore: publish {body.tag}",
        )
    except GitServiceError as e:
        raise HTTPException(status_code=500, detail=str(e))

    try:
        content_fingerprint = git_service.version_fingerprint(ns_name, skill_name, body.tag)
    except GitServiceError as e:
        raise HTTPException(status_code=500, detail=str(e))

    version = SkillVersion(
        skill_id=skill.id,
        tag=body.tag,
        commit_sha=commit_sha,
        status=SkillVersionStatus.QUARANTINE,
        review_status=(
            SkillVersionReviewStatus.PENDING
            if policy.manual_review_required
            else SkillVersionReviewStatus.NOT_REQUIRED
        ),
        review_required=policy.manual_review_required,
        review_requested_at=datetime.now(timezone.utc) if policy.manual_review_required else None,
        skill_metadata=metadata,
        changelog=changelog,
        publish_tags=publish_tags,
        content_fingerprint=content_fingerprint,
        file_count=len(files),
        published_by=current_user.id if not robot else None,
    )
    db.add(version)
    await db.flush()
    previous_sync_state = derive_sync_state(version, None)
    if body.share_public:
        public_release = await set_public_release(
            db,
            version=version,
            shared=True,
            shared_by=current_user.id if not robot else None,
            policy=policy,
            license_name=body.license_name,
            license_attested=body.license_attested,
            risk_acknowledged=body.risk_acknowledged,
            public_expires_in_days=body.public_expires_in_days,
            acting_user_id=current_user.id if not robot else None,
        )
        await record_version_sync_event(
            db,
            namespace_id=ns.id,
            namespace_name=ns_name,
            skill_name=skill_name,
            description=str(metadata.get("description") or skill.description),
            version=version,
            public_release=public_release,
            previous_state=previous_sync_state,
            reason="published",
        )

    try:
        artifact_service.publish_version_artifacts(
            namespace=ns_name,
            skill_name=skill_name,
            tag=body.tag,
            commit_sha=commit_sha,
            description=str(metadata.get("description") or skill.description),
            status=version.status.value,
            skill_metadata=metadata,
            changelog=changelog,
            publish_tags=publish_tags,
            content_fingerprint=version.content_fingerprint,
            file_count=version.file_count,
            files=files,
        )
    except ArtifactStorageError as exc:
        try:
            git_service.delete_tag(ns_name, skill_name, body.tag)
        except GitServiceError:
            pass
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Auto-create ScanResult record and enqueue scan
    from app.models.scan import ScanResult, ScanStatus
    from app.workers.scan_tasks import trigger_scan
    scan = ScanResult(version_id=version.id, status=ScanStatus.PENDING)
    sandbox_run = SandboxValidationRun(
        version_id=version.id,
        status=SandboxValidationStatus.PENDING,
    )
    db.add(scan)
    db.add(sandbox_run)
    await db.flush()
    await db.commit()

    trigger_scan.delay(version.id)

    await audit(
        db,
        user=current_user,
        action="skill.version.published",
        resource_type="skill_version",
        resource_id=version.id,
        namespace_id=ns.id,
        details={"skill": skill_name, "tag": body.tag},
    )
    await dispatch_event(
        db,
        namespace_id=ns.id,
        event="skill.published",
        payload={"namespace": ns_name, "skill": skill_name, "tag": body.tag, "version_id": version.id},
    )
    await run_on_publish_replication(
        db,
        namespace_id=ns.id,
        skill_name=skill_name,
        tag=body.tag,
        triggered_by=current_user.id if not robot else None,
    )

    await db.refresh(version)
    return await _version_out(db, ns_name, skill_name, version)


@router.get(
    "/namespaces/{ns_name}/skills/{skill_name}/versions",
    response_model=list[SkillVersionOut],
)
async def list_versions(ns_name: str, skill_name: str, db: DB, current_user: CurrentUser):
    ns = await _get_namespace(ns_name, db)
    await require_namespace_member(current_user, ns.id, db)
    skill = await _get_skill(ns.id, skill_name, db)
    result = await db.execute(
        select(SkillVersion)
        .where(SkillVersion.skill_id == skill.id)
        .order_by(SkillVersion.created_at.desc())
    )
    versions = result.scalars().all()
    # Batch-load public releases in a single query to avoid an N+1 (one
    # PublicSkillRelease lookup per version).
    version_ids = [version.id for version in versions]
    releases_by_version: dict[int, PublicSkillRelease] = {}
    if version_ids:
        release_rows = await db.execute(
            select(PublicSkillRelease).where(
                PublicSkillRelease.version_id.in_(version_ids)
            )
        )
        releases_by_version = {
            release.version_id: release for release in release_rows.scalars().all()
        }
    return [
        await _version_out(
            db,
            ns_name,
            skill_name,
            version,
            public_release=releases_by_version.get(version.id),
        )
        for version in versions
    ]


@router.get(
    "/namespaces/{ns_name}/skills/{skill_name}/versions/{tag}",
    response_model=SkillVersionDetail,
)
async def get_version(
    ns_name: str, skill_name: str, tag: str, db: DB, current_user: CurrentUser
):
    ns = await _get_namespace(ns_name, db)
    await require_namespace_member(current_user, ns.id, db)
    skill = await _get_skill(ns.id, skill_name, db)
    result = await db.execute(
        select(SkillVersion).where(
            SkillVersion.skill_id == skill.id, SkillVersion.tag == tag
        )
    )
    version = result.scalar_one_or_none()
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    try:
        files = git_service.get_version_files(ns_name, skill_name, tag)
    except GitServiceError as e:
        raise HTTPException(status_code=500, detail=str(e))

    public_release = await get_public_release(db, version_id=version.id)

    return SkillVersionDetail(
        id=version.id,
        skill_id=version.skill_id,
        tag=version.tag,
        commit_sha=version.commit_sha,
        status=version.status,
        review_status=version.review_status,
        review_required=version.review_required,
        review_notes=version.review_notes,
        review_requested_at=version.review_requested_at,
        reviewed_at=version.reviewed_at,
        gate_result=version.gate_result,
        skill_metadata=version.skill_metadata,
        changelog=version.changelog,
        publish_tags=version.publish_tags,
        content_fingerprint=version.content_fingerprint,
        file_count=version.file_count,
        created_at=version.created_at,
        is_public_shared=public_release is not None,
        public_shared_at=public_release.created_at if public_release else None,
        files=files,
    )


@router.get(
    "/namespaces/{ns_name}/skills/{skill_name}/versions/{tag}/sharing",
    response_model=SkillVersionSharingState,
)
async def get_version_sharing(
    ns_name: str,
    skill_name: str,
    tag: str,
    request: Request,
    db: DB,
    current_user: CurrentUser,
):
    ns = await _get_namespace(ns_name, db)
    await require_namespace_member(current_user, ns.id, db)
    policy = await get_or_create_namespace_governance(db, namespace_id=ns.id)
    skill = await _get_skill(ns.id, skill_name, db)
    result = await db.execute(
        select(SkillVersion).where(
            SkillVersion.skill_id == skill.id,
            SkillVersion.tag == tag,
        )
    )
    version = result.scalar_one_or_none()
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    public_release = await get_public_release(db, version_id=version.id)
    return _sharing_state(
        request,
        ns_name,
        skill_name,
        version,
        public_release=public_release,
        requires_approval=policy.public_sharing_requires_approval,
    )


@router.put(
    "/namespaces/{ns_name}/skills/{skill_name}/versions/{tag}/sharing",
    response_model=SkillVersionSharingState,
)
async def update_version_sharing(
    ns_name: str,
    skill_name: str,
    tag: str,
    body: SkillVersionSharingUpdate,
    request: Request,
    db: DB,
    current_user: CurrentUser,
):
    ns = await _get_namespace(ns_name, db)
    await require_namespace_writer(current_user, ns.id, db)
    policy = await get_or_create_namespace_governance(db, namespace_id=ns.id)
    robot = get_robot_account(current_user)
    skill = await _get_skill(ns.id, skill_name, db)
    result = await db.execute(
        select(SkillVersion).where(
            SkillVersion.skill_id == skill.id,
            SkillVersion.tag == tag,
        )
    )
    version = result.scalar_one_or_none()
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")
    if body.is_public_shared:
        if policy.require_license_attestation and not body.license_attested:
            raise HTTPException(status_code=422, detail="License attestation is required for public sharing")
        if not body.license_name:
            raise HTTPException(status_code=422, detail="license_name is required for public sharing")
        if not is_public_license_allowed(policy, body.license_name):
            raise HTTPException(status_code=422, detail="The selected public license is not allowed by namespace policy")
        if not body.risk_acknowledged:
            raise HTTPException(status_code=422, detail="Risk acknowledgment is required for public sharing")

    previous_public_release = await get_public_release(db, version_id=version.id)
    previous_sync_state = derive_sync_state(version, previous_public_release)
    public_release = await set_public_release(
        db,
        version=version,
        shared=body.is_public_shared,
        shared_by=current_user.id if not robot else None,
        policy=policy,
        license_name=body.license_name,
        license_attested=body.license_attested,
        risk_acknowledged=body.risk_acknowledged,
        public_expires_in_days=body.public_expires_in_days,
        acting_user_id=current_user.id if not robot else None,
    )
    await record_version_sync_event(
        db,
        namespace_id=ns.id,
        namespace_name=ns_name,
        skill_name=skill_name,
        description=skill.description,
        version=version,
        public_release=public_release,
        previous_state=previous_sync_state,
        reason="public_sharing_updated",
    )
    await audit(
        db,
        user=current_user,
        action="skill.version.public_shared.updated",
        resource_type="skill_version",
        resource_id=version.id,
        namespace_id=ns.id,
        details={
            "skill": skill_name,
            "tag": tag,
            "is_public_shared": body.is_public_shared,
        },
    )
    await dispatch_event(
        db,
        namespace_id=ns.id,
        event="skill.version.public_shared.updated",
        payload={
            "namespace": ns_name,
            "skill": skill_name,
            "tag": tag,
            "version_id": version.id,
            "is_public_shared": body.is_public_shared,
            "public_ready": is_publicly_available(version, public_release),
        },
    )
    return _sharing_state(
        request,
        ns_name,
        skill_name,
        version,
        public_release=public_release,
        requires_approval=policy.public_sharing_requires_approval,
    )


@router.put(
    "/namespaces/{ns_name}/skills/{skill_name}/versions/{tag}/review",
    response_model=SkillVersionOut,
)
async def update_version_review(
    ns_name: str,
    skill_name: str,
    tag: str,
    body: SkillVersionReviewUpdate,
    db: DB,
    current_user: CurrentUser,
):
    ns = await _get_namespace(ns_name, db)
    await require_namespace_admin(current_user, ns.id, db)
    skill = await _get_skill(ns.id, skill_name, db)
    policy = await get_or_create_namespace_governance(db, namespace_id=ns.id)
    version = (
        await db.execute(
            select(SkillVersion).where(
                SkillVersion.skill_id == skill.id,
                SkillVersion.tag == tag,
            )
        )
    ).scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")
    public_release = await get_public_release(db, version_id=version.id)
    previous_sync_state = derive_sync_state(version, public_release)

    if body.decision == "approve":
        scan = (
            await db.execute(select(ScanResult).where(ScanResult.version_id == version.id))
        ).scalar_one_or_none()
        sandbox_run = (
            await db.execute(select(SandboxValidationRun).where(SandboxValidationRun.version_id == version.id))
        ).scalar_one_or_none()
        version.review_status = SkillVersionReviewStatus.APPROVED
        version.review_notes = body.notes
        version.reviewed_at = datetime.now(timezone.utc)
        version.reviewed_by = current_user.id
        gate_decision = await release_gate_service.evaluate(
            db,
            namespace=ns,
            skill=skill,
            version=version,
            files=git_service.get_version_files(ns_name, skill_name, version.tag),
            policy=policy,
            scan=scan,
            sandbox_run=sandbox_run,
        )
        version.status = gate_decision.status
        version.review_status = gate_decision.review_status
        version.gate_result = gate_decision.gate_result
    elif body.decision == "reject":
        version.review_status = SkillVersionReviewStatus.REJECTED
        version.review_notes = body.notes
        version.reviewed_at = datetime.now(timezone.utc)
        version.reviewed_by = current_user.id
        version.status = SkillVersionStatus.REJECTED
    else:
        version.review_status = SkillVersionReviewStatus.PENDING if version.review_required else SkillVersionReviewStatus.NOT_REQUIRED
        version.review_notes = body.notes
        version.reviewed_at = None
        version.reviewed_by = None
        version.status = SkillVersionStatus.REVIEW if version.review_required else SkillVersionStatus.QUARANTINE
        version.gate_result = None

    await record_version_sync_event(
        db,
        namespace_id=ns.id,
        namespace_name=ns_name,
        skill_name=skill_name,
        description=skill.description,
        version=version,
        public_release=public_release,
        previous_state=previous_sync_state,
        reason="review_updated",
    )
    await audit(
        db,
        user=current_user,
        action="skill.version.review.updated",
        resource_type="skill_version",
        resource_id=version.id,
        namespace_id=ns.id,
        details={"skill": skill_name, "tag": tag, "decision": body.decision, "notes": body.notes},
    )
    await dispatch_event(
        db,
        namespace_id=ns.id,
        event="skill.version.review.updated",
        payload={"namespace": ns_name, "skill": skill_name, "tag": tag, "decision": body.decision},
    )
    await db.flush()
    return await _version_out(db, ns_name, skill_name, version)


@router.put(
    "/namespaces/{ns_name}/skills/{skill_name}/versions/{tag}/sharing/approval",
    response_model=SkillVersionSharingState,
)
async def update_version_sharing_approval(
    ns_name: str,
    skill_name: str,
    tag: str,
    body: SkillVersionSharingApprovalUpdate,
    request: Request,
    db: DB,
    current_user: CurrentUser,
):
    ns = await _get_namespace(ns_name, db)
    await require_namespace_admin(current_user, ns.id, db)
    policy = await get_or_create_namespace_governance(db, namespace_id=ns.id)
    skill = await _get_skill(ns.id, skill_name, db)
    version = (
        await db.execute(
            select(SkillVersion).where(
                SkillVersion.skill_id == skill.id,
                SkillVersion.tag == tag,
            )
        )
    ).scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")

    public_release = await get_public_release(db, version_id=version.id)
    if public_release is None:
        raise HTTPException(status_code=404, detail="Public sharing is not enabled for this version")

    previous_sync_state = derive_sync_state(version, public_release)
    public_release.approval_status = body.approval_status
    public_release.approval_notes = body.approval_notes
    if body.approval_status == PublicReleaseApprovalStatus.APPROVED:
        public_release.approved_by = current_user.id
        public_release.approved_at = datetime.now(timezone.utc)
    else:
        public_release.approved_by = None
        public_release.approved_at = None

    await record_version_sync_event(
        db,
        namespace_id=ns.id,
        namespace_name=ns_name,
        skill_name=skill_name,
        description=skill.description,
        version=version,
        public_release=public_release,
        previous_state=previous_sync_state,
        reason="public_sharing_approval_updated",
    )
    await audit(
        db,
        user=current_user,
        action="skill.version.public_shared.approval.updated",
        resource_type="skill_version",
        resource_id=version.id,
        namespace_id=ns.id,
        details={
            "skill": skill_name,
            "tag": tag,
            "approval_status": body.approval_status.value,
            "approval_notes": body.approval_notes,
        },
    )
    await dispatch_event(
        db,
        namespace_id=ns.id,
        event="skill.version.public_shared.approval.updated",
        payload={"namespace": ns_name, "skill": skill_name, "tag": tag, "approval_status": body.approval_status.value},
    )
    await db.flush()
    return _sharing_state(
        request,
        ns_name,
        skill_name,
        version,
        public_release=public_release,
        requires_approval=policy.public_sharing_requires_approval,
    )


@router.delete(
    "/namespaces/{ns_name}/skills/{skill_name}/versions/{tag}",
    status_code=204,
)
async def delete_version(
    ns_name: str,
    skill_name: str,
    tag: str,
    db: DB,
    current_user: CurrentUser,
):
    ns = await _get_namespace(ns_name, db)
    await require_namespace_writer(current_user, ns.id, db)
    skill = await _get_skill(ns.id, skill_name, db)
    result = await db.execute(
        select(SkillVersion).where(
            SkillVersion.skill_id == skill.id,
            SkillVersion.tag == tag,
        )
    )
    version = result.scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")

    public_release = await get_public_release(db, version_id=version.id)
    previous_sync_state = derive_sync_state(version, public_release)
    await record_version_sync_event(
        db,
        namespace_id=ns.id,
        namespace_name=ns_name,
        skill_name=skill_name,
        description=skill.description,
        version=version,
        public_release=None,
        previous_state=previous_sync_state,
        reason="version_deleted",
    )

    # Determine whether this deletion removes the last publicly shared version for the skill.
    remaining_public = False
    remaining_versions = (
        await db.execute(select(SkillVersion).where(SkillVersion.skill_id == skill.id, SkillVersion.id != version.id))
    ).scalars().all()
    for candidate in remaining_versions:
        if await get_public_release(db, version_id=candidate.id) is not None:
            remaining_public = True
            break
    if not remaining_public and public_release is not None:
        await record_skill_tombstone_event(
            db,
            namespace_id=ns.id,
            namespace_name=ns_name,
            skill_name=skill_name,
            had_public_visibility=True,
            reason="last_public_version_deleted",
        )

    try:
        git_service.delete_tag(ns_name, skill_name, tag)
    except GitServiceError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    artifact_service.delete_version_artifacts(namespace=ns_name, skill_name=skill_name, tag=tag)
    await audit(
        db,
        user=current_user,
        action="skill.version.deleted",
        resource_type="skill_version",
        resource_id=version.id,
        namespace_id=ns.id,
        details={"skill": skill_name, "tag": tag},
    )
    await dispatch_event(
        db,
        namespace_id=ns.id,
        event="skill.version.deleted",
        payload={"namespace": ns_name, "skill": skill_name, "tag": tag, "version_id": version.id},
    )
    await db.delete(version)


@router.get(
    "/namespaces/{ns_name}/skills/{skill_name}/versions/{tag}/download",
)
async def download_version(
    ns_name: str, skill_name: str, tag: str, db: DB, current_user: CurrentUser
):
    ns = await _get_namespace(ns_name, db)
    await require_namespace_member(current_user, ns.id, db)
    skill = await _get_skill(ns.id, skill_name, db)
    version_result = await db.execute(
        select(SkillVersion).where(SkillVersion.skill_id == skill.id, SkillVersion.tag == tag)
    )
    version = version_result.scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")
    try:
        tarball = git_service.clone_to_tarball(ns_name, skill_name, tag)
    except GitServiceError as e:
        raise HTTPException(status_code=404, detail=str(e))
    await audit(
        db,
        user=current_user,
        action="skill.version.downloaded",
        resource_type="skill_version",
        resource_id=version.id,
        namespace_id=ns.id,
        details={"namespace": ns_name, "skill": skill_name, "tag": tag, "format": "tar.gz"},
    )
    return Response(
        content=tarball,
        media_type="application/gzip",
        headers={
            "Content-Disposition": f'attachment; filename="{skill_name}-{tag}.tar.gz"'
        },
    )


@router.get(
    "/namespaces/{ns_name}/skills/{skill_name}/diff",
    response_model=DiffResponse,
)
async def diff_versions(
    ns_name: str,
    skill_name: str,
    from_tag: str,
    to_tag: str,
    db: DB,
    current_user: CurrentUser,
):
    ns = await _get_namespace(ns_name, db)
    await require_namespace_member(current_user, ns.id, db)
    await _get_skill(ns.id, skill_name, db)
    try:
        diff = git_service.diff(ns_name, skill_name, from_tag, to_tag)
    except GitServiceError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return DiffResponse(from_tag=from_tag, to_tag=to_tag, diff=diff)


# ── Search ──────────────────────────────────────────────────────────

@router.get("/skills/search", response_model=SearchResult)
async def search_skills(
    q: str = "",
    namespace: str | None = None,
    skip: int = 0,
    limit: int = 20,
    db: DB = None,
    current_user: CurrentUser = None,
):
    robot = get_robot_account(current_user)
    query = (
        select(Skill)
        .join(Namespace, Namespace.id == Skill.namespace_id)
        .options(selectinload(Skill.versions))
        .where(_active_skill_clause(), _active_namespace_clause())
    )
    if robot:
        query = query.where(Namespace.id == robot.namespace_id)
    else:
        query = query.join(NamespaceMember, NamespaceMember.namespace_id == Namespace.id).where(
            NamespaceMember.user_id == current_user.id
        )
    if q:
        pattern = f"%{q}%"
        query = query.where(
            or_(
                Skill.name.ilike(pattern),
                Skill.description.ilike(pattern),
            )
        )
    if namespace:
        query = query.where(Namespace.name == namespace)

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar_one()

    result = await db.execute(query.offset(skip).limit(limit).order_by(Skill.name))
    skills = result.scalars().all()

    return SearchResult(
        items=[_skill_out(s, list(s.versions)) for s in skills],
        total=total,
    )


# ── Internal helpers ────────────────────────────────────────────────
