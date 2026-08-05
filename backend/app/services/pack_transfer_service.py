"""Durable Pack export, batch acknowledgements and evaluation replay."""

from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.control_plane import RuntimeInstance
from app.models.execution import AgentRun
from app.models.outbox import OutboxEvent
from app.models.telemetry import (
    AgentRunArtifact,
    AgentRunArtifactCompleteness,
    AgentRunArtifactKind,
    PackBatchDisposition,
    PackBatchReceipt,
    PackBatchStream,
    PackExport,
    PackImport,
)
from app.schemas.pack_import import (
    EvaluationReplayCreate,
    PackBatchAckItemOut,
    PackBatchAckOut,
    PackBatchCreate,
    PackBatchItem,
    PackExportCreate,
    PackManifest,
    PackPayloadManifest,
    PackProducer,
)
from app.services.artifact_service import (
    ArtifactStorageError,
    artifact_service,
)
from app.services.atif_codec import (
    AtifValidationError,
    atif_trajectory_codec,
    contains_content,
    contains_secret_canary,
)
from app.services.envelope_canonicalization_service import canonical_sha256
from app.services.fleet_service import FleetHandshakeRequiredError
from app.services.outbox_event_service import (
    DomainEvent,
    EVALUATION_RESULT_REPLAYED,
    enqueue_domain_event,
    serialize_event_payload,
)
from app.services.pack_import_service import (
    PackImportManifestError,
    create_pack_import,
)
from app.services.reporter_identity_service import ReporterExecutionIdentity


class PackTransferError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class PackExportResult:
    pack_export: PackExport
    run_public_id: str
    download: dict[str, Any]
    replayed: bool


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _public_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _object_key(object_uri: str) -> str:
    prefix = f"s3://{artifact_service.bucket}/"
    if not object_uri.startswith(prefix):
        raise PackTransferError("artifact_storage_scope_invalid")
    key = object_uri[len(prefix) :]
    if not key:
        raise PackTransferError("artifact_storage_scope_invalid")
    return key


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(
        buffer,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path, payload in sorted(files.items()):
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100600 << 16
            archive.writestr(info, payload, compress_type=zipfile.ZIP_DEFLATED)
    return buffer.getvalue()


async def create_pack_export(
    db: AsyncSession,
    *,
    identity: ReporterExecutionIdentity,
    request: PackExportCreate,
) -> PackExportResult:
    request_sha256 = canonical_sha256(request)
    existing = (
        await db.execute(
            select(PackExport).where(
                PackExport.reporter_credential_id == identity.credential_id,
                PackExport.idempotency_key == request.idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.request_sha256 != request_sha256:
            raise PackTransferError("idempotency_conflict")
        run = await db.get(AgentRun, existing.agent_run_id)
        if run is None:
            raise PackTransferError("run_not_found")
        return PackExportResult(
            pack_export=existing,
            run_public_id=run.public_id,
            download=artifact_service.generate_presigned_get_url(
                object_key=existing.object_key
            ),
            replayed=True,
        )

    run = (
        await db.execute(
            select(AgentRun).where(
                AgentRun.public_id == request.run_public_id,
                AgentRun.namespace_id == identity.namespace_id,
                AgentRun.runtime_id == identity.runtime_id,
            )
        )
    ).scalar_one_or_none()
    if run is None:
        raise PackTransferError("run_not_found")
    runtime = await db.get(RuntimeInstance, identity.runtime_id)
    if runtime is None:
        raise PackTransferError("runtime_not_found")
    artifacts = list(
        (
            await db.execute(
                select(AgentRunArtifact)
                .where(
                    AgentRunArtifact.namespace_id
                    == identity.namespace_id,
                    AgentRunArtifact.agent_run_id == run.id,
                    AgentRunArtifact.kind
                    == AgentRunArtifactKind.TRAJECTORY,
                )
                .order_by(AgentRunArtifact.id)
            )
        ).scalars()
    )
    if not artifacts:
        raise PackTransferError("trajectory_artifact_not_found")
    if len(artifacts) > settings.PACK_IMPORT_MAX_PAYLOADS:
        raise PackTransferError("payload_limit_exceeded")

    payload_files: dict[str, bytes] = {}
    payload_manifests: list[PackPayloadManifest] = []
    for index, artifact in enumerate(artifacts, start=1):
        if artifact.schema_name != "ATIF":
            raise PackTransferError("atif_schema_unsupported")
        payload = await artifact_service.read_object_bytes_async(
            _object_key(artifact.object_uri),
            max_bytes=settings.PACK_IMPORT_MAX_ENTRY_BYTES,
        )
        if len(payload) != artifact.size_bytes:
            raise PackTransferError("artifact_size_mismatch")
        if hashlib.sha256(payload).hexdigest() != artifact.sha256:
            raise PackTransferError("artifact_checksum_mismatch")
        if contains_secret_canary(payload):
            raise PackTransferError("secret_canary_detected")
        try:
            document = atif_trajectory_codec.decode(payload)
        except AtifValidationError as exc:
            raise PackTransferError("atif_payload_invalid") from exc
        if document["schema_version"] != artifact.schema_version:
            raise PackTransferError("atif_schema_version_mismatch")
        if contains_content(document):
            raise PackTransferError("content_policy_required")
        path = f"trajectories/{index:04d}-{artifact.sha256}.json"
        payload_files[path] = payload
        payload_manifests.append(
            PackPayloadManifest(
                path=path,
                media_type="application/atif+json",
                size_bytes=len(payload),
                sha256=artifact.sha256,
                schema_name="ATIF",
                schema_version=artifact.schema_version,
                sensitivity=artifact.sensitivity,
                completeness=artifact.completeness,
                loss_reason=(
                    None
                    if artifact.completeness
                    == AgentRunArtifactCompleteness.COMPLETE
                    else "source_artifact_incomplete"
                ),
            )
        )

    source_digest = hashlib.sha256(
        "".join(item.sha256 for item in payload_manifests).encode("ascii")
    ).hexdigest()
    pack_id = f"export-{run.public_id}-{source_digest[:16]}"
    manifest = PackManifest(
        pack_id=pack_id,
        producer=PackProducer(
            adapter_id="duckdock-server-export",
            adapter_version="1.0",
            instance_id=runtime.public_id,
        ),
        created_at=_now(),
        run_public_id=run.public_id,
        external_run_id=run.external_run_id,
        otel_trace_id=run.otel_trace_id,
        payloads=payload_manifests,
    )
    manifest_bytes = json.dumps(
        manifest.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    pack_bytes = _zip_bytes(
        {"manifest.json": manifest_bytes, **payload_files}
    )
    if len(pack_bytes) > settings.PACK_IMPORT_MAX_COMPRESSED_BYTES:
        raise PackTransferError("pack_too_large")
    pack_sha256 = hashlib.sha256(pack_bytes).hexdigest()
    object_key = artifact_service.pack_export_object_key(
        namespace_id=identity.namespace_id,
        runtime_id=identity.runtime_id,
        run_public_id=run.public_id,
        pack_id=pack_id,
    )
    await artifact_service.put_object_bytes_async(
        object_key=object_key,
        payload=pack_bytes,
        content_type="application/zip",
        metadata={
            "sha256": pack_sha256,
            "schema": "duckdock-pack",
            "schema-version": "1.0",
        },
    )
    row = PackExport(
        public_id=_public_id("pke"),
        namespace_id=identity.namespace_id,
        runtime_id=identity.runtime_id,
        reporter_credential_id=identity.credential_id,
        agent_run_id=run.id,
        idempotency_key=request.idempotency_key,
        request_sha256=request_sha256,
        pack_id=pack_id,
        object_key=object_key,
        sha256=pack_sha256,
        size_bytes=len(pack_bytes),
        payload_count=len(payload_manifests),
    )
    db.add(row)
    await db.flush()
    return PackExportResult(
        pack_export=row,
        run_public_id=run.public_id,
        download=artifact_service.generate_presigned_get_url(
            object_key=object_key
        ),
        replayed=False,
    )


async def _stream(
    db: AsyncSession,
    *,
    identity: ReporterExecutionIdentity,
    stream_key: str,
) -> PackBatchStream:
    row = (
        await db.execute(
            select(PackBatchStream)
            .where(
                PackBatchStream.reporter_credential_id
                == identity.credential_id,
                PackBatchStream.stream_key == stream_key,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        row = PackBatchStream(
            public_id=_public_id("pbs"),
            namespace_id=identity.namespace_id,
            runtime_id=identity.runtime_id,
            reporter_credential_id=identity.credential_id,
            stream_key=stream_key,
            ack_cursor=0,
        )
        db.add(row)
        await db.flush()
    return row


async def _receipt_result(
    db: AsyncSession,
    *,
    receipt: PackBatchReceipt,
    replayed: bool,
) -> PackBatchAckItemOut:
    pack_import = (
        await db.get(PackImport, receipt.pack_import_id)
        if receipt.pack_import_id is not None
        else None
    )
    disposition = (
        "replayed"
        if replayed
        and receipt.disposition == PackBatchDisposition.ACCEPTED
        else receipt.disposition.value.lower()
    )
    return PackBatchAckItemOut(
        sequence=receipt.sequence,
        idempotency_key=receipt.idempotency_key,
        disposition=cast(
            Literal[
                "accepted",
                "replayed",
                "retryable",
                "rejected",
            ],
            disposition,
        ),
        code=receipt.code,
        pack_import_public_id=(
            pack_import.public_id if pack_import is not None else None
        ),
        pack_import_status=(
            pack_import.status if pack_import is not None else None
        ),
        upload_mode=(
            pack_import.upload_mode if pack_import is not None else None
        ),
    )


async def _process_batch_item(
    db: AsyncSession,
    *,
    identity: ReporterExecutionIdentity,
    stream: PackBatchStream,
    item: PackBatchItem,
) -> PackBatchAckItemOut:
    request_sha256 = canonical_sha256(item)
    existing_rows = list(
        (
            await db.execute(
            select(PackBatchReceipt).where(
                PackBatchReceipt.pack_batch_stream_id == stream.id,
                (
                    (PackBatchReceipt.sequence == item.sequence)
                    | (
                        PackBatchReceipt.idempotency_key
                        == item.idempotency_key
                    )
                ),
                )
            )
        )
        .scalars()
    )
    if existing_rows:
        if len(existing_rows) != 1:
            return PackBatchAckItemOut(
                sequence=item.sequence,
                idempotency_key=item.idempotency_key,
                disposition="rejected",
                code="IDEMPOTENCY_CONFLICT",
            )
        existing = existing_rows[0]
        if (
            existing.sequence != item.sequence
            or existing.idempotency_key != item.idempotency_key
            or existing.request_sha256 != request_sha256
        ):
            return PackBatchAckItemOut(
                sequence=item.sequence,
                idempotency_key=item.idempotency_key,
                disposition="rejected",
                code="IDEMPOTENCY_CONFLICT",
            )
        return await _receipt_result(db, receipt=existing, replayed=True)

    try:
        async with db.begin_nested():
            result = await create_pack_import(
                db,
                identity=identity,
                request=item.pack_import,
            )
            disposition = (
                PackBatchDisposition.REJECTED
                if result.conflict
                else PackBatchDisposition.ACCEPTED
            )
            code = (
                "IDEMPOTENCY_CONFLICT"
                if result.conflict
                else (
                    "PACK_IMPORT_REPLAYED"
                    if result.replayed
                    else "PACK_IMPORT_ACCEPTED"
                )
            )
            receipt = PackBatchReceipt(
                pack_batch_stream_id=stream.id,
                pack_import_id=result.pack_import.id,
                sequence=item.sequence,
                idempotency_key=item.idempotency_key,
                request_sha256=request_sha256,
                disposition=disposition,
                code=code,
            )
            db.add(receipt)
            await db.flush()
    except ArtifactStorageError:
        return PackBatchAckItemOut(
            sequence=item.sequence,
            idempotency_key=item.idempotency_key,
            disposition="retryable",
            code="ARTIFACT_STORAGE_UNAVAILABLE",
        )
    except (PackImportManifestError, FleetHandshakeRequiredError) as exc:
        code = (
            "PACK_TOO_LARGE"
            if isinstance(exc, PackImportManifestError)
            else "ADAPTER_HANDSHAKE_REQUIRED"
        )
        receipt = PackBatchReceipt(
            pack_batch_stream_id=stream.id,
            sequence=item.sequence,
            idempotency_key=item.idempotency_key,
            request_sha256=request_sha256,
            disposition=PackBatchDisposition.REJECTED,
            code=code,
        )
        db.add(receipt)
        await db.flush()
    return await _receipt_result(db, receipt=receipt, replayed=False)


async def create_pack_batch(
    db: AsyncSession,
    *,
    identity: ReporterExecutionIdentity,
    request: PackBatchCreate,
) -> PackBatchAckOut:
    stream = await _stream(
        db,
        identity=identity,
        stream_key=request.stream_id,
    )
    results: list[PackBatchAckItemOut] = []
    for item in sorted(request.items, key=lambda candidate: candidate.sequence):
        results.append(
            await _process_batch_item(
                db,
                identity=identity,
                stream=stream,
                item=item,
            )
        )
    acknowledged = set(
        (
            await db.execute(
                select(PackBatchReceipt.sequence).where(
                    PackBatchReceipt.pack_batch_stream_id == stream.id,
                    PackBatchReceipt.sequence > stream.ack_cursor,
                )
            )
        ).scalars()
    )
    cursor = stream.ack_cursor
    while cursor + 1 in acknowledged:
        cursor += 1
    stream.ack_cursor = cursor
    await db.flush()
    return PackBatchAckOut(
        stream_id=request.stream_id,
        ack_cursor=cursor,
        idempotency_retention_seconds=(
            settings.PACK_BATCH_REPLAY_WINDOW_SECONDS
        ),
        items=results,
    )


async def get_pack_batch_cursor(
    db: AsyncSession,
    *,
    identity: ReporterExecutionIdentity,
    stream_key: str,
) -> int:
    stream = (
        await db.execute(
            select(PackBatchStream).where(
                PackBatchStream.reporter_credential_id
                == identity.credential_id,
                PackBatchStream.stream_key == stream_key,
            )
        )
    ).scalar_one_or_none()
    if stream is None:
        raise PackTransferError("batch_stream_not_found")
    return stream.ack_cursor


async def replay_evaluation_result(
    db: AsyncSession,
    *,
    identity: ReporterExecutionIdentity,
    request: EvaluationReplayCreate,
) -> tuple[OutboxEvent, bool]:
    row = (
        await db.execute(
            select(AgentRunArtifact, AgentRun)
            .join(AgentRun, AgentRun.id == AgentRunArtifact.agent_run_id)
            .where(
                AgentRunArtifact.public_id == request.artifact_public_id,
                AgentRunArtifact.namespace_id == identity.namespace_id,
                AgentRunArtifact.kind == AgentRunArtifactKind.EVALUATION,
                AgentRun.public_id == request.run_public_id,
                AgentRun.runtime_id == identity.runtime_id,
            )
        )
    ).one_or_none()
    if row is None:
        raise PackTransferError("evaluation_artifact_not_found")
    artifact, run = row
    payload = await artifact_service.read_object_bytes_async(
        _object_key(artifact.object_uri),
        max_bytes=settings.PACK_IMPORT_MAX_ENTRY_BYTES,
    )
    if len(payload) != artifact.size_bytes:
        raise PackTransferError("artifact_size_mismatch")
    if hashlib.sha256(payload).hexdigest() != artifact.sha256:
        raise PackTransferError("artifact_checksum_mismatch")
    if contains_secret_canary(payload):
        raise PackTransferError("secret_canary_detected")
    idempotency_key = (
        f"EvaluationReplay:{identity.credential_id}:"
        f"{request.idempotency_key}"
    )
    existing = (
        await db.execute(
            select(OutboxEvent).where(
                OutboxEvent.idempotency_key == idempotency_key
            )
        )
    ).scalar_one_or_none()
    event_payload = serialize_event_payload(
        EVALUATION_RESULT_REPLAYED,
        {
            "namespace_id": identity.namespace_id,
            "runtime_id": identity.runtime_id,
            "run_public_id": run.public_id,
            "artifact_public_id": artifact.public_id,
            "schema_name": artifact.schema_name,
            "schema_version": artifact.schema_version,
            "sha256": artifact.sha256,
            "size_bytes": artifact.size_bytes,
            "sensitivity": artifact.sensitivity.name,
            "completeness": artifact.completeness.value,
        },
    )
    event = DomainEvent(
        namespace_id=identity.namespace_id,
        aggregate_type="EvaluationReplay",
        aggregate_public_id=artifact.public_id,
        event_type=EVALUATION_RESULT_REPLAYED,
        idempotency_key=idempotency_key,
        occurred_at=_now(),
        payload=event_payload,
    )
    outbox = await enqueue_domain_event(db, event)
    return outbox, existing is not None
