"""Characterize the v1 Structured Report -> WorkTrace compatibility contract.

These tests intentionally require Structured Reports to keep materializing the
legacy WorkTrace projection. They do not forbid a future AgentRun dual-write;
they prevent AgentRun from silently replacing the existing WorkTrace behavior.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from app.core.time import ensure_utc
from app.models.control_plane import (
    AIAsset,
    AssetType,
    CollectionJob,
    CollectionTriggerType,
    Criticality,
    JobStatus,
    ReporterCredential,
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
    Sensitivity,
    TraceType,
    WorkTrace,
)
from app.models.namespace import Namespace
from app.models.user import SystemRole, User
from app.schemas.control_plane import StructuredReportAssetRef, StructuredReportSubmit
from app.services.report_upload_service import ReporterAuthContext
from app.services.structured_report_service import ingest_structured_report


async def _reporter_context(session) -> tuple[User, RuntimeInstance, ReporterAuthContext]:
    actor = User(
        username="foundation-worktrace-actor",
        email="foundation-worktrace@example.com",
        full_name="Foundation WorkTrace Actor",
        hashed_password="not-used-by-this-test",
        system_role=SystemRole.USER,
    )
    session.add(actor)
    await session.flush()
    namespace = Namespace(name="foundation-worktrace", owner_id=actor.id)
    session.add(namespace)
    await session.flush()
    runtime = RuntimeInstance(
        namespace_id=namespace.id,
        provider=RuntimeProvider.CUSTOM,
        name="Foundation Structured Reporter",
        deploy_type=RuntimeDeployType.PRIVATE,
    )
    session.add(runtime)
    await session.flush()

    credential = ReporterCredential(
        runtime_id=runtime.id,
        user_id=actor.id,
        device_id="foundation-worktrace-device",
        name="Foundation WorkTrace Reporter",
        token_prefix="fndwt001",
        token_hash="not-used-by-this-test",
        scopes=["report.structured"],
    )
    session.add(credential)
    await session.flush()
    return actor, runtime, ReporterAuthContext(
        runtime=runtime,
        token=credential,
        source="reporter_credential",
    )


async def test_structured_report_materializes_legacy_work_trace_fields(async_session) -> None:
    actor, runtime, reporter = await _reporter_context(async_session)
    period_start = datetime(2031, 2, 3, 8, 30, tzinfo=timezone.utc)
    period_end = datetime(2031, 2, 7, 18, 45, tzinfo=timezone.utc)
    body = StructuredReportSubmit(
        runtime_id=runtime.id,
        report_type="weekly",
        title="Foundation compatibility report",
        summary="The legacy structured-report projection remains available.",
        period_start=period_start,
        period_end=period_end,
        idempotency_key="foundation-worktrace-week-2031-06",
        sensitivity=Sensitivity.CONFIDENTIAL,
        highlights=["Structured input materialized successfully."],
        blockers=["AgentRun must not silently replace WorkTrace."],
        next_actions=["Keep the v1 projection during the compatibility window."],
        project_refs=["duckdock-2-foundation"],
        asset_refs=[
            StructuredReportAssetRef(
                external_id="foundation/reporter-agent",
                asset_type=AssetType.AGENT,
                name="Foundation Reporter Agent",
                criticality=Criticality.HIGH,
            )
        ],
        metadata_json={"source_commit": "compat-baseline", "attempt": 1},
    )

    result = await ingest_structured_report(async_session, reporter=reporter, body=body)
    await async_session.flush()

    trace = (await async_session.execute(select(WorkTrace))).scalar_one()
    asset = (await async_session.execute(select(AIAsset))).scalar_one()
    job = await async_session.get(CollectionJob, trace.metadata_json["collection_job_id"])

    assert result.status == "succeeded"
    assert result.work_trace.id == trace.id
    assert trace.runtime_id == runtime.id
    assert trace.asset_id == asset.id == result.assets[0].id
    assert trace.external_session_id == (
        f"structured-report:{runtime.id}:foundation-worktrace-week-2031-06"
    )
    assert trace.actor_user_id == actor.id
    assert trace.title == "Foundation compatibility report"
    assert trace.summary == (
        "The legacy structured-report projection remains available.\n\n"
        "## Highlights\n- Structured input materialized successfully.\n\n"
        "## Blockers\n- AgentRun must not silently replace WorkTrace.\n\n"
        "## Next actions\n- Keep the v1 projection during the compatibility window.\n\n"
        "## Projects\n- duckdock-2-foundation"
    )
    assert trace.trace_type == TraceType.TASK_RUN
    assert trace.started_at is not None
    assert trace.ended_at is not None
    assert ensure_utc(trace.started_at) == period_start
    assert ensure_utc(trace.ended_at) == period_end
    assert trace.sensitivity == Sensitivity.CONFIDENTIAL
    assert trace.metadata_json == {
        "source": "structured_report",
        "schema_version": "duckdock-structured-report-v1",
        "report_id": result.report_id,
        "report_type": "weekly",
        "collection_job_id": job.id,
        "idempotency_key": "foundation-worktrace-week-2031-06",
        "project_refs": ["duckdock-2-foundation"],
        "asset_external_ids": ["foundation/reporter-agent"],
        "metadata_json": {"source_commit": "compat-baseline", "attempt": 1},
    }

    assert job is not None
    assert result.job.id == job.id
    assert job.runtime_id == runtime.id
    assert job.trigger_type == CollectionTriggerType.SCHEDULED
    assert job.status == JobStatus.SUCCEEDED
    assert job.scope_json == {
        "source": "duckdock_structured_report",
        "schema_version": "duckdock-structured-report-v1",
        "report_id": result.report_id,
        "report_type": "weekly",
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "idempotency_key": "foundation-worktrace-week-2031-06",
    }
    assert job.summary_json == {
        "source": "structured_report",
        "report_id": result.report_id,
        "asset_ref_count": 1,
        "memory_candidate_count": 0,
        "handover_signal_count": 0,
        "project_ref_count": 1,
        "normalization": "schema_first",
    }


async def test_structured_report_changed_replay_keeps_first_work_trace(async_session) -> None:
    _, runtime, reporter = await _reporter_context(async_session)
    idempotency_key = "foundation-worktrace-first-write-wins"
    first_body = StructuredReportSubmit(
        runtime_id=runtime.id,
        report_type="daily",
        title="Original structured report",
        summary="The first payload is the compatibility source of truth.",
        idempotency_key=idempotency_key,
        highlights=["Original highlight."],
    )
    changed_replay = StructuredReportSubmit(
        runtime_id=runtime.id,
        report_type="handover",
        title="Replacement payload",
        summary="This changed replay must not overwrite the existing WorkTrace.",
        idempotency_key=idempotency_key,
        highlights=["Replacement highlight."],
    )

    first = await ingest_structured_report(async_session, reporter=reporter, body=first_body)
    replay = await ingest_structured_report(async_session, reporter=reporter, body=changed_replay)
    await async_session.flush()

    trace_count = (
        await async_session.execute(select(func.count()).select_from(WorkTrace))
    ).scalar_one()
    job_count = (
        await async_session.execute(select(func.count()).select_from(CollectionJob))
    ).scalar_one()
    trace = (await async_session.execute(select(WorkTrace))).scalar_one()

    assert first.status == "succeeded"
    assert replay.status == "deduped"
    assert replay.report_id == first.report_id
    assert replay.work_trace.id == first.work_trace.id == trace.id
    assert replay.warnings == ["idempotency_key matched an existing structured report"]
    assert trace_count == 1
    assert job_count == 1
    assert trace.external_session_id == f"structured-report:{runtime.id}:{idempotency_key}"
    assert trace.title == "Original structured report"
    assert trace.summary == (
        "The first payload is the compatibility source of truth.\n\n"
        "## Highlights\n- Original highlight."
    )
    assert trace.trace_type == TraceType.TASK_RUN
    assert trace.metadata_json["report_type"] == "daily"
    assert trace.metadata_json["idempotency_key"] == idempotency_key
