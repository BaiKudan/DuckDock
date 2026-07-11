from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from app.core.deps import AdminUser, CurrentUser, DB, get_robot_account
from app.core.security import hash_password
from app.models.control_plane import (
    AnalysisJobStatus,
    AnalysisResultArtifact,
    AnalysisWorker,
    MemoryCandidate,
    MemoryCandidateStatus,
    ReportAnalysisJob,
)
from app.models.user import SystemRole, User
from app.schemas.control_plane import (
    AnalysisJobCancel,
    AnalysisJobFail,
    AnalysisJobFinalize,
    AnalysisJobLeaseRequest,
    AnalysisJobLeaseOut,
    AnalysisJobLeaseResponse,
    AnalysisQueueMetricsOut,
    AnalysisJobRetry,
    AnalysisResultArtifactDownloadOut,
    AnalysisResultArtifactOut,
    AnalysisResultUploadOut,
    AnalysisWorkerCreate,
    AnalysisWorkerCreatedOut,
    AnalysisWorkerDisableOut,
    AnalysisWorkerOut,
    MemoryCandidateOut,
    MemoryCandidateReview,
    ReportAnalysisJobOut,
)
from app.services.artifact_service import ArtifactStorageError, artifact_service
from app.services.analysis_service import (
    AnalysisWorkerAuthContext,
    authenticate_analysis_worker_token,
    cancel_analysis_job,
    disable_analysis_worker,
    fail_analysis_job,
    finalize_analysis_job,
    generate_analysis_worker_token,
    get_analysis_queue_metrics,
    heartbeat_analysis_job,
    lease_next_analysis_job,
    retry_analysis_job,
    review_memory_candidate,
)
from app.services.audit_service import audit
from app.services.iam_service import ensure_builtin_rbac, has_permission


router = APIRouter(tags=["analysis"])
worker_bearer = HTTPBearer()


async def _get_worker_context(
    db: DB,
    credentials: HTTPAuthorizationCredentials = Depends(worker_bearer),
) -> AnalysisWorkerAuthContext:
    return await authenticate_analysis_worker_token(db, credentials.credentials)


@dataclass(frozen=True)
class AnalysisArtifactAccess:
    artifact: AnalysisResultArtifact
    current_user: User


async def _can_read_analysis(db: DB, current_user: User) -> bool:
    if get_robot_account(current_user) is not None:
        return False
    if current_user.system_role == SystemRole.ADMIN:
        return True
    await ensure_builtin_rbac(db)
    return (
        await has_permission(db, user=current_user, permission_key="asset.read")
        or await has_permission(db, user=current_user, permission_key="asset.manage")
    )


async def require_analysis_reader(current_user: CurrentUser, db: DB) -> User:
    if await _can_read_analysis(db, current_user):
        return current_user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Asset read privileges required",
    )


AnalysisReaderUser = Annotated[User, Depends(require_analysis_reader)]


async def require_analysis_artifact_reader(
    artifact_id: int,
    db: DB,
    current_user: CurrentUser,
) -> AnalysisArtifactAccess:
    artifact = (
        await db.execute(select(AnalysisResultArtifact).where(AnalysisResultArtifact.id == artifact_id))
    ).scalar_one_or_none()
    if artifact is None:
        raise HTTPException(status_code=404, detail="Analysis artifact not found")
    if not await _can_read_analysis(db, current_user):
        await audit(
            db,
            user=current_user,
            action="analysis_artifact.download.denied",
            resource_type="analysis_artifact",
            resource_id=artifact.id,
            details={"runtime_id": artifact.runtime_id, "object_key": artifact.object_key},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Asset read privileges required",
        )
    return AnalysisArtifactAccess(artifact=artifact, current_user=current_user)


AnalysisArtifactReader = Annotated[AnalysisArtifactAccess, Depends(require_analysis_artifact_reader)]


@router.post(
    "/analysis/workers",
    response_model=AnalysisWorkerCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_analysis_worker(body: AnalysisWorkerCreate, db: DB, current_user: AdminUser):
    worker_key, prefix, secret, token = generate_analysis_worker_token()
    worker = AnalysisWorker(
        name=body.name,
        worker_key=worker_key,
        token_prefix=prefix,
        token_hash=hash_password(secret),
        capabilities_json=body.capabilities_json,
        created_by=current_user.id,
    )
    db.add(worker)
    await db.flush()
    await audit(
        db,
        user=current_user,
        action="analysis_worker.created",
        resource_type="analysis_worker",
        resource_id=worker.id,
        details={"worker_key": worker.worker_key, "token_prefix": prefix},
    )
    payload = AnalysisWorkerOut.model_validate(worker).model_dump()
    return AnalysisWorkerCreatedOut(**payload, token=token)


@router.get("/analysis/workers", response_model=list[AnalysisWorkerOut])
async def list_analysis_workers(db: DB, current_user: AdminUser):
    return (
        await db.execute(select(AnalysisWorker).order_by(AnalysisWorker.created_at.desc()))
    ).scalars().all()


@router.post("/analysis/workers/{worker_id}/disable", response_model=AnalysisWorkerDisableOut)
async def disable_worker(worker_id: int, db: DB, current_user: AdminUser):
    result = await disable_analysis_worker(db, worker_id=worker_id, disabled_by=current_user.id)
    await audit(
        db,
        user=current_user,
        action="analysis_worker.disabled",
        resource_type="analysis_worker",
        resource_id=result.worker.id,
        details={
            "worker_key": result.worker.worker_key,
            "requeued_job_count": result.requeued_job_count,
            "failed_job_count": result.failed_job_count,
        },
    )
    return AnalysisWorkerDisableOut(
        worker=AnalysisWorkerOut.model_validate(result.worker),
        requeued_job_count=result.requeued_job_count,
        failed_job_count=result.failed_job_count,
    )


@router.get("/analysis/jobs", response_model=list[ReportAnalysisJobOut])
async def list_analysis_jobs(
    db: DB,
    current_user: AnalysisReaderUser,
    status_filter: AnalysisJobStatus | None = None,
    runtime_id: int | None = None,
):
    stmt = select(ReportAnalysisJob).order_by(ReportAnalysisJob.created_at.desc()).limit(200)
    if status_filter is not None:
        stmt = stmt.where(ReportAnalysisJob.status == status_filter)
    if runtime_id is not None:
        stmt = stmt.where(ReportAnalysisJob.runtime_id == runtime_id)
    return (await db.execute(stmt)).scalars().all()


@router.get("/analysis/queue/metrics", response_model=AnalysisQueueMetricsOut)
async def get_queue_metrics(db: DB, current_user: AnalysisReaderUser):
    metrics = await get_analysis_queue_metrics(db)
    return AnalysisQueueMetricsOut(**asdict(metrics))


@router.post("/analysis/jobs/lease", response_model=AnalysisJobLeaseResponse)
async def lease_analysis_job(
    body: AnalysisJobLeaseRequest,
    db: DB,
    worker_context: AnalysisWorkerAuthContext = Depends(_get_worker_context),
):
    lease = await lease_next_analysis_job(
        db,
        worker=worker_context.worker,
        lease_seconds=body.lease_seconds,
        worker_name=body.worker_name,
        capabilities_json=body.capabilities_json,
    )
    if lease is None:
        return AnalysisJobLeaseResponse(job=None)
    return AnalysisJobLeaseResponse(
        job=AnalysisJobLeaseOut(
            job=ReportAnalysisJobOut.model_validate(lease.job),
            download_url=lease.download["download_url"],
            download_expires_in=lease.download["expires_in"],
            result_upload_url=lease.upload["upload_url"],
            result_upload_expires_in=lease.upload["expires_in"],
            result_bucket=lease.job.result_bucket or "",
            result_object_key=lease.result_object_key,
            result_content_type=lease.job.result_content_type or "application/json",
            result_uploads=[
                AnalysisResultUploadOut(
                    kind=item["kind"],
                    filename=item["filename"],
                    content_type=item["content_type"],
                    bucket=item["bucket"],
                    object_key=item["object_key"],
                    upload_url=item["upload_url"],
                    expires_in=item["expires_in"],
                )
                for item in lease.uploads
            ],
        )
    )


@router.post("/analysis/jobs/{job_id}/heartbeat", response_model=ReportAnalysisJobOut)
async def heartbeat_worker_analysis_job(
    job_id: int,
    db: DB,
    worker_context: AnalysisWorkerAuthContext = Depends(_get_worker_context),
):
    return await heartbeat_analysis_job(db, worker=worker_context.worker, job_id=job_id)


@router.post("/analysis/jobs/{job_id}/finalize", response_model=ReportAnalysisJobOut)
async def finalize_worker_analysis_job(
    job_id: int,
    body: AnalysisJobFinalize,
    db: DB,
    worker_context: AnalysisWorkerAuthContext = Depends(_get_worker_context),
):
    return await finalize_analysis_job(db, worker=worker_context.worker, job_id=job_id, body=body)


@router.post("/analysis/jobs/{job_id}/fail", response_model=ReportAnalysisJobOut)
async def fail_worker_analysis_job(
    job_id: int,
    body: AnalysisJobFail,
    db: DB,
    worker_context: AnalysisWorkerAuthContext = Depends(_get_worker_context),
):
    return await fail_analysis_job(db, worker=worker_context.worker, job_id=job_id, body=body)


@router.post("/analysis/jobs/{job_id}/cancel", response_model=ReportAnalysisJobOut)
async def admin_cancel_analysis_job(job_id: int, body: AnalysisJobCancel, db: DB, current_user: AdminUser):
    job = await cancel_analysis_job(db, job_id=job_id, reason=body.reason, cancelled_by=current_user.id)
    await audit(
        db,
        user=current_user,
        action="analysis_job.cancelled",
        resource_type="analysis_job",
        resource_id=job.id,
        details={"reason": body.reason, "runtime_id": job.runtime_id},
    )
    return job


@router.post("/analysis/jobs/{job_id}/retry", response_model=ReportAnalysisJobOut)
async def admin_retry_analysis_job(job_id: int, body: AnalysisJobRetry, db: DB, current_user: AdminUser):
    job = await retry_analysis_job(
        db,
        job_id=job_id,
        reason=body.reason,
        reset_attempts=body.reset_attempts,
        retried_by=current_user.id,
    )
    await audit(
        db,
        user=current_user,
        action="analysis_job.retried",
        resource_type="analysis_job",
        resource_id=job.id,
        details={"reason": body.reason, "reset_attempts": body.reset_attempts, "runtime_id": job.runtime_id},
    )
    return job


@router.get("/analysis/jobs/{job_id}/artifacts", response_model=list[AnalysisResultArtifactOut])
async def list_analysis_job_artifacts(job_id: int, db: DB, current_user: AnalysisReaderUser):
    job = (
        await db.execute(select(ReportAnalysisJob).where(ReportAnalysisJob.id == job_id))
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found")
    return (
        await db.execute(
            select(AnalysisResultArtifact)
            .where(AnalysisResultArtifact.analysis_job_id == job_id)
            .order_by(AnalysisResultArtifact.kind, AnalysisResultArtifact.created_at)
        )
    ).scalars().all()


@router.get("/analysis/artifacts/{artifact_id}/download-link", response_model=AnalysisResultArtifactDownloadOut)
async def get_analysis_artifact_download_link(access: AnalysisArtifactReader, db: DB):
    artifact = access.artifact
    try:
        link = artifact_service.generate_presigned_get_url(object_key=artifact.object_key)
    except ArtifactStorageError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    await audit(
        db,
        user=access.current_user,
        action="analysis_artifact.download.issued",
        resource_type="analysis_artifact",
        resource_id=artifact.id,
        details={"runtime_id": artifact.runtime_id, "object_key": artifact.object_key},
    )
    return AnalysisResultArtifactDownloadOut(
        filename=artifact.filename,
        content_type=artifact.content_type,
        download_url=link["download_url"],
        expires_in=link["expires_in"],
        expires_at=link["expires_at"],
    )


@router.get("/memory/candidates", response_model=list[MemoryCandidateOut])
async def list_memory_candidates(
    db: DB,
    current_user: AnalysisReaderUser,
    status_filter: MemoryCandidateStatus | None = None,
    runtime_id: int | None = None,
):
    stmt = select(MemoryCandidate).order_by(MemoryCandidate.created_at.desc()).limit(200)
    if status_filter is not None:
        stmt = stmt.where(MemoryCandidate.status == status_filter)
    if runtime_id is not None:
        stmt = stmt.where(MemoryCandidate.runtime_id == runtime_id)
    return (await db.execute(stmt)).scalars().all()


@router.patch("/memory/candidates/{candidate_id}", response_model=MemoryCandidateOut)
async def review_candidate(
    candidate_id: int,
    body: MemoryCandidateReview,
    db: DB,
    current_user: AdminUser,
):
    if body.status == MemoryCandidateStatus.CANDIDATE:
        raise HTTPException(status_code=422, detail="Review status must change from candidate")
    candidate = await review_memory_candidate(
        db,
        candidate_id=candidate_id,
        status_value=body.status,
        reviewed_by=current_user.id,
    )
    await audit(
        db,
        user=current_user,
        action="memory_candidate.reviewed",
        resource_type="memory_candidate",
        resource_id=candidate.id,
        details={"status": candidate.status.value},
    )
    return candidate
