"""G1 v1 Structured Report compatibility and explainable v2 reconciliation."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.api.v2.endpoints.compatibility import (
    get_work_trace_reconciliation_endpoint,
    reconciliation_health,
    reconcile_work_trace_endpoint,
)
from app.core.deps import require_admin
from app.models.compatibility import (
    OutboxConsumerReceipt,
    ReconciliationStatus,
    V1V2Reconciliation,
)
from app.models.control_plane import (
    CollectionJob,
    ReporterCredential,
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
    WorkTrace,
)
from app.models.execution import (
    AgentRun,
    AgentRunStatus,
    ContentCaptureMode,
    TrustLevel,
    TrustSource,
)
from app.models.namespace import Namespace
from app.models.outbox import OutboxEvent
from app.models.user import SystemRole, User
from app.schemas.compatibility import V1V2ReconciliationOut
from app.schemas.control_plane import StructuredReportSubmit
from app.schemas.execution import RunStartEnvelope
from app.services.compatibility_reconciliation_service import (
    get_reconciliation_health,
    process_compatibility_outbox_event,
)
from app.services.execution_service import start_run
from app.services.outbox_event_service import STRUCTURED_REPORT_RECORDED
from app.services.report_upload_service import ReporterAuthContext
from app.services.structured_report_service import ingest_structured_report


NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


async def _scope(db, suffix: str):
    user = User(
        username=f"reconcile-{suffix}",
        email=f"reconcile-{suffix}@example.test",
        hashed_password="unused",
        system_role=SystemRole.USER,
    )
    db.add(user)
    await db.flush()
    namespace = Namespace(name=f"reconcile-{suffix}", owner_id=user.id)
    db.add(namespace)
    await db.flush()
    runtime = RuntimeInstance(
        namespace_id=namespace.id,
        provider=RuntimeProvider.CUSTOM,
        name=f"Reconciliation Runtime {suffix}",
        deploy_type=RuntimeDeployType.PRIVATE,
    )
    db.add(runtime)
    await db.flush()
    credential = ReporterCredential(
        runtime_id=runtime.id,
        user_id=user.id,
        device_id=f"reconcile-device-{suffix}",
        name=f"Reconciliation Reporter {suffix}",
        token_prefix=f"rec{suffix}"[:16],
        token_hash="unused",
        scopes=["report.structured", "execution.write"],
    )
    db.add(credential)
    await db.flush()
    return user, namespace, runtime, ReporterAuthContext(
        runtime=runtime,
        token=credential,
        source="reporter_credential",
    )


def _report(runtime_id: int, suffix: str) -> StructuredReportSubmit:
    return StructuredReportSubmit(
        runtime_id=runtime_id,
        report_type="weekly",
        title=f"Compatibility report {suffix}",
        summary="Management summary must not become a synthetic AgentRun.",
        period_start=NOW,
        period_end=NOW,
        idempotency_key=f"reconciliation-{suffix}",
        highlights=["low-sensitive event only"],
        metadata_json={"attempt": 1},
    )


async def _ingest(db, suffix: str = "base"):
    _, namespace, runtime, reporter = await _scope(db, suffix)
    result = await ingest_structured_report(
        db,
        reporter=reporter,
        body=_report(runtime.id, suffix),
    )
    trace = await db.get(WorkTrace, result.work_trace.id)
    event = (
        await db.execute(
            select(OutboxEvent).where(
                OutboxEvent.event_type == STRUCTURED_REPORT_RECORDED
            )
        )
    ).scalar_one()
    assert trace is not None
    return namespace, runtime, trace, event


async def test_structured_report_writes_low_sensitive_event_without_synthetic_run(
    async_session,
) -> None:
    namespace, runtime, trace, event = await _ingest(async_session)

    assert event.namespace_id == namespace.id
    assert event.aggregate_type == "WorkTrace"
    assert event.aggregate_public_id == str(trace.id)
    assert event.payload_json == {
        "ended_at": NOW.isoformat().replace("+00:00", "Z"),
        "namespace_id": namespace.id,
        "report_id": trace.metadata_json["report_id"],
        "report_type": "weekly",
        "runtime_id": runtime.id,
        "started_at": NOW.isoformat().replace("+00:00", "Z"),
        "work_trace_id": trace.id,
    }
    serialized = str(event.payload_json).lower()
    assert "management summary" not in serialized
    assert "highlight" not in serialized
    assert await async_session.scalar(
        select(func.count(AgentRun.id))
    ) == 0


async def test_consumer_records_expected_legacy_only_and_dedupes_event(
    async_session,
) -> None:
    _, _, trace, event = await _ingest(async_session, "legacy-only")

    first = await process_compatibility_outbox_event(
        async_session,
        event_id=event.event_id,
    )
    await async_session.commit()
    second = await process_compatibility_outbox_event(
        async_session,
        event_id=event.event_id,
    )

    assert first.handled is True
    assert first.replayed is False
    assert first.status == ReconciliationStatus.EXPECTED_LEGACY_ONLY
    assert second.handled is True
    assert second.replayed is True
    reconciliation = (
        await async_session.execute(select(V1V2Reconciliation))
    ).scalar_one()
    assert reconciliation.work_trace_id == trace.id
    assert reconciliation.reason_code == (
        "management_summary_without_execution_fact"
    )
    assert reconciliation.linked_run_count == 0
    assert reconciliation.linked_artifact_count == 0
    assert await async_session.scalar(
        select(func.count(OutboxConsumerReceipt.id))
    ) == 1
    assert await async_session.scalar(
        select(func.count(AgentRun.id))
    ) == 0


async def test_typed_agent_run_link_reconciles_without_latest_fallback(
    async_session,
) -> None:
    namespace, runtime, trace, event = await _ingest(async_session, "matched")
    run = await start_run(
        async_session,
        namespace_id=namespace.id,
        runtime_id=runtime.id,
        idempotency_key="reconciliation-run-start",
        envelope=RunStartEnvelope(
            external_run_id="reconciliation-run",
            work_trace_id=trace.id,
            started_at=NOW,
            source_schema="duckdock-run-envelope",
            source_schema_version="1.0",
            content_capture_mode="metadata_only",
        ),
        trust_level=TrustLevel.CHANNEL_AUTHENTICATED,
        trust_source=TrustSource.REPORTER,
    )

    result = await process_compatibility_outbox_event(
        async_session,
        event_id=event.event_id,
    )

    assert result.status == ReconciliationStatus.MATCHED
    row = (
        await async_session.execute(select(V1V2Reconciliation))
    ).scalar_one()
    assert row.linked_run_count == 1
    assert row.linked_artifact_count == 0
    assert row.reason_code == "typed_agent_run_links_match"
    assert run.work_trace_id == trace.id


async def test_cross_tenant_typed_link_is_an_unexplained_mismatch(
    async_session,
) -> None:
    _, _, trace, event = await _ingest(async_session, "mismatch-source")
    _, other_namespace, other_runtime, _ = await _scope(
        async_session,
        "mismatch-target",
    )
    async_session.add(
        AgentRun(
            public_id="run_reconciliation_mismatch",
            namespace_id=other_namespace.id,
            runtime_id=other_runtime.id,
            work_trace_id=trace.id,
            external_run_id="reconciliation-mismatch",
            status=AgentRunStatus.STARTED,
            trust_level=TrustLevel.UNVERIFIED,
            trust_source=TrustSource.IMPORT,
            source_schema="legacy-fixture",
            source_schema_version="1.0",
            normalizer_version="fixture-v1",
            content_capture_mode=ContentCaptureMode.METADATA_ONLY,
            started_at=NOW,
            start_idempotency_key="reconciliation-mismatch",
            start_envelope_sha256="a" * 64,
        )
    )
    await async_session.flush()

    result = await process_compatibility_outbox_event(
        async_session,
        event_id=event.event_id,
    )
    health = await get_reconciliation_health(async_session)

    assert result.status == ReconciliationStatus.MISMATCH
    assert health.mismatch_count == 1
    assert health.unexplained_difference_count == 1


async def test_structured_report_rolls_back_if_compatibility_event_fails(
    async_session,
    monkeypatch,
) -> None:
    _, _, runtime, reporter = await _scope(async_session, "atomic")
    await async_session.commit()

    async def fail_enqueue(*_args, **_kwargs):
        raise RuntimeError("injected compatibility outbox failure")

    monkeypatch.setattr(
        "app.services.structured_report_service.enqueue_domain_event",
        fail_enqueue,
    )
    with pytest.raises(RuntimeError, match="injected"):
        await ingest_structured_report(
            async_session,
            reporter=reporter,
            body=_report(runtime.id, "atomic"),
        )
    await async_session.rollback()

    assert await async_session.scalar(
        select(func.count(WorkTrace.id))
    ) == 0
    assert await async_session.scalar(
        select(func.count(CollectionJob.id))
    ) == 0
    assert await async_session.scalar(
        select(func.count(OutboxEvent.id))
    ) == 0


async def test_admin_api_reconciles_and_returns_only_safe_status(
    async_session,
) -> None:
    _, _, trace, _ = await _ingest(async_session, "admin-api")
    ordinary_user = await async_session.scalar(
        select(User).where(User.system_role == SystemRole.USER)
    )
    admin = User(
        username="reconcile-admin",
        email="reconcile-admin@example.test",
        hashed_password="unused",
        system_role=SystemRole.ADMIN,
    )
    async_session.add(admin)
    await async_session.flush()

    assert ordinary_user is not None
    with pytest.raises(HTTPException) as forbidden:
        await require_admin(ordinary_user)
    assert forbidden.value.status_code == 403

    reconciled = await reconcile_work_trace_endpoint(
        db=async_session,
        current_user=admin,
        work_trace_id=trace.id,
    )
    fetched = await get_work_trace_reconciliation_endpoint(
        db=async_session,
        current_user=admin,
        work_trace_id=trace.id,
    )
    health = await reconciliation_health(async_session, admin)

    assert reconciled.id == fetched.id
    assert reconciled.status == ReconciliationStatus.EXPECTED_LEGACY_ONLY
    assert health.expected_legacy_only_count == 1
    payload = V1V2ReconciliationOut.model_validate(fetched).model_dump()
    assert {
        "last_event_id",
        "payload_json",
        "title",
        "summary",
        "highlights",
    }.isdisjoint(payload)

    with pytest.raises(HTTPException) as missing:
        await get_work_trace_reconciliation_endpoint(
            db=async_session,
            current_user=admin,
            work_trace_id=trace.id + 100_000,
        )
    assert missing.value.status_code == 404


@pytest.mark.mysql
async def test_reconciliation_is_idempotent_on_real_mysql(
    async_session_mysql,
) -> None:
    _, _, trace, event = await _ingest(
        async_session_mysql,
        "real-mysql",
    )

    first = await process_compatibility_outbox_event(
        async_session_mysql,
        event_id=event.event_id,
    )
    await async_session_mysql.commit()
    replay = await process_compatibility_outbox_event(
        async_session_mysql,
        event_id=event.event_id,
    )

    assert first.status == ReconciliationStatus.EXPECTED_LEGACY_ONLY
    assert first.replayed is False
    assert replay.status == ReconciliationStatus.EXPECTED_LEGACY_ONLY
    assert replay.replayed is True
    assert await async_session_mysql.scalar(
        select(func.count(V1V2Reconciliation.id)).where(
            V1V2Reconciliation.work_trace_id == trace.id
        )
    ) == 1
    assert await async_session_mysql.scalar(
        select(func.count(OutboxConsumerReceipt.id)).where(
            OutboxConsumerReceipt.event_id == event.event_id
        )
    ) == 1
