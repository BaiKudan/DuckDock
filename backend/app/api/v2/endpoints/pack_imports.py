"""Reporter-authenticated Pack/ATIF upload and validation lifecycle."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.api.v2.endpoints.executions import (
    get_reporter_execution_identity,
)
from app.core.config import settings
from app.core.deps import DB
from app.models.telemetry import (
    AgentRunArtifact,
    PackImport,
    PackImportArtifact,
    PackImportStatus,
)
from app.schemas.pack_import import (
    EvaluationReplayCreate,
    EvaluationReplayOut,
    PackBatchAckOut,
    PackBatchCreate,
    PackExportCreate,
    PackExportOut,
    PackImportedArtifactOut,
    PackImportCreate,
    PackImportCreatedOut,
    PackImportFinalize,
    PackImportOut,
    PackMultipartComplete,
    PackMultipartPartOut,
    PackMultipartPartUrlOut,
    PackMultipartStateOut,
)
from app.services.artifact_service import ArtifactStorageError
from app.services.pack_import_service import (
    PackImportManifestError,
    PackImportNotFoundError,
    PackMultipartError,
    complete_pack_multipart_upload,
    create_pack_multipart_part_url,
    create_pack_import,
    finalize_pack_import,
    get_pack_import,
    list_pack_multipart_parts,
)
from app.services.outbox_event_service import OutboxConflictError
from app.services.pack_transfer_service import (
    PackTransferError,
    create_pack_batch,
    create_pack_export,
    get_pack_batch_cursor,
    replay_evaluation_result,
)
from app.services.fleet_service import FleetHandshakeRequiredError
from app.services.reporter_identity_service import ReporterExecutionIdentity


router = APIRouter(prefix="/reporter/pack-imports", tags=["pack-atif"])
transfer_router = APIRouter(prefix="/reporter", tags=["pack-atif"])
ReporterIdentity = Annotated[
    ReporterExecutionIdentity,
    Depends(get_reporter_execution_identity),
]


def _problem(
    *,
    status_code: int,
    title: str,
    code: str,
    detail: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        media_type="application/problem+json",
        content={
            "type": "about:blank",
            "title": title,
            "status": status_code,
            "code": code,
            "detail": detail,
            "retryable": status_code in {429, 503},
            "details": details or {"operation": "pack_import"},
        },
    )


async def _pack_import_out(
    db: DB,
    pack_import: PackImport,
    *,
    upload: dict[str, Any] | None = None,
) -> PackImportOut | PackImportCreatedOut:
    rows = (
        await db.execute(
            select(
                PackImportArtifact.payload_path,
                AgentRunArtifact,
            )
            .join(
                AgentRunArtifact,
                AgentRunArtifact.id
                == PackImportArtifact.agent_run_artifact_id,
            )
            .where(
                PackImportArtifact.pack_import_id == pack_import.id
            )
            .order_by(PackImportArtifact.id)
        )
    ).all()
    artifacts = [
        PackImportedArtifactOut(
            public_id=artifact.public_id,
            payload_path=payload_path,
            object_uri=artifact.object_uri,
            sha256=artifact.sha256,
            size_bytes=artifact.size_bytes,
            schema_name=artifact.schema_name,
            schema_version=artifact.schema_version,
            sensitivity=artifact.sensitivity,
            completeness=artifact.completeness,
        )
        for payload_path, artifact in rows
    ]
    base = PackImportOut(
        public_id=pack_import.public_id,
        pack_id=pack_import.pack_id,
        namespace_id=pack_import.namespace_id,
        runtime_id=pack_import.runtime_id,
        run_public_id=pack_import.run_public_id,
        status=pack_import.status,
        manifest_schema=pack_import.manifest_schema,
        manifest_version=pack_import.manifest_version,
        expected_pack_sha256=pack_import.expected_pack_sha256,
        actual_pack_sha256=pack_import.actual_pack_sha256,
        expected_size_bytes=pack_import.expected_size_bytes,
        actual_size_bytes=pack_import.actual_size_bytes,
        upload_mode=pack_import.upload_mode,
        multipart_part_size_bytes=pack_import.multipart_part_size_bytes,
        multipart_completed_at=pack_import.multipart_completed_at,
        payload_count=pack_import.payload_count,
        verified_payload_count=pack_import.verified_payload_count,
        loss_reason=pack_import.loss_reason,
        trust_level=pack_import.trust_level,  # type: ignore[arg-type]
        trust_source=pack_import.trust_source,  # type: ignore[arg-type]
        content_capture_mode=pack_import.content_capture_mode,  # type: ignore[arg-type]
        last_error_code=pack_import.last_error_code,
        expires_at=pack_import.expires_at,
        validated_at=pack_import.validated_at,
        imported_at=pack_import.imported_at,
        created_at=pack_import.created_at,
        updated_at=pack_import.updated_at,
        artifacts=artifacts,
    )
    if upload is not None:
        return PackImportCreatedOut.model_validate(
            {
                **base.model_dump(mode="python"),
                "upload_url": upload["upload_url"],
                "upload_expires_at": upload["expires_at"],
            }
        )
    return base


def _multipart_problem(exc: PackMultipartError) -> JSONResponse:
    conflict_codes = {
        "multipart_not_enabled",
        "multipart_upload_not_pending",
        "multipart_already_completed",
        "multipart_session_unavailable",
        "upload_expired",
    }
    status_code = (
        status.HTTP_409_CONFLICT
        if exc.code in conflict_codes
        else status.HTTP_422_UNPROCESSABLE_CONTENT
    )
    return _problem(
        status_code=status_code,
        title="Multipart upload rejected",
        code=exc.code.upper(),
        detail="The multipart upload state or submitted part receipts are invalid.",
        details={"operation": "pack_multipart_upload"},
    )


@router.post(
    "",
    operation_id="createPackImport",
    response_model=PackImportCreatedOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {"description": "Pack ID was reused with changed content."},
        413: {"description": "Pack exceeds the configured upload limit."},
        503: {"description": "Artifact storage is unavailable."},
    },
)
async def create_reporter_pack_import(
    body: PackImportCreate,
    db: DB,
    identity: ReporterIdentity,
):
    try:
        result = await create_pack_import(
            db,
            identity=identity,
            request=body,
        )
    except PackImportManifestError:
        return _problem(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            title="Pack rejected",
            code="PACK_TOO_LARGE",
            detail="The declared Pack exceeds an import limit.",
        )
    except FleetHandshakeRequiredError:
        return _problem(
            status_code=status.HTTP_409_CONFLICT,
            title="Adapter handshake required",
            code="ADAPTER_HANDSHAKE_REQUIRED",
            detail=(
                "The referenced Pack adapter handshake is unavailable, "
                "expired, degraded, or missing artifact_import."
            ),
        )
    except ArtifactStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Artifact storage is unavailable",
        ) from exc
    if result.conflict:
        return _problem(
            status_code=status.HTTP_409_CONFLICT,
            title="Idempotency conflict",
            code="IDEMPOTENCY_CONFLICT",
            detail="The Pack ID is already bound to different content.",
            details={
                "operation": "pack_import",
                "pack_import_public_id": result.pack_import.public_id,
            },
        )
    return await _pack_import_out(
        db,
        result.pack_import,
        upload=result.upload,
    )


@router.get(
    "/{public_id}",
    operation_id="getPackImport",
    response_model=PackImportOut,
)
async def get_reporter_pack_import(
    public_id: str,
    db: DB,
    identity: ReporterIdentity,
):
    try:
        pack_import = await get_pack_import(
            db,
            identity=identity,
            public_id=public_id,
        )
    except PackImportNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pack import not found",
        ) from exc
    return await _pack_import_out(db, pack_import)


@router.get(
    "/{public_id}/multipart",
    operation_id="getPackMultipartState",
    response_model=PackMultipartStateOut,
)
async def get_reporter_pack_multipart_state(
    public_id: str,
    db: DB,
    identity: ReporterIdentity,
):
    try:
        pack_import = await get_pack_import(
            db,
            identity=identity,
            public_id=public_id,
        )
        parts = await list_pack_multipart_parts(
            db,
            identity=identity,
            public_id=public_id,
        )
    except PackImportNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pack import not found",
        ) from exc
    except PackMultipartError as exc:
        return _multipart_problem(exc)
    return PackMultipartStateOut(
        pack_import_public_id=pack_import.public_id,
        part_size_bytes=pack_import.multipart_part_size_bytes or 0,
        expected_size_bytes=pack_import.expected_size_bytes,
        completed=pack_import.multipart_completed_at is not None,
        uploaded_parts=[
            PackMultipartPartOut.model_validate(part) for part in parts
        ],
    )


@router.post(
    "/{public_id}/multipart/parts/{part_number}",
    operation_id="createPackMultipartPartUrl",
    response_model=PackMultipartPartUrlOut,
)
async def create_reporter_pack_multipart_part_url(
    public_id: str,
    part_number: int,
    db: DB,
    identity: ReporterIdentity,
):
    try:
        pack_import, signed = await create_pack_multipart_part_url(
            db,
            identity=identity,
            public_id=public_id,
            part_number=part_number,
        )
    except PackImportNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pack import not found",
        ) from exc
    except PackMultipartError as exc:
        return _multipart_problem(exc)
    except ArtifactStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Artifact storage is unavailable",
        ) from exc
    return PackMultipartPartUrlOut(
        pack_import_public_id=pack_import.public_id,
        part_number=part_number,
        upload_url=signed["upload_url"],
        upload_expires_at=signed["expires_at"],
    )


@router.post(
    "/{public_id}/multipart/complete",
    operation_id="completePackMultipartUpload",
    response_model=PackImportOut,
)
async def complete_reporter_pack_multipart(
    public_id: str,
    body: PackMultipartComplete,
    db: DB,
    identity: ReporterIdentity,
):
    try:
        pack_import = await complete_pack_multipart_upload(
            db,
            identity=identity,
            public_id=public_id,
            request=body,
        )
    except PackImportNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pack import not found",
        ) from exc
    except PackMultipartError as exc:
        return _multipart_problem(exc)
    except ArtifactStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Artifact storage is unavailable",
        ) from exc
    response = await _pack_import_out(db, pack_import)
    response_status = status.HTTP_200_OK
    if pack_import.status in {
        PackImportStatus.PENDING_VALIDATION,
        PackImportStatus.VALIDATING,
    }:
        response_status = status.HTTP_202_ACCEPTED
    elif pack_import.status == PackImportStatus.QUARANTINED:
        response_status = status.HTTP_409_CONFLICT
    elif pack_import.status == PackImportStatus.REJECTED:
        response_status = status.HTTP_422_UNPROCESSABLE_CONTENT
    return JSONResponse(
        status_code=response_status,
        content=response.model_dump(mode="json"),
    )


@router.post(
    "/{public_id}/finalize",
    operation_id="finalizePackImport",
    response_model=PackImportOut,
    responses={
        202: {"description": "Upload is still pending or retryable."},
        409: {"description": "Import was quarantined."},
        422: {"description": "Import was permanently rejected."},
    },
)
async def finalize_reporter_pack_import(
    public_id: str,
    db: DB,
    identity: ReporterIdentity,
    body: PackImportFinalize = Body(default_factory=PackImportFinalize),
):
    try:
        pack_import = await finalize_pack_import(
            db,
            identity=identity,
            public_id=public_id,
            observed_pack_sha256=body.observed_pack_sha256,
            observed_size_bytes=body.observed_size_bytes,
        )
    except PackImportNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pack import not found",
        ) from exc
    response = await _pack_import_out(db, pack_import)
    response_status = status.HTTP_200_OK
    if pack_import.status in {
        PackImportStatus.PENDING_VALIDATION,
        PackImportStatus.VALIDATING,
    }:
        response_status = status.HTTP_202_ACCEPTED
    elif pack_import.status == PackImportStatus.QUARANTINED:
        response_status = status.HTTP_409_CONFLICT
    elif pack_import.status == PackImportStatus.REJECTED:
        response_status = status.HTTP_422_UNPROCESSABLE_CONTENT
    return JSONResponse(
        status_code=response_status,
        content=response.model_dump(mode="json"),
    )


@transfer_router.post(
    "/pack-import-batches",
    operation_id="createPackImportBatch",
    response_model=PackBatchAckOut,
)
async def create_reporter_pack_import_batch(
    body: PackBatchCreate,
    db: DB,
    identity: ReporterIdentity,
):
    return await create_pack_batch(
        db,
        identity=identity,
        request=body,
    )


@transfer_router.get(
    "/pack-import-batches/{stream_id}/cursor",
    operation_id="getPackImportBatchCursor",
)
async def get_reporter_pack_import_batch_cursor(
    stream_id: str,
    db: DB,
    identity: ReporterIdentity,
):
    try:
        cursor = await get_pack_batch_cursor(
            db,
            identity=identity,
            stream_key=stream_id,
        )
    except PackTransferError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pack batch stream not found",
        ) from exc
    return {
        "stream_id": stream_id,
        "ack_cursor": cursor,
        "idempotency_retention_seconds": (
            settings.PACK_BATCH_REPLAY_WINDOW_SECONDS
        ),
    }


@transfer_router.post(
    "/pack-exports",
    operation_id="createPackExport",
    response_model=PackExportOut,
)
async def create_reporter_pack_export(
    body: PackExportCreate,
    db: DB,
    identity: ReporterIdentity,
):
    try:
        result = await create_pack_export(
            db,
            identity=identity,
            request=body,
        )
    except PackTransferError as exc:
        if exc.code == "idempotency_conflict":
            return _problem(
                status_code=status.HTTP_409_CONFLICT,
                title="Idempotency conflict",
                code="IDEMPOTENCY_CONFLICT",
                detail="The export key is already bound to another request.",
                details={"operation": "pack_export"},
            )
        status_code = (
            status.HTTP_404_NOT_FOUND
            if exc.code
            in {"run_not_found", "trajectory_artifact_not_found"}
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        return _problem(
            status_code=status_code,
            title="Pack export rejected",
            code=exc.code.upper(),
            detail="The Run cannot be exported as a verified metadata-only ATIF Pack.",
            details={"operation": "pack_export"},
        )
    except ArtifactStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Artifact storage is unavailable",
        ) from exc
    row = result.pack_export
    return PackExportOut(
        public_id=row.public_id,
        pack_id=row.pack_id,
        run_public_id=result.run_public_id,
        sha256=row.sha256,
        size_bytes=row.size_bytes,
        payload_count=row.payload_count,
        download_url=result.download["download_url"],
        download_expires_at=result.download["expires_at"],
        replayed=result.replayed,
        created_at=row.created_at,
    )


@transfer_router.post(
    "/evaluation-replays",
    operation_id="replayEvaluationResult",
    response_model=EvaluationReplayOut,
)
async def replay_reporter_evaluation_result(
    body: EvaluationReplayCreate,
    db: DB,
    identity: ReporterIdentity,
):
    try:
        event, replayed = await replay_evaluation_result(
            db,
            identity=identity,
            request=body,
        )
    except PackTransferError as exc:
        return _problem(
            status_code=(
                status.HTTP_404_NOT_FOUND
                if exc.code == "evaluation_artifact_not_found"
                else status.HTTP_422_UNPROCESSABLE_CONTENT
            ),
            title="Evaluation replay rejected",
            code=exc.code.upper(),
            detail="The evaluation artifact is missing or failed immutable integrity checks.",
            details={"operation": "evaluation_replay"},
        )
    except ArtifactStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Artifact storage is unavailable",
        ) from exc
    except OutboxConflictError:
        return _problem(
            status_code=status.HTTP_409_CONFLICT,
            title="Idempotency conflict",
            code="IDEMPOTENCY_CONFLICT",
            detail="The replay key is already bound to another evaluation artifact.",
            details={"operation": "evaluation_replay"},
        )
    return EvaluationReplayOut(
        event_id=event.event_id,
        run_public_id=body.run_public_id,
        artifact_public_id=body.artifact_public_id,
        status=event.status.value,
        replayed=replayed,
    )
