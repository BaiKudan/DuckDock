"""Authenticated, metadata-only Pack/ATIF import lifecycle."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import stat
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.deployment import AgentDeployment
from app.models.execution import (
    AgentRun,
    AgentSession,
    TrustLevel,
    TrustSource,
)
from app.models.fleet import AdapterHandshake, AdapterProfile
from app.models.telemetry import (
    AgentRunArtifact,
    AgentRunArtifactCompleteness,
    AgentRunArtifactKind,
    PackImport,
    PackImportArtifact,
    PackImportStatus,
    PackUploadMode,
)
from app.models.user import User
from app.schemas.pack_import import (
    PackImportCreate,
    PackManifest,
    PackMultipartComplete,
    PackPayloadManifest,
)
from app.services.artifact_service import (
    ArtifactStorageError,
    artifact_service,
)
from app.services.atif_codec import (
    AtifSchemaUnsupportedError,
    AtifValidationError,
    atif_trajectory_codec,
    contains_content,
    contains_secret_canary,
)
from app.services.audit_service import audit
from app.services.envelope_canonicalization_service import canonical_sha256
from app.services.fleet_service import require_active_adapter_handshake
from app.services.outbox_event_service import (
    build_trace_artifact_stored,
    enqueue_domain_event,
)
from app.services.reporter_identity_service import ReporterExecutionIdentity


@dataclass(frozen=True, slots=True)
class PackImportCreateResult:
    pack_import: PackImport
    upload: dict[str, Any] | None
    replayed: bool = False
    conflict: bool = False


class PackImportError(ValueError):
    pass


class PackImportNotFoundError(PackImportError):
    pass


class PackImportManifestError(PackImportError):
    pass


class PackImportArchiveError(PackImportError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PackImportMappingError(PackImportError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PackMultipartError(PackImportError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _public_id() -> str:
    return f"pki_{uuid.uuid4().hex}"


def _artifact_public_id() -> str:
    return f"art_{uuid.uuid4().hex}"


def _safe_zip_identity(path: str) -> str:
    normalized = unicodedata.normalize("NFC", path)
    if (
        not normalized
        or len(normalized) > 512
        or normalized.startswith("/")
        or "\\" in normalized
        or any(ord(char) < 32 for char in normalized)
    ):
        raise PackImportArchiveError("unsafe_archive_path")
    parsed = PurePosixPath(normalized)
    if (
        parsed.is_absolute()
        or any(part in {"", ".", ".."} for part in parsed.parts)
        or str(parsed) != normalized
    ):
        raise PackImportArchiveError("unsafe_archive_path")
    return normalized.casefold()


def _strict_json_object(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PackImportArchiveError("duplicate_manifest_key")
        result[key] = value
    return result


def _parse_archive(
    pack_bytes: bytes,
) -> tuple[PackManifest, dict[str, bytes]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(pack_bytes), mode="r")
    except (zipfile.BadZipFile, ValueError) as exc:
        raise PackImportArchiveError("invalid_zip_archive") from exc

    with archive:
        infos = archive.infolist()
        if not infos:
            raise PackImportArchiveError("empty_zip_archive")
        if len(infos) > settings.PACK_IMPORT_MAX_PAYLOADS + 1:
            raise PackImportArchiveError("archive_entry_limit_exceeded")

        seen: set[str] = set()
        total_uncompressed = 0
        total_compressed = 0
        for info in infos:
            identity = _safe_zip_identity(info.filename)
            if identity in seen:
                raise PackImportArchiveError("duplicate_archive_entry")
            seen.add(identity)
            mode = info.external_attr >> 16
            if (
                info.is_dir()
                or stat.S_ISLNK(mode)
                or info.flag_bits & 0x1
            ):
                raise PackImportArchiveError("unsupported_archive_entry")
            if info.file_size < 0 or info.compress_size < 0:
                raise PackImportArchiveError("invalid_archive_size")
            if info.file_size > settings.PACK_IMPORT_MAX_ENTRY_BYTES:
                raise PackImportArchiveError("archive_entry_too_large")
            if info.file_size > 0 and info.compress_size == 0:
                raise PackImportArchiveError("compression_ratio_exceeded")
            if (
                info.compress_size > 0
                and info.file_size / info.compress_size
                > settings.PACK_IMPORT_MAX_COMPRESSION_RATIO
            ):
                raise PackImportArchiveError("compression_ratio_exceeded")
            total_uncompressed += info.file_size
            total_compressed += info.compress_size
            if (
                total_uncompressed
                > settings.PACK_IMPORT_MAX_UNCOMPRESSED_BYTES
            ):
                raise PackImportArchiveError(
                    "archive_uncompressed_limit_exceeded"
                )
        if (
            total_compressed > 0
            and total_uncompressed / total_compressed
            > settings.PACK_IMPORT_MAX_COMPRESSION_RATIO
        ):
            raise PackImportArchiveError("compression_ratio_exceeded")

        manifest_info = next(
            (info for info in infos if info.filename == "manifest.json"),
            None,
        )
        if manifest_info is None:
            raise PackImportArchiveError("manifest_missing")
        if manifest_info.file_size > settings.PACK_IMPORT_MAX_MANIFEST_BYTES:
            raise PackImportArchiveError("manifest_too_large")
        try:
            manifest_bytes = archive.read(manifest_info)
            manifest_raw = json.loads(
                manifest_bytes.decode("utf-8"),
                object_pairs_hook=_strict_json_object,
            )
            manifest = PackManifest.model_validate(manifest_raw)
        except PackImportArchiveError:
            raise
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValidationError,
            zipfile.BadZipFile,
            RuntimeError,
        ) as exc:
            raise PackImportArchiveError("manifest_invalid") from exc

        expected_entries = {
            _safe_zip_identity("manifest.json"),
            *(_safe_zip_identity(item.path) for item in manifest.payloads),
        }
        if seen != expected_entries:
            raise PackImportArchiveError("archive_entry_manifest_mismatch")

        payloads: dict[str, bytes] = {}
        for declared in manifest.payloads:
            try:
                info = archive.getinfo(declared.path)
                payload = archive.read(info)
            except (
                KeyError,
                zipfile.BadZipFile,
                RuntimeError,
            ) as exc:
                raise PackImportArchiveError(
                    "payload_unreadable"
                ) from exc
            if len(payload) != declared.size_bytes:
                raise PackImportArchiveError("payload_size_mismatch")
            if hashlib.sha256(payload).hexdigest() != declared.sha256:
                raise PackImportArchiveError("payload_checksum_mismatch")
            payloads[declared.path] = payload
        return manifest, payloads


async def _safe_audit(
    db: AsyncSession,
    *,
    identity: ReporterExecutionIdentity,
    pack_import: PackImport,
    action: str,
    reason_code: str | None = None,
) -> None:
    if identity.actor_user_id is None:
        return
    actor = await db.get(User, identity.actor_user_id)
    if actor is None:
        return
    details = {
        "public_id": pack_import.public_id,
        "pack_id": pack_import.pack_id,
        "status": pack_import.status.value,
        "expected_pack_sha256": pack_import.expected_pack_sha256,
        "payload_count": pack_import.payload_count,
    }
    if reason_code is not None:
        details["reason_code"] = reason_code
    await audit(
        db,
        user=actor,
        action=action,
        resource_type="pack_import",
        resource_id=pack_import.id,
        namespace_id=identity.namespace_id,
        details=details,
    )


async def create_pack_import(
    db: AsyncSession,
    *,
    identity: ReporterExecutionIdentity,
    request: PackImportCreate,
) -> PackImportCreateResult:
    if (
        request.expected_size_bytes
        > settings.PACK_IMPORT_MAX_COMPRESSED_BYTES
    ):
        raise PackImportManifestError("pack_too_large")
    if len(request.manifest.payloads) > settings.PACK_IMPORT_MAX_PAYLOADS:
        raise PackImportManifestError("payload_limit_exceeded")

    adapter_handshake: AdapterHandshake | None = None
    if request.manifest.producer.handshake_id is not None:
        adapter_handshake = await require_active_adapter_handshake(
            db,
            identity=identity,
            handshake_public_id=(
                request.manifest.producer.handshake_id
            ),
            profile=AdapterProfile.PACK_ATIF_IMPORT,
            required_capabilities={"artifact_import"},
        )

    manifest_sha256 = canonical_sha256(request.manifest)
    existing = (
        await db.execute(
            select(PackImport)
            .where(
                PackImport.namespace_id == identity.namespace_id,
                PackImport.runtime_id == identity.runtime_id,
                PackImport.pack_id == request.manifest.pack_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if existing is not None:
        replay = (
            existing.expected_pack_sha256
            == request.expected_pack_sha256
            and existing.expected_size_bytes
            == request.expected_size_bytes
            and existing.manifest_sha256 == manifest_sha256
            and existing.upload_mode == request.upload_mode
            and existing.multipart_part_size_bytes
            == request.multipart_part_size_bytes
        )
        if not replay:
            existing.status = PackImportStatus.QUARANTINED
            existing.last_error_code = "pack_id_checksum_conflict"
            await db.flush()
            await _safe_audit(
                db,
                identity=identity,
                pack_import=existing,
                action="pack_import.quarantined",
                reason_code="pack_id_checksum_conflict",
            )
            return PackImportCreateResult(
                pack_import=existing,
                upload=None,
                conflict=True,
            )
        upload = None
        if (
            existing.status == PackImportStatus.PENDING_VALIDATION
            and existing.upload_mode == PackUploadMode.SINGLE_PUT
        ):
            upload = artifact_service.generate_presigned_put_url(
                object_key=existing.staging_object_key,
                content_type="application/zip",
            )
        return PackImportCreateResult(
            pack_import=existing,
            upload=upload,
            replayed=True,
        )

    manifest = request.manifest
    partial_payload = next(
        (
            payload
            for payload in manifest.payloads
            if payload.completeness
            != AgentRunArtifactCompleteness.COMPLETE
        ),
        None,
    )
    policy_payload = next(
        (
            payload
            for payload in manifest.payloads
            if payload.redaction_policy_version is not None
        ),
        None,
    )
    staging_key = artifact_service.pack_import_staging_object_key(
        namespace_id=identity.namespace_id,
        runtime_id=identity.runtime_id,
        pack_id=manifest.pack_id,
        sha256=request.expected_pack_sha256,
    )
    pack_import = PackImport(
        public_id=_public_id(),
        namespace_id=identity.namespace_id,
        runtime_id=identity.runtime_id,
        reporter_credential_id=identity.credential_id,
        adapter_handshake_id=(
            adapter_handshake.id
            if adapter_handshake is not None
            else None
        ),
        pack_id=manifest.pack_id,
        status=PackImportStatus.PENDING_VALIDATION,
        manifest_schema=manifest.manifest_schema,
        manifest_version=manifest.manifest_version,
        manifest_sha256=manifest_sha256,
        producer_adapter_id=manifest.producer.adapter_id,
        producer_adapter_version=manifest.producer.adapter_version,
        producer_instance_id=manifest.producer.instance_id,
        external_session_id=manifest.external_session_id,
        external_run_id=manifest.external_run_id,
        run_public_id=manifest.run_public_id,
        external_deployment_id=(
            manifest.deployment.external_deployment_id
            if manifest.deployment
            else None
        ),
        deployment_revision=(
            manifest.deployment.revision
            if manifest.deployment
            else None
        ),
        otel_trace_id=manifest.otel_trace_id,
        staging_object_key=staging_key,
        upload_mode=request.upload_mode,
        multipart_part_size_bytes=request.multipart_part_size_bytes,
        expected_pack_sha256=request.expected_pack_sha256,
        expected_size_bytes=request.expected_size_bytes,
        payload_count=len(manifest.payloads),
        verified_payload_count=0,
        loss_reason=(
            partial_payload.loss_reason if partial_payload else None
        ),
        trust_level=TrustLevel.CHANNEL_AUTHENTICATED.value,
        trust_source=TrustSource.IMPORT.value,
        content_capture_mode="metadata_only",
        redaction_policy_version=(
            policy_payload.redaction_policy_version
            if policy_payload
            else None
        ),
        redaction_receipt_sha256=(
            policy_payload.redaction_receipt_sha256
            if policy_payload
            else None
        ),
        expires_at=(
            _now()
            + timedelta(
                seconds=settings.PACK_IMPORT_PENDING_TTL_SECONDS
            )
        ),
    )
    db.add(pack_import)
    await db.flush()
    upload = None
    if request.upload_mode == PackUploadMode.MULTIPART:
        pack_import.multipart_upload_id = (
            await asyncio.to_thread(
                artifact_service.initiate_multipart_upload,
                object_key=staging_key,
                content_type="application/zip",
            )
        )
        await db.flush()
    else:
        upload = artifact_service.generate_presigned_put_url(
            object_key=staging_key,
            content_type="application/zip",
        )
    await _safe_audit(
        db,
        identity=identity,
        pack_import=pack_import,
        action="pack_import.created",
    )
    return PackImportCreateResult(
        pack_import=pack_import,
        upload=upload,
    )


async def get_pack_import(
    db: AsyncSession,
    *,
    identity: ReporterExecutionIdentity,
    public_id: str,
) -> PackImport:
    pack_import = (
        await db.execute(
            select(PackImport).where(
                PackImport.public_id == public_id,
                PackImport.namespace_id == identity.namespace_id,
                PackImport.runtime_id == identity.runtime_id,
            )
        )
    ).scalar_one_or_none()
    if pack_import is None:
        raise PackImportNotFoundError("pack import not found")
    return pack_import


def _multipart_expected_part_count(pack_import: PackImport) -> int:
    part_size = pack_import.multipart_part_size_bytes
    if part_size is None:
        raise PackMultipartError("multipart_not_enabled")
    return (
        pack_import.expected_size_bytes + part_size - 1
    ) // part_size


async def list_pack_multipart_parts(
    db: AsyncSession,
    *,
    identity: ReporterExecutionIdentity,
    public_id: str,
) -> list[dict[str, Any]]:
    pack_import = await get_pack_import(
        db,
        identity=identity,
        public_id=public_id,
    )
    if pack_import.upload_mode != PackUploadMode.MULTIPART:
        raise PackMultipartError("multipart_not_enabled")
    if pack_import.multipart_completed_at is not None:
        return []
    if pack_import.multipart_upload_id is None:
        raise PackMultipartError("multipart_session_unavailable")
    return await asyncio.to_thread(
        artifact_service.list_multipart_parts,
        object_key=pack_import.staging_object_key,
        upload_id=pack_import.multipart_upload_id,
    )


async def create_pack_multipart_part_url(
    db: AsyncSession,
    *,
    identity: ReporterExecutionIdentity,
    public_id: str,
    part_number: int,
) -> tuple[PackImport, dict[str, Any]]:
    pack_import = await get_pack_import(
        db,
        identity=identity,
        public_id=public_id,
    )
    if pack_import.upload_mode != PackUploadMode.MULTIPART:
        raise PackMultipartError("multipart_not_enabled")
    if pack_import.status != PackImportStatus.PENDING_VALIDATION:
        raise PackMultipartError("multipart_upload_not_pending")
    expires_at = pack_import.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= _now():
        raise PackMultipartError("upload_expired")
    if pack_import.multipart_completed_at is not None:
        raise PackMultipartError("multipart_already_completed")
    if pack_import.multipart_upload_id is None:
        raise PackMultipartError("multipart_session_unavailable")
    if not 1 <= part_number <= _multipart_expected_part_count(pack_import):
        raise PackMultipartError("multipart_part_out_of_range")
    signed = artifact_service.generate_presigned_upload_part_url(
        object_key=pack_import.staging_object_key,
        upload_id=pack_import.multipart_upload_id,
        part_number=part_number,
    )
    return pack_import, signed


async def complete_pack_multipart_upload(
    db: AsyncSession,
    *,
    identity: ReporterExecutionIdentity,
    public_id: str,
    request: PackMultipartComplete,
) -> PackImport:
    pack_import = await get_pack_import(
        db,
        identity=identity,
        public_id=public_id,
    )
    if pack_import.upload_mode != PackUploadMode.MULTIPART:
        raise PackMultipartError("multipart_not_enabled")
    if pack_import.multipart_completed_at is None:
        upload_id = pack_import.multipart_upload_id
        if upload_id is None:
            raise PackMultipartError("multipart_session_unavailable")
        expected_count = _multipart_expected_part_count(pack_import)
        if len(request.parts) != expected_count:
            raise PackMultipartError("multipart_part_count_mismatch")
        stored_parts = await asyncio.to_thread(
            artifact_service.list_multipart_parts,
            object_key=pack_import.staging_object_key,
            upload_id=upload_id,
        )
        declared = [
            {
                "part_number": part.part_number,
                "etag": part.etag,
            }
            for part in request.parts
        ]
        stored_identity = [
            {
                "part_number": part["part_number"],
                "etag": part["etag"],
            }
            for part in stored_parts
        ]
        if declared != stored_identity:
            raise PackMultipartError("multipart_part_receipt_mismatch")
        if sum(int(part["size_bytes"]) for part in stored_parts) != (
            pack_import.expected_size_bytes
        ):
            raise PackMultipartError("multipart_size_mismatch")
        part_size = pack_import.multipart_part_size_bytes
        if part_size is None:
            raise PackMultipartError("multipart_session_unavailable")
        expected_last_size = (
            pack_import.expected_size_bytes
            - part_size * (expected_count - 1)
        )
        expected_sizes = [
            *([part_size] * (expected_count - 1)),
            expected_last_size,
        ]
        if [
            int(part["size_bytes"]) for part in stored_parts
        ] != expected_sizes:
            raise PackMultipartError("multipart_part_size_mismatch")
        await asyncio.to_thread(
            artifact_service.complete_multipart_upload,
            object_key=pack_import.staging_object_key,
            upload_id=upload_id,
            parts=declared,
        )
        pack_import.multipart_completed_at = _now()
        await db.flush()
    return await finalize_pack_import(
        db,
        identity=identity,
        public_id=public_id,
        observed_pack_sha256=request.observed_pack_sha256,
        observed_size_bytes=request.observed_size_bytes,
    )


async def _delete_rejected_staging(pack_import: PackImport) -> None:
    if (
        pack_import.upload_mode == PackUploadMode.MULTIPART
        and pack_import.multipart_upload_id is not None
        and pack_import.multipart_completed_at is None
    ):
        await asyncio.to_thread(
            artifact_service.abort_multipart_upload,
            object_key=pack_import.staging_object_key,
            upload_id=pack_import.multipart_upload_id,
        )
    await artifact_service.delete_object_async(
        pack_import.staging_object_key
    )


async def _reject(
    db: AsyncSession,
    *,
    identity: ReporterExecutionIdentity,
    pack_import: PackImport,
    code: str,
    quarantined: bool = False,
) -> PackImport:
    pack_import.status = (
        PackImportStatus.QUARANTINED
        if quarantined
        else PackImportStatus.REJECTED
    )
    pack_import.last_error_code = code
    pack_import.verified_payload_count = 0
    pack_import.validated_at = _now()
    await _delete_rejected_staging(pack_import)
    await db.flush()
    await _safe_audit(
        db,
        identity=identity,
        pack_import=pack_import,
        action=(
            "pack_import.quarantined"
            if quarantined
            else "pack_import.rejected"
        ),
        reason_code=code,
    )
    return pack_import


async def _resolve_existing_run(
    db: AsyncSession,
    *,
    identity: ReporterExecutionIdentity,
    manifest: PackManifest,
) -> AgentRun:
    candidates: list[AgentRun] = []
    missing_signals = 0

    if manifest.run_public_id is not None:
        candidate = (
            await db.execute(
                select(AgentRun).where(
                    AgentRun.public_id == manifest.run_public_id
                )
            )
        ).scalar_one_or_none()
        if candidate is None:
            missing_signals += 1
        elif (
            candidate.namespace_id != identity.namespace_id
            or candidate.runtime_id != identity.runtime_id
        ):
            raise PackImportMappingError("run_scope_conflict")
        else:
            candidates.append(candidate)

    if manifest.external_run_id is not None:
        candidate = (
            await db.execute(
                select(AgentRun).where(
                    AgentRun.namespace_id == identity.namespace_id,
                    AgentRun.runtime_id == identity.runtime_id,
                    AgentRun.external_run_id
                    == manifest.external_run_id,
                )
            )
        ).scalar_one_or_none()
        if candidate is None:
            missing_signals += 1
        else:
            candidates.append(candidate)

    if manifest.otel_trace_id is not None:
        candidate = (
            await db.execute(
                select(AgentRun).where(
                    AgentRun.namespace_id == identity.namespace_id,
                    AgentRun.otel_trace_id == manifest.otel_trace_id,
                )
            )
        ).scalar_one_or_none()
        if candidate is None:
            missing_signals += 1
        elif candidate.runtime_id != identity.runtime_id:
            raise PackImportMappingError("trace_runtime_conflict")
        else:
            candidates.append(candidate)

    candidate_ids = {candidate.id for candidate in candidates}
    if len(candidate_ids) > 1:
        raise PackImportMappingError("run_trace_mapping_conflict")
    if not candidates:
        raise PackImportMappingError("run_not_found")
    if missing_signals:
        raise PackImportMappingError("run_signal_conflict")
    run = candidates[0]

    if manifest.external_session_id is not None:
        session = (
            await db.get(AgentSession, run.session_id)
            if run.session_id is not None
            else None
        )
        if (
            session is None
            or session.external_session_id
            != manifest.external_session_id
        ):
            raise PackImportMappingError("session_mapping_conflict")

    if manifest.deployment is not None:
        deployment = (
            await db.get(AgentDeployment, run.deployment_id)
            if run.deployment_id is not None
            else None
        )
        if (
            deployment is None
            or deployment.external_deployment_id
            != manifest.deployment.external_deployment_id
            or deployment.revision != manifest.deployment.revision
        ):
            raise PackImportMappingError("deployment_mapping_conflict")
    return run


def _payload_object_key(
    *,
    namespace_id: int,
    run: AgentRun,
    payload: PackPayloadManifest,
) -> str:
    return artifact_service.pack_import_artifact_object_key(
        namespace_id=namespace_id,
        run_public_id=run.public_id,
        sha256=payload.sha256,
        filename="trajectory.json",
    )


async def _materialize_artifact(
    db: AsyncSession,
    *,
    pack_import: PackImport,
    run: AgentRun,
    declared: PackPayloadManifest,
    payload: bytes,
) -> AgentRunArtifact:
    object_key = _payload_object_key(
        namespace_id=pack_import.namespace_id,
        run=run,
        payload=declared,
    )
    object_uri = f"s3://{artifact_service.bucket}/{object_key}"
    existing = (
        await db.execute(
            select(AgentRunArtifact).where(
                AgentRunArtifact.namespace_id
                == pack_import.namespace_id,
                AgentRunArtifact.agent_run_id == run.id,
                AgentRunArtifact.kind
                == AgentRunArtifactKind.TRAJECTORY,
                AgentRunArtifact.sha256 == declared.sha256,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if not (
            existing.schema_name == declared.schema_name
            and existing.schema_version == declared.schema_version
            and existing.object_uri == object_uri
            and existing.size_bytes == declared.size_bytes
            and existing.sensitivity == declared.sensitivity
            and existing.redaction_policy_version
            == declared.redaction_policy_version
            and existing.completeness == declared.completeness
        ):
            raise PackImportMappingError("artifact_metadata_conflict")
        return existing

    await artifact_service.put_object_bytes_async(
        object_key=object_key,
        payload=payload,
        content_type=declared.media_type,
        metadata={
            "sha256": declared.sha256,
            "schema": declared.schema_name,
            "schema-version": declared.schema_version,
            "pack-import": pack_import.public_id,
        },
    )
    artifact = AgentRunArtifact(
        public_id=_artifact_public_id(),
        namespace_id=pack_import.namespace_id,
        agent_run_id=run.id,
        kind=AgentRunArtifactKind.TRAJECTORY,
        schema_name=declared.schema_name,
        schema_version=declared.schema_version,
        object_uri=object_uri,
        sha256=declared.sha256,
        size_bytes=declared.size_bytes,
        sensitivity=declared.sensitivity,
        redaction_policy_version=declared.redaction_policy_version,
        completeness=declared.completeness,
    )
    db.add(artifact)
    await db.flush()
    await enqueue_domain_event(
        db,
        build_trace_artifact_stored(
            artifact,
            run_public_id=run.public_id,
        ),
    )
    return artifact


async def finalize_pack_import(
    db: AsyncSession,
    *,
    identity: ReporterExecutionIdentity,
    public_id: str,
    observed_pack_sha256: str | None = None,
    observed_size_bytes: int | None = None,
) -> PackImport:
    pack_import = await get_pack_import(
        db,
        identity=identity,
        public_id=public_id,
    )
    if pack_import.status in {
        PackImportStatus.IMPORTED,
        PackImportStatus.IMPORTED_PARTIAL,
        PackImportStatus.QUARANTINED,
        PackImportStatus.REJECTED,
    }:
        return pack_import
    expires_at = pack_import.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= _now():
        return await _reject(
            db,
            identity=identity,
            pack_import=pack_import,
            code="upload_expired",
        )

    pack_import.status = PackImportStatus.VALIDATING
    pack_import.last_error_code = None
    await db.flush()
    try:
        head = await artifact_service.head_object_async(
            pack_import.staging_object_key
        )
    except ArtifactStorageError:
        pack_import.status = PackImportStatus.PENDING_VALIDATION
        pack_import.last_error_code = "upload_pending"
        await db.flush()
        return pack_import

    actual_size = int(head["size_bytes"])
    pack_import.actual_size_bytes = actual_size
    if (
        observed_size_bytes is not None
        and observed_size_bytes != pack_import.expected_size_bytes
    ):
        return await _reject(
            db,
            identity=identity,
            pack_import=pack_import,
            code="observed_size_conflict",
        )
    if actual_size != pack_import.expected_size_bytes:
        return await _reject(
            db,
            identity=identity,
            pack_import=pack_import,
            code="pack_size_mismatch",
        )
    if actual_size > settings.PACK_IMPORT_MAX_COMPRESSED_BYTES:
        return await _reject(
            db,
            identity=identity,
            pack_import=pack_import,
            code="pack_too_large",
        )

    try:
        digest = await artifact_service.hash_object_async(
            pack_import.staging_object_key
        )
    except ArtifactStorageError:
        pack_import.status = PackImportStatus.PENDING_VALIDATION
        pack_import.last_error_code = "storage_unavailable"
        await db.flush()
        return pack_import
    actual_sha256 = str(digest["sha256"])
    pack_import.actual_pack_sha256 = actual_sha256
    if (
        observed_pack_sha256 is not None
        and observed_pack_sha256 != pack_import.expected_pack_sha256
    ):
        return await _reject(
            db,
            identity=identity,
            pack_import=pack_import,
            code="observed_checksum_conflict",
        )
    if actual_sha256 != pack_import.expected_pack_sha256:
        return await _reject(
            db,
            identity=identity,
            pack_import=pack_import,
            code="pack_checksum_mismatch",
        )

    try:
        pack_bytes = await artifact_service.read_object_bytes_async(
            pack_import.staging_object_key,
            max_bytes=settings.PACK_IMPORT_MAX_COMPRESSED_BYTES,
        )
        manifest, payloads = _parse_archive(pack_bytes)
    except ArtifactStorageError:
        pack_import.status = PackImportStatus.PENDING_VALIDATION
        pack_import.last_error_code = "storage_unavailable"
        await db.flush()
        return pack_import
    except PackImportArchiveError as exc:
        return await _reject(
            db,
            identity=identity,
            pack_import=pack_import,
            code=exc.code,
        )

    if canonical_sha256(manifest) != pack_import.manifest_sha256:
        return await _reject(
            db,
            identity=identity,
            pack_import=pack_import,
            code="manifest_conflict",
            quarantined=True,
        )

    for declared in manifest.payloads:
        payload = payloads[declared.path]
        if contains_secret_canary(payload):
            return await _reject(
                db,
                identity=identity,
                pack_import=pack_import,
                code="secret_canary_detected",
                quarantined=True,
            )
        try:
            document = atif_trajectory_codec.decode(payload)
        except AtifSchemaUnsupportedError:
            return await _reject(
                db,
                identity=identity,
                pack_import=pack_import,
                code="atif_schema_unsupported",
            )
        except AtifValidationError:
            return await _reject(
                db,
                identity=identity,
                pack_import=pack_import,
                code="atif_payload_invalid",
            )
        if document["schema_version"] != declared.schema_version:
            return await _reject(
                db,
                identity=identity,
                pack_import=pack_import,
                code="atif_schema_version_mismatch",
            )
        if contains_content(document):
            # Foundation has no Namespace content-authorization model yet.
            # A producer-declared receipt alone cannot grant that authority.
            return await _reject(
                db,
                identity=identity,
                pack_import=pack_import,
                code="content_policy_required",
                quarantined=True,
            )
    try:
        run = await _resolve_existing_run(
            db,
            identity=identity,
            manifest=manifest,
        )
    except PackImportMappingError as exc:
        return await _reject(
            db,
            identity=identity,
            pack_import=pack_import,
            code=exc.code,
            quarantined=exc.code != "run_not_found",
        )

    artifacts_by_path: dict[str, AgentRunArtifact] = {}
    try:
        for declared in manifest.payloads:
            artifacts_by_path[declared.path] = (
                await _materialize_artifact(
                    db,
                    pack_import=pack_import,
                    run=run,
                    declared=declared,
                    payload=payloads[declared.path],
                )
            )
    except ArtifactStorageError:
        pack_import.status = PackImportStatus.PENDING_VALIDATION
        pack_import.last_error_code = "storage_unavailable"
        await db.flush()
        return pack_import
    except PackImportMappingError as exc:
        return await _reject(
            db,
            identity=identity,
            pack_import=pack_import,
            code=exc.code,
            quarantined=True,
        )

    existing_paths = set(
        (
            await db.execute(
                select(PackImportArtifact.payload_path).where(
                    PackImportArtifact.pack_import_id == pack_import.id
                )
            )
        ).scalars()
    )
    for path, artifact in artifacts_by_path.items():
        if path not in existing_paths:
            db.add(
                PackImportArtifact(
                    pack_import_id=pack_import.id,
                    agent_run_artifact_id=artifact.id,
                    payload_path=path,
                )
            )

    pack_import.agent_run_id = run.id
    pack_import.run_public_id = run.public_id
    pack_import.verified_payload_count = len(manifest.payloads)
    pack_import.validated_at = _now()
    pack_import.imported_at = pack_import.validated_at
    pack_import.last_error_code = None
    pack_import.status = (
        PackImportStatus.IMPORTED_PARTIAL
        if any(
            payload.completeness
            != AgentRunArtifactCompleteness.COMPLETE
            for payload in manifest.payloads
        )
        else PackImportStatus.IMPORTED
    )
    await db.flush()
    await _safe_audit(
        db,
        identity=identity,
        pack_import=pack_import,
        action="pack_import.imported",
    )
    return pack_import
