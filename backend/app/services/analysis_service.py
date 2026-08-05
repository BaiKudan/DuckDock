from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select

from app.core.config import settings
from app.core.security import verify_password
from app.models.control_plane import (
    AnalysisJobStatus,
    AnalysisResultArtifact,
    AnalysisResultArtifactKind,
    AnalysisWorker,
    AnalysisWorkerStatus,
    CollectionJob,
    JobStatus,
    MemoryCandidate,
    MemoryCandidateStatus,
    ReportAnalysisJob,
    ReportUploadSession,
    ReportUploadStatus,
    RuntimeInstance,
)
from app.schemas.control_plane import AnalysisJobFail, AnalysisJobFinalize
from app.services.analysis_materializer import materialize_analysis_result
from app.services.artifact_service import ArtifactStorageError, artifact_service


DEFAULT_ANALYSIS_RESULT_FILES: tuple[tuple[AnalysisResultArtifactKind, str, str], ...] = (
    (AnalysisResultArtifactKind.ANALYSIS_RESULT, "analysis-result.json", "application/json"),
    (AnalysisResultArtifactKind.ASSET_CARDS, "asset-cards.json", "application/json"),
    (AnalysisResultArtifactKind.WORKTRACE_SUMMARY, "worktrace-summary.md", "text/markdown"),
    (AnalysisResultArtifactKind.MEMORY_CANDIDATES, "memory-candidates.json", "application/json"),
    (AnalysisResultArtifactKind.HANDOVER_SIGNALS, "handover-signals.json", "application/json"),
)

ANALYSIS_RESULT_SCHEMA_VERSION = "duckdock-analysis-v1"
ANALYSIS_WORKER_METADATA_FIELDS = (
    "recipe_version",
    "prompt_version",
    "model",
    "trace_id",
    "token_usage",
    "analysis_mode",
    "ai_assist",
)
VALID_RESULT_ASSET_TYPES = {
    "skill",
    "agent",
    "prompt",
    "workflow",
    "mcp",
    "tool",
    "knowledge_base",
    "scheduled_task",
    "credential_ref",
    "workspace",
    "other",
}
VALID_RESULT_ASSET_STATUSES = {"active", "inactive", "archived", "orphaned", "risky", "transferred"}
VALID_RESULT_CRITICALITIES = {"low", "medium", "high", "critical"}
VALID_RESULT_CANDIDATE_TYPES = {
    "asset_summary",
    "worktrace_summary",
    "project_context",
    "ownership_signal",
    "handover_signal",
    "risk_signal",
    "knowledge_note",
}
VALID_RESULT_SENSITIVITIES = {"public", "internal", "confidential", "restricted"}
VALID_RESULT_SIGNAL_TYPES = {"handover", "risk"}


class AnalysisResultValidationError(ValueError):
    pass


@dataclass(slots=True)
class AnalysisWorkerAuthContext:
    worker: AnalysisWorker


@dataclass(slots=True)
class AnalysisJobLease:
    job: ReportAnalysisJob
    download: dict[str, Any]
    upload: dict[str, Any]
    result_object_key: str
    uploads: list[dict[str, Any]]


@dataclass(slots=True)
class AnalysisWorkerDisableResult:
    worker: AnalysisWorker
    requeued_job_count: int = 0
    failed_job_count: int = 0


@dataclass(slots=True)
class AnalysisLeaseReaperResult:
    requeued_job_count: int = 0
    failed_job_count: int = 0


@dataclass(slots=True)
class AnalysisQueueMetrics:
    status_counts: dict[str, int]
    worker_status_counts: dict[str, int]
    pending_count: int
    active_job_count: int
    backlog_count: int
    terminal_job_count: int
    expired_lease_count: int
    retryable_expired_lease_count: int
    registered_worker_count: int
    online_worker_count: int
    stale_worker_count: int
    disabled_worker_count: int
    oldest_pending_seconds: int | None
    queued_per_online_worker: float
    saturation_level: str
    saturation_reason: str | None


def generate_analysis_worker_token() -> tuple[str, str, str, str]:
    worker_key = f"ocw_{uuid4().hex[:12]}"
    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    return worker_key, prefix, secret, f"dkr_worker_{prefix}_{secret}"


async def authenticate_analysis_worker_token(db, token: str) -> AnalysisWorkerAuthContext:
    parts = token.split("_", 3)
    if len(parts) != 4 or parts[0] != "dkr" or parts[1] != "worker":
        raise _worker_auth_error()
    prefix, secret = parts[2], parts[3]
    worker = (
        await db.execute(
            select(AnalysisWorker).where(
                AnalysisWorker.token_prefix == prefix,
                AnalysisWorker.status == AnalysisWorkerStatus.ACTIVE,
            )
        )
    ).scalar_one_or_none()
    if worker is None or not verify_password(secret, worker.token_hash):
        raise _worker_auth_error()
    worker.last_seen_at = _now()
    return AnalysisWorkerAuthContext(worker=worker)


async def get_analysis_queue_metrics(db, *, now: datetime | None = None) -> AnalysisQueueMetrics:
    now = now or _now()
    status_counts = {status.value: 0 for status in AnalysisJobStatus}
    status_rows = (
        await db.execute(
            select(ReportAnalysisJob.status, func.count(ReportAnalysisJob.id)).group_by(ReportAnalysisJob.status)
        )
    ).all()
    for status_value, count in status_rows:
        key = status_value.value if isinstance(status_value, AnalysisJobStatus) else str(status_value)
        status_counts[key] = int(count or 0)

    worker_status_counts = {status.value: 0 for status in AnalysisWorkerStatus}
    worker_rows = (
        await db.execute(select(AnalysisWorker.status, func.count(AnalysisWorker.id)).group_by(AnalysisWorker.status))
    ).all()
    for status_value, count in worker_rows:
        key = status_value.value if isinstance(status_value, AnalysisWorkerStatus) else str(status_value)
        worker_status_counts[key] = int(count or 0)

    stale_cutoff = now - timedelta(seconds=settings.ANALYSIS_JOB_LEASE_SECONDS * 2)
    online_worker_count = int(
        (
            await db.execute(
                select(func.count(AnalysisWorker.id)).where(
                    AnalysisWorker.status == AnalysisWorkerStatus.ACTIVE,
                    AnalysisWorker.last_seen_at.is_not(None),
                    AnalysisWorker.last_seen_at >= stale_cutoff,
                )
            )
        ).scalar_one()
        or 0
    )
    stale_active_worker_count = int(
        (
            await db.execute(
                select(func.count(AnalysisWorker.id)).where(
                    AnalysisWorker.status == AnalysisWorkerStatus.ACTIVE,
                    AnalysisWorker.last_seen_at.is_not(None),
                    AnalysisWorker.last_seen_at < stale_cutoff,
                )
            )
        ).scalar_one()
        or 0
    )

    expired_lease_count = int(
        (
            await db.execute(
                select(func.count(ReportAnalysisJob.id)).where(
                    ReportAnalysisJob.status.in_([AnalysisJobStatus.LEASED, AnalysisJobStatus.RUNNING]),
                    ReportAnalysisJob.lease_expires_at < now,
                )
            )
        ).scalar_one()
        or 0
    )
    retryable_expired_lease_count = int(
        (
            await db.execute(
                select(func.count(ReportAnalysisJob.id)).where(
                    ReportAnalysisJob.status.in_([AnalysisJobStatus.LEASED, AnalysisJobStatus.RUNNING]),
                    ReportAnalysisJob.lease_expires_at < now,
                    ReportAnalysisJob.attempts < ReportAnalysisJob.max_attempts,
                )
            )
        ).scalar_one()
        or 0
    )
    oldest_pending_at = (
        await db.execute(
            select(func.min(ReportAnalysisJob.created_at)).where(ReportAnalysisJob.status == AnalysisJobStatus.PENDING)
        )
    ).scalar_one_or_none()
    oldest_pending_seconds = None
    if isinstance(oldest_pending_at, datetime):
        oldest_pending_seconds = max(0, int((now - _coerce_aware_datetime(oldest_pending_at)).total_seconds()))

    pending_count = status_counts[AnalysisJobStatus.PENDING.value]
    active_job_count = status_counts[AnalysisJobStatus.LEASED.value] + status_counts[AnalysisJobStatus.RUNNING.value]
    backlog_count = pending_count + retryable_expired_lease_count
    terminal_job_count = (
        status_counts[AnalysisJobStatus.SUCCEEDED.value]
        + status_counts[AnalysisJobStatus.FAILED.value]
        + status_counts[AnalysisJobStatus.CANCELLED.value]
    )
    registered_worker_count = worker_status_counts[AnalysisWorkerStatus.ACTIVE.value]
    disabled_worker_count = worker_status_counts[AnalysisWorkerStatus.DISABLED.value]
    stale_worker_count = worker_status_counts[AnalysisWorkerStatus.STALE.value] + stale_active_worker_count
    queued_per_online_worker = round(backlog_count / max(online_worker_count, 1), 2)
    saturation_level, saturation_reason = _analysis_saturation(
        backlog_count=backlog_count,
        online_worker_count=online_worker_count,
        queued_per_online_worker=queued_per_online_worker,
        expired_lease_count=expired_lease_count,
        oldest_pending_seconds=oldest_pending_seconds,
    )
    return AnalysisQueueMetrics(
        status_counts=status_counts,
        worker_status_counts=worker_status_counts,
        pending_count=pending_count,
        active_job_count=active_job_count,
        backlog_count=backlog_count,
        terminal_job_count=terminal_job_count,
        expired_lease_count=expired_lease_count,
        retryable_expired_lease_count=retryable_expired_lease_count,
        registered_worker_count=registered_worker_count,
        online_worker_count=online_worker_count,
        stale_worker_count=stale_worker_count,
        disabled_worker_count=disabled_worker_count,
        oldest_pending_seconds=oldest_pending_seconds,
        queued_per_online_worker=queued_per_online_worker,
        saturation_level=saturation_level,
        saturation_reason=saturation_reason,
    )


async def create_report_analysis_job_for_session(db, session: ReportUploadSession) -> ReportAnalysisJob:
    existing = (
        await db.execute(
            select(ReportAnalysisJob).where(ReportAnalysisJob.report_upload_session_id == session.id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    job = ReportAnalysisJob(
        report_upload_session_id=session.id,
        runtime_id=session.runtime_id,
        status=AnalysisJobStatus.PENDING,
        input_bucket=session.bucket,
        input_object_key=session.object_key,
        input_sha256=session.actual_sha256 or session.expected_sha256,
        input_size_bytes=session.actual_size_bytes or session.expected_size_bytes,
        summary_json={
            "source": "report_upload_session",
            "report_id": session.report_id,
            "schema_version": session.schema_version,
            "report_type": session.report_type,
            "period_start": session.period_start.isoformat() if session.period_start else None,
            "period_end": session.period_end.isoformat() if session.period_end else None,
        },
    )
    db.add(job)
    await db.flush()
    return job


async def lease_next_analysis_job(
    db,
    *,
    worker: AnalysisWorker,
    lease_seconds: int | None = None,
    worker_name: str | None = None,
    capabilities_json: dict[str, Any] | None = None,
) -> AnalysisJobLease | None:
    now = _now()
    ttl = lease_seconds or settings.ANALYSIS_JOB_LEASE_SECONDS
    if worker_name:
        worker.name = worker_name
    if capabilities_json is not None:
        worker.capabilities_json = capabilities_json
    worker.last_seen_at = now

    # Reclaim PENDING jobs, and as a defense-in-depth liveness net also re-lease jobs
    # whose worker crashed (status LEASED/RUNNING with an expired lease). This must NOT
    # be removed: the periodic reaper (reap_expired_analysis_jobs) only runs under celery
    # beat, so without this inline pickup a crashed worker would deadlock a job whenever
    # beat is down. Re-leasing consumes one bounded attempt (attempts < max_attempts);
    # attempt-exhausted expired jobs are left for the reaper to terminally FAIL.
    stmt = (
        select(ReportAnalysisJob)
        .where(
            ReportAnalysisJob.attempts < ReportAnalysisJob.max_attempts,
            or_(
                ReportAnalysisJob.status == AnalysisJobStatus.PENDING,
                ReportAnalysisJob.status.in_([AnalysisJobStatus.LEASED, AnalysisJobStatus.RUNNING])
                & (ReportAnalysisJob.lease_expires_at < now),
            ),
        )
        .order_by(ReportAnalysisJob.priority.desc(), ReportAnalysisJob.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = (await db.execute(stmt)).scalar_one_or_none()
    if job is None:
        return None

    session = (
        await db.execute(select(ReportUploadSession).where(ReportUploadSession.id == job.report_upload_session_id))
    ).scalar_one_or_none()
    if session is None:
        job.status = AnalysisJobStatus.FAILED
        job.error_message = "Report upload session no longer exists"
        job.finished_at = now
        await db.flush()
        return None

    job.status = AnalysisJobStatus.LEASED
    job.worker_id = worker.id
    job.lease_owner = worker.worker_key
    job.lease_expires_at = now + timedelta(seconds=ttl)
    job.attempts += 1
    job.started_at = job.started_at or now
    job.error_message = None
    # PUSH-04: derive a fresh result_object_key per lease attempt. When the reaper or
    # lease_next recycles a crashed worker's job, a slow old worker may still be holding
    # a presigned PUT URL for the previous attempt's key. Encoding the attempt number in
    # the key guarantees the new worker writes a distinct object, so the stale write
    # cannot clobber (or be confused with) the new attempt's result.
    new_result_object_key = artifact_service.report_analysis_result_object_key(
        report_id=session.report_id,
        job_id=job.id,
        filename=_attempt_result_filename(job.attempts),
    )
    if not job.result_object_key or new_result_object_key != job.result_object_key:
        job.result_bucket = artifact_service.bucket
        job.result_object_key = new_result_object_key
        job.result_content_type = settings.ANALYSIS_RESULT_CONTENT_TYPE
    session.status = ReportUploadStatus.INGESTING
    collection_job = await _get_collection_job(db, session)
    if collection_job is not None:
        collection_job.status = JobStatus.RUNNING
        collection_job.started_at = collection_job.started_at or now

    download = artifact_service.generate_presigned_get_url(
        object_key=job.input_object_key,
        expires_in=settings.REPORT_UPLOAD_URL_EXPIRE_SECONDS,
    )
    uploads = _build_result_uploads(session=session, job=job)
    upload = next(item for item in uploads if item["kind"] == AnalysisResultArtifactKind.ANALYSIS_RESULT)
    await db.flush()
    return AnalysisJobLease(
        job=job,
        download=download,
        upload=upload,
        result_object_key=job.result_object_key,
        uploads=uploads,
    )


async def heartbeat_analysis_job(db, *, worker: AnalysisWorker, job_id: int) -> ReportAnalysisJob:
    job = await _get_worker_job(db, worker=worker, job_id=job_id)
    now = _now()
    worker.last_seen_at = now
    job.status = AnalysisJobStatus.RUNNING
    job.lease_expires_at = now + timedelta(seconds=settings.ANALYSIS_JOB_LEASE_SECONDS)
    await db.flush()
    return job


async def finalize_analysis_job(
    db,
    *,
    worker: AnalysisWorker,
    job_id: int,
    body: AnalysisJobFinalize,
) -> ReportAnalysisJob:
    job = await _get_worker_job(db, worker=worker, job_id=job_id)
    session = (
        await db.execute(select(ReportUploadSession).where(ReportUploadSession.id == job.report_upload_session_id))
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Report upload session not found")
    if not job.result_object_key:
        raise HTTPException(status_code=409, detail="Analysis job has no result object key")

    artifacts = await _verify_and_persist_result_artifacts(db, job=job, session=session, body=body)
    try:
        result_contract = _validate_analysis_result_artifacts(session=session, artifacts=artifacts)
    except AnalysisResultValidationError as exc:
        await _fail_analysis_finalize(
            db,
            worker=worker,
            job=job,
            session=session,
            reason=str(exc),
        )
        # get_db rolls back on raised HTTPException. Persist this terminal worker
        # contract failure before returning 422 so operators can see the failed job.
        await db.commit()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    primary_artifact = next(
        (item for item in artifacts if item.kind == AnalysisResultArtifactKind.ANALYSIS_RESULT),
        artifacts[0] if artifacts else None,
    )
    if primary_artifact is None:
        raise HTTPException(status_code=422, detail="Analysis result artifact is required")

    now = _now()
    worker.last_seen_at = now
    job.status = AnalysisJobStatus.SUCCEEDED
    job.result_bucket = artifact_service.bucket
    job.result_object_key = primary_artifact.object_key
    job.result_content_type = primary_artifact.content_type
    job.result_sha256 = primary_artifact.sha256
    job.result_size_bytes = primary_artifact.size_bytes
    worker_summary = body.summary_json or {}
    memory_candidate_count = worker_summary.get("memory_candidate_count")
    if not isinstance(memory_candidate_count, int):
        memory_candidate_count = len(body.memory_candidates)
    worker_metadata = result_contract.get("worker_metadata") or {}

    job.summary_json = {
        **(job.summary_json or {}),
        **worker_summary,
        "analysis_result_schema_version": result_contract["schema_version"],
        "worker_metadata": worker_metadata,
        "memory_candidate_count": memory_candidate_count,
        "inline_memory_candidate_count": len(body.memory_candidates),
        "result_artifact_count": len(artifacts),
        "result_artifacts": [
            {
                "kind": item.kind.value,
                "filename": item.filename,
                "object_key": item.object_key,
                "sha256": item.sha256,
                "size_bytes": item.size_bytes,
            }
            for item in artifacts
        ],
    }
    job.error_message = None
    job.finished_at = now
    job.lease_expires_at = None
    session.status = ReportUploadStatus.SUCCEEDED
    session.error_message = None
    collection_job = await _get_collection_job(db, session)
    if collection_job is not None:
        collection_job.status = JobStatus.SUCCEEDED
        collection_job.finished_at = now
        collection_job.summary_json = {
            **(collection_job.summary_json or {}),
            **(body.summary_json or {}),
            "analysis_job_id": job.id,
            "report_id": session.report_id,
            "result_object_key": job.result_object_key,
            "result_artifact_count": len(artifacts),
            "analysis_result_schema_version": result_contract["schema_version"],
            "worker_metadata": worker_metadata,
        }
    materialization_failures: list[dict[str, Any]] = []
    runtime = (
        await db.execute(select(RuntimeInstance).where(RuntimeInstance.id == job.runtime_id))
    ).scalar_one_or_none()
    if runtime is not None:
        runtime.last_sync_at = now
        materialized_counts = await materialize_analysis_result(
            db,
            job=job,
            session=session,
            runtime=runtime,
            artifacts=artifacts,
        )
        failures = materialized_counts.get("failures") if isinstance(materialized_counts, dict) else None
        if isinstance(failures, list):
            materialization_failures = failures
        job.summary_json = {
            **(job.summary_json or {}),
            "materialized_counts": materialized_counts,
        }
        if collection_job is not None:
            collection_job.summary_json = {
                **(collection_job.summary_json or {}),
                "materialized_counts": materialized_counts,
            }

    # Per-artifact failures determine the terminal collection-job status.
    # Some succeeded + some failed -> PARTIAL_FAILED; all artifacts failed -> FAILED;
    # otherwise SUCCEEDED. The analysis job itself stays SUCCEEDED (AnalysisJobStatus
    # has no partial state) but records the failure detail in summary_json.
    if materialization_failures:
        succeeded_artifacts = 0
        if isinstance(materialized_counts, dict):
            succeeded_artifacts = int(materialized_counts.get("succeeded_artifacts") or 0)
        derived_status = JobStatus.PARTIAL_FAILED if succeeded_artifacts > 0 else JobStatus.FAILED
        failure_summary = {
            "partial_failure_count": len(materialization_failures),
            "partial_failures": materialization_failures,
        }
        job.summary_json = {**(job.summary_json or {}), **failure_summary}
        if collection_job is not None:
            collection_job.status = derived_status
            collection_job.error_message = (
                f"{len(materialization_failures)} analysis artifact(s) could not be materialized"
            )
            collection_job.summary_json = {
                **(collection_job.summary_json or {}),
                **failure_summary,
            }

    source_uri = f"s3://{artifact_service.bucket}/{primary_artifact.object_key}"
    # PUSH-02: dedup inline candidates on (job, type, subject_key, title) — the same
    # natural key _ensure_memory_candidate uses in the materializer — so re-finalize or
    # duplicate payloads never create duplicate rows.
    seen_inline_candidates: set[tuple[Any, ...]] = set()
    for item in body.memory_candidates:
        dedup_key = (job.id, item.candidate_type, item.subject_key, item.title)
        if dedup_key in seen_inline_candidates:
            continue
        existing = (
            await db.execute(
                select(MemoryCandidate.id).where(
                    MemoryCandidate.analysis_job_id == job.id,
                    MemoryCandidate.candidate_type == item.candidate_type,
                    MemoryCandidate.subject_key == item.subject_key,
                    MemoryCandidate.title == item.title,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            seen_inline_candidates.add(dedup_key)
            continue
        seen_inline_candidates.add(dedup_key)
        db.add(
            MemoryCandidate(
                analysis_job_id=job.id,
                report_upload_session_id=session.id,
                runtime_id=job.runtime_id,
                candidate_type=item.candidate_type,
                status=MemoryCandidateStatus.CANDIDATE,
                subject_type=item.subject_type,
                subject_key=item.subject_key,
                title=item.title,
                summary=item.summary,
                confidence=item.confidence,
                sensitivity=item.sensitivity,
                source_object_uri=item.source_object_uri or source_uri,
                source_sha256=item.source_sha256 or primary_artifact.sha256,
                payload_json=item.payload_json,
            )
        )
        await db.flush()
    await db.flush()
    return job


async def fail_analysis_job(
    db,
    *,
    worker: AnalysisWorker,
    job_id: int,
    body: AnalysisJobFail,
) -> ReportAnalysisJob:
    job = await _get_worker_job(db, worker=worker, job_id=job_id)
    session = (
        await db.execute(select(ReportUploadSession).where(ReportUploadSession.id == job.report_upload_session_id))
    ).scalar_one_or_none()
    now = _now()
    worker.last_seen_at = now
    job.error_message = body.error_message
    job.summary_json = {**(job.summary_json or {}), **(body.summary_json or {})}
    job.worker_id = None
    job.lease_owner = None
    job.lease_expires_at = None
    if body.retryable and job.attempts < job.max_attempts:
        job.status = AnalysisJobStatus.PENDING
        if session is not None:
            session.status = ReportUploadStatus.UPLOADED
            session.error_message = body.error_message
            collection_job = await _get_collection_job(db, session)
            if collection_job is not None:
                collection_job.status = JobStatus.PENDING
                collection_job.error_message = body.error_message
    else:
        job.status = AnalysisJobStatus.FAILED
        job.finished_at = now
        if session is not None:
            session.status = ReportUploadStatus.FAILED
            session.error_message = body.error_message
            collection_job = await _get_collection_job(db, session)
            if collection_job is not None:
                collection_job.status = JobStatus.FAILED
                collection_job.error_message = body.error_message
                collection_job.finished_at = now
    await db.flush()
    return job


async def reap_expired_analysis_jobs(
    db,
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> AnalysisLeaseReaperResult:
    now = now or _now()
    rows = (
        await db.execute(
            select(ReportAnalysisJob)
            .where(
                ReportAnalysisJob.status.in_([AnalysisJobStatus.LEASED, AnalysisJobStatus.RUNNING]),
                ReportAnalysisJob.lease_expires_at < now,
            )
            .order_by(ReportAnalysisJob.lease_expires_at.asc(), ReportAnalysisJob.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    result = AnalysisLeaseReaperResult()
    for job in rows:
        session = (
            await db.execute(select(ReportUploadSession).where(ReportUploadSession.id == job.report_upload_session_id))
        ).scalar_one_or_none()
        if job.attempts >= job.max_attempts:
            _mark_analysis_job_failed(
                job,
                reason="Analysis job lease expired and attempts are exhausted",
                now=now,
                actor_user_id=None,
            )
            if session is not None:
                await _mark_session_failed(db, session, reason=job.error_message or "Analysis job lease expired")
            result.failed_job_count += 1
            continue

        _release_expired_analysis_job(job, now=now)
        if session is not None:
            await _mark_session_pending_retry(db, session, reason="Analysis job lease expired")
        result.requeued_job_count += 1
    await db.flush()
    return result


async def review_memory_candidate(
    db,
    *,
    candidate_id: int,
    status_value: MemoryCandidateStatus,
    reviewed_by: int,
) -> MemoryCandidate:
    candidate = (
        await db.execute(select(MemoryCandidate).where(MemoryCandidate.id == candidate_id))
    ).scalar_one_or_none()
    if candidate is None:
        raise HTTPException(status_code=404, detail="Memory candidate not found")
    candidate.status = status_value
    candidate.reviewed_by = reviewed_by
    candidate.reviewed_at = _now()
    await db.flush()
    return candidate


async def disable_analysis_worker(db, *, worker_id: int, disabled_by: int | None = None) -> AnalysisWorkerDisableResult:
    worker = (
        await db.execute(select(AnalysisWorker).where(AnalysisWorker.id == worker_id))
    ).scalar_one_or_none()
    if worker is None:
        raise HTTPException(status_code=404, detail="Analysis worker not found")
    if worker.status == AnalysisWorkerStatus.DISABLED:
        return AnalysisWorkerDisableResult(worker=worker)

    worker.status = AnalysisWorkerStatus.DISABLED
    now = _now()
    running_jobs = (
        await db.execute(
            select(ReportAnalysisJob).where(
                ReportAnalysisJob.worker_id == worker.id,
                ReportAnalysisJob.status.in_([AnalysisJobStatus.LEASED, AnalysisJobStatus.RUNNING]),
            )
        )
    ).scalars().all()

    requeued = 0
    failed = 0
    for job in running_jobs:
        session = (
            await db.execute(select(ReportUploadSession).where(ReportUploadSession.id == job.report_upload_session_id))
        ).scalar_one_or_none()
        if job.attempts < job.max_attempts:
            _prepare_analysis_job_for_retry(
                job,
                reason="Worker disabled before completing the analysis job",
                reset_attempts=False,
                now=now,
                actor_user_id=disabled_by,
            )
            if session is not None:
                await _mark_session_pending_retry(db, session, reason="Worker disabled before completion")
            requeued += 1
        else:
            _mark_analysis_job_failed(
                job,
                reason="Worker disabled before completion and job attempts are exhausted",
                now=now,
                actor_user_id=disabled_by,
            )
            if session is not None:
                await _mark_session_failed(db, session, reason=job.error_message or "Analysis job failed")
            failed += 1
    await db.flush()
    return AnalysisWorkerDisableResult(worker=worker, requeued_job_count=requeued, failed_job_count=failed)


async def cancel_analysis_job(
    db,
    *,
    job_id: int,
    reason: str,
    cancelled_by: int | None = None,
) -> ReportAnalysisJob:
    job = await _get_analysis_job(db, job_id)
    if job.status in {AnalysisJobStatus.SUCCEEDED, AnalysisJobStatus.FAILED, AnalysisJobStatus.CANCELLED}:
        raise HTTPException(status_code=409, detail=f"Analysis job is already {job.status.value}")

    now = _now()
    job.status = AnalysisJobStatus.CANCELLED
    job.worker_id = None
    job.lease_owner = None
    job.lease_expires_at = None
    job.error_message = reason
    job.finished_at = now
    job.summary_json = {
        **(job.summary_json or {}),
        "admin_cancelled_at": now.isoformat(),
        "admin_cancelled_by": cancelled_by,
        "admin_cancel_reason": reason,
    }
    session = (
        await db.execute(select(ReportUploadSession).where(ReportUploadSession.id == job.report_upload_session_id))
    ).scalar_one_or_none()
    if session is not None:
        await _mark_session_failed(db, session, reason=reason, collection_status=JobStatus.CANCELLED)
    await db.flush()
    return job


async def retry_analysis_job(
    db,
    *,
    job_id: int,
    reason: str,
    reset_attempts: bool = True,
    retried_by: int | None = None,
) -> ReportAnalysisJob:
    job = await _get_analysis_job(db, job_id)
    if job.status not in {AnalysisJobStatus.FAILED, AnalysisJobStatus.CANCELLED}:
        raise HTTPException(status_code=409, detail=f"Only failed or cancelled jobs can be retried; current status is {job.status.value}")

    now = _now()
    _prepare_analysis_job_for_retry(
        job,
        reason=reason,
        reset_attempts=reset_attempts,
        now=now,
        actor_user_id=retried_by,
    )
    session = (
        await db.execute(select(ReportUploadSession).where(ReportUploadSession.id == job.report_upload_session_id))
    ).scalar_one_or_none()
    if session is not None:
        await _mark_session_pending_retry(db, session, reason=reason)
    await db.flush()
    return job


async def _get_collection_job(db, session: ReportUploadSession) -> CollectionJob | None:
    if session.collection_job_id is None:
        return None
    return (
        await db.execute(select(CollectionJob).where(CollectionJob.id == session.collection_job_id))
    ).scalar_one_or_none()


async def _get_analysis_job(db, job_id: int) -> ReportAnalysisJob:
    job = (
        await db.execute(select(ReportAnalysisJob).where(ReportAnalysisJob.id == job_id))
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found")
    return job


def _prepare_analysis_job_for_retry(
    job: ReportAnalysisJob,
    *,
    reason: str,
    reset_attempts: bool,
    now: datetime,
    actor_user_id: int | None,
) -> None:
    retry_count = 0
    if isinstance(job.summary_json, dict):
        retry_count = int(job.summary_json.get("admin_retry_count") or 0)
    job.status = AnalysisJobStatus.PENDING
    job.worker_id = None
    job.lease_owner = None
    job.lease_expires_at = None
    job.error_message = None
    job.finished_at = None
    job.started_at = None
    job.result_sha256 = None
    job.result_size_bytes = None
    if reset_attempts:
        job.attempts = 0
    job.summary_json = {
        **(job.summary_json or {}),
        "admin_retry_count": retry_count + 1,
        "admin_retry_at": now.isoformat(),
        "admin_retry_by": actor_user_id,
        "admin_retry_reason": reason,
    }


def _release_expired_analysis_job(job: ReportAnalysisJob, *, now: datetime) -> None:
    reaper_count = 0
    if isinstance(job.summary_json, dict):
        reaper_count = int(job.summary_json.get("lease_reaper_count") or 0)
    job.status = AnalysisJobStatus.PENDING
    job.worker_id = None
    job.lease_owner = None
    job.lease_expires_at = None
    job.error_message = None
    job.started_at = None
    job.summary_json = {
        **(job.summary_json or {}),
        "lease_reaper_count": reaper_count + 1,
        "lease_reaper_at": now.isoformat(),
        "lease_reaper_reason": "Analysis job lease expired",
    }


def _mark_analysis_job_failed(
    job: ReportAnalysisJob,
    *,
    reason: str,
    now: datetime,
    actor_user_id: int | None,
) -> None:
    job.status = AnalysisJobStatus.FAILED
    job.worker_id = None
    job.lease_owner = None
    job.lease_expires_at = None
    job.error_message = reason
    job.finished_at = now
    job.summary_json = {
        **(job.summary_json or {}),
        "admin_failure_at": now.isoformat(),
        "admin_failure_by": actor_user_id,
        "admin_failure_reason": reason,
    }


async def _mark_session_pending_retry(db, session: ReportUploadSession, *, reason: str) -> None:
    session.status = ReportUploadStatus.UPLOADED
    session.error_message = None
    collection_job = await _get_collection_job(db, session)
    if collection_job is not None:
        collection_job.status = JobStatus.PENDING
        collection_job.error_message = None
        collection_job.finished_at = None
        collection_job.summary_json = {
            **(collection_job.summary_json or {}),
            "analysis_retry_reason": reason,
        }


async def _mark_session_failed(
    db,
    session: ReportUploadSession,
    *,
    reason: str,
    collection_status: JobStatus = JobStatus.FAILED,
) -> None:
    now = _now()
    session.status = ReportUploadStatus.FAILED
    session.error_message = reason
    collection_job = await _get_collection_job(db, session)
    if collection_job is not None:
        collection_job.status = collection_status
        collection_job.error_message = reason
        collection_job.finished_at = now


async def _fail_analysis_finalize(
    db,
    *,
    worker: AnalysisWorker,
    job: ReportAnalysisJob,
    session: ReportUploadSession,
    reason: str,
) -> None:
    now = _now()
    worker.last_seen_at = now
    job.status = AnalysisJobStatus.FAILED
    job.worker_id = None
    job.lease_owner = None
    job.lease_expires_at = None
    job.error_message = reason
    job.finished_at = now
    job.summary_json = {
        **(job.summary_json or {}),
        "result_validation_error": reason,
    }
    await _mark_session_failed(db, session, reason=reason)
    collection_job = await _get_collection_job(db, session)
    if collection_job is not None:
        collection_job.summary_json = {
            **(collection_job.summary_json or {}),
            "analysis_job_id": job.id,
            "report_id": session.report_id,
            "result_validation_error": reason,
        }
    await db.flush()


def _validate_analysis_result_artifacts(
    *,
    session: ReportUploadSession,
    artifacts: list[AnalysisResultArtifact],
) -> dict[str, Any]:
    contract: dict[str, Any] = {"schema_version": ANALYSIS_RESULT_SCHEMA_VERSION, "worker_metadata": {}}
    saw_analysis_result = False
    for artifact in artifacts:
        if artifact.kind == AnalysisResultArtifactKind.ANALYSIS_RESULT:
            saw_analysis_result = True
            payload = _load_result_json_object(artifact)
            _validate_analysis_result_payload(payload, session=session, artifact=artifact)
            contract = {
                "schema_version": payload["schema_version"],
                "worker_metadata": _extract_worker_metadata(payload),
            }
        elif artifact.kind == AnalysisResultArtifactKind.ASSET_CARDS:
            for index, item in enumerate(_load_result_json_items(artifact)):
                _validate_asset_card(item, artifact=artifact, index=index)
        elif artifact.kind == AnalysisResultArtifactKind.MEMORY_CANDIDATES:
            for index, item in enumerate(_load_result_json_items(artifact)):
                _validate_memory_candidate(item, artifact=artifact, index=index)
        elif artifact.kind == AnalysisResultArtifactKind.HANDOVER_SIGNALS:
            for index, item in enumerate(_load_result_json_items(artifact)):
                _validate_handover_signal(item, artifact=artifact, index=index)
        elif artifact.kind == AnalysisResultArtifactKind.WORKTRACE_SUMMARY:
            text = _load_result_text(artifact)
            if not text.strip():
                raise AnalysisResultValidationError(f"{artifact.filename} must not be empty")
    if not saw_analysis_result:
        raise AnalysisResultValidationError("analysis-result.json is required")
    return contract


def _load_result_json_object(artifact: AnalysisResultArtifact) -> dict[str, Any]:
    value = _load_result_json(artifact)
    if not isinstance(value, dict):
        raise AnalysisResultValidationError(f"{artifact.filename} must be a JSON object")
    return value


def _load_result_json_items(artifact: AnalysisResultArtifact) -> list[dict[str, Any]]:
    value = _load_result_json(artifact)
    if isinstance(value, list):
        items = value
    elif isinstance(value, dict):
        items = None
        for key in ("items", "assets", "asset_cards", "memory_candidates", "signals", "handover_signals"):
            nested = value.get(key)
            if isinstance(nested, list):
                items = nested
                break
        if items is None:
            raise AnalysisResultValidationError(f"{artifact.filename} must contain a JSON array")
    else:
        raise AnalysisResultValidationError(f"{artifact.filename} must be a JSON array")
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise AnalysisResultValidationError(f"{artifact.filename}[{index}] must be a JSON object")
    return items


def _load_result_json(artifact: AnalysisResultArtifact) -> Any:
    try:
        payload = artifact_service.read_object_bytes(artifact.object_key, max_bytes=16 * 1024 * 1024)
    except ArtifactStorageError as exc:
        raise AnalysisResultValidationError(f"Could not read {artifact.filename}: {exc}") from exc
    try:
        return json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnalysisResultValidationError(f"Could not parse {artifact.filename}: {exc}") from exc


def _load_result_text(artifact: AnalysisResultArtifact) -> str:
    try:
        payload = artifact_service.read_object_bytes(artifact.object_key, max_bytes=16 * 1024 * 1024)
    except ArtifactStorageError as exc:
        raise AnalysisResultValidationError(f"Could not read {artifact.filename}: {exc}") from exc
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AnalysisResultValidationError(f"Could not decode {artifact.filename}: {exc}") from exc


def _validate_analysis_result_payload(
    payload: dict[str, Any],
    *,
    session: ReportUploadSession,
    artifact: AnalysisResultArtifact,
) -> None:
    if payload.get("schema_version") != ANALYSIS_RESULT_SCHEMA_VERSION:
        raise AnalysisResultValidationError(
            f"{artifact.filename} schema_version must be {ANALYSIS_RESULT_SCHEMA_VERSION}"
        )
    report_id = payload.get("report_id")
    if not isinstance(report_id, str) or not report_id.strip():
        raise AnalysisResultValidationError(f"{artifact.filename} report_id is required")
    if report_id != session.report_id:
        raise AnalysisResultValidationError(f"{artifact.filename} report_id does not match upload session")
    if not isinstance(payload.get("summary"), dict):
        raise AnalysisResultValidationError(f"{artifact.filename} summary must be a JSON object")
    limitations = payload.get("limitations", [])
    if not isinstance(limitations, list):
        raise AnalysisResultValidationError(f"{artifact.filename} limitations must be a JSON array")
    worker_metadata = payload.get("worker_metadata")
    if worker_metadata is not None and not isinstance(worker_metadata, dict):
        raise AnalysisResultValidationError(f"{artifact.filename} worker_metadata must be a JSON object")
    token_usage = None
    if isinstance(worker_metadata, dict):
        token_usage = worker_metadata.get("token_usage")
    if token_usage is None and isinstance(payload.get("processing"), dict):
        token_usage = payload["processing"].get("token_usage")
    if token_usage is not None and not isinstance(token_usage, dict):
        raise AnalysisResultValidationError(f"{artifact.filename} token_usage must be a JSON object")


def _extract_worker_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    declared = payload.get("worker_metadata") if isinstance(payload.get("worker_metadata"), dict) else {}
    processing = payload.get("processing") if isinstance(payload.get("processing"), dict) else {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}

    candidates = {
        "recipe_version": (declared.get("recipe_version"), processing.get("recipe_version")),
        "prompt_version": (declared.get("prompt_version"), processing.get("prompt_version")),
        "model": (declared.get("model"), processing.get("model"), summary.get("model")),
        "trace_id": (declared.get("trace_id"), processing.get("trace_id")),
        "token_usage": (declared.get("token_usage"), processing.get("token_usage")),
        "analysis_mode": (declared.get("analysis_mode"), summary.get("analysis_mode"), processing.get("mode")),
        "ai_assist": (declared.get("ai_assist"), summary.get("ai_assist")),
    }
    for key in ANALYSIS_WORKER_METADATA_FIELDS:
        for value in candidates[key]:
            normalized = _normalize_worker_metadata_value(value)
            if normalized is not None:
                metadata[key] = normalized
                break
    return metadata


def _normalize_worker_metadata_value(value: Any) -> Any | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped[:512] if stripped else None
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value
    if isinstance(value, dict):
        return value
    return None


def _validate_asset_card(item: dict[str, Any], *, artifact: AnalysisResultArtifact, index: int) -> None:
    if not _first_result_text(item, "name", "title", "asset_name"):
        raise AnalysisResultValidationError(f"{artifact.filename}[{index}].name is required")
    _validate_optional_enum(
        item,
        "asset_type",
        VALID_RESULT_ASSET_TYPES,
        artifact=artifact,
        index=index,
    )
    _validate_optional_enum(item, "status", VALID_RESULT_ASSET_STATUSES, artifact=artifact, index=index)
    _validate_optional_enum(item, "criticality", VALID_RESULT_CRITICALITIES, artifact=artifact, index=index)
    _validate_optional_confidence(item, artifact=artifact, index=index)


def _validate_memory_candidate(item: dict[str, Any], *, artifact: AnalysisResultArtifact, index: int) -> None:
    if not _first_result_text(item, "title", "name"):
        raise AnalysisResultValidationError(f"{artifact.filename}[{index}].title is required")
    if not _first_result_text(item, "subject_type"):
        raise AnalysisResultValidationError(f"{artifact.filename}[{index}].subject_type is required")
    _validate_optional_enum(item, "candidate_type", VALID_RESULT_CANDIDATE_TYPES, artifact=artifact, index=index)
    _validate_optional_enum(item, "sensitivity", VALID_RESULT_SENSITIVITIES, artifact=artifact, index=index)
    _validate_optional_confidence(item, artifact=artifact, index=index)


def _validate_handover_signal(item: dict[str, Any], *, artifact: AnalysisResultArtifact, index: int) -> None:
    if not _first_result_text(item, "title", "name"):
        raise AnalysisResultValidationError(f"{artifact.filename}[{index}].title is required")
    if not _first_result_text(item, "subject_type"):
        raise AnalysisResultValidationError(f"{artifact.filename}[{index}].subject_type is required")
    _validate_optional_enum(item, "signal_type", VALID_RESULT_SIGNAL_TYPES, artifact=artifact, index=index)
    _validate_optional_enum(item, "sensitivity", VALID_RESULT_SENSITIVITIES, artifact=artifact, index=index)
    _validate_optional_confidence(item, artifact=artifact, index=index)


def _validate_optional_enum(
    item: dict[str, Any],
    key: str,
    valid_values: set[str],
    *,
    artifact: AnalysisResultArtifact,
    index: int,
) -> None:
    value = item.get(key)
    if value is None:
        return
    if not isinstance(value, str) or value.strip().lower().replace("-", "_") not in valid_values:
        raise AnalysisResultValidationError(f"{artifact.filename}[{index}].{key} is invalid")


def _validate_optional_confidence(item: dict[str, Any], *, artifact: AnalysisResultArtifact, index: int) -> None:
    value = item.get("confidence")
    if value is None:
        return
    if not isinstance(value, int | float) or value < 0 or value > 1:
        raise AnalysisResultValidationError(f"{artifact.filename}[{index}].confidence must be between 0 and 1")


def _first_result_text(item: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _build_result_uploads(*, session: ReportUploadSession, job: ReportAnalysisJob) -> list[dict[str, Any]]:
    uploads: list[dict[str, Any]] = []
    for kind, filename, content_type in DEFAULT_ANALYSIS_RESULT_FILES:
        object_key = (
            job.result_object_key
            if kind == AnalysisResultArtifactKind.ANALYSIS_RESULT and job.result_object_key
            else artifact_service.report_analysis_result_object_key(
                report_id=session.report_id,
                job_id=job.id,
                filename=filename,
            )
        )
        signed = artifact_service.generate_presigned_put_url(
            object_key=object_key,
            content_type=content_type,
            expires_in=settings.REPORT_UPLOAD_URL_EXPIRE_SECONDS,
        )
        uploads.append(
            {
                "kind": kind,
                "filename": filename,
                "content_type": content_type,
                "bucket": artifact_service.bucket,
                "object_key": object_key,
                "upload_url": signed["upload_url"],
                "expires_in": signed["expires_in"],
                "expires_at": signed["expires_at"],
            }
        )
    return uploads


async def _verify_and_persist_result_artifacts(
    db,
    *,
    job: ReportAnalysisJob,
    session: ReportUploadSession,
    body: AnalysisJobFinalize,
) -> list[AnalysisResultArtifact]:
    if body.result_artifacts:
        requested = body.result_artifacts
    else:
        requested = [
            _legacy_result_artifact_payload(
                object_key=job.result_object_key,
                sha256=body.result_sha256,
                size_bytes=body.result_size_bytes,
            )
        ]

    persisted: list[AnalysisResultArtifact] = []
    for item in requested:
        _validate_result_object_key(object_key=item.object_key, session=session, job=job)
        try:
            head = await artifact_service.head_object_async(item.object_key)
            actual_size = int(head["size_bytes"])
            if item.size_bytes is not None and actual_size != item.size_bytes:
                raise HTTPException(status_code=422, detail=f"{item.filename} size does not match finalize payload")
            digest = await artifact_service.hash_object_async(item.object_key)
            actual_sha256 = str(digest["sha256"])
            if item.sha256 is not None and actual_sha256 != item.sha256:
                raise HTTPException(status_code=422, detail=f"{item.filename} sha256 does not match finalize payload")
        except ArtifactStorageError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        row = (
            await db.execute(
                select(AnalysisResultArtifact).where(
                    AnalysisResultArtifact.analysis_job_id == job.id,
                    AnalysisResultArtifact.object_key == item.object_key,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = AnalysisResultArtifact(
                analysis_job_id=job.id,
                report_upload_session_id=session.id,
                runtime_id=job.runtime_id,
                kind=item.kind,
                bucket=artifact_service.bucket,
                object_key=item.object_key,
                filename=item.filename,
                content_type=item.content_type,
            )
            db.add(row)
        row.kind = item.kind
        row.bucket = artifact_service.bucket
        row.filename = item.filename
        row.content_type = item.content_type
        row.sha256 = actual_sha256
        row.size_bytes = actual_size
        row.summary_json = item.summary_json
        persisted.append(row)
    await db.flush()
    return persisted


def _legacy_result_artifact_payload(*, object_key: str, sha256: str | None, size_bytes: int | None):
    from app.schemas.control_plane import AnalysisResultArtifactFinalize

    return AnalysisResultArtifactFinalize(
        kind=AnalysisResultArtifactKind.ANALYSIS_RESULT,
        filename="analysis-result.json",
        object_key=object_key,
        content_type=settings.ANALYSIS_RESULT_CONTENT_TYPE,
        sha256=sha256,
        size_bytes=size_bytes,
    )


def _validate_result_object_key(*, object_key: str, session: ReportUploadSession, job: ReportAnalysisJob) -> None:
    expected_marker = f"/{session.report_id}/job-{job.id}/"
    if not object_key.startswith("analysis/") or expected_marker not in object_key:
        raise HTTPException(status_code=422, detail="Analysis result object key is outside this job result prefix")


async def _get_worker_job(db, *, worker: AnalysisWorker, job_id: int) -> ReportAnalysisJob:
    job = (
        await db.execute(select(ReportAnalysisJob).where(ReportAnalysisJob.id == job_id))
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found")
    if job.worker_id != worker.id or job.lease_owner != worker.worker_key:
        raise HTTPException(status_code=403, detail="Analysis job is not leased by this worker")
    if job.status not in {AnalysisJobStatus.LEASED, AnalysisJobStatus.RUNNING}:
        raise HTTPException(status_code=409, detail=f"Analysis job is {job.status.value}")
    return job


def _attempt_result_filename(attempt: int) -> str:
    # PUSH-04: the first attempt keeps the canonical filename for backward
    # compatibility; recycled attempts get an attempt-scoped filename so each
    # lease writes a distinct result object and a stale worker cannot collide.
    if attempt <= 1:
        return "analysis-result.json"
    return f"analysis-result.attempt-{attempt}.json"


def _analysis_saturation(
    *,
    backlog_count: int,
    online_worker_count: int,
    queued_per_online_worker: float,
    expired_lease_count: int,
    oldest_pending_seconds: int | None,
) -> tuple[str, str | None]:
    if backlog_count > 0 and online_worker_count == 0:
        return "saturated", "no_online_workers"
    if expired_lease_count > 0:
        return "watch", "expired_leases"
    if queued_per_online_worker >= 5:
        return "watch", "queue_per_worker_high"
    if oldest_pending_seconds is not None and oldest_pending_seconds >= 600:
        return "watch", "oldest_pending_over_10m"
    return "healthy", None


def _coerce_aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _worker_auth_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid analysis worker token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)
