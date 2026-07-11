from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.core.deps import (
    AdminUser,
    DB,
    CurrentUser,
    require_namespace_scan_reader,
    require_namespace_scan_runner,
)
from app.models.namespace import Namespace
from app.models.skill import Skill, SkillVersion, SkillVersionStatus
from app.models.scan import ScanResult, ScanStatus
from app.models.scanner_suppression import ScannerRuleSuppression
from app.models.sandbox_validation import SandboxValidationRun, SandboxValidationStatus
from app.schemas.scan import ScanResultOut
from app.schemas.sandbox_validation import SandboxValidationRunOut
from app.schemas.scanner_suppression import (
    ScannerRuleSuppressionCreate,
    ScannerRuleSuppressionOut,
)
from app.services.audit_service import audit
from app.services.sandbox_validation_service import sandbox_validation_service
from app.workers.scan_tasks import trigger_scan

router = APIRouter(tags=["scans"])


@router.get("/scan/sandbox/readiness")
async def get_sandbox_readiness(current_user: AdminUser):
    return sandbox_validation_service.check_readiness()


async def _resolve(ns_name: str, skill_name: str, tag: str, db: DB):
    """Resolve a version, return (namespace, version)."""
    ns = await _get_namespace(ns_name, db)

    skill_result = await db.execute(
        select(Skill).where(
            Skill.namespace_id == ns.id,
            Skill.name == skill_name,
            Skill.deleted_at.is_(None),
        )
    )
    skill = skill_result.scalar_one_or_none()
    if not skill:
        raise HTTPException(404, "Skill not found")

    version_result = await db.execute(
        select(SkillVersion).where(
            SkillVersion.skill_id == skill.id, SkillVersion.tag == tag
        )
    )
    version = version_result.scalar_one_or_none()
    if not version:
        raise HTTPException(404, "Version not found")

    return ns, version


async def _get_namespace(ns_name: str, db: DB):
    ns_result = await db.execute(
        select(Namespace).where(Namespace.name == ns_name, Namespace.deleted_at.is_(None))
    )
    ns = ns_result.scalar_one_or_none()
    if not ns:
        raise HTTPException(404, "Namespace not found")
    return ns


@router.get(
    "/namespaces/{ns_name}/skills/{skill_name}/versions/{tag}/scan",
    response_model=ScanResultOut,
)
async def get_scan_result(
    ns_name: str,
    skill_name: str,
    tag: str,
    db: DB,
    current_user: CurrentUser,
):
    namespace = await _get_namespace(ns_name, db)
    await require_namespace_scan_reader(current_user, namespace.id, db)
    _, version = await _resolve(ns_name, skill_name, tag, db)
    result = await db.execute(
        select(ScanResult).where(ScanResult.version_id == version.id)
    )
    scan = result.scalar_one_or_none()
    if not scan:
        raise HTTPException(404, "No scan result yet. Trigger a scan first.")
    return scan


@router.post(
    "/namespaces/{ns_name}/skills/{skill_name}/versions/{tag}/scan",
    response_model=ScanResultOut,
    status_code=202,
)
async def trigger_scan_endpoint(
    ns_name: str,
    skill_name: str,
    tag: str,
    db: DB,
    current_user: CurrentUser,
):
    """Manually trigger or re-trigger a scan for a specific version."""
    namespace = await _get_namespace(ns_name, db)
    await require_namespace_scan_runner(current_user, namespace.id, db)
    _, version = await _resolve(ns_name, skill_name, tag, db)

    # Upsert scan result record
    existing = await db.execute(
        select(ScanResult).where(ScanResult.version_id == version.id)
    )
    scan = existing.scalar_one_or_none()
    if not scan:
        scan = ScanResult(version_id=version.id, status=ScanStatus.PENDING)
        db.add(scan)
    else:
        scan.status = ScanStatus.PENDING
        scan.issues = None
        scan.critical_count = 0
        scan.high_count = 0
        scan.medium_count = 0
        scan.low_count = 0
        scan.started_at = None
        scan.completed_at = None

    sandbox_existing = await db.execute(
        select(SandboxValidationRun).where(SandboxValidationRun.version_id == version.id)
    )
    sandbox_run = sandbox_existing.scalar_one_or_none()
    if sandbox_run is None:
        sandbox_run = SandboxValidationRun(version_id=version.id, status=SandboxValidationStatus.PENDING)
        db.add(sandbox_run)
    else:
        sandbox_run.status = SandboxValidationStatus.PENDING
        sandbox_run.summary = None
        sandbox_run.checks = None
        sandbox_run.logs = None
        sandbox_run.started_at = None
        sandbox_run.completed_at = None

    version.status = SkillVersionStatus.QUARANTINE
    await db.flush()
    await db.refresh(scan)
    await db.commit()

    # Enqueue Celery task
    trigger_scan.delay(version.id)
    await audit(
        db,
        user=current_user,
        action="scan.triggered",
        resource_type="skill_version",
        resource_id=version.id,
        namespace_id=namespace.id,
        details={"skill": skill_name, "tag": tag},
    )

    return scan


@router.get(
    "/namespaces/{ns_name}/scanner/suppressions",
    response_model=list[ScannerRuleSuppressionOut],
)
async def list_scanner_suppressions(
    ns_name: str,
    db: DB,
    current_user: CurrentUser,
):
    namespace = await _get_namespace(ns_name, db)
    await require_namespace_scan_reader(current_user, namespace.id, db)
    rows = (
        await db.execute(
            select(ScannerRuleSuppression)
            .where(ScannerRuleSuppression.namespace_id == namespace.id)
            .order_by(ScannerRuleSuppression.rule_id, ScannerRuleSuppression.file_pattern)
        )
    ).scalars().all()
    return rows


@router.post(
    "/namespaces/{ns_name}/scanner/suppressions",
    response_model=ScannerRuleSuppressionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_scanner_suppression(
    ns_name: str,
    body: ScannerRuleSuppressionCreate,
    db: DB,
    current_user: CurrentUser,
):
    namespace = await _get_namespace(ns_name, db)
    await require_namespace_scan_runner(current_user, namespace.id, db)
    existing = (
        await db.execute(
            select(ScannerRuleSuppression).where(
                ScannerRuleSuppression.namespace_id == namespace.id,
                ScannerRuleSuppression.rule_id == body.rule_id,
                ScannerRuleSuppression.file_pattern == body.file_pattern,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Suppression for this rule_id and file_pattern already exists",
        )
    suppression = ScannerRuleSuppression(
        namespace_id=namespace.id,
        rule_id=body.rule_id,
        file_pattern=body.file_pattern,
        reason=body.reason,
        created_by=current_user.id if current_user.id and current_user.id > 0 else None,
    )
    db.add(suppression)
    await db.flush()
    await audit(
        db,
        user=current_user,
        action="scanner.suppression.created",
        resource_type="scanner_suppression",
        resource_id=suppression.id,
        namespace_id=namespace.id,
        details={
            "rule_id": body.rule_id,
            "file_pattern": body.file_pattern,
            "reason": body.reason,
        },
    )
    await db.refresh(suppression)
    return suppression


@router.delete(
    "/namespaces/{ns_name}/scanner/suppressions/{suppression_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_scanner_suppression(
    ns_name: str,
    suppression_id: int,
    db: DB,
    current_user: CurrentUser,
):
    namespace = await _get_namespace(ns_name, db)
    await require_namespace_scan_runner(current_user, namespace.id, db)
    suppression = (
        await db.execute(
            select(ScannerRuleSuppression).where(
                ScannerRuleSuppression.id == suppression_id,
                ScannerRuleSuppression.namespace_id == namespace.id,
            )
        )
    ).scalar_one_or_none()
    if suppression is None:
        raise HTTPException(status_code=404, detail="Suppression not found")
    rule_id = suppression.rule_id
    file_pattern = suppression.file_pattern
    await db.delete(suppression)
    await audit(
        db,
        user=current_user,
        action="scanner.suppression.deleted",
        resource_type="scanner_suppression",
        resource_id=suppression_id,
        namespace_id=namespace.id,
        details={"rule_id": rule_id, "file_pattern": file_pattern},
    )


@router.get(
    "/namespaces/{ns_name}/skills/{skill_name}/versions/{tag}/sandbox",
    response_model=SandboxValidationRunOut,
)
async def get_sandbox_validation(
    ns_name: str,
    skill_name: str,
    tag: str,
    db: DB,
    current_user: CurrentUser,
):
    namespace = await _get_namespace(ns_name, db)
    await require_namespace_scan_reader(current_user, namespace.id, db)
    _, version = await _resolve(ns_name, skill_name, tag, db)
    result = await db.execute(
        select(SandboxValidationRun).where(SandboxValidationRun.version_id == version.id)
    )
    sandbox_run = result.scalar_one_or_none()
    if not sandbox_run:
        raise HTTPException(404, "No sandbox validation result yet.")
    return sandbox_run
