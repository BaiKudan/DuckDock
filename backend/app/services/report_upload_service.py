from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import select

from app.core.config import settings
from app.core.api_token_security import verify_reporter_token_secret
from app.models.control_plane import (
    AdapterError,
    AdapterRunStep,
    AdapterStepStatus,
    CollectionJob,
    CollectionTriggerType,
    JobStatus,
    ReporterCredential,
    ReportUploadSession,
    ReportUploadStatus,
    RuntimeInstance,
    RuntimeReportToken,
)
from app.services.adapter_collection_service import persist_collection_result
from app.services.analysis_service import create_report_analysis_job_for_session
from app.services.artifact_service import ArtifactStorageError, artifact_service
from app.services.report_pack_service import normalize_duckdock_report_pack


@dataclass(slots=True)
class ReporterAuthContext:
    runtime: RuntimeInstance
    token: RuntimeReportToken | ReporterCredential
    source: Literal["reporter_credential", "runtime_report_token"]


def generate_runtime_report_token() -> tuple[str, str, str]:
    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    return prefix, secret, f"dkr_report_{prefix}_{secret}"


def _reporter_auth_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid reporter credential",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def authenticate_runtime_report_token(
    db,
    token: str,
    *,
    touch_usage: bool = True,
) -> ReporterAuthContext:
    parts = token.split("_", 3)
    if len(parts) != 4 or parts[0] != "dkr" or parts[1] != "report":
        raise _reporter_auth_error()
    prefix, secret = parts[2], parts[3]
    credential = (
        await db.execute(
            select(ReporterCredential).where(
                ReporterCredential.token_prefix == prefix,
                ReporterCredential.is_active == True,
                ReporterCredential.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if credential is not None:
        if not verify_reporter_token_secret(secret, credential.token_hash):
            raise _reporter_auth_error()
        now = _now()
        if credential.expires_at is not None and _as_aware(credential.expires_at) < now:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Reporter credential expired",
                headers={"WWW-Authenticate": "Bearer"},
            )
        runtime = (
            await db.execute(select(RuntimeInstance).where(RuntimeInstance.id == credential.runtime_id))
        ).scalar_one_or_none()
        if runtime is None:
            raise HTTPException(status_code=404, detail="Runtime not found")
        if touch_usage:
            credential.last_used_at = now
        return ReporterAuthContext(runtime=runtime, token=credential, source="reporter_credential")

    legacy_token = (
        await db.execute(
            select(RuntimeReportToken).where(
                RuntimeReportToken.token_prefix == prefix,
                RuntimeReportToken.is_active == True,
            )
        )
    ).scalar_one_or_none()
    if legacy_token is None or not verify_reporter_token_secret(
        secret,
        legacy_token.token_hash,
    ):
        raise _reporter_auth_error()
    now = _now()
    if legacy_token.expires_at is not None and _as_aware(legacy_token.expires_at) < now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Runtime report token expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    runtime = (
        await db.execute(select(RuntimeInstance).where(RuntimeInstance.id == legacy_token.runtime_id))
    ).scalar_one_or_none()
    if runtime is None:
        raise HTTPException(status_code=404, detail="Runtime not found")
    if touch_usage:
        legacy_token.last_used_at = now
    return ReporterAuthContext(runtime=runtime, token=legacy_token, source="runtime_report_token")


async def create_report_upload_session(
    db,
    *,
    runtime: RuntimeInstance,
    schema_version: str,
    report_type: str,
    period_start: datetime | None,
    period_end: datetime | None,
    filename: str,
    content_type: str,
    expected_size_bytes: int | None,
    expected_sha256: str | None,
    idempotency_key: str | None,
    metadata_json: dict[str, Any] | None,
    created_by: int | None,
    created_via: str,
) -> tuple[ReportUploadSession, dict[str, Any] | None]:
    if expected_size_bytes is not None and expected_size_bytes > _max_upload_bytes():
        raise HTTPException(
            status_code=413,
            detail=f"Report pack exceeds max size {settings.REPORT_UPLOAD_MAX_SIZE_MB} MB",
        )

    if idempotency_key:
        existing = (
            await db.execute(
                select(ReportUploadSession)
                .where(
                    ReportUploadSession.runtime_id == runtime.id,
                    ReportUploadSession.idempotency_key == idempotency_key,
                )
                .order_by(ReportUploadSession.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.status == ReportUploadStatus.PENDING and _as_aware(existing.upload_expires_at) > _now():
                signed = artifact_service.generate_presigned_put_url(
                    object_key=existing.object_key,
                    content_type=existing.content_type,
                    expires_in=settings.REPORT_UPLOAD_URL_EXPIRE_SECONDS,
                )
                existing.upload_expires_at = signed["expires_at"]
                return existing, signed
            if existing.status in {ReportUploadStatus.UPLOADED, ReportUploadStatus.INGESTING, ReportUploadStatus.SUCCEEDED}:
                return existing, None

    report_id = _new_report_id()
    object_key = artifact_service.report_pack_object_key(
        runtime_id=runtime.id,
        report_id=report_id,
        filename=filename,
    )
    signed = artifact_service.generate_presigned_put_url(
        object_key=object_key,
        content_type=content_type,
        expires_in=settings.REPORT_UPLOAD_URL_EXPIRE_SECONDS,
    )
    job = CollectionJob(
        runtime_id=runtime.id,
        trigger_type=_trigger_type(report_type),
        status=JobStatus.PENDING,
        scope_json={
            "source": "duckdock_report_upload",
            "report_id": report_id,
            "report_type": report_type,
            "schema_version": schema_version,
            "period_start": period_start.isoformat() if period_start else None,
            "period_end": period_end.isoformat() if period_end else None,
        },
    )
    db.add(job)
    await db.flush()
    session = ReportUploadSession(
        report_id=report_id,
        runtime_id=runtime.id,
        collection_job_id=job.id,
        status=ReportUploadStatus.PENDING,
        schema_version=schema_version,
        report_type=report_type,
        period_start=period_start,
        period_end=period_end,
        bucket=artifact_service.bucket,
        object_key=object_key,
        filename=filename,
        content_type=content_type,
        expected_size_bytes=expected_size_bytes,
        expected_sha256=expected_sha256,
        metadata_json=metadata_json,
        idempotency_key=idempotency_key,
        upload_expires_at=signed["expires_at"],
        created_by=created_by,
        created_via=created_via,
    )
    db.add(session)
    await db.flush()
    return session, signed


async def finalize_report_upload_session(
    db,
    *,
    report_id: str,
    runtime: RuntimeInstance,
    sha256: str | None,
    size_bytes: int | None,
    manifest: dict[str, Any] | None,
) -> ReportUploadSession:
    session = await get_report_upload_session(db, report_id=report_id)
    if session.runtime_id != runtime.id:
        raise HTTPException(status_code=403, detail="Report session does not belong to this runtime")
    if session.status in {ReportUploadStatus.INGESTING, ReportUploadStatus.SUCCEEDED}:
        return session

    try:
        head = await artifact_service.head_object_async(session.object_key)
        actual_size = int(head["size_bytes"])
        if actual_size > _max_upload_bytes():
            raise HTTPException(
                status_code=413,
                detail=f"Report pack exceeds max size {settings.REPORT_UPLOAD_MAX_SIZE_MB} MB",
            )
        if size_bytes is not None and actual_size != size_bytes:
            raise HTTPException(status_code=422, detail="Uploaded object size does not match finalize payload")
        if session.expected_size_bytes is not None and actual_size != session.expected_size_bytes:
            raise HTTPException(status_code=422, detail="Uploaded object size does not match upload session")
        digest = await artifact_service.hash_object_async(session.object_key)
        actual_sha256 = str(digest["sha256"])
        if sha256 is not None and actual_sha256 != sha256:
            raise HTTPException(status_code=422, detail="Uploaded object sha256 does not match finalize payload")
        if session.expected_sha256 is not None and actual_sha256 != session.expected_sha256:
            raise HTTPException(status_code=422, detail="Uploaded object sha256 does not match upload session")
    except ArtifactStorageError as exc:
        session.status = ReportUploadStatus.FAILED
        session.error_message = str(exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    session.actual_size_bytes = actual_size
    session.actual_sha256 = actual_sha256
    session.manifest_json = manifest
    session.finalized_at = _now()
    session.status = ReportUploadStatus.UPLOADED
    await db.flush()
    # Production path: every finalized report becomes an analysis-worker job.
    # Direct report-pack ingestion remains available only through the admin-only
    # /reports/{report_id}/ingest compatibility endpoint; finalize must not
    # bypass the worker pipeline or LLM/baseline result contract.
    await create_report_analysis_job_for_session(db, session)
    return session


async def ingest_report_upload_session(db, *, report_id: str) -> ReportUploadSession:
    session = await get_report_upload_session(db, report_id=report_id)
    runtime = (
        await db.execute(select(RuntimeInstance).where(RuntimeInstance.id == session.runtime_id))
    ).scalar_one_or_none()
    if runtime is None:
        raise HTTPException(status_code=404, detail="Runtime not found")
    job = None
    if session.collection_job_id is not None:
        job = (
            await db.execute(select(CollectionJob).where(CollectionJob.id == session.collection_job_id))
        ).scalar_one_or_none()
    if job is None:
        job = CollectionJob(
            runtime_id=runtime.id,
            trigger_type=_trigger_type(session.report_type),
            status=JobStatus.PENDING,
            scope_json={"source": "duckdock_report_upload", "report_id": session.report_id},
        )
        db.add(job)
        await db.flush()
        session.collection_job_id = job.id

    now = _now()
    session.status = ReportUploadStatus.INGESTING
    session.error_message = None
    job.status = JobStatus.RUNNING
    job.started_at = job.started_at or now
    await _run_step(db, job, "report.object_verified", {
        "report_id": session.report_id,
        "object_key": session.object_key,
        "size_bytes": session.actual_size_bytes,
        "sha256": session.actual_sha256,
    })
    await db.flush()

    try:
        content = await artifact_service.read_object_bytes_async(session.object_key, max_bytes=_max_upload_bytes())
        # TOCTOU guard (PUSH-06): the stored object may have been swapped after finalize
        # recorded its hash. Recompute sha256 from the bytes we actually read and refuse to
        # parse if it diverges from session.actual_sha256.
        if session.actual_sha256 is not None:
            read_sha256 = hashlib.sha256(content).hexdigest()
            if read_sha256 != session.actual_sha256:
                raise ValueError(
                    "Report object sha256 mismatch on ingest: read "
                    f"{read_sha256} but session recorded {session.actual_sha256}"
                )
        manifest, result = normalize_duckdock_report_pack(
            content=content,
            runtime=runtime,
            object_uri=f"s3://{session.bucket}/{session.object_key}",
        )
        session.manifest_json = manifest
        await _run_step(db, job, "report.pack_parsed", {
            "schema_version": manifest.get("schema_version"),
            "manifest_keys": sorted(manifest.keys()),
            "raw_records": len(result.raw_records),
        })
        counts = await persist_collection_result(db, job=job, runtime=runtime, result=result)
        runtime.last_sync_at = now
        session.status = ReportUploadStatus.SUCCEEDED
        job.status = JobStatus.SUCCEEDED
        job.finished_at = _now()
        job.summary_json = {
            **(job.summary_json or {}),
            "report_id": session.report_id,
            "object_key": session.object_key,
            "counts": counts,
        }
        return session
    except Exception as exc:
        session.status = ReportUploadStatus.FAILED
        session.error_message = str(exc)
        job.status = JobStatus.FAILED
        job.error_message = str(exc)
        job.finished_at = _now()
        db.add(
            AdapterError(
                collection_job_id=job.id,
                runtime_id=runtime.id,
                provider=runtime.provider,
                adapter_name="duckdock_reporter",
                step_name="report_pack_ingest",
                error_code=exc.__class__.__name__,
                message=str(exc),
                retryable=True,
            )
        )
        await db.flush()
        await db.commit()
        raise


async def get_report_upload_session(db, *, report_id: str) -> ReportUploadSession:
    session = (
        await db.execute(select(ReportUploadSession).where(ReportUploadSession.report_id == report_id))
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Report upload session not found")
    return session


async def _run_step(db, job: CollectionJob, step_name: str, summary: dict[str, Any] | None = None) -> AdapterRunStep:
    row = AdapterRunStep(
        collection_job_id=job.id,
        step_name=step_name,
        status=AdapterStepStatus.SUCCEEDED,
        summary_json=summary,
        started_at=_now(),
        finished_at=_now(),
    )
    db.add(row)
    await db.flush()
    return row


def _enqueue_report_ingestion(report_id: str, session: ReportUploadSession) -> None:
    try:
        from app.workers.report_upload_tasks import ingest_report_upload_session_task

        ingest_report_upload_session_task.delay(report_id)
    except Exception as exc:
        session.error_message = f"Failed to enqueue report ingestion: {exc}"


def _trigger_type(report_type: str) -> CollectionTriggerType:
    normalized = (report_type or "").lower()
    if normalized in {"weekly", "daily", "scheduled"}:
        return CollectionTriggerType.SCHEDULED
    if normalized in {"offboarding", "employee_offboarding"}:
        return CollectionTriggerType.OFFBOARDING
    if normalized in {"project_handover", "handover"}:
        return CollectionTriggerType.PROJECT_HANDOVER
    return CollectionTriggerType.MANUAL


def _new_report_id() -> str:
    return f"rpt_{datetime.now(timezone.utc):%Y%m%d%H%M%S}_{uuid4().hex[:12]}"


def _max_upload_bytes() -> int:
    return settings.REPORT_UPLOAD_MAX_SIZE_MB * 1024 * 1024


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
