from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.compatibility import (
    OutboxConsumerReceipt,
    ReconciliationStatus,
    V1V2Reconciliation,
)
from app.models.control_plane import WorkTrace
from app.models.execution import AgentRun
from app.models.outbox import OutboxEvent
from app.models.telemetry import AgentRunArtifact
from app.services.outbox_event_service import (
    OUTBOX_SCHEMA_VERSION,
    STRUCTURED_REPORT_RECORDED,
)


COMPATIBILITY_CONSUMER_NAME = "v1_v2_reconciliation_v1"


class CompatibilityReconciliationError(ValueError):
    pass


class CompatibilityEventNotFoundError(CompatibilityReconciliationError):
    pass


class CompatibilityWorkTraceNotFoundError(
    CompatibilityReconciliationError
):
    pass


@dataclass(frozen=True, slots=True)
class CompatibilityProcessingResult:
    handled: bool
    replayed: bool
    status: ReconciliationStatus | None


@dataclass(frozen=True, slots=True)
class ReconciliationHealth:
    matched_count: int
    expected_legacy_only_count: int
    mismatch_count: int
    unexplained_difference_count: int


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _required_int(payload: dict, field: str) -> int:
    value = payload.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise CompatibilityReconciliationError(
            f"{field} must be a positive integer"
        )
    return value


async def _reconcile_work_trace(
    db: AsyncSession,
    *,
    event: OutboxEvent,
) -> V1V2Reconciliation:
    payload = event.payload_json
    if not isinstance(payload, dict):
        raise CompatibilityReconciliationError(
            "compatibility event payload must be an object"
        )
    work_trace_id = _required_int(payload, "work_trace_id")
    namespace_id = _required_int(payload, "namespace_id")
    runtime_id = _required_int(payload, "runtime_id")
    report_id = payload.get("report_id")
    if not isinstance(report_id, str) or not report_id:
        raise CompatibilityReconciliationError(
            "report_id must be a non-empty string"
        )

    trace = (
        await db.execute(
            select(WorkTrace)
            .where(WorkTrace.id == work_trace_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if trace is None:
        raise CompatibilityReconciliationError(
            "structured report WorkTrace does not exist"
        )

    runs = list(
        (
            await db.execute(
                select(AgentRun).where(
                    AgentRun.work_trace_id == trace.id
                )
            )
        ).scalars()
    )
    run_ids = [run.id for run in runs]
    artifact_count = 0
    if run_ids:
        artifact_count = int(
            await db.scalar(
                select(func.count(AgentRunArtifact.id)).where(
                    AgentRunArtifact.agent_run_id.in_(run_ids)
                )
            )
            or 0
        )

    event_scope_matches = (
        event.namespace_id == namespace_id == trace.namespace_id
        and runtime_id == trace.runtime_id
        and event.aggregate_type == "WorkTrace"
        and event.aggregate_public_id == str(trace.id)
    )
    linked_scope_matches = all(
        run.namespace_id == trace.namespace_id
        and run.runtime_id == trace.runtime_id
        for run in runs
    )
    if not event_scope_matches or not linked_scope_matches:
        status = ReconciliationStatus.MISMATCH
        reason_code = "tenant_or_runtime_mismatch"
    elif runs:
        status = ReconciliationStatus.MATCHED
        reason_code = "typed_agent_run_links_match"
    else:
        status = ReconciliationStatus.EXPECTED_LEGACY_ONLY
        reason_code = "management_summary_without_execution_fact"

    row = (
        await db.execute(
            select(V1V2Reconciliation)
            .where(V1V2Reconciliation.work_trace_id == trace.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    now = _utcnow()
    if row is None:
        row = V1V2Reconciliation(
            namespace_id=trace.namespace_id,
            runtime_id=runtime_id,
            work_trace_id=trace.id,
            source_report_id=report_id,
            status=status,
            reason_code=reason_code,
            linked_run_count=len(runs),
            linked_artifact_count=artifact_count,
            last_event_id=event.event_id,
            checked_at=now,
        )
        db.add(row)
    else:
        row.namespace_id = trace.namespace_id
        row.runtime_id = runtime_id
        row.source_report_id = report_id
        row.status = status
        row.reason_code = reason_code
        row.linked_run_count = len(runs)
        row.linked_artifact_count = artifact_count
        row.last_event_id = event.event_id
        row.checked_at = now
    await db.flush()
    return row


async def process_compatibility_outbox_event(
    db: AsyncSession,
    *,
    event_id: str,
) -> CompatibilityProcessingResult:
    event = (
        await db.execute(
            select(OutboxEvent)
            .where(OutboxEvent.event_id == event_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if event is None:
        raise CompatibilityEventNotFoundError(
            "Outbox event does not exist"
        )
    if event.event_type != STRUCTURED_REPORT_RECORDED:
        return CompatibilityProcessingResult(
            handled=False,
            replayed=False,
            status=None,
        )
    if event.schema_version != OUTBOX_SCHEMA_VERSION:
        raise CompatibilityReconciliationError(
            "unsupported compatibility event schema"
        )

    receipt = (
        await db.execute(
            select(OutboxConsumerReceipt).where(
                OutboxConsumerReceipt.consumer_name
                == COMPATIBILITY_CONSUMER_NAME,
                OutboxConsumerReceipt.event_id == event.event_id,
            )
        )
    ).scalar_one_or_none()
    if receipt is not None:
        return CompatibilityProcessingResult(
            handled=True,
            replayed=True,
            status=ReconciliationStatus(receipt.result_code),
        )

    reconciliation = await _reconcile_work_trace(db, event=event)
    db.add(
        OutboxConsumerReceipt(
            consumer_name=COMPATIBILITY_CONSUMER_NAME,
            event_id=event.event_id,
            event_type=event.event_type,
            result_code=reconciliation.status.value,
        )
    )
    await db.flush()
    return CompatibilityProcessingResult(
        handled=True,
        replayed=False,
        status=reconciliation.status,
    )


async def get_reconciliation_health(
    db: AsyncSession,
) -> ReconciliationHealth:
    async def count(status: ReconciliationStatus) -> int:
        return int(
            await db.scalar(
                select(func.count(V1V2Reconciliation.id)).where(
                    V1V2Reconciliation.status == status
                )
            )
            or 0
        )

    matched = await count(ReconciliationStatus.MATCHED)
    expected = await count(
        ReconciliationStatus.EXPECTED_LEGACY_ONLY
    )
    mismatch = await count(ReconciliationStatus.MISMATCH)
    return ReconciliationHealth(
        matched_count=matched,
        expected_legacy_only_count=expected,
        mismatch_count=mismatch,
        unexplained_difference_count=mismatch,
    )


async def reconcile_work_trace_now(
    db: AsyncSession,
    *,
    work_trace_id: int,
) -> V1V2Reconciliation:
    event = (
        await db.execute(
            select(OutboxEvent)
            .where(
                OutboxEvent.event_type
                == STRUCTURED_REPORT_RECORDED,
                OutboxEvent.aggregate_type == "WorkTrace",
                OutboxEvent.aggregate_public_id
                == str(work_trace_id),
            )
            .order_by(OutboxEvent.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if event is None:
        raise CompatibilityWorkTraceNotFoundError(
            "Structured Report reconciliation source not found"
        )
    return await _reconcile_work_trace(db, event=event)


async def get_work_trace_reconciliation(
    db: AsyncSession,
    *,
    work_trace_id: int,
) -> V1V2Reconciliation:
    row = await db.scalar(
        select(V1V2Reconciliation).where(
            V1V2Reconciliation.work_trace_id == work_trace_id
        )
    )
    if row is None:
        raise CompatibilityWorkTraceNotFoundError(
            "WorkTrace reconciliation not found"
        )
    return row
