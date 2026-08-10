from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import HttpMetricSummary, http_metrics
from app.core.time import ensure_utc
from app.models.operations import (
    OpsIncident,
    OpsIncidentSeverity,
    OpsIncidentStatus,
    OpsRecoveryDrill,
    OpsRecoveryDrillStatus,
    OpsSLOEvaluation,
    OpsSLOEvaluationStatus,
)
from app.models.user import User
from app.schemas.operations import OpsRecoveryDrillCreate
from app.services.audit_service import audit
from app.services.outbox_dispatcher_service import get_outbox_health


PROFILE_VERSION = "duckdock-ga-slo-v1"
THRESHOLDS: dict[str, float | int] = {
    "http_error_ratio_max": 0.01,
    "evidence_ingest_p95_ms_max": 200.0,
    "run_timeline_p95_ms_max": 2000.0,
    "policy_decision_p95_ms_max": 100.0,
    "outbox_failed_count_max": 0,
    "outbox_pending_age_seconds_max": 300,
    "recovery_rpo_seconds_max": 900,
    "recovery_rto_seconds_max": 14400,
}


class OperationsError(ValueError):
    pass


class OperationsNotFoundError(OperationsError):
    pass


class OperationsStateError(OperationsError):
    pass


class OperationsConflictError(OperationsError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _canonical(value: Any) -> str:
    def default(item: Any) -> Any:
        if isinstance(item, datetime):
            return ensure_utc(item).isoformat().replace("+00:00", "Z")
        raise TypeError(f"unsupported canonical type: {type(item).__name__}")

    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=default)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _route_metrics(*, window_minutes: int | None = None) -> list[HttpMetricSummary]:
    window_seconds = window_minutes * 60 if window_minutes is not None else None
    return [
        item
        for item in http_metrics.summaries(window_seconds=window_seconds)
        if "/operations" not in item.route
    ]


def _category_p95(rows: list[HttpMetricSummary], category: str) -> float | None:
    values: list[float] = []
    for row in rows:
        route = row.route.lower()
        match = False
        if category == "evidence_ingest":
            match = row.method == "POST" and "/reporter" in route
        elif category == "run_timeline":
            match = row.method == "GET" and "/agent-runs" in route
        elif category == "policy_decision":
            match = row.method == "POST" and "policy-evaluations" in route
        if match and row.p95_ms is not None:
            values.append(row.p95_ms)
    return max(values) if values else None


async def get_operations_overview(db: AsyncSession) -> dict[str, Any]:
    outbox = await get_outbox_health(db)
    latest_evaluation = await db.scalar(
        select(OpsSLOEvaluation).order_by(OpsSLOEvaluation.created_at.desc()).limit(1)
    )
    open_incidents = list(
        (
            await db.execute(
                select(OpsIncident)
                .where(OpsIncident.status != OpsIncidentStatus.RESOLVED)
                .order_by(OpsIncident.created_at.desc())
                .limit(100)
            )
        ).scalars()
    )
    latest_drill = await db.scalar(
        select(OpsRecoveryDrill).order_by(OpsRecoveryDrill.finished_at.desc()).limit(1)
    )
    return {
        "profile_version": PROFILE_VERSION,
        "metrics_path": "/metrics",
        "thresholds": dict(THRESHOLDS),
        "route_metrics": _route_metrics(),
        "outbox": {
            "pending_count": outbox.pending_count,
            "leased_count": outbox.leased_count,
            "expired_lease_count": outbox.expired_lease_count,
            "failed_count": outbox.failed_count,
            "published_count": outbox.published_count,
            "oldest_pending_age_seconds": outbox.oldest_pending_age_seconds,
        },
        "latest_evaluation": latest_evaluation,
        "open_incidents": open_incidents,
        "latest_recovery_drill": latest_drill,
    }


async def evaluate_operations_slo(
    db: AsyncSession,
    *,
    idempotency_key: str,
    window_minutes: int,
    actor: User,
    now: datetime | None = None,
) -> OpsSLOEvaluation:
    existing = await db.scalar(
        select(OpsSLOEvaluation).where(
            OpsSLOEvaluation.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        if existing.window_minutes != window_minutes:
            raise OperationsConflictError("idempotency key was used with another window")
        return existing

    current = now or _utcnow()
    rows = _route_metrics(window_minutes=window_minutes)
    request_count = sum(row.request_count for row in rows)
    error_count = sum(row.error_count for row in rows)
    error_ratio = error_count / request_count if request_count else None
    evidence_p95 = _category_p95(rows, "evidence_ingest")
    timeline_p95 = _category_p95(rows, "run_timeline")
    policy_p95 = _category_p95(rows, "policy_decision")
    outbox = await get_outbox_health(db, now=current)

    breaches: list[str] = []
    degraded: list[str] = []
    if request_count < 20:
        degraded.append("HTTP_SAMPLE_WINDOW_INSUFFICIENT")
    elif error_ratio is not None and error_ratio > float(THRESHOLDS["http_error_ratio_max"]):
        breaches.append("HTTP_ERROR_RATIO_EXCEEDED")
    for value, threshold_key, missing_code, breach_code in (
        (
            evidence_p95,
            "evidence_ingest_p95_ms_max",
            "EVIDENCE_INGEST_NO_TRAFFIC",
            "EVIDENCE_INGEST_P95_EXCEEDED",
        ),
        (
            timeline_p95,
            "run_timeline_p95_ms_max",
            "RUN_TIMELINE_NO_TRAFFIC",
            "RUN_TIMELINE_P95_EXCEEDED",
        ),
        (
            policy_p95,
            "policy_decision_p95_ms_max",
            "POLICY_DECISION_NO_TRAFFIC",
            "POLICY_DECISION_P95_EXCEEDED",
        ),
    ):
        if value is None:
            degraded.append(missing_code)
        elif value > float(THRESHOLDS[threshold_key]):
            breaches.append(breach_code)
    if outbox.failed_count > int(THRESHOLDS["outbox_failed_count_max"]):
        breaches.append("OUTBOX_FAILED_EVENTS_PRESENT")
    if (
        outbox.oldest_pending_age_seconds is not None
        and outbox.oldest_pending_age_seconds
        > int(THRESHOLDS["outbox_pending_age_seconds_max"])
    ):
        breaches.append("OUTBOX_PENDING_AGE_EXCEEDED")

    reason_codes = sorted(set(breaches or degraded))
    status = (
        OpsSLOEvaluationStatus.BREACHED
        if breaches
        else OpsSLOEvaluationStatus.DEGRADED
        if degraded
        else OpsSLOEvaluationStatus.HEALTHY
    )
    evidence = {
        "profile_version": PROFILE_VERSION,
        "window_minutes": window_minutes,
        "request_count": request_count,
        "error_count": error_count,
        "http_error_ratio": round(error_ratio, 8) if error_ratio is not None else None,
        "evidence_ingest_p95_ms": evidence_p95,
        "run_timeline_p95_ms": timeline_p95,
        "policy_decision_p95_ms": policy_p95,
        "outbox_failed_count": outbox.failed_count,
        "outbox_oldest_pending_age_seconds": outbox.oldest_pending_age_seconds,
        "status": status.value,
        "reason_codes": reason_codes,
        "evaluated_at": current,
    }
    row = OpsSLOEvaluation(
        idempotency_key=idempotency_key,
        profile_version=PROFILE_VERSION,
        window_minutes=window_minutes,
        request_count=request_count,
        error_count=error_count,
        http_error_ratio=evidence["http_error_ratio"],
        evidence_ingest_p95_ms=evidence_p95,
        run_timeline_p95_ms=timeline_p95,
        policy_decision_p95_ms=policy_p95,
        outbox_failed_count=outbox.failed_count,
        outbox_oldest_pending_age_seconds=outbox.oldest_pending_age_seconds,
        status=status,
        reason_codes_json=reason_codes,
        evidence_digest=_digest(evidence),
        evaluated_by_user_id=actor.id,
        evaluated_at=current,
        created_at=current,
    )
    db.add(row)
    await db.flush()

    if status == OpsSLOEvaluationStatus.BREACHED:
        severe = (
            "OUTBOX_FAILED_EVENTS_PRESENT" in breaches
            or (error_ratio or 0.0) > 0.05
            or any(
                value is not None and value > float(THRESHOLDS[key]) * 2
                for value, key in (
                    (evidence_p95, "evidence_ingest_p95_ms_max"),
                    (timeline_p95, "run_timeline_p95_ms_max"),
                    (policy_p95, "policy_decision_p95_ms_max"),
                )
            )
        )
        incident_evidence = {
            "slo_evaluation_public_id": row.public_id,
            "slo_evidence_digest": row.evidence_digest,
            "severity": "CRITICAL" if severe else "WARNING",
            "reason_codes": breaches,
        }
        incident = OpsIncident(
            slo_evaluation_id=row.id,
            severity=(
                OpsIncidentSeverity.CRITICAL if severe else OpsIncidentSeverity.WARNING
            ),
            status=OpsIncidentStatus.OPEN,
            reason_codes_json=breaches,
            evidence_digest=_digest(incident_evidence),
            created_at=current,
            updated_at=current,
        )
        db.add(incident)
    elif status == OpsSLOEvaluationStatus.HEALTHY:
        unresolved = list(
            (
                await db.execute(
                    select(OpsIncident)
                    .where(OpsIncident.status != OpsIncidentStatus.RESOLVED)
                    .with_for_update()
                )
            ).scalars()
        )
        for incident in unresolved:
            incident.status = OpsIncidentStatus.RESOLVED
            incident.resolved_by_user_id = actor.id
            incident.resolved_at = current
            incident.updated_at = current

    await audit(
        db,
        user=actor,
        action="operations.slo_evaluated",
        resource_type="ops_slo_evaluation",
        resource_id=row.id,
        details={
            "public_id": row.public_id,
            "profile_version": PROFILE_VERSION,
            "status": status.value,
            "reason_codes": reason_codes,
            "evidence_digest": row.evidence_digest,
        },
    )
    await db.flush()
    return row


async def list_slo_evaluations(db: AsyncSession, *, limit: int) -> list[OpsSLOEvaluation]:
    return list(
        (
            await db.execute(
                select(OpsSLOEvaluation)
                .order_by(OpsSLOEvaluation.created_at.desc())
                .limit(limit)
            )
        ).scalars()
    )


async def list_ops_incidents(db: AsyncSession, *, limit: int) -> list[OpsIncident]:
    return list(
        (
            await db.execute(
                select(OpsIncident).order_by(OpsIncident.created_at.desc()).limit(limit)
            )
        ).scalars()
    )


async def _get_incident_for_update(db: AsyncSession, public_id: str) -> OpsIncident:
    row = await db.scalar(
        select(OpsIncident).where(OpsIncident.public_id == public_id).with_for_update()
    )
    if row is None:
        raise OperationsNotFoundError("operations incident was not found")
    return row


async def acknowledge_ops_incident(
    db: AsyncSession, *, public_id: str, actor: User, note: str
) -> OpsIncident:
    row = await _get_incident_for_update(db, public_id)
    if row.status != OpsIncidentStatus.OPEN:
        raise OperationsStateError("only an OPEN incident can be acknowledged")
    current = _utcnow()
    row.status = OpsIncidentStatus.ACKNOWLEDGED
    row.acknowledged_by_user_id = actor.id
    row.acknowledged_at = current
    row.updated_at = current
    await audit(
        db,
        user=actor,
        action="operations.incident_acknowledged",
        resource_type="ops_incident",
        resource_id=row.id,
        details={"public_id": row.public_id, "note_provided": bool(note.strip())},
    )
    await db.flush()
    return row


async def resolve_ops_incident(
    db: AsyncSession, *, public_id: str, actor: User, note: str
) -> OpsIncident:
    row = await _get_incident_for_update(db, public_id)
    if row.status == OpsIncidentStatus.RESOLVED:
        raise OperationsStateError("incident is already resolved")
    current = _utcnow()
    row.status = OpsIncidentStatus.RESOLVED
    row.resolved_by_user_id = actor.id
    row.resolved_at = current
    row.updated_at = current
    await audit(
        db,
        user=actor,
        action="operations.incident_resolved",
        resource_type="ops_incident",
        resource_id=row.id,
        details={"public_id": row.public_id, "note_provided": bool(note.strip())},
    )
    await db.flush()
    return row


async def record_recovery_drill(
    db: AsyncSession, *, request: OpsRecoveryDrillCreate, actor: User
) -> OpsRecoveryDrill:
    started_at = ensure_utc(request.started_at)
    finished_at = ensure_utc(request.finished_at)
    reasons: list[str] = []
    if finished_at < started_at:
        reasons.append("INVALID_TIME_WINDOW")
    if request.mysql_row_count < 1:
        reasons.append("MYSQL_RESTORE_EMPTY")
    if request.object_count < 1:
        reasons.append("OBJECT_STORE_RESTORE_EMPTY")
    if request.rpo_seconds > int(THRESHOLDS["recovery_rpo_seconds_max"]):
        reasons.append("RPO_EXCEEDED")
    if request.rto_seconds > int(THRESHOLDS["recovery_rto_seconds_max"]):
        reasons.append("RTO_EXCEEDED")
    status = OpsRecoveryDrillStatus.FAILED if reasons else OpsRecoveryDrillStatus.PASSED
    evidence = {
        "environment": request.environment,
        "git_head": request.git_head,
        "backup_set_digest": request.backup_set_digest,
        "mysql_digest": request.mysql_digest,
        "object_store_digest": request.object_store_digest,
        "mysql_row_count": request.mysql_row_count,
        "object_count": request.object_count,
        "rpo_seconds": request.rpo_seconds,
        "rto_seconds": request.rto_seconds,
        "started_at": started_at,
        "finished_at": finished_at,
        "status": status.value,
        "reason_codes": reasons,
    }
    evidence_digest = _digest(evidence)
    existing = await db.scalar(
        select(OpsRecoveryDrill).where(
            OpsRecoveryDrill.idempotency_key == request.idempotency_key
        )
    )
    if existing is not None:
        if existing.evidence_digest != evidence_digest:
            raise OperationsConflictError("recovery drill idempotency payload conflict")
        return existing
    row = OpsRecoveryDrill(
        idempotency_key=request.idempotency_key,
        environment=request.environment,
        git_head=request.git_head,
        backup_set_digest=request.backup_set_digest,
        mysql_digest=request.mysql_digest,
        object_store_digest=request.object_store_digest,
        mysql_row_count=request.mysql_row_count,
        object_count=request.object_count,
        rpo_seconds=request.rpo_seconds,
        rto_seconds=request.rto_seconds,
        status=status,
        reason_codes_json=reasons,
        evidence_digest=evidence_digest,
        executed_by_user_id=actor.id,
        started_at=started_at,
        finished_at=finished_at,
    )
    db.add(row)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="operations.recovery_drill_recorded",
        resource_type="ops_recovery_drill",
        resource_id=row.id,
        details={
            "public_id": row.public_id,
            "environment": row.environment,
            "status": row.status.value,
            "rpo_seconds": row.rpo_seconds,
            "rto_seconds": row.rto_seconds,
            "evidence_digest": row.evidence_digest,
        },
    )
    await db.flush()
    return row


async def list_recovery_drills(db: AsyncSession, *, limit: int) -> list[OpsRecoveryDrill]:
    return list(
        (
            await db.execute(
                select(OpsRecoveryDrill)
                .order_by(OpsRecoveryDrill.finished_at.desc())
                .limit(limit)
            )
        ).scalars()
    )
