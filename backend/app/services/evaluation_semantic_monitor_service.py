from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.evaluation import (
    EvaluationSemanticClusteringOutcome,
    EvaluationSemanticClusteringPolicyStatus,
    EvaluationSemanticClusteringPolicyVersion,
    EvaluationSemanticClusteringRun,
    EvaluationSemanticMonitor,
    EvaluationSemanticMonitorAlert,
    EvaluationSemanticMonitorAlertSeverity,
    EvaluationSemanticMonitorAlertStatus,
    EvaluationSemanticMonitorRun,
    EvaluationSemanticMonitorRunStatus,
    EvaluationSemanticMonitorStatus,
    EvaluationSemanticRegressionComparison,
    EvaluationSemanticRegressionOutcome,
    EvaluationSemanticRegressionPolicyStatus,
    EvaluationSemanticRegressionPolicyVersion,
)
from app.models.user import User
from app.schemas.evaluation import (
    EvaluationSemanticClusteringRunCreate,
    EvaluationSemanticMonitorAcknowledge,
    EvaluationSemanticMonitorCreate,
    EvaluationSemanticRegressionComparisonCreate,
)
from app.services.audit_service import audit
from app.services.evaluation_semantic_clustering_service import (
    get_evaluation_semantic_clustering_policy_version,
    get_evaluation_semantic_clustering_run,
    run_evaluation_semantic_clustering,
)
from app.services.evaluation_semantic_regression_service import (
    create_evaluation_semantic_regression_comparison,
    get_evaluation_semantic_regression_policy_version,
)
from app.services.evaluation_service import (
    EvaluationHubConflictError,
    EvaluationHubNotFoundError,
    EvaluationHubStateError,
)
from app.services.evaluation_ports import SemanticEmbeddingEvidencePort
from app.services.outbox_event_service import (
    build_evaluation_semantic_monitor_alert_acknowledged,
    build_evaluation_semantic_monitor_alert_opened,
    build_evaluation_semantic_monitor_created,
    build_evaluation_semantic_monitor_run_completed,
    build_evaluation_semantic_monitor_run_queued,
    enqueue_domain_event,
)
from app.services.tenant_write_service import require_active_namespace


_SAFE_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


def _public_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _utcnow_mysql_safe() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _mysql_safe(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0)


def _digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _monitor_options():
    return (
        selectinload(EvaluationSemanticMonitor.baseline_run).selectinload(
            EvaluationSemanticClusteringRun.source_case_routing_run
        ),
        selectinload(EvaluationSemanticMonitor.candidate_policy_version).selectinload(
            EvaluationSemanticClusteringPolicyVersion.policy
        ),
        selectinload(EvaluationSemanticMonitor.regression_policy_version).selectinload(
            EvaluationSemanticRegressionPolicyVersion.policy
        ),
    )


def _run_options():
    return (
        selectinload(EvaluationSemanticMonitorRun.monitor)
        .selectinload(EvaluationSemanticMonitor.baseline_run)
        .selectinload(EvaluationSemanticClusteringRun.source_case_routing_run),
        selectinload(EvaluationSemanticMonitorRun.monitor)
        .selectinload(EvaluationSemanticMonitor.candidate_policy_version)
        .selectinload(EvaluationSemanticClusteringPolicyVersion.policy),
        selectinload(EvaluationSemanticMonitorRun.monitor)
        .selectinload(EvaluationSemanticMonitor.regression_policy_version)
        .selectinload(EvaluationSemanticRegressionPolicyVersion.policy),
        selectinload(EvaluationSemanticMonitorRun.candidate_run),
        selectinload(EvaluationSemanticMonitorRun.comparison),
        selectinload(EvaluationSemanticMonitorRun.alert),
    )


def _alert_options():
    return (
        selectinload(EvaluationSemanticMonitorAlert.monitor),
        selectinload(EvaluationSemanticMonitorAlert.monitor_run),
        selectinload(EvaluationSemanticMonitorAlert.comparison),
    )


async def create_evaluation_semantic_monitor(
    db: AsyncSession,
    *,
    request: EvaluationSemanticMonitorCreate,
    actor: User,
) -> EvaluationSemanticMonitor:
    await require_active_namespace(db, request.namespace_id)
    baseline = await get_evaluation_semantic_clustering_run(
        db, public_id=request.baseline_run_public_id
    )
    if baseline.namespace_id != request.namespace_id:
        raise EvaluationHubNotFoundError("EvaluationSemanticClusteringRun not found")
    if baseline.outcome != EvaluationSemanticClusteringOutcome.CLUSTERED:
        raise EvaluationHubStateError("semantic monitor baseline must be CLUSTERED")
    candidate_version = await get_evaluation_semantic_clustering_policy_version(
        db, public_id=request.candidate_policy_version_public_id
    )
    if candidate_version.policy.namespace_id != request.namespace_id:
        raise EvaluationHubNotFoundError(
            "EvaluationSemanticClusteringPolicyVersion not found"
        )
    if candidate_version.policy.status != EvaluationSemanticClusteringPolicyStatus.ACTIVE:
        raise EvaluationHubStateError("candidate semantic clustering policy is not active")
    if (
        candidate_version.source_case_routing_policy_version_id
        != baseline.source_case_routing_run.policy_version_id
    ):
        raise EvaluationHubStateError(
            "candidate policy does not pin the baseline Case Routing policy version"
        )
    regression_version = await get_evaluation_semantic_regression_policy_version(
        db,
        public_id=request.regression_policy_version_public_id,
        namespace_id=request.namespace_id,
    )
    if regression_version.policy.status != EvaluationSemanticRegressionPolicyStatus.ACTIVE:
        raise EvaluationHubStateError("semantic regression policy is not active")

    config_digest = _digest(
        {
            "baseline_run_public_id": baseline.public_id,
            "candidate_policy_version_public_id": candidate_version.public_id,
            "regression_policy_version_public_id": regression_version.public_id,
            "interval_seconds": request.interval_seconds,
        }
    )
    existing_name = await db.scalar(
        select(EvaluationSemanticMonitor.id).where(
            EvaluationSemanticMonitor.namespace_id == request.namespace_id,
            EvaluationSemanticMonitor.name == request.name,
        )
    )
    if existing_name is not None:
        raise EvaluationHubConflictError("semantic monitor name already exists")
    existing_config = await db.scalar(
        select(EvaluationSemanticMonitor.id).where(
            EvaluationSemanticMonitor.namespace_id == request.namespace_id,
            EvaluationSemanticMonitor.config_digest == config_digest,
        )
    )
    if existing_config is not None:
        raise EvaluationHubConflictError("identical semantic monitor already exists")

    current = _utcnow_mysql_safe()
    first_run_at = _mysql_safe(request.first_run_at) if request.first_run_at else current
    monitor = EvaluationSemanticMonitor(
        public_id=_public_id("esmp"),
        namespace_id=request.namespace_id,
        name=request.name,
        description=request.description,
        baseline_run_id=baseline.id,
        candidate_policy_version_id=candidate_version.id,
        regression_policy_version_id=regression_version.id,
        interval_seconds=request.interval_seconds,
        config_digest=config_digest,
        status=EvaluationSemanticMonitorStatus.ACTIVE,
        next_run_at=first_run_at,
        created_by_user_id=actor.id,
        created_at=current,
        updated_at=current,
        baseline_run=baseline,
        candidate_policy_version=candidate_version,
        regression_policy_version=regression_version,
    )
    db.add(monitor)
    await db.flush()
    await enqueue_domain_event(
        db,
        build_evaluation_semantic_monitor_created(monitor),
        guaranteed_new=True,
    )
    await audit(
        db,
        user=actor,
        action="evaluation_semantic_monitor.created",
        resource_type="evaluation_semantic_monitor",
        resource_id=monitor.id,
        namespace_id=monitor.namespace_id,
        details={
            "public_id": monitor.public_id,
            "baseline_run_public_id": baseline.public_id,
            "candidate_policy_version_public_id": candidate_version.public_id,
            "regression_policy_version_public_id": regression_version.public_id,
            "interval_seconds": monitor.interval_seconds,
            "config_digest": monitor.config_digest,
            "status": monitor.status.value,
            "next_run_at": monitor.next_run_at.isoformat(),
        },
    )
    return monitor


async def get_evaluation_semantic_monitor(
    db: AsyncSession,
    *,
    public_id: str,
    namespace_id: int | None = None,
) -> EvaluationSemanticMonitor:
    query = (
        select(EvaluationSemanticMonitor)
        .options(*_monitor_options())
        .where(EvaluationSemanticMonitor.public_id == public_id)
    )
    if namespace_id is not None:
        query = query.where(EvaluationSemanticMonitor.namespace_id == namespace_id)
    monitor = (await db.execute(query)).scalar_one_or_none()
    if monitor is None:
        raise EvaluationHubNotFoundError("EvaluationSemanticMonitor not found")
    return monitor


async def list_evaluation_semantic_monitors(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int = 100,
) -> list[EvaluationSemanticMonitor]:
    values = await db.scalars(
        select(EvaluationSemanticMonitor)
        .options(*_monitor_options())
        .where(EvaluationSemanticMonitor.namespace_id == namespace_id)
        .order_by(EvaluationSemanticMonitor.created_at.desc())
        .limit(limit)
    )
    return list(values.unique())


async def set_evaluation_semantic_monitor_paused(
    db: AsyncSession,
    *,
    monitor: EvaluationSemanticMonitor,
    paused: bool,
    actor: User,
) -> EvaluationSemanticMonitor:
    await require_active_namespace(db, monitor.namespace_id)
    expected = (
        EvaluationSemanticMonitorStatus.ACTIVE
        if paused
        else EvaluationSemanticMonitorStatus.PAUSED
    )
    target = (
        EvaluationSemanticMonitorStatus.PAUSED
        if paused
        else EvaluationSemanticMonitorStatus.ACTIVE
    )
    if monitor.status != expected:
        raise EvaluationHubStateError(
            f"semantic monitor must be {expected.value}"
        )
    current = _utcnow_mysql_safe()
    monitor.status = target
    if not paused:
        monitor.next_run_at = current
    monitor.updated_at = current
    await db.flush()
    await audit(
        db,
        user=actor,
        action=f"evaluation_semantic_monitor.{target.value.lower()}",
        resource_type="evaluation_semantic_monitor",
        resource_id=monitor.id,
        namespace_id=monitor.namespace_id,
        details={"public_id": monitor.public_id, "status": target.value},
    )
    return monitor


async def _create_monitor_run(
    db: AsyncSession,
    *,
    monitor: EvaluationSemanticMonitor,
    scheduled_for: datetime,
    idempotency_key: str,
    actor: User | None,
) -> EvaluationSemanticMonitorRun:
    existing = await db.scalar(
        select(EvaluationSemanticMonitorRun)
        .options(*_run_options())
        .where(
            EvaluationSemanticMonitorRun.namespace_id == monitor.namespace_id,
            EvaluationSemanticMonitorRun.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.monitor_id != monitor.id:
            raise EvaluationHubConflictError("semantic monitor idempotency key is already bound")
        return existing
    current = _utcnow_mysql_safe()
    run = EvaluationSemanticMonitorRun(
        public_id=_public_id("esmr"),
        namespace_id=monitor.namespace_id,
        monitor_id=monitor.id,
        monitor=monitor,
        scheduled_for=_mysql_safe(scheduled_for),
        idempotency_key=idempotency_key,
        status=EvaluationSemanticMonitorRunStatus.QUEUED,
        attempt_count=0,
        available_at=current,
        created_at=current,
        updated_at=current,
    )
    run.candidate_run = None
    run.comparison = None
    run.alert = None
    db.add(run)
    await db.flush()
    await enqueue_domain_event(
        db,
        build_evaluation_semantic_monitor_run_queued(run),
        guaranteed_new=True,
    )
    await audit(
        db,
        user=actor,
        action="evaluation_semantic_monitor_run.queued",
        resource_type="evaluation_semantic_monitor_run",
        resource_id=run.id,
        namespace_id=run.namespace_id,
        details={
            "public_id": run.public_id,
            "monitor_public_id": monitor.public_id,
            "scheduled_for": run.scheduled_for.isoformat(),
            "status": run.status.value,
        },
    )
    return run


async def run_evaluation_semantic_monitor_now(
    db: AsyncSession,
    *,
    monitor: EvaluationSemanticMonitor,
    idempotency_key: str,
    actor: User,
) -> EvaluationSemanticMonitorRun:
    if _SAFE_IDEMPOTENCY_KEY.fullmatch(idempotency_key) is None:
        raise EvaluationHubStateError("semantic monitor idempotency key is invalid")
    await require_active_namespace(db, monitor.namespace_id)
    if monitor.status == EvaluationSemanticMonitorStatus.RETIRED:
        raise EvaluationHubStateError("semantic monitor is retired")
    return await _create_monitor_run(
        db,
        monitor=monitor,
        scheduled_for=_utcnow_mysql_safe(),
        idempotency_key=idempotency_key,
        actor=actor,
    )


async def queue_due_evaluation_semantic_monitors(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 20,
) -> list[EvaluationSemanticMonitorRun]:
    current = _mysql_safe(now) if now else _utcnow_mysql_safe()
    monitors = list(
        (
            await db.scalars(
                select(EvaluationSemanticMonitor)
                .options(*_monitor_options())
                .where(
                    EvaluationSemanticMonitor.status == EvaluationSemanticMonitorStatus.ACTIVE,
                    EvaluationSemanticMonitor.next_run_at <= current,
                )
                .order_by(EvaluationSemanticMonitor.next_run_at, EvaluationSemanticMonitor.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).unique()
    )
    queued: list[EvaluationSemanticMonitorRun] = []
    for monitor in monitors:
        scheduled_for = _mysql_safe(monitor.next_run_at)
        key = f"schedule:{monitor.public_id}:{int(scheduled_for.timestamp())}"
        creator = await db.get(User, monitor.created_by_user_id)
        queued.append(
            await _create_monitor_run(
                db,
                monitor=monitor,
                scheduled_for=scheduled_for,
                idempotency_key=key,
                actor=creator,
            )
        )
        next_run = scheduled_for + timedelta(seconds=monitor.interval_seconds)
        while next_run <= current:
            next_run += timedelta(seconds=monitor.interval_seconds)
        monitor.next_run_at = next_run
        monitor.updated_at = current
    await db.flush()
    return queued


async def list_due_evaluation_semantic_monitor_run_ids(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 20,
) -> list[str]:
    current = _mysql_safe(now) if now else _utcnow_mysql_safe()
    values = await db.scalars(
        select(EvaluationSemanticMonitorRun.public_id)
        .where(
            or_(
                (
                    EvaluationSemanticMonitorRun.status
                    == EvaluationSemanticMonitorRunStatus.QUEUED
                )
                & (EvaluationSemanticMonitorRun.available_at <= current),
                (
                    EvaluationSemanticMonitorRun.status
                    == EvaluationSemanticMonitorRunStatus.RUNNING
                )
                & (EvaluationSemanticMonitorRun.lease_expires_at <= current),
            )
        )
        .order_by(EvaluationSemanticMonitorRun.available_at, EvaluationSemanticMonitorRun.id)
        .limit(limit)
    )
    return list(values)


async def lease_evaluation_semantic_monitor_run(
    db: AsyncSession,
    *,
    run_public_id: str,
    worker_id: str,
    lease_seconds: int,
    max_attempts: int,
) -> EvaluationSemanticMonitorRun | None:
    current = _utcnow_mysql_safe()
    run = await db.scalar(
        select(EvaluationSemanticMonitorRun)
        .options(*_run_options())
        .where(EvaluationSemanticMonitorRun.public_id == run_public_id)
        .with_for_update()
    )
    if run is None:
        return None
    leaseable = (
        run.status == EvaluationSemanticMonitorRunStatus.QUEUED
        and _mysql_safe(run.available_at) <= current
    ) or (
        run.status == EvaluationSemanticMonitorRunStatus.RUNNING
        and run.lease_expires_at is not None
        and _mysql_safe(run.lease_expires_at) <= current
    )
    if not leaseable or run.attempt_count >= max_attempts:
        return None
    run.status = EvaluationSemanticMonitorRunStatus.RUNNING
    run.attempt_count += 1
    run.lease_owner = worker_id
    run.lease_expires_at = current + timedelta(seconds=lease_seconds)
    run.started_at = run.started_at or current
    run.updated_at = current
    await db.flush()
    return run


async def execute_evaluation_semantic_monitor_run(
    db: AsyncSession,
    *,
    run_public_id: str,
    worker_id: str,
    adapter: SemanticEmbeddingEvidencePort | None = None,
) -> EvaluationSemanticMonitorRun | None:
    run = await db.scalar(
        select(EvaluationSemanticMonitorRun)
        .options(*_run_options())
        .where(EvaluationSemanticMonitorRun.public_id == run_public_id)
        .with_for_update()
    )
    if (
        run is None
        or run.status != EvaluationSemanticMonitorRunStatus.RUNNING
        or run.lease_owner != worker_id
    ):
        return None
    monitor = run.monitor
    actor = await db.get(User, monitor.created_by_user_id)
    if actor is None:
        raise EvaluationHubStateError("semantic monitor creator no longer exists")
    candidate = await run_evaluation_semantic_clustering(
        db,
        version_public_id=monitor.candidate_policy_version.public_id,
        request=EvaluationSemanticClusteringRunCreate(
            source_case_routing_run_public_id=monitor.baseline_run.source_case_routing_run.public_id
        ),
        idempotency_key=f"monitor-cluster:{run.public_id}",
        actor=actor,
        adapter=adapter,
        allow_duplicate_evidence=True,
    )
    comparison = await create_evaluation_semantic_regression_comparison(
        db,
        request=EvaluationSemanticRegressionComparisonCreate(
            namespace_id=monitor.namespace_id,
            baseline_run_public_id=monitor.baseline_run.public_id,
            candidate_run_public_id=candidate.public_id,
            policy_version_public_id=monitor.regression_policy_version.public_id,
        ),
        idempotency_key=f"monitor-regress:{run.public_id}",
        actor=actor,
    )
    current = _utcnow_mysql_safe()
    run.candidate_run_id = candidate.id
    run.candidate_run = candidate
    run.comparison_id = comparison.id
    run.comparison = comparison
    run.outcome = comparison.outcome
    run.status = EvaluationSemanticMonitorRunStatus.COMPLETED
    run.error_code = None
    run.finished_at = current
    run.lease_owner = None
    run.lease_expires_at = None
    run.updated_at = current
    if comparison.outcome != EvaluationSemanticRegressionOutcome.PASS:
        severity = (
            EvaluationSemanticMonitorAlertSeverity.CRITICAL
            if comparison.outcome == EvaluationSemanticRegressionOutcome.DRIFTED
            else EvaluationSemanticMonitorAlertSeverity.WARNING
        )
        alert = await _open_alert(
            db,
            run=run,
            severity=severity,
            reason_codes=list(comparison.reason_codes_json),
            comparison=comparison,
        )
        run.alert = alert
    await db.flush()
    await enqueue_domain_event(
        db,
        build_evaluation_semantic_monitor_run_completed(run),
        guaranteed_new=True,
    )
    await audit(
        db,
        user=actor,
        action="evaluation_semantic_monitor_run.completed",
        resource_type="evaluation_semantic_monitor_run",
        resource_id=run.id,
        namespace_id=run.namespace_id,
        details={
            "public_id": run.public_id,
            "monitor_public_id": monitor.public_id,
            "candidate_run_public_id": candidate.public_id,
            "comparison_public_id": comparison.public_id,
            "outcome": comparison.outcome.value,
            "attempt_count": run.attempt_count,
        },
    )
    return run


async def _open_alert(
    db: AsyncSession,
    *,
    run: EvaluationSemanticMonitorRun,
    severity: EvaluationSemanticMonitorAlertSeverity,
    reason_codes: list[str],
    comparison: EvaluationSemanticRegressionComparison | None = None,
    error_code: str | None = None,
) -> EvaluationSemanticMonitorAlert:
    existing = await db.scalar(
        select(EvaluationSemanticMonitorAlert)
        .options(*_alert_options())
        .where(EvaluationSemanticMonitorAlert.monitor_run_id == run.id)
    )
    if existing is not None:
        return existing
    current = _utcnow_mysql_safe()
    alert = EvaluationSemanticMonitorAlert(
        public_id=_public_id("esma"),
        namespace_id=run.namespace_id,
        monitor_id=run.monitor_id,
        monitor=run.monitor,
        monitor_run_id=run.id,
        monitor_run=run,
        comparison_id=comparison.id if comparison is not None else None,
        comparison=comparison,
        severity=severity,
        status=EvaluationSemanticMonitorAlertStatus.OPEN,
        reason_codes_json=reason_codes,
        error_code=error_code,
        created_at=current,
        updated_at=current,
    )
    db.add(alert)
    await db.flush()
    await enqueue_domain_event(
        db,
        build_evaluation_semantic_monitor_alert_opened(alert),
        guaranteed_new=True,
    )
    return alert


async def fail_evaluation_semantic_monitor_run(
    db: AsyncSession,
    *,
    run_public_id: str,
    worker_id: str,
    error_code: str,
    max_attempts: int,
    base_retry_seconds: int,
    max_retry_seconds: int,
) -> EvaluationSemanticMonitorRunStatus | None:
    run = await db.scalar(
        select(EvaluationSemanticMonitorRun)
        .options(*_run_options())
        .where(EvaluationSemanticMonitorRun.public_id == run_public_id)
        .with_for_update()
    )
    if (
        run is None
        or run.status != EvaluationSemanticMonitorRunStatus.RUNNING
        or run.lease_owner != worker_id
    ):
        return None
    current = _utcnow_mysql_safe()
    run.error_code = error_code[:100]
    run.lease_owner = None
    run.lease_expires_at = None
    run.updated_at = current
    if run.attempt_count < max_attempts:
        delay = min(
            max_retry_seconds,
            base_retry_seconds * (2 ** max(0, run.attempt_count - 1)),
        )
        run.status = EvaluationSemanticMonitorRunStatus.QUEUED
        run.available_at = current + timedelta(seconds=delay)
    else:
        run.status = EvaluationSemanticMonitorRunStatus.FAILED
        run.finished_at = current
        alert = await _open_alert(
            db,
            run=run,
            severity=EvaluationSemanticMonitorAlertSeverity.WARNING,
            reason_codes=["semantic_monitor_execution_failed"],
            error_code=run.error_code,
        )
        run.alert = alert
        actor = await db.get(User, run.monitor.created_by_user_id)
        await audit(
            db,
            user=actor,
            action="evaluation_semantic_monitor_run.failed",
            resource_type="evaluation_semantic_monitor_run",
            resource_id=run.id,
            namespace_id=run.namespace_id,
            details={
                "public_id": run.public_id,
                "monitor_public_id": run.monitor.public_id,
                "status": run.status.value,
                "attempt_count": run.attempt_count,
                "error_code": run.error_code,
            },
        )
    await db.flush()
    return run.status


async def get_evaluation_semantic_monitor_run(
    db: AsyncSession,
    *,
    public_id: str,
) -> EvaluationSemanticMonitorRun:
    run = await db.scalar(
        select(EvaluationSemanticMonitorRun)
        .options(*_run_options())
        .where(EvaluationSemanticMonitorRun.public_id == public_id)
    )
    if run is None:
        raise EvaluationHubNotFoundError("EvaluationSemanticMonitorRun not found")
    return run


async def list_evaluation_semantic_monitor_runs(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int = 100,
) -> list[EvaluationSemanticMonitorRun]:
    values = await db.scalars(
        select(EvaluationSemanticMonitorRun)
        .options(*_run_options())
        .where(EvaluationSemanticMonitorRun.namespace_id == namespace_id)
        .order_by(EvaluationSemanticMonitorRun.created_at.desc())
        .limit(limit)
    )
    return list(values.unique())


async def list_evaluation_semantic_monitor_alerts(
    db: AsyncSession,
    *,
    namespace_id: int,
    status: EvaluationSemanticMonitorAlertStatus | None = None,
    limit: int = 100,
) -> list[EvaluationSemanticMonitorAlert]:
    query = (
        select(EvaluationSemanticMonitorAlert)
        .options(*_alert_options())
        .where(EvaluationSemanticMonitorAlert.namespace_id == namespace_id)
    )
    if status is not None:
        query = query.where(EvaluationSemanticMonitorAlert.status == status)
    values = await db.scalars(
        query.order_by(EvaluationSemanticMonitorAlert.created_at.desc()).limit(limit)
    )
    return list(values.unique())


async def get_evaluation_semantic_monitor_alert(
    db: AsyncSession,
    *,
    public_id: str,
) -> EvaluationSemanticMonitorAlert:
    alert = await db.scalar(
        select(EvaluationSemanticMonitorAlert)
        .options(*_alert_options())
        .where(EvaluationSemanticMonitorAlert.public_id == public_id)
    )
    if alert is None:
        raise EvaluationHubNotFoundError("EvaluationSemanticMonitorAlert not found")
    return alert


async def acknowledge_evaluation_semantic_monitor_alert(
    db: AsyncSession,
    *,
    alert: EvaluationSemanticMonitorAlert,
    request: EvaluationSemanticMonitorAcknowledge,
    actor: User,
) -> EvaluationSemanticMonitorAlert:
    await require_active_namespace(db, alert.namespace_id)
    if alert.status != EvaluationSemanticMonitorAlertStatus.OPEN:
        raise EvaluationHubStateError("semantic monitor alert is already acknowledged")
    current = _utcnow_mysql_safe()
    alert.status = EvaluationSemanticMonitorAlertStatus.ACKNOWLEDGED
    alert.acknowledged_by_user_id = actor.id
    alert.acknowledged_at = current
    alert.acknowledgement_note = request.note
    alert.updated_at = current
    await db.flush()
    await enqueue_domain_event(
        db,
        build_evaluation_semantic_monitor_alert_acknowledged(alert),
        guaranteed_new=True,
    )
    await audit(
        db,
        user=actor,
        action="evaluation_semantic_monitor_alert.acknowledged",
        resource_type="evaluation_semantic_monitor_alert",
        resource_id=alert.id,
        namespace_id=alert.namespace_id,
        details={
            "public_id": alert.public_id,
            "monitor_public_id": alert.monitor.public_id,
            "monitor_run_public_id": alert.monitor_run.public_id,
            "status": alert.status.value,
            "acknowledged_at": current.isoformat(),
        },
    )
    return alert
