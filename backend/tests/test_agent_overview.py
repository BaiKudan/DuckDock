from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.api.v1.endpoints.control_plane import enroll_reporter_endpoint, submit_structured_report_endpoint
from app.core.config import settings
from app.core.security import hash_password
from app.models.control_plane import (
    AgentInsightStatus,
    AnalysisJobStatus,
    AssetType,
    CollectionJob,
    CollectionTriggerType,
    Criticality,
    JobStatus,
    ReportAnalysisJob,
    ReportUploadSession,
    ReportUploadStatus,
)
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.user import SystemRole, User
from app.schemas.control_plane import (
    ReporterEnrollmentCreate,
    StructuredReportAssetRef,
    StructuredReportSignalInput,
    StructuredReportSubmit,
)
from app.services.agent_overview_service import build_agent_overview, create_agent_insight_job, run_agent_insight_job
from app.services.report_upload_service import authenticate_runtime_report_token


async def _seed_user(session) -> User:
    user = User(
        username="agent.owner",
        email="agent.owner@example.com",
        full_name="Agent Owner",
        hashed_password=hash_password("password"),
        system_role=SystemRole.USER,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_structured_report(session) -> tuple[User, int]:
    user = await _seed_user(session)
    namespace = Namespace(name="agent-overview", owner_id=user.id)
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
            runtime_name="Hermes Mac Mini",
            device_id="test-workstation",
            agent_kind="hermes",
            reporter_version="0.18.1",
        ),
        session,
        user,
    )
    reporter = await authenticate_runtime_report_token(session, enrolled.credential.token)
    await submit_structured_report_endpoint(
        StructuredReportSubmit(
            runtime_id=enrolled.runtime.id,
            report_type="daily",
            title="Hermes daily report",
            summary="Hermes submitted structured work status.",
            period_start=datetime(2030, 1, 1, 0, 0, tzinfo=timezone.utc),
            period_end=datetime(2030, 1, 1, 23, 59, tzinfo=timezone.utc),
            idempotency_key="daily-20300101",
            highlights=["Reporter produced schema-first JSON."],
            blockers=["GitHub push needs proxy on the local network."],
            next_actions=["Keep the cron reporter enabled."],
            project_refs=["duckdock"],
            asset_refs=[
                StructuredReportAssetRef(
                    external_id="hermes/runtime",
                    asset_type=AssetType.AGENT,
                    name="Hermes Agent Runtime",
                    criticality=Criticality.HIGH,
                )
            ],
            handover_signals=[
                StructuredReportSignalInput(
                    signal_type="handover",
                    subject_type="agent",
                    subject_key="hermes/runtime",
                    title="Hermes reporter needs fallback owner",
                    summary="The scheduled reporter should have a documented fallback owner.",
                )
            ],
        ),
        session,
        reporter,
    )
    await session.flush()
    return user, enrolled.runtime.id


async def _seed_pack_report(session, *, runtime_id: int) -> None:
    now = datetime(2030, 1, 2, tzinfo=timezone.utc)
    collection_job = CollectionJob(
        runtime_id=runtime_id,
        trigger_type=CollectionTriggerType.PROJECT_HANDOVER,
        status=JobStatus.SUCCEEDED,
        scope_json={"source": "report_pack"},
        started_at=now,
        finished_at=now,
    )
    session.add(collection_job)
    await session.flush()
    upload = ReportUploadSession(
        report_id="rpt_pack_20300102",
        runtime_id=runtime_id,
        collection_job_id=collection_job.id,
        status=ReportUploadStatus.SUCCEEDED,
        schema_version="duckdock-pack-v1",
        report_type="handover",
        period_start=now - timedelta(days=7),
        period_end=now,
        bucket="duckdock",
        object_key="reports/runtime-1/rpt_pack_20300102/pack.zip",
        filename="pack.zip",
        content_type="application/zip",
        upload_expires_at=now + timedelta(hours=1),
        finalized_at=now,
        created_via="test",
    )
    session.add(upload)
    await session.flush()
    session.add(
        ReportAnalysisJob(
            report_upload_session_id=upload.id,
            runtime_id=runtime_id,
            status=AnalysisJobStatus.SUCCEEDED,
            input_bucket=upload.bucket,
            input_object_key=upload.object_key,
            input_size_bytes=2048,
            result_bucket="duckdock",
            result_object_key="analysis/rpt_pack_20300102/result.json",
            result_content_type="application/json",
            result_size_bytes=512,
            summary_json={
                "worker_metadata": {"analysis_mode": "llm"},
                "asset_card_count": 2,
                "memory_candidate_count": 3,
                "risk_signal_count": 1,
                "handover_signal_count": 1,
            },
            started_at=now,
            finished_at=now,
        )
    )
    await session.flush()


async def test_agent_overview_merges_structured_and_pack_reports(async_session):
    user, runtime_id = await _seed_structured_report(async_session)
    await _seed_pack_report(async_session, runtime_id=runtime_id)

    overview = await build_agent_overview(async_session, user_id=user.id, runtime_id=runtime_id)

    assert overview.metrics.asset_count == 1
    assert overview.metrics.report_count == 2
    assert overview.metrics.structured_report_count == 1
    assert overview.metrics.pack_report_count == 1
    assert overview.metrics.risk_signal_count >= 1
    assert overview.metrics.handover_signal_count >= 1
    assert overview.reporter.state == "waiting"
    assert {item.source for item in overview.timeline} == {"structured_report", "report_pack"}
    structured = next(item for item in overview.timeline if item.source == "structured_report")
    assert structured.blockers == ["GitHub push needs proxy on the local network."]
    assert structured.project_refs == ["duckdock"]
    pack = next(item for item in overview.timeline if item.source == "report_pack")
    assert pack.analysis_job_id is not None
    assert pack.asset_count == 2


async def test_agent_insight_job_succeeds_with_baseline_when_llm_is_unconfigured(async_session, monkeypatch):
    monkeypatch.setattr(settings, "DUCKDOCK_LLM_API_KEY", "")
    monkeypatch.setattr(settings, "DUCKDOCK_LLM_BASE_URL", "")
    monkeypatch.setattr(settings, "DUCKDOCK_LLM_MODEL", "")
    user, runtime_id = await _seed_structured_report(async_session)

    job = await create_agent_insight_job(async_session, user_id=user.id, runtime_id=runtime_id, requested_by=user.id)
    await async_session.flush()
    assert job.status == AgentInsightStatus.PENDING

    finished = await run_agent_insight_job(async_session, job_id=job.id)

    assert finished.status == AgentInsightStatus.SUCCEEDED
    assert finished.ai_assist_json["mode"] == "baseline"
    assert finished.ai_assist_json["degraded"] is True
    assert finished.result_json["baseline_reason"] == "llm_unconfigured"
    assert "Hermes daily report" in finished.result_json["timeline_highlights"]
