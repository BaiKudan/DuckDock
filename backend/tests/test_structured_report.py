from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.api.v1.endpoints.control_plane import enroll_reporter_endpoint, submit_structured_report_endpoint
from app.core.security import hash_password
from app.models.control_plane import (
    AIAsset,
    AssetType,
    Criticality,
    MemoryCandidate,
    MemoryCandidateType,
    ReporterCredential,
    ReportAnalysisJob,
    ReportUploadSession,
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
    WorkTrace,
)
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.outbox import OutboxEvent
from app.models.user import SystemRole, User
from app.schemas.control_plane import (
    ReporterEnrollmentCreate,
    StructuredReportAssetRef,
    StructuredReportMemoryInput,
    StructuredReportSignalInput,
    StructuredReportSubmit,
)
from app.services.report_upload_service import authenticate_runtime_report_token


async def _user(session) -> User:
    user = User(
        username="employee",
        email="employee@example.com",
        full_name="Employee One",
        hashed_password=hash_password("password"),
        system_role=SystemRole.USER,
    )
    session.add(user)
    await session.flush()
    return user


async def _enrolled_reporter(session):
    user = await _user(session)
    namespace = Namespace(name="structured-report", owner_id=user.id)
    session.add(namespace)
    await session.flush()
    session.add(
        NamespaceMember(
            namespace_id=namespace.id,
            user_id=user.id,
            role=NamespaceRole.ADMIN,
        )
    )
    await session.flush()
    enrolled = await enroll_reporter_endpoint(
        ReporterEnrollmentCreate(
            namespace_id=namespace.id,
            runtime_name="Employee Hermes",
            device_id="test-workstation",
            agent_kind="hermes",
            reporter_version="0.18.1",
        ),
        session,
        user,
    )
    reporter = await authenticate_runtime_report_token(session, enrolled.credential.token)
    return user, enrolled, reporter


async def test_structured_daily_report_materializes_timeline_without_pack(async_session):
    user, enrolled, reporter = await _enrolled_reporter(async_session)
    body = StructuredReportSubmit(
        runtime_id=enrolled.runtime.id,
        report_type="daily",
        title="DuckDock daily status",
        summary="Validated structured daily reporting from Hermes.",
        period_start=datetime(2030, 1, 1, 0, 0, tzinfo=timezone.utc),
        period_end=datetime(2030, 1, 1, 23, 59, tzinfo=timezone.utc),
        idempotency_key="daily-20300101",
        highlights=["Reporter produced schema-first JSON."],
        blockers=["WorkBuddy still needs a post-006 field test."],
        next_actions=["Keep the Hermes cron enabled."],
        project_refs=["duckdock"],
        asset_refs=[
            StructuredReportAssetRef(
                external_id="hermes/runtime",
                asset_type=AssetType.AGENT,
                name="Hermes Agent Runtime",
                criticality=Criticality.HIGH,
            )
        ],
        memory_candidates=[
            StructuredReportMemoryInput(
                candidate_type=MemoryCandidateType.PROJECT_CONTEXT,
                subject_type="project",
                subject_key="duckdock",
                title="DuckDock structured reporting",
                summary="Daily reports should be schema-first and not upload evidence packs.",
            )
        ],
        handover_signals=[
            StructuredReportSignalInput(
                signal_type="handover",
                subject_type="agent",
                subject_key="hermes/runtime",
                title="Hermes reporter schedule should be owned",
                summary="The scheduled reporter should have a fallback owner before handover.",
            )
        ],
    )

    out = await submit_structured_report_endpoint(body, async_session, reporter)
    await async_session.flush()

    assert out.status == "succeeded"
    assert out.report_id.startswith("srpt_")
    assert out.work_trace.actor_user_id == user.id
    assert out.work_trace.runtime_id == enrolled.runtime.id
    assert "Reporter produced schema-first JSON" in (out.work_trace.summary or "")
    assert out.assets[0].name == "Hermes Agent Runtime"
    assert {item.candidate_type for item in out.memory_candidates} >= {
        MemoryCandidateType.PROJECT_CONTEXT,
        MemoryCandidateType.HANDOVER_SIGNAL,
        MemoryCandidateType.RISK_SIGNAL,
    }

    upload_count = (await async_session.execute(select(func.count()).select_from(ReportUploadSession))).scalar_one()
    analysis_count = (await async_session.execute(select(func.count()).select_from(ReportAnalysisJob))).scalar_one()
    assert upload_count == 0
    assert analysis_count == 0

    trace = (
        await async_session.execute(
            select(WorkTrace).where(
                WorkTrace.external_session_id == f"structured-report:{enrolled.runtime.id}:daily-20300101"
            )
        )
    ).scalar_one()
    assert trace.actor_user_id == user.id
    asset = (await async_session.execute(select(AIAsset).where(AIAsset.external_id == "hermes/runtime"))).scalar_one()
    assert asset.source_runtime_id == enrolled.runtime.id
    memory_count = (await async_session.execute(select(func.count()).select_from(MemoryCandidate))).scalar_one()
    assert memory_count == len(out.memory_candidates)


async def test_structured_report_requires_structured_scope(async_session):
    user = await _user(async_session)
    runtime = RuntimeInstance(provider=RuntimeProvider.CUSTOM, name="old reporter", deploy_type=RuntimeDeployType.PRIVATE)
    async_session.add(runtime)
    await async_session.flush()
    credential = ReporterCredential(
        runtime_id=runtime.id,
        user_id=user.id,
        device_id="test-workstation",
        name="old credential",
        token_prefix="abcd1234",
        token_hash=hash_password("secret"),
        scopes=["report.upload", "report.heartbeat"],
    )
    async_session.add(credential)
    await async_session.flush()
    reporter = await authenticate_runtime_report_token(async_session, "dkr_report_abcd1234_secret")

    with pytest.raises(HTTPException) as exc_info:
        await submit_structured_report_endpoint(
            StructuredReportSubmit(
                runtime_id=runtime.id,
                title="Daily",
                summary="This old credential lacks structured scope.",
            ),
            async_session,
            reporter,
        )

    assert exc_info.value.status_code == 403
    assert "report.structured" in exc_info.value.detail


async def test_structured_report_idempotency_reuses_existing_trace(async_session):
    _, enrolled, reporter = await _enrolled_reporter(async_session)
    body = StructuredReportSubmit(
        runtime_id=enrolled.runtime.id,
        title="Weekly report",
        summary="First payload wins for this idempotency key.",
        report_type="weekly",
        idempotency_key="week-2030-01",
    )

    first = await submit_structured_report_endpoint(body, async_session, reporter)
    second = await submit_structured_report_endpoint(body, async_session, reporter)
    await async_session.flush()

    assert first.status == "succeeded"
    assert second.status == "deduped"
    assert second.work_trace.id == first.work_trace.id
    trace_count = (await async_session.execute(select(func.count()).select_from(WorkTrace))).scalar_one()
    outbox_count = (
        await async_session.execute(
            select(func.count()).select_from(OutboxEvent)
        )
    ).scalar_one()
    assert trace_count == 1
    assert outbox_count == 1
