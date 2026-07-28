from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401 - register SQLAlchemy models for create_all
from app.core.database import Base
from app.core.security import hash_password
from app.models.control_plane import (
    AIAsset,
    AnalysisJobStatus,
    AnalysisResultArtifact,
    AnalysisResultArtifactKind,
    AnalysisWorker,
    AnalysisWorkerStatus,
    AssetOwnership,
    CollectionJob,
    CollectionTriggerType,
    JobStatus,
    MemoryCandidate,
    MemoryCandidateType,
    ReportAnalysisJob,
    ReportUploadSession,
    ReportUploadStatus,
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
    RuntimeStatus,
    WorkTrace,
)
from app.models.namespace import Namespace
from app.models.user import SystemRole, User
import app.services.analysis_materializer as analysis_materializer
import app.services.analysis_service as analysis_service
from app.services.analysis_materializer import materialize_analysis_result
from app.services.analysis_service import (
    DEFAULT_ANALYSIS_RESULT_FILES,
    authenticate_analysis_worker_token,
    cancel_analysis_job,
    disable_analysis_worker,
    fail_analysis_job,
    finalize_analysis_job,
    get_analysis_queue_metrics,
    heartbeat_analysis_job,
    lease_next_analysis_job,
    reap_expired_analysis_jobs,
    retry_analysis_job,
)
from app.schemas.control_plane import (
    AnalysisJobFail,
    AnalysisJobFinalize,
    AnalysisResultArtifactFinalize,
    MemoryCandidateCreate,
)
from app.services.artifact_service import ArtifactStorageError


@asynccontextmanager
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


def test_retry_failed_job_requeues_session_and_resets_attempts():
    asyncio.run(_test_retry_failed_job_requeues_session_and_resets_attempts())


async def _test_retry_failed_job_requeues_session_and_resets_attempts():
    async with db_session() as session_db:
        job, session, collection_job = await seed_analysis_job(
            session_db,
            status=AnalysisJobStatus.FAILED,
            report_status=ReportUploadStatus.FAILED,
            collection_status=JobStatus.FAILED,
            attempts=3,
            max_attempts=3,
        )

        retried = await retry_analysis_job(
            session_db,
            job_id=job.id,
            reason="manual retry after worker outage",
            reset_attempts=True,
            retried_by=42,
        )

        assert retried.status == AnalysisJobStatus.PENDING
        assert retried.attempts == 0
        assert retried.worker_id is None
        assert retried.lease_owner is None
        assert retried.lease_expires_at is None
        assert retried.error_message is None
        assert retried.summary_json["admin_retry_count"] == 1
        assert retried.summary_json["admin_retry_by"] == 42
        assert session.status == ReportUploadStatus.UPLOADED
        assert session.error_message is None
        assert collection_job.status == JobStatus.PENDING
        assert collection_job.error_message is None


def test_cancel_running_job_marks_upload_failed_and_collection_cancelled():
    asyncio.run(_test_cancel_running_job_marks_upload_failed_and_collection_cancelled())


async def _test_cancel_running_job_marks_upload_failed_and_collection_cancelled():
    async with db_session() as session_db:
        worker = await seed_worker(session_db)
        job, session, collection_job = await seed_analysis_job(
            session_db,
            status=AnalysisJobStatus.RUNNING,
            report_status=ReportUploadStatus.INGESTING,
            collection_status=JobStatus.RUNNING,
            worker=worker,
            attempts=1,
            max_attempts=3,
        )

        cancelled = await cancel_analysis_job(
            session_db,
            job_id=job.id,
            reason="admin maintenance window",
            cancelled_by=7,
        )

        assert cancelled.status == AnalysisJobStatus.CANCELLED
        assert cancelled.worker_id is None
        assert cancelled.lease_owner is None
        assert cancelled.lease_expires_at is None
        assert cancelled.error_message == "admin maintenance window"
        assert cancelled.summary_json["admin_cancelled_by"] == 7
        assert session.status == ReportUploadStatus.FAILED
        assert session.error_message == "admin maintenance window"
        assert collection_job.status == JobStatus.CANCELLED
        assert collection_job.finished_at is not None


def test_disable_worker_requeues_available_job_fails_exhausted_job_and_blocks_token():
    asyncio.run(_test_disable_worker_requeues_available_job_fails_exhausted_job_and_blocks_token())


async def _test_disable_worker_requeues_available_job_fails_exhausted_job_and_blocks_token():
    async with db_session() as session_db:
        worker = await seed_worker(session_db, prefix="abcd1234", secret="worker-secret")
        requeue_job, requeue_session, _ = await seed_analysis_job(
            session_db,
            status=AnalysisJobStatus.LEASED,
            report_status=ReportUploadStatus.INGESTING,
            collection_status=JobStatus.RUNNING,
            worker=worker,
            attempts=1,
            max_attempts=3,
        )
        exhausted_job, exhausted_session, _ = await seed_analysis_job(
            session_db,
            status=AnalysisJobStatus.RUNNING,
            report_status=ReportUploadStatus.INGESTING,
            collection_status=JobStatus.RUNNING,
            worker=worker,
            attempts=3,
            max_attempts=3,
        )

        result = await disable_analysis_worker(session_db, worker_id=worker.id, disabled_by=99)

        assert result.worker.status == AnalysisWorkerStatus.DISABLED
        assert result.requeued_job_count == 1
        assert result.failed_job_count == 1
        assert requeue_job.status == AnalysisJobStatus.PENDING
        assert requeue_job.attempts == 1
        assert requeue_job.worker_id is None
        assert requeue_session.status == ReportUploadStatus.UPLOADED
        assert exhausted_job.status == AnalysisJobStatus.FAILED
        assert exhausted_job.error_message == "Worker disabled before completion and job attempts are exhausted"
        assert exhausted_session.status == ReportUploadStatus.FAILED

        with pytest.raises(HTTPException) as exc_info:
            await authenticate_analysis_worker_token(session_db, "dkr_worker_abcd1234_worker-secret")
        assert exc_info.value.status_code == 401


def test_worker_leases_pending_job_and_receives_standard_result_uploads(monkeypatch):
    asyncio.run(_test_worker_leases_pending_job_and_receives_standard_result_uploads(monkeypatch))


async def _test_worker_leases_pending_job_and_receives_standard_result_uploads(monkeypatch):
    async with db_session() as session_db:
        fake_artifacts = FakeAnalysisArtifactService()
        monkeypatch.setattr(analysis_service, "artifact_service", fake_artifacts)
        worker = await seed_worker(session_db)
        job, upload_session, collection_job = await seed_analysis_job(
            session_db,
            status=AnalysisJobStatus.PENDING,
            report_status=ReportUploadStatus.UPLOADED,
            collection_status=JobStatus.PENDING,
        )

        lease = await lease_next_analysis_job(
            session_db,
            worker=worker,
            lease_seconds=600,
            worker_name="openclaw-analysis-worker-01",
            capabilities_json={"runtime": "openclaw", "skill": "duckdock-analysis-worker"},
        )

        assert lease is not None
        assert lease.job.id == job.id
        assert job.status == AnalysisJobStatus.LEASED
        assert job.worker_id == worker.id
        assert job.lease_owner == worker.worker_key
        assert job.attempts == 1
        assert worker.name == "openclaw-analysis-worker-01"
        assert upload_session.status == ReportUploadStatus.INGESTING
        assert collection_job.status == JobStatus.RUNNING
        assert lease.download["download_url"].endswith(upload_session.object_key)

        expected_filenames = {filename for _, filename, _ in DEFAULT_ANALYSIS_RESULT_FILES}
        assert {item["filename"] for item in lease.uploads} == expected_filenames
        assert all(f"/{upload_session.report_id}/job-{job.id}/" in item["object_key"] for item in lease.uploads)


def test_queue_metrics_reports_backlog_workers_and_saturation():
    asyncio.run(_test_queue_metrics_reports_backlog_workers_and_saturation())


async def _test_queue_metrics_reports_backlog_workers_and_saturation():
    async with db_session() as session_db:
        now = datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc)
        online_worker = await seed_worker(session_db)
        online_worker.last_seen_at = now - timedelta(minutes=5)
        stale_worker = await seed_worker(session_db)
        stale_worker.last_seen_at = now - timedelta(hours=2)
        disabled_worker = await seed_worker(session_db)
        disabled_worker.status = AnalysisWorkerStatus.DISABLED
        disabled_worker.last_seen_at = now - timedelta(minutes=1)

        pending_job, _, _ = await seed_analysis_job(
            session_db,
            status=AnalysisJobStatus.PENDING,
            report_status=ReportUploadStatus.UPLOADED,
            collection_status=JobStatus.PENDING,
            attempts=0,
            max_attempts=3,
        )
        pending_job.created_at = now - timedelta(minutes=12)
        expired_job, _, _ = await seed_analysis_job(
            session_db,
            status=AnalysisJobStatus.LEASED,
            report_status=ReportUploadStatus.INGESTING,
            collection_status=JobStatus.RUNNING,
            worker=online_worker,
            attempts=1,
            max_attempts=3,
        )
        expired_job.lease_expires_at = now - timedelta(seconds=30)
        await seed_analysis_job(
            session_db,
            status=AnalysisJobStatus.RUNNING,
            report_status=ReportUploadStatus.INGESTING,
            collection_status=JobStatus.RUNNING,
            worker=online_worker,
            attempts=1,
            max_attempts=3,
        )
        await seed_analysis_job(
            session_db,
            status=AnalysisJobStatus.SUCCEEDED,
            report_status=ReportUploadStatus.SUCCEEDED,
            collection_status=JobStatus.SUCCEEDED,
        )
        await session_db.flush()

        metrics = await get_analysis_queue_metrics(session_db, now=now)

        assert metrics.status_counts["pending"] == 1
        assert metrics.status_counts["leased"] == 1
        assert metrics.status_counts["running"] == 1
        assert metrics.status_counts["succeeded"] == 1
        assert metrics.pending_count == 1
        assert metrics.active_job_count == 2
        assert metrics.backlog_count == 2
        assert metrics.expired_lease_count == 1
        assert metrics.retryable_expired_lease_count == 1
        assert metrics.registered_worker_count == 2
        assert metrics.online_worker_count == 1
        assert metrics.stale_worker_count == 1
        assert metrics.disabled_worker_count == 1
        assert metrics.oldest_pending_seconds == 720
        assert metrics.queued_per_online_worker == 2
        assert metrics.saturation_level == "watch"
        assert metrics.saturation_reason == "expired_leases"


def test_worker_heartbeat_and_retryable_failure_releases_lease(monkeypatch):
    asyncio.run(_test_worker_heartbeat_and_retryable_failure_releases_lease(monkeypatch))


async def _test_worker_heartbeat_and_retryable_failure_releases_lease(monkeypatch):
    async with db_session() as session_db:
        fake_artifacts = FakeAnalysisArtifactService()
        monkeypatch.setattr(analysis_service, "artifact_service", fake_artifacts)
        worker = await seed_worker(session_db)
        job, upload_session, collection_job = await seed_analysis_job(
            session_db,
            status=AnalysisJobStatus.LEASED,
            report_status=ReportUploadStatus.INGESTING,
            collection_status=JobStatus.RUNNING,
            worker=worker,
            attempts=1,
            max_attempts=3,
        )

        running = await heartbeat_analysis_job(session_db, worker=worker, job_id=job.id)
        assert running.status == AnalysisJobStatus.RUNNING
        assert running.lease_expires_at is not None

        failed = await fail_analysis_job(
            session_db,
            worker=worker,
            job_id=job.id,
            body=AnalysisJobFail(
                error_message="temporary model outage",
                retryable=True,
                summary_json={"stage": "model_analysis"},
            ),
        )

        assert failed.status == AnalysisJobStatus.PENDING
        assert failed.worker_id is None
        assert failed.lease_owner is None
        assert failed.lease_expires_at is None
        assert failed.summary_json["stage"] == "model_analysis"
        assert upload_session.status == ReportUploadStatus.UPLOADED
        assert collection_job.status == JobStatus.PENDING


def test_reap_expired_analysis_job_requeues_without_consuming_attempts():
    asyncio.run(_test_reap_expired_analysis_job_requeues_without_consuming_attempts())


async def _test_reap_expired_analysis_job_requeues_without_consuming_attempts():
    async with db_session() as session_db:
        worker = await seed_worker(session_db)
        job, upload_session, collection_job = await seed_analysis_job(
            session_db,
            status=AnalysisJobStatus.LEASED,
            report_status=ReportUploadStatus.INGESTING,
            collection_status=JobStatus.RUNNING,
            worker=worker,
            attempts=1,
            max_attempts=3,
        )
        now = datetime.now(timezone.utc)
        job.lease_expires_at = now - timedelta(seconds=1)
        await session_db.flush()

        result = await reap_expired_analysis_jobs(session_db, now=now)

        assert result.requeued_job_count == 1
        assert result.failed_job_count == 0
        assert job.status == AnalysisJobStatus.PENDING
        assert job.attempts == 1
        assert job.worker_id is None
        assert job.lease_owner is None
        assert job.lease_expires_at is None
        assert upload_session.status == ReportUploadStatus.UPLOADED
        assert collection_job.status == JobStatus.PENDING


def test_reap_expired_analysis_job_fails_exhausted_attempts_and_releases_session():
    asyncio.run(_test_reap_expired_analysis_job_fails_exhausted_attempts_and_releases_session())


async def _test_reap_expired_analysis_job_fails_exhausted_attempts_and_releases_session():
    async with db_session() as session_db:
        worker = await seed_worker(session_db)
        job, upload_session, collection_job = await seed_analysis_job(
            session_db,
            status=AnalysisJobStatus.RUNNING,
            report_status=ReportUploadStatus.INGESTING,
            collection_status=JobStatus.RUNNING,
            worker=worker,
            attempts=3,
            max_attempts=3,
        )
        now = datetime.now(timezone.utc)
        job.lease_expires_at = now - timedelta(seconds=1)
        await session_db.flush()

        result = await reap_expired_analysis_jobs(session_db, now=now)

        assert result.requeued_job_count == 0
        assert result.failed_job_count == 1
        assert job.status == AnalysisJobStatus.FAILED
        assert job.worker_id is None
        assert job.lease_owner is None
        assert job.lease_expires_at is None
        assert upload_session.status == ReportUploadStatus.FAILED
        assert collection_job.status == JobStatus.FAILED
        assert collection_job.finished_at is not None


def test_lease_next_resumes_expired_lease_without_reaper(monkeypatch):
    asyncio.run(_test_lease_next_resumes_expired_lease_without_reaper(monkeypatch))


async def _test_lease_next_resumes_expired_lease_without_reaper(monkeypatch):
    # Defense-in-depth (PUSH-01): the periodic reaper only runs under celery beat, so
    # lease_next must itself reclaim a crashed worker's expired lease — otherwise a job
    # deadlocks whenever beat is down. Re-leasing consumes one bounded attempt.
    async with db_session() as session_db:
        monkeypatch.setattr(analysis_service, "artifact_service", FakeAnalysisArtifactService())
        worker = await seed_worker(session_db)
        job, upload_session, _collection_job = await seed_analysis_job(
            session_db,
            status=AnalysisJobStatus.LEASED,
            report_status=ReportUploadStatus.INGESTING,
            collection_status=JobStatus.RUNNING,
            worker=worker,
            attempts=1,
            max_attempts=3,
        )
        now = datetime.now(timezone.utc)
        job.lease_expires_at = now - timedelta(seconds=1)
        await session_db.flush()

        new_worker = await seed_worker(session_db)
        lease = await lease_next_analysis_job(session_db, worker=new_worker, lease_seconds=600)

        assert lease is not None
        assert lease.job.id == job.id
        assert job.status == AnalysisJobStatus.LEASED
        assert job.worker_id == new_worker.id
        assert job.lease_owner == new_worker.worker_key
        assert job.attempts == 2
        assert job.lease_expires_at is not None and job.lease_expires_at > now


def test_lease_next_does_not_steal_unexpired_lease(monkeypatch):
    asyncio.run(_test_lease_next_does_not_steal_unexpired_lease(monkeypatch))


async def _test_lease_next_does_not_steal_unexpired_lease(monkeypatch):
    async with db_session() as session_db:
        monkeypatch.setattr(analysis_service, "artifact_service", FakeAnalysisArtifactService())
        worker = await seed_worker(session_db)
        job, _upload_session, _collection_job = await seed_analysis_job(
            session_db,
            status=AnalysisJobStatus.LEASED,
            report_status=ReportUploadStatus.INGESTING,
            collection_status=JobStatus.RUNNING,
            worker=worker,
            attempts=1,
            max_attempts=3,
        )
        # seed_analysis_job sets lease_expires_at to now + 30min, i.e. still valid.
        await session_db.flush()

        other_worker = await seed_worker(session_db)
        lease = await lease_next_analysis_job(session_db, worker=other_worker, lease_seconds=600)

        assert lease is None
        assert job.lease_owner == worker.worker_key
        assert job.attempts == 1


def test_lease_next_leaves_attempt_exhausted_expired_job_for_reaper(monkeypatch):
    asyncio.run(_test_lease_next_leaves_attempt_exhausted_expired_job_for_reaper(monkeypatch))


async def _test_lease_next_leaves_attempt_exhausted_expired_job_for_reaper(monkeypatch):
    async with db_session() as session_db:
        monkeypatch.setattr(analysis_service, "artifact_service", FakeAnalysisArtifactService())
        worker = await seed_worker(session_db)
        job, upload_session, collection_job = await seed_analysis_job(
            session_db,
            status=AnalysisJobStatus.RUNNING,
            report_status=ReportUploadStatus.INGESTING,
            collection_status=JobStatus.RUNNING,
            worker=worker,
            attempts=3,
            max_attempts=3,
        )
        now = datetime.now(timezone.utc)
        job.lease_expires_at = now - timedelta(seconds=1)
        await session_db.flush()

        other_worker = await seed_worker(session_db)
        lease = await lease_next_analysis_job(session_db, worker=other_worker, lease_seconds=600)
        # attempts are exhausted: lease_next must NOT resurrect it; the reaper FAILs it.
        assert lease is None

        result = await reap_expired_analysis_jobs(session_db, now=now)
        assert result.failed_job_count == 1
        assert job.status == AnalysisJobStatus.FAILED
        assert upload_session.status == ReportUploadStatus.FAILED
        assert collection_job.status == JobStatus.FAILED


def test_reaper_beat_schedule_is_registered_in_celery_config():
    # NOTE: config-only — this proves the schedule entry exists, NOT that a beat runner
    # is deployed to execute it. Deployment is guarded by
    # test_prod_ops_config.test_compose_deploys_celery_beat_scheduler_for_reaper.
    from app.workers.celery_app import celery_app

    assert "app.workers.analysis_tasks" in celery_app.conf.include
    assert celery_app.conf.beat_schedule["reap-expired-analysis-jobs"]["task"] == "reap_expired_analysis_jobs"


def test_finalize_preserves_worker_summary_counts_and_materializes_results(monkeypatch):
    asyncio.run(_test_finalize_preserves_worker_summary_counts_and_materializes_results(monkeypatch))


async def _test_finalize_preserves_worker_summary_counts_and_materializes_results(monkeypatch):
    async with db_session() as session_db:
        fake_artifacts = FakeAnalysisArtifactService(sha256="d" * 64, size_bytes=256)
        monkeypatch.setattr(analysis_service, "artifact_service", fake_artifacts)

        async def fake_materialize_analysis_result(db, *, job, session, runtime, artifacts):
            return {"asset_cards": 1, "worktrace_summaries": 1, "memory_candidates": 2}

        monkeypatch.setattr(analysis_service, "materialize_analysis_result", fake_materialize_analysis_result)
        worker = await seed_worker(session_db)
        job, upload_session, collection_job = await seed_analysis_job(
            session_db,
            status=AnalysisJobStatus.RUNNING,
            report_status=ReportUploadStatus.INGESTING,
            collection_status=JobStatus.RUNNING,
            worker=worker,
            attempts=1,
            max_attempts=3,
        )
        job.result_object_key = fake_artifacts.report_analysis_result_object_key(
            report_id=upload_session.report_id,
            job_id=job.id,
        )
        await session_db.flush()

        finalized = await finalize_analysis_job(
            session_db,
            worker=worker,
            job_id=job.id,
            body=AnalysisJobFinalize(
                result_sha256=fake_artifacts.sha256,
                result_size_bytes=fake_artifacts.size_bytes,
                summary_json={"asset_count": 1, "memory_candidate_count": 2},
                result_artifacts=[
                    AnalysisResultArtifactFinalize(
                        kind=AnalysisResultArtifactKind.ANALYSIS_RESULT,
                        filename="analysis-result.json",
                        object_key=job.result_object_key,
                        content_type="application/json",
                        sha256=fake_artifacts.sha256,
                        size_bytes=fake_artifacts.size_bytes,
                    )
                ],
                memory_candidates=[],
            ),
        )

        assert finalized.status == AnalysisJobStatus.SUCCEEDED
        assert finalized.worker_id == worker.id
        assert finalized.summary_json["asset_count"] == 1
        assert finalized.summary_json["memory_candidate_count"] == 2
        assert finalized.summary_json["inline_memory_candidate_count"] == 0
        assert finalized.summary_json["materialized_counts"]["memory_candidates"] == 2
        assert upload_session.status == ReportUploadStatus.SUCCEEDED
        assert collection_job.status == JobStatus.SUCCEEDED

        result_artifacts = (
            await session_db.execute(
                select(AnalysisResultArtifact).where(AnalysisResultArtifact.analysis_job_id == job.id)
            )
        ).scalars().all()
        assert len(result_artifacts) == 1
        assert result_artifacts[0].sha256 == fake_artifacts.sha256


def test_finalize_extracts_worker_metadata_from_result_file(monkeypatch):
    asyncio.run(_test_finalize_extracts_worker_metadata_from_result_file(monkeypatch))


async def _test_finalize_extracts_worker_metadata_from_result_file(monkeypatch):
    async with db_session() as session_db:
        fake_artifacts = FakeAnalysisArtifactService(sha256="d" * 64, size_bytes=256)
        monkeypatch.setattr(analysis_service, "artifact_service", fake_artifacts)

        async def fake_materialize(db, *, job, session, runtime, artifacts):
            return {"succeeded_artifacts": 0, "failures": []}

        monkeypatch.setattr(analysis_service, "materialize_analysis_result", fake_materialize)
        job, upload_session, collection_job, worker = await _seed_finalizable_job(session_db, fake_artifacts)
        fake_artifacts.objects[job.result_object_key] = {
            "schema_version": "duckdock-analysis-v1",
            "report_id": upload_session.report_id,
            "summary": {
                "memory_candidate_count": 0,
                "analysis_mode": "llm-worker",
                "model": "qwen-test",
                "ai_assist": {"mode": "llm", "degraded": False, "reason": None},
            },
            "processing": {"recipe_version": "duckdock-analysis-worker-v1"},
            "worker_metadata": {
                "recipe_version": "duckdock-analysis-worker-v2",
                "prompt_version": "duckdock-analysis-prompt-v3",
                "trace_id": "trace-123",
                "token_usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
            },
            "limitations": [],
        }

        finalized = await finalize_analysis_job(
            session_db,
            worker=worker,
            job_id=job.id,
            body=_finalize_body(job, fake_artifacts),
        )

        metadata = finalized.summary_json["worker_metadata"]
        assert finalized.summary_json["analysis_result_schema_version"] == "duckdock-analysis-v1"
        assert metadata["recipe_version"] == "duckdock-analysis-worker-v2"
        assert metadata["prompt_version"] == "duckdock-analysis-prompt-v3"
        assert metadata["model"] == "qwen-test"
        assert metadata["trace_id"] == "trace-123"
        assert metadata["token_usage"]["total_tokens"] == 20
        assert metadata["analysis_mode"] == "llm-worker"
        assert metadata["ai_assist"]["mode"] == "llm"
        assert collection_job.summary_json["worker_metadata"]["trace_id"] == "trace-123"


def test_finalize_invalid_analysis_result_schema_marks_job_failed(monkeypatch):
    asyncio.run(_test_finalize_invalid_analysis_result_schema_marks_job_failed(monkeypatch))


async def _test_finalize_invalid_analysis_result_schema_marks_job_failed(monkeypatch):
    async with db_session() as session_db:
        fake_artifacts = FakeAnalysisArtifactService(sha256="d" * 64, size_bytes=256)
        monkeypatch.setattr(analysis_service, "artifact_service", fake_artifacts)
        job, upload_session, collection_job, worker = await _seed_finalizable_job(session_db, fake_artifacts)
        fake_artifacts.objects[job.result_object_key] = {
            "schema_version": "duckdock-analysis-v0",
            "report_id": upload_session.report_id,
            "summary": {},
            "limitations": [],
        }

        with pytest.raises(HTTPException) as exc_info:
            await finalize_analysis_job(
                session_db,
                worker=worker,
                job_id=job.id,
                body=_finalize_body(job, fake_artifacts),
            )

        assert exc_info.value.status_code == 422
        assert "schema_version" in exc_info.value.detail
        assert job.status == AnalysisJobStatus.FAILED
        assert job.error_message == exc_info.value.detail
        assert upload_session.status == ReportUploadStatus.FAILED
        assert collection_job.status == JobStatus.FAILED
        assert collection_job.summary_json["result_validation_error"] == exc_info.value.detail


def test_finalize_invalid_standard_artifact_blocks_materialization(monkeypatch):
    asyncio.run(_test_finalize_invalid_standard_artifact_blocks_materialization(monkeypatch))


async def _test_finalize_invalid_standard_artifact_blocks_materialization(monkeypatch):
    async with db_session() as session_db:
        fake_artifacts = FakeAnalysisArtifactService(sha256="d" * 64, size_bytes=256)
        monkeypatch.setattr(analysis_service, "artifact_service", fake_artifacts)
        job, upload_session, _, worker = await _seed_finalizable_job(session_db, fake_artifacts)
        asset_key = fake_artifacts.report_analysis_result_object_key(
            report_id=upload_session.report_id,
            job_id=job.id,
            filename="asset-cards.json",
        )
        fake_artifacts.objects[job.result_object_key] = {
            "schema_version": "duckdock-analysis-v1",
            "report_id": upload_session.report_id,
            "summary": {},
            "limitations": [],
        }
        fake_artifacts.objects[asset_key] = [{"name": "Broken Asset", "asset_type": "not-real"}]

        with pytest.raises(HTTPException) as exc_info:
            await finalize_analysis_job(
                session_db,
                worker=worker,
                job_id=job.id,
                body=AnalysisJobFinalize(
                    result_sha256=fake_artifacts.sha256,
                    result_size_bytes=fake_artifacts.size_bytes,
                    summary_json={"memory_candidate_count": 0},
                    result_artifacts=[
                        AnalysisResultArtifactFinalize(
                            kind=AnalysisResultArtifactKind.ANALYSIS_RESULT,
                            filename="analysis-result.json",
                            object_key=job.result_object_key,
                            content_type="application/json",
                            sha256=fake_artifacts.sha256,
                            size_bytes=fake_artifacts.size_bytes,
                        ),
                        AnalysisResultArtifactFinalize(
                            kind=AnalysisResultArtifactKind.ASSET_CARDS,
                            filename="asset-cards.json",
                            object_key=asset_key,
                            content_type="application/json",
                            sha256=fake_artifacts.sha256,
                            size_bytes=fake_artifacts.size_bytes,
                        ),
                    ],
                    memory_candidates=[],
                ),
            )

        assert exc_info.value.status_code == 422
        assert "asset-cards.json[0].asset_type" in exc_info.value.detail
        assert job.status == AnalysisJobStatus.FAILED


def test_cancel_terminal_job_is_rejected():
    asyncio.run(_test_cancel_terminal_job_is_rejected())


async def _test_cancel_terminal_job_is_rejected():
    async with db_session() as session_db:
        job, _, _ = await seed_analysis_job(
            session_db,
            status=AnalysisJobStatus.SUCCEEDED,
            report_status=ReportUploadStatus.SUCCEEDED,
            collection_status=JobStatus.SUCCEEDED,
        )

        with pytest.raises(HTTPException) as exc_info:
            await cancel_analysis_job(session_db, job_id=job.id, reason="too late", cancelled_by=1)
        assert exc_info.value.status_code == 409


def test_materializer_resolves_owner_and_actor_user(monkeypatch):
    asyncio.run(_test_materializer_resolves_owner_and_actor_user(monkeypatch))


async def _test_materializer_resolves_owner_and_actor_user(monkeypatch):
    async with db_session() as session_db:
        user = User(
            username="employee",
            email="employee@duckdock-ai.com",
            hashed_password="test",
            system_role=SystemRole.USER,
        )
        session_db.add(user)
        await session_db.flush()

        job, upload_session, _ = await seed_analysis_job(
            session_db,
            status=AnalysisJobStatus.SUCCEEDED,
            report_status=ReportUploadStatus.SUCCEEDED,
            collection_status=JobStatus.SUCCEEDED,
        )
        runtime = await session_db.get(RuntimeInstance, job.runtime_id)
        upload_session.manifest_json = {"actor_username": "employee"}

        asset_artifact = AnalysisResultArtifact(
            analysis_job_id=job.id,
            report_upload_session_id=upload_session.id,
            runtime_id=job.runtime_id,
            kind=AnalysisResultArtifactKind.ASSET_CARDS,
            bucket="duckdock",
            object_key="analysis/test/asset-cards.json",
            filename="asset-cards.json",
            content_type="application/json",
            sha256="b" * 64,
            size_bytes=128,
        )
        trace_artifact = AnalysisResultArtifact(
            analysis_job_id=job.id,
            report_upload_session_id=upload_session.id,
            runtime_id=job.runtime_id,
            kind=AnalysisResultArtifactKind.WORKTRACE_SUMMARY,
            bucket="duckdock",
            object_key="analysis/test/worktrace-summary.md",
            filename="worktrace-summary.md",
            content_type="text/markdown",
            sha256="c" * 64,
            size_bytes=128,
            summary_json={"actor_email": "employee@duckdock-ai.com"},
        )
        session_db.add_all([asset_artifact, trace_artifact])
        await session_db.flush()

        monkeypatch.setattr(
            analysis_materializer,
            "_load_json_items",
            lambda object_key: [
                {
                    "name": "Employee-owned Reporter Skill",
                    "asset_type": "skill",
                    "external_id": "skill-owned-by-employee",
                    "owner_username": "employee",
                    "owner_email": "employee@duckdock-ai.com",
                    "owner_type": "creator",
                    "ownership_confidence": 0.92,
                }
            ]
            if object_key.endswith("asset-cards.json")
            else [],
        )
        monkeypatch.setattr(
            analysis_materializer,
            "_load_text",
            lambda object_key: "# Employee Weekly Summary\nCollected via DuckDock Reporter.",
        )

        counts = await materialize_analysis_result(
            session_db,
            job=job,
            session=upload_session,
            runtime=runtime,
            artifacts=[asset_artifact, trace_artifact],
        )

        assert counts["asset_cards"] == 1
        assert counts["asset_ownerships"] == 1
        assert counts["worktrace_summaries"] == 1

        asset = (
            await session_db.execute(select(AIAsset).where(AIAsset.external_id == "skill-owned-by-employee"))
        ).scalar_one()
        ownership = (
            await session_db.execute(select(AssetOwnership).where(AssetOwnership.asset_id == asset.id))
        ).scalar_one()
        assert ownership.user_id == user.id
        assert ownership.is_primary is True

        trace = (
            await session_db.execute(select(WorkTrace).where(WorkTrace.external_session_id.like("analysis-summary:%")))
        ).scalar_one()
        assert trace.actor_user_id == user.id


def test_materializer_falls_back_to_reporter_enrollment_actor(monkeypatch):
    asyncio.run(_test_materializer_falls_back_to_reporter_enrollment_actor(monkeypatch))


async def _test_materializer_falls_back_to_reporter_enrollment_actor(monkeypatch):
    async with db_session() as session_db:
        user = User(
            username="employee",
            email="employee@duckdock-ai.com",
            hashed_password="test",
            system_role=SystemRole.USER,
        )
        session_db.add(user)
        await session_db.flush()

        job, upload_session, _ = await seed_analysis_job(
            session_db,
            status=AnalysisJobStatus.SUCCEEDED,
            report_status=ReportUploadStatus.SUCCEEDED,
            collection_status=JobStatus.SUCCEEDED,
        )
        runtime = await session_db.get(RuntimeInstance, job.runtime_id)
        runtime.metadata_json = {
            "reporter": {
                "enrollment": {
                    "mode": "self_service",
                    "user_id": user.id,
                    "username": user.username,
                    "device_id": "hermes-reporter",
                    "agent_kind": "hermes",
                }
            }
        }
        upload_session.metadata_json = {"agent_kind": "hermes"}
        upload_session.manifest_json = {"provider": "custom", "agent_kind": "hermes"}

        trace_artifact = AnalysisResultArtifact(
            analysis_job_id=job.id,
            report_upload_session_id=upload_session.id,
            runtime_id=job.runtime_id,
            kind=AnalysisResultArtifactKind.WORKTRACE_SUMMARY,
            bucket="duckdock",
            object_key="analysis/test/worktrace-summary.md",
            filename="worktrace-summary.md",
            content_type="text/markdown",
            sha256="c" * 64,
            size_bytes=128,
            summary_json={},
        )
        session_db.add(trace_artifact)
        await session_db.flush()

        monkeypatch.setattr(
            analysis_materializer,
            "_load_text",
            lambda object_key: "# Hermes Reporter Summary\nCollected through self-service Reporter enrollment.",
        )

        counts = await materialize_analysis_result(
            session_db,
            job=job,
            session=upload_session,
            runtime=runtime,
            artifacts=[trace_artifact],
        )

        assert counts["worktrace_summaries"] == 1
        trace = (
            await session_db.execute(select(WorkTrace).where(WorkTrace.external_session_id.like("analysis-summary:%")))
        ).scalar_one()
        assert trace.actor_user_id == user.id


# --- FR-005-PARTIAL-FAILED ---------------------------------------------------


def test_loader_distinguishes_read_failure_from_absent_data(monkeypatch):
    # _load_json_items / _load_text must raise ArtifactReadError on a read/parse
    # failure (so finalize can record a partial failure) but return empty on
    # genuinely-absent-but-valid data, instead of swallowing both as empty.
    from app.services.analysis_materializer import ArtifactReadError

    class LoaderArtifactService:
        def read_object_bytes(self, object_key, *, max_bytes=None):
            if object_key == "missing.json":
                raise ArtifactStorageError("object not found")
            if object_key == "broken.json":
                return b"{not valid json"
            if object_key == "empty.json":
                return b"[]"
            if object_key == "broken.md":
                return b"\xff\xfe\x00bad-utf8"
            return b"# heading"

    monkeypatch.setattr(analysis_materializer, "artifact_service", LoaderArtifactService())

    # genuinely-absent data -> empty, no raise
    assert analysis_materializer._load_json_items("empty.json") == []
    # read failure and parse failure -> raise (distinguishable from "no data")
    with pytest.raises(ArtifactReadError):
        analysis_materializer._load_json_items("missing.json")
    with pytest.raises(ArtifactReadError):
        analysis_materializer._load_json_items("broken.json")
    with pytest.raises(ArtifactReadError):
        analysis_materializer._load_text("broken.md")
    # readable text -> returned
    assert analysis_materializer._load_text("ok.md") == "# heading"


def test_materialize_accumulates_per_artifact_failures(monkeypatch):
    asyncio.run(_test_materialize_accumulates_per_artifact_failures(monkeypatch))


async def _test_materialize_accumulates_per_artifact_failures(monkeypatch):
    from app.services.analysis_materializer import ArtifactReadError

    async with db_session() as session_db:
        job, upload_session, _ = await seed_analysis_job(
            session_db,
            status=AnalysisJobStatus.SUCCEEDED,
            report_status=ReportUploadStatus.SUCCEEDED,
            collection_status=JobStatus.SUCCEEDED,
        )
        runtime = await session_db.get(RuntimeInstance, job.runtime_id)

        ok_artifact = AnalysisResultArtifact(
            analysis_job_id=job.id,
            report_upload_session_id=upload_session.id,
            runtime_id=job.runtime_id,
            kind=AnalysisResultArtifactKind.MEMORY_CANDIDATES,
            bucket="duckdock",
            object_key="analysis/test/memory-candidates.json",
            filename="memory-candidates.json",
            content_type="application/json",
            sha256="b" * 64,
            size_bytes=128,
        )
        bad_artifact = AnalysisResultArtifact(
            analysis_job_id=job.id,
            report_upload_session_id=upload_session.id,
            runtime_id=job.runtime_id,
            kind=AnalysisResultArtifactKind.HANDOVER_SIGNALS,
            bucket="duckdock",
            object_key="analysis/test/handover-signals.json",
            filename="handover-signals.json",
            content_type="application/json",
            sha256="c" * 64,
            size_bytes=128,
        )
        session_db.add_all([ok_artifact, bad_artifact])
        await session_db.flush()

        def fake_load_json_items(object_key):
            if object_key.endswith("handover-signals.json"):
                raise ArtifactReadError("could not read handover signals")
            return [{"title": "Knowledge note", "subject_key": "k-1"}]

        monkeypatch.setattr(analysis_materializer, "_load_json_items", fake_load_json_items)

        counts = await materialize_analysis_result(
            session_db,
            job=job,
            session=upload_session,
            runtime=runtime,
            artifacts=[ok_artifact, bad_artifact],
        )

        assert counts["memory_candidates"] == 1
        assert counts["succeeded_artifacts"] == 1
        assert len(counts["failures"]) == 1
        assert counts["failures"][0]["kind"] == AnalysisResultArtifactKind.HANDOVER_SIGNALS.value
        assert counts["failures"][0]["object_key"] == "analysis/test/handover-signals.json"


def test_materialize_reports_parsed_inserted_updated_and_deduped_counts(monkeypatch):
    asyncio.run(_test_materialize_reports_parsed_inserted_updated_and_deduped_counts(monkeypatch))


async def _test_materialize_reports_parsed_inserted_updated_and_deduped_counts(monkeypatch):
    async with db_session() as session_db:
        job, upload_session, _ = await seed_analysis_job(
            session_db,
            status=AnalysisJobStatus.RUNNING,
            report_status=ReportUploadStatus.INGESTING,
            collection_status=JobStatus.RUNNING,
        )
        runtime = (
            await session_db.execute(select(RuntimeInstance).where(RuntimeInstance.id == job.runtime_id))
        ).scalar_one()
        asset_artifact = AnalysisResultArtifact(
            analysis_job_id=job.id,
            report_upload_session_id=upload_session.id,
            runtime_id=job.runtime_id,
            kind=AnalysisResultArtifactKind.ASSET_CARDS,
            bucket="duckdock",
            object_key="analysis/test/asset-cards.json",
            filename="asset-cards.json",
            content_type="application/json",
            sha256="c" * 64,
            size_bytes=128,
        )
        trace_artifact = AnalysisResultArtifact(
            analysis_job_id=job.id,
            report_upload_session_id=upload_session.id,
            runtime_id=job.runtime_id,
            kind=AnalysisResultArtifactKind.WORKTRACE_SUMMARY,
            bucket="duckdock",
            object_key="analysis/test/worktrace-summary.md",
            filename="worktrace-summary.md",
            content_type="text/markdown",
            sha256="c" * 64,
            size_bytes=128,
        )
        memory_artifact = AnalysisResultArtifact(
            analysis_job_id=job.id,
            report_upload_session_id=upload_session.id,
            runtime_id=job.runtime_id,
            kind=AnalysisResultArtifactKind.MEMORY_CANDIDATES,
            bucket="duckdock",
            object_key="analysis/test/memory-candidates.json",
            filename="memory-candidates.json",
            content_type="application/json",
            sha256="c" * 64,
            size_bytes=128,
        )
        signal_artifact = AnalysisResultArtifact(
            analysis_job_id=job.id,
            report_upload_session_id=upload_session.id,
            runtime_id=job.runtime_id,
            kind=AnalysisResultArtifactKind.HANDOVER_SIGNALS,
            bucket="duckdock",
            object_key="analysis/test/handover-signals.json",
            filename="handover-signals.json",
            content_type="application/json",
            sha256="c" * 64,
            size_bytes=128,
        )
        session_db.add_all([asset_artifact, trace_artifact, memory_artifact, signal_artifact])
        await session_db.flush()

        def fake_load_json_items(object_key):
            if object_key.endswith("asset-cards.json"):
                return [{"name": "Reusable Reporter Skill", "asset_type": "skill", "external_id": "skill/reporter"}]
            if object_key.endswith("memory-candidates.json"):
                return [{"title": "Runtime context", "subject_type": "runtime", "subject_key": "runtime-context"}]
            if object_key.endswith("handover-signals.json"):
                return [
                    {
                        "signal_type": "handover",
                        "title": "Confirm reporter owner",
                        "subject_type": "skill",
                        "subject_key": "skill/reporter",
                    },
                    {
                        "signal_type": "risk",
                        "title": "Review credential reference",
                        "subject_type": "credential_ref",
                        "subject_key": "vault://duckdock/reporter",
                    },
                ]
            return []

        monkeypatch.setattr(analysis_materializer, "_load_json_items", fake_load_json_items)
        monkeypatch.setattr(analysis_materializer, "_load_text", lambda object_key: "# Reporter Summary\nStable.\n")
        artifacts = [asset_artifact, trace_artifact, memory_artifact, signal_artifact]

        first = await materialize_analysis_result(
            session_db,
            job=job,
            session=upload_session,
            runtime=runtime,
            artifacts=artifacts,
        )
        second = await materialize_analysis_result(
            session_db,
            job=job,
            session=upload_session,
            runtime=runtime,
            artifacts=artifacts,
        )

        assert first["parsed_counts"]["asset_cards"] == 1
        assert first["inserted_counts"]["asset_cards"] == 1
        assert first["inserted_counts"]["runtime_bindings"] == 1
        assert first["inserted_counts"]["worktrace_summaries"] == 1
        assert first["inserted_counts"]["memory_candidates"] == 1
        assert first["inserted_counts"]["handover_signals"] == 1
        assert first["inserted_counts"]["risk_signals"] == 1
        assert first["asset_cards"] == 1

        assert second["parsed_counts"]["asset_cards"] == 1
        assert second["inserted_counts"]["asset_cards"] == 0
        assert second["updated_counts"]["asset_cards"] == 1
        assert second["updated_counts"]["runtime_bindings"] == 1
        assert second["updated_counts"]["worktrace_summaries"] == 1
        assert second["deduped_counts"]["memory_candidates"] == 1
        assert second["deduped_counts"]["handover_signals"] == 1
        assert second["deduped_counts"]["risk_signals"] == 1
        assert second["asset_cards"] == 0


def test_finalize_partial_failure_sets_collection_partial_failed(monkeypatch):
    asyncio.run(_test_finalize_partial_failure_sets_collection_partial_failed(monkeypatch))


async def _test_finalize_partial_failure_sets_collection_partial_failed(monkeypatch):
    async with db_session() as session_db:
        fake_artifacts = FakeAnalysisArtifactService(sha256="d" * 64, size_bytes=256)
        monkeypatch.setattr(analysis_service, "artifact_service", fake_artifacts)

        async def fake_materialize(db, *, job, session, runtime, artifacts):
            return {
                "memory_candidates": 1,
                "succeeded_artifacts": 1,
                "failures": [
                    {"kind": "handover_signals", "object_key": "analysis/x/handover.json", "error": "boom"}
                ],
            }

        monkeypatch.setattr(analysis_service, "materialize_analysis_result", fake_materialize)
        job, upload_session, collection_job, worker = await _seed_finalizable_job(session_db, fake_artifacts)

        finalized = await finalize_analysis_job(
            session_db,
            worker=worker,
            job_id=job.id,
            body=_finalize_body(job, fake_artifacts),
        )

        # Analysis job itself stays SUCCEEDED (enum has no partial state) but records detail.
        assert finalized.status == AnalysisJobStatus.SUCCEEDED
        assert finalized.summary_json["partial_failure_count"] == 1
        assert finalized.summary_json["partial_failures"][0]["kind"] == "handover_signals"
        # The collection job carries the explicit PARTIAL_FAILED status.
        assert collection_job.status == JobStatus.PARTIAL_FAILED
        assert collection_job.summary_json["partial_failure_count"] == 1
        assert collection_job.error_message


def test_finalize_all_artifacts_failed_marks_collection_failed(monkeypatch):
    asyncio.run(_test_finalize_all_artifacts_failed_marks_collection_failed(monkeypatch))


async def _test_finalize_all_artifacts_failed_marks_collection_failed(monkeypatch):
    async with db_session() as session_db:
        fake_artifacts = FakeAnalysisArtifactService(sha256="d" * 64, size_bytes=256)
        monkeypatch.setattr(analysis_service, "artifact_service", fake_artifacts)

        async def fake_materialize(db, *, job, session, runtime, artifacts):
            return {
                "memory_candidates": 0,
                "succeeded_artifacts": 0,
                "failures": [
                    {"kind": "asset_cards", "object_key": "analysis/x/asset.json", "error": "boom"},
                    {"kind": "handover_signals", "object_key": "analysis/x/handover.json", "error": "boom"},
                ],
            }

        monkeypatch.setattr(analysis_service, "materialize_analysis_result", fake_materialize)
        job, upload_session, collection_job, worker = await _seed_finalizable_job(session_db, fake_artifacts)

        await finalize_analysis_job(
            session_db,
            worker=worker,
            job_id=job.id,
            body=_finalize_body(job, fake_artifacts),
        )

        assert collection_job.status == JobStatus.FAILED
        assert collection_job.summary_json["partial_failure_count"] == 2


def test_finalize_all_artifacts_ok_marks_collection_succeeded(monkeypatch):
    asyncio.run(_test_finalize_all_artifacts_ok_marks_collection_succeeded(monkeypatch))


async def _test_finalize_all_artifacts_ok_marks_collection_succeeded(monkeypatch):
    async with db_session() as session_db:
        fake_artifacts = FakeAnalysisArtifactService(sha256="d" * 64, size_bytes=256)
        monkeypatch.setattr(analysis_service, "artifact_service", fake_artifacts)

        async def fake_materialize(db, *, job, session, runtime, artifacts):
            return {"memory_candidates": 2, "succeeded_artifacts": 2, "failures": []}

        monkeypatch.setattr(analysis_service, "materialize_analysis_result", fake_materialize)
        job, upload_session, collection_job, worker = await _seed_finalizable_job(session_db, fake_artifacts)

        finalized = await finalize_analysis_job(
            session_db,
            worker=worker,
            job_id=job.id,
            body=_finalize_body(job, fake_artifacts),
        )

        assert finalized.status == AnalysisJobStatus.SUCCEEDED
        assert "partial_failure_count" not in finalized.summary_json
        assert collection_job.status == JobStatus.SUCCEEDED


# --- PUSH-02 (inline memory candidate dedup) ---------------------------------


def test_finalize_dedups_inline_memory_candidates(monkeypatch):
    asyncio.run(_test_finalize_dedups_inline_memory_candidates(monkeypatch))


async def _test_finalize_dedups_inline_memory_candidates(monkeypatch):
    async with db_session() as session_db:
        fake_artifacts = FakeAnalysisArtifactService(sha256="d" * 64, size_bytes=256)
        monkeypatch.setattr(analysis_service, "artifact_service", fake_artifacts)

        async def fake_materialize(db, *, job, session, runtime, artifacts):
            return {"succeeded_artifacts": 1, "failures": []}

        monkeypatch.setattr(analysis_service, "materialize_analysis_result", fake_materialize)
        job, upload_session, _, worker = await _seed_finalizable_job(session_db, fake_artifacts)

        duplicate = MemoryCandidateCreate(
            candidate_type=MemoryCandidateType.KNOWLEDGE_NOTE,
            subject_type="runtime",
            subject_key="subj-1",
            title="Duplicated candidate",
        )
        unique = MemoryCandidateCreate(
            candidate_type=MemoryCandidateType.KNOWLEDGE_NOTE,
            subject_type="runtime",
            subject_key="subj-2",
            title="Unique candidate",
        )

        await finalize_analysis_job(
            session_db,
            worker=worker,
            job_id=job.id,
            body=_finalize_body(job, fake_artifacts, memory_candidates=[duplicate, duplicate, unique]),
        )

        rows = (
            await session_db.execute(
                select(MemoryCandidate).where(MemoryCandidate.analysis_job_id == job.id)
            )
        ).scalars().all()
        titles = sorted(row.title for row in rows)
        assert titles == ["Duplicated candidate", "Unique candidate"]


# --- PUSH-04 (per-attempt result_object_key) ---------------------------------


def test_lease_varies_result_object_key_per_attempt(monkeypatch):
    asyncio.run(_test_lease_varies_result_object_key_per_attempt(monkeypatch))


async def _test_lease_varies_result_object_key_per_attempt(monkeypatch):
    # PUSH-04: a recycled job must write to a different result object on each attempt
    # so a slow old worker's stale PUT cannot clobber the new worker's result.
    async with db_session() as session_db:
        monkeypatch.setattr(analysis_service, "artifact_service", FakeAnalysisArtifactService())
        worker = await seed_worker(session_db)
        job, _upload_session, _ = await seed_analysis_job(
            session_db,
            status=AnalysisJobStatus.PENDING,
            report_status=ReportUploadStatus.UPLOADED,
            collection_status=JobStatus.PENDING,
        )

        first_lease = await lease_next_analysis_job(session_db, worker=worker, lease_seconds=600)
        assert first_lease is not None
        first_key = first_lease.result_object_key
        assert job.attempts == 1

        # Simulate the reaper recycling the crashed worker's job (PUSH-01 behaviour).
        now = datetime.now(timezone.utc)
        job.lease_expires_at = now - timedelta(seconds=1)
        await session_db.flush()

        new_worker = await seed_worker(session_db)
        second_lease = await lease_next_analysis_job(session_db, worker=new_worker, lease_seconds=600)
        assert second_lease is not None
        assert second_lease.job.id == job.id
        assert job.attempts == 2

        assert second_lease.result_object_key != first_key
        assert job.result_object_key == second_lease.result_object_key


async def _seed_finalizable_job(session_db, fake_artifacts):
    worker = await seed_worker(session_db)
    job, upload_session, collection_job = await seed_analysis_job(
        session_db,
        status=AnalysisJobStatus.RUNNING,
        report_status=ReportUploadStatus.INGESTING,
        collection_status=JobStatus.RUNNING,
        worker=worker,
        attempts=1,
        max_attempts=3,
    )
    job.result_object_key = fake_artifacts.report_analysis_result_object_key(
        report_id=upload_session.report_id,
        job_id=job.id,
    )
    await session_db.flush()
    return job, upload_session, collection_job, worker


def _finalize_body(job, fake_artifacts, *, memory_candidates=None):
    return AnalysisJobFinalize(
        result_sha256=fake_artifacts.sha256,
        result_size_bytes=fake_artifacts.size_bytes,
        summary_json={"memory_candidate_count": 0},
        result_artifacts=[
            AnalysisResultArtifactFinalize(
                kind=AnalysisResultArtifactKind.ANALYSIS_RESULT,
                filename="analysis-result.json",
                object_key=job.result_object_key,
                content_type="application/json",
                sha256=fake_artifacts.sha256,
                size_bytes=fake_artifacts.size_bytes,
            )
        ],
        memory_candidates=memory_candidates or [],
    )


async def seed_worker(
    db: AsyncSession,
    *,
    prefix: str | None = None,
    secret: str = "secret",
) -> AnalysisWorker:
    prefix = prefix or uuid4().hex[:8]
    worker = AnalysisWorker(
        name=f"worker-{prefix}",
        worker_key=f"ocw_{uuid4().hex[:12]}",
        token_prefix=prefix,
        token_hash=hash_password(secret),
        status=AnalysisWorkerStatus.ACTIVE,
        capabilities_json={"test": True},
    )
    db.add(worker)
    await db.flush()
    return worker


class FakeAnalysisArtifactService:
    bucket = "duckdock"

    def __init__(
        self,
        *,
        sha256: str = "a" * 64,
        size_bytes: int = 128,
        objects: dict[str, bytes | str | dict | list] | None = None,
    ):
        self.sha256 = sha256
        self.size_bytes = size_bytes
        self.objects = objects or {}

    def report_analysis_result_object_key(
        self,
        *,
        report_id: str,
        job_id: int,
        filename: str = "analysis-result.json",
    ) -> str:
        return f"analysis/2026/05/23/{report_id}/job-{job_id}/{filename}"

    def generate_presigned_get_url(self, *, object_key: str, expires_in: int):
        return {
            "download_url": f"http://minio.test/{object_key}",
            "expires_in": expires_in,
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        }

    def generate_presigned_put_url(self, *, object_key: str, content_type: str, expires_in: int):
        return {
            "upload_url": f"http://minio.test/{object_key}",
            "expires_in": expires_in,
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        }

    def head_object(self, object_key: str):
        return {"size_bytes": self.size_bytes}

    def hash_object(self, object_key: str):
        return {"sha256": self.sha256, "size_bytes": self.size_bytes}

    async def head_object_async(self, object_key: str):
        return self.head_object(object_key)

    async def hash_object_async(self, object_key: str):
        return self.hash_object(object_key)

    def read_object_bytes(self, object_key: str, *, max_bytes: int | None = None):
        value = self.objects.get(object_key)
        if value is None:
            value = self._default_object(object_key)
        if isinstance(value, bytes):
            payload = value
        elif isinstance(value, str):
            payload = value.encode("utf-8")
        else:
            payload = json.dumps(value).encode("utf-8")
        if max_bytes is not None and len(payload) > max_bytes:
            raise ArtifactStorageError("object is too large")
        return payload

    def _default_object(self, object_key: str):
        if object_key.endswith("analysis-result.json"):
            return {
                "schema_version": "duckdock-analysis-v1",
                "report_id": self._report_id_from_object_key(object_key),
                "summary": {"memory_candidate_count": 0},
                "limitations": [],
            }
        if object_key.endswith("worktrace-summary.md"):
            return "# Work summary\n\n- Test summary.\n"
        return []

    @staticmethod
    def _report_id_from_object_key(object_key: str) -> str:
        marker = "/job-"
        before_job = object_key.split(marker, 1)[0]
        return before_job.rsplit("/", 1)[-1]


async def seed_analysis_job(
    db: AsyncSession,
    *,
    status: AnalysisJobStatus,
    report_status: ReportUploadStatus,
    collection_status: JobStatus,
    worker: AnalysisWorker | None = None,
    attempts: int = 0,
    max_attempts: int = 3,
) -> tuple[ReportAnalysisJob, ReportUploadSession, CollectionJob]:
    now = datetime.now(timezone.utc)
    suffix = uuid4().hex
    owner = User(
        username=f"analysis-owner-{suffix}",
        email=f"analysis-owner-{suffix}@example.test",
        hashed_password="unused",
    )
    db.add(owner)
    await db.flush()
    namespace = Namespace(name=f"analysis-{suffix}", owner_id=owner.id)
    db.add(namespace)
    await db.flush()
    runtime = RuntimeInstance(
        namespace_id=namespace.id,
        provider=RuntimeProvider.OPENCLAW,
        name=f"runtime-{suffix}",
        deploy_type=RuntimeDeployType.PRIVATE,
        status=RuntimeStatus.ACTIVE,
    )
    db.add(runtime)
    await db.flush()

    collection_job = CollectionJob(
        runtime_id=runtime.id,
        trigger_type=CollectionTriggerType.SCHEDULED,
        status=collection_status,
        scope_json={"test": True},
    )
    db.add(collection_job)
    await db.flush()

    session = ReportUploadSession(
        report_id=f"rpt_{suffix}",
        runtime_id=runtime.id,
        collection_job_id=collection_job.id,
        status=report_status,
        schema_version="duckdock-pack-v1",
        report_type="weekly",
        bucket="duckdock",
        object_key=f"reports/runtime-{runtime.id}/{suffix}/pack.zip",
        filename="pack.zip",
        content_type="application/zip",
        upload_expires_at=now + timedelta(minutes=15),
        error_message="previous error" if report_status == ReportUploadStatus.FAILED else None,
        created_via="test",
    )
    db.add(session)
    await db.flush()

    job = ReportAnalysisJob(
        report_upload_session_id=session.id,
        runtime_id=runtime.id,
        status=status,
        priority=100,
        worker_id=worker.id if worker is not None and status in {AnalysisJobStatus.LEASED, AnalysisJobStatus.RUNNING} else None,
        lease_owner=worker.worker_key if worker is not None and status in {AnalysisJobStatus.LEASED, AnalysisJobStatus.RUNNING} else None,
        lease_expires_at=now + timedelta(minutes=30) if worker is not None and status in {AnalysisJobStatus.LEASED, AnalysisJobStatus.RUNNING} else None,
        attempts=attempts,
        max_attempts=max_attempts,
        input_bucket="duckdock",
        input_object_key=session.object_key,
        input_sha256=None,
        input_size_bytes=128,
        result_bucket="duckdock" if status != AnalysisJobStatus.PENDING else None,
        result_object_key=f"analysis/{suffix}/analysis-result.json" if status != AnalysisJobStatus.PENDING else None,
        result_content_type="application/json" if status != AnalysisJobStatus.PENDING else None,
        result_sha256="a" * 64 if status == AnalysisJobStatus.SUCCEEDED else None,
        result_size_bytes=64 if status == AnalysisJobStatus.SUCCEEDED else None,
        summary_json={"seed": True},
        error_message="previous error" if status == AnalysisJobStatus.FAILED else None,
        started_at=now if status in {AnalysisJobStatus.LEASED, AnalysisJobStatus.RUNNING, AnalysisJobStatus.SUCCEEDED, AnalysisJobStatus.FAILED} else None,
        finished_at=now if status in {AnalysisJobStatus.SUCCEEDED, AnalysisJobStatus.FAILED, AnalysisJobStatus.CANCELLED} else None,
    )
    db.add(job)
    await db.flush()
    return job, session, collection_job
