from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from pathlib import PurePosixPath
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.models.control_plane import Sensitivity
from app.models.telemetry import (
    AgentRunArtifactCompleteness,
    PackImportStatus,
    PackUploadMode,
)


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TRACE_ID = re.compile(r"^[0-9a-f]{32}$")


def _safe_id(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value.strip())
    if _SAFE_ID.fullmatch(normalized) is None:
        raise ValueError("value contains unsupported characters")
    return normalized


def _safe_version(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value.strip())
    if _SAFE_VERSION.fullmatch(normalized) is None:
        raise ValueError("version contains unsupported characters")
    return normalized


def _safe_archive_path(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    if (
        not normalized
        or len(normalized) > 512
        or normalized.startswith("/")
        or "\\" in normalized
        or any(ord(char) < 32 for char in normalized)
    ):
        raise ValueError("payload path is not a safe relative POSIX path")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or str(path) != normalized
        or normalized == "manifest.json"
    ):
        raise ValueError("payload path is not a safe relative POSIX path")
    return normalized


class PackProducer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter_id: str = Field(min_length=1, max_length=100)
    adapter_version: str = Field(min_length=1, max_length=50)
    profile: Literal["pack-atif-import"] = "pack-atif-import"
    protocol: Literal["duckdock-adapter"] = "duckdock-adapter"
    protocol_version: Literal["1.0"] = "1.0"
    instance_id: str = Field(min_length=1, max_length=128)
    handshake_id: str | None = Field(
        default=None,
        min_length=35,
        max_length=35,
        pattern=r"^hs_[0-9a-f]{32}$",
    )

    @field_validator(
        "adapter_id",
        "instance_id",
    )
    @classmethod
    def _ids(cls, value: str) -> str:
        return _safe_id(value)

    @field_validator("adapter_version")
    @classmethod
    def _version(cls, value: str) -> str:
        return _safe_version(value)


class PackDeploymentReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_deployment_id: str = Field(min_length=1, max_length=255)
    revision: str = Field(min_length=1, max_length=128)

    @field_validator("external_deployment_id", "revision")
    @classmethod
    def _ids(cls, value: str) -> str:
        return _safe_id(value)


class PackPayloadManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=512)
    media_type: Literal[
        "application/json",
        "application/atif+json",
        "application/vnd.atif+json",
    ]
    size_bytes: int = Field(gt=0)
    sha256: str = Field(min_length=64, max_length=64)
    schema_name: Literal["ATIF"] = "ATIF"
    schema_version: str = Field(min_length=1, max_length=50)
    sensitivity: Sensitivity = Sensitivity.RESTRICTED
    completeness: AgentRunArtifactCompleteness
    loss_reason: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    content_capture_mode: Literal["metadata_only"] = "metadata_only"
    content_policy_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
    )
    redaction_policy_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
    )
    redaction_receipt_sha256: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        return _safe_archive_path(value)

    @field_validator("sha256", "redaction_receipt_sha256")
    @classmethod
    def _digest(cls, value: str | None) -> str | None:
        if value is not None and _SHA256.fullmatch(value) is None:
            raise ValueError(
                "digest must be 64 lowercase hexadecimal characters"
            )
        return value

    @field_validator(
        "schema_version",
        "loss_reason",
        "content_policy_version",
        "redaction_policy_version",
    )
    @classmethod
    def _versions(cls, value: str | None) -> str | None:
        if value is not None:
            return _safe_version(value)
        return None

    @model_validator(mode="after")
    def _completeness_and_receipt(self) -> "PackPayloadManifest":
        if self.completeness == AgentRunArtifactCompleteness.COMPLETE:
            if self.loss_reason is not None:
                raise ValueError(
                    "complete payload cannot declare a loss reason"
                )
        elif self.loss_reason is None:
            raise ValueError(
                "partial or unknown payload must declare a loss reason"
            )
        policy_fields = (
            self.content_policy_version,
            self.redaction_policy_version,
            self.redaction_receipt_sha256,
        )
        if any(policy_fields) and not all(policy_fields):
            raise ValueError(
                "content authorization requires policy and redaction receipt"
            )
        return self


class PackManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_schema: Literal["duckdock-pack"] = "duckdock-pack"
    manifest_version: Literal["1.0"] = "1.0"
    pack_id: str = Field(min_length=1, max_length=128)
    producer: PackProducer
    created_at: datetime
    run_public_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=36,
    )
    external_session_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
    )
    external_run_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
    )
    deployment: PackDeploymentReference | None = None
    otel_trace_id: str | None = Field(
        default=None,
        min_length=32,
        max_length=32,
    )
    content_capture_mode: Literal["metadata_only"] = "metadata_only"
    payloads: list[PackPayloadManifest] = Field(
        min_length=1,
        max_length=64,
    )

    @field_validator(
        "pack_id",
        "run_public_id",
        "external_session_id",
        "external_run_id",
    )
    @classmethod
    def _ids(cls, value: str | None) -> str | None:
        if value is not None:
            return _safe_id(value)
        return None

    @field_validator("otel_trace_id")
    @classmethod
    def _trace_id(cls, value: str | None) -> str | None:
        if value is not None and _TRACE_ID.fullmatch(value) is None:
            raise ValueError(
                "otel_trace_id must be 32 lowercase hexadecimal characters"
            )
        return value

    @field_validator("created_at")
    @classmethod
    def _aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value

    @model_validator(mode="after")
    def _run_and_paths(self) -> "PackManifest":
        if not (
            self.run_public_id
            or self.external_run_id
            or self.otel_trace_id
        ):
            raise ValueError(
                "manifest must declare a reliable existing Run correlation"
            )
        seen: set[str] = set()
        for payload in self.payloads:
            identity = unicodedata.normalize(
                "NFC",
                payload.path,
            ).casefold()
            if identity in seen:
                raise ValueError(
                    "payload paths must be unique after normalization"
                )
            seen.add(identity)
        return self


class PackImportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_pack_sha256: str = Field(min_length=64, max_length=64)
    expected_size_bytes: int = Field(gt=0)
    upload_mode: PackUploadMode = PackUploadMode.SINGLE_PUT
    multipart_part_size_bytes: int | None = Field(
        default=None,
        ge=5 * 1024 * 1024,
        le=64 * 1024 * 1024,
    )
    manifest: PackManifest

    @field_validator("expected_pack_sha256")
    @classmethod
    def _digest(cls, value: str) -> str:
        if _SHA256.fullmatch(value) is None:
            raise ValueError(
                "expected_pack_sha256 must be lowercase hexadecimal"
            )
        return value

    @model_validator(mode="after")
    def _multipart_settings(self) -> "PackImportCreate":
        if self.upload_mode == PackUploadMode.MULTIPART:
            if self.multipart_part_size_bytes is None:
                self.multipart_part_size_bytes = 8 * 1024 * 1024
        elif self.multipart_part_size_bytes is not None:
            raise ValueError(
                "multipart_part_size_bytes requires MULTIPART upload mode"
            )
        return self


class PackImportFinalize(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed_pack_sha256: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )
    observed_size_bytes: int | None = Field(default=None, gt=0)

    @field_validator("observed_pack_sha256")
    @classmethod
    def _digest(cls, value: str | None) -> str | None:
        if value is not None and _SHA256.fullmatch(value) is None:
            raise ValueError(
                "observed_pack_sha256 must be lowercase hexadecimal"
            )
        return value


class PackImportedArtifactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    payload_path: str
    object_uri: str
    sha256: str
    size_bytes: int
    schema_name: str
    schema_version: str
    sensitivity: Sensitivity
    completeness: AgentRunArtifactCompleteness


class PackImportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    pack_id: str
    namespace_id: int
    runtime_id: int
    run_public_id: str | None
    status: PackImportStatus
    manifest_schema: str
    manifest_version: str
    expected_pack_sha256: str
    actual_pack_sha256: str | None
    expected_size_bytes: int
    actual_size_bytes: int | None
    upload_mode: PackUploadMode
    multipart_part_size_bytes: int | None
    multipart_completed_at: datetime | None
    payload_count: int
    verified_payload_count: int
    loss_reason: str | None
    trust_level: Literal["CHANNEL_AUTHENTICATED"]
    trust_source: Literal["IMPORT"]
    content_capture_mode: Literal["metadata_only"]
    last_error_code: str | None
    expires_at: datetime
    validated_at: datetime | None
    imported_at: datetime | None
    created_at: datetime
    updated_at: datetime
    artifacts: list[PackImportedArtifactOut] = Field(default_factory=list)


class PackImportCreatedOut(PackImportOut):
    upload_url: str | None = None
    upload_expires_at: datetime | None = None


class PackMultipartPartOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_number: int = Field(ge=1, le=10_000)
    etag: str
    size_bytes: int = Field(gt=0)


class PackMultipartStateOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack_import_public_id: str
    part_size_bytes: int
    expected_size_bytes: int
    completed: bool
    uploaded_parts: list[PackMultipartPartOut]


class PackMultipartPartUrlOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack_import_public_id: str
    part_number: int
    upload_url: str
    upload_expires_at: datetime


class PackMultipartCompletedPart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_number: int = Field(ge=1, le=10_000)
    etag: str = Field(min_length=1, max_length=1024)


class PackMultipartComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parts: list[PackMultipartCompletedPart] = Field(
        min_length=1,
        max_length=10_000,
    )
    observed_pack_sha256: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )
    observed_size_bytes: int | None = Field(default=None, gt=0)

    @field_validator("observed_pack_sha256")
    @classmethod
    def _digest(cls, value: str | None) -> str | None:
        if value is not None and _SHA256.fullmatch(value) is None:
            raise ValueError(
                "observed_pack_sha256 must be lowercase hexadecimal"
            )
        return value

    @model_validator(mode="after")
    def _unique_sorted_parts(self) -> "PackMultipartComplete":
        numbers = [part.part_number for part in self.parts]
        if numbers != sorted(numbers) or len(numbers) != len(set(numbers)):
            raise ValueError("multipart parts must be unique and sorted")
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError("multipart parts must be contiguous from one")
        return self


class PackBatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    idempotency_key: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    pack_import: PackImportCreate


class PackBatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stream_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    items: list[PackBatchItem] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _unique_items(self) -> "PackBatchCreate":
        sequences = [item.sequence for item in self.items]
        keys = [item.idempotency_key for item in self.items]
        if len(sequences) != len(set(sequences)):
            raise ValueError("batch sequences must be unique")
        if len(keys) != len(set(keys)):
            raise ValueError("batch idempotency keys must be unique")
        return self


class PackBatchAckItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int
    idempotency_key: str
    disposition: Literal[
        "accepted",
        "replayed",
        "retryable",
        "rejected",
    ]
    code: str
    pack_import_public_id: str | None = None
    pack_import_status: PackImportStatus | None = None
    upload_mode: PackUploadMode | None = None


class PackBatchAckOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stream_id: str
    ack_cursor: int
    idempotency_retention_seconds: int
    items: list[PackBatchAckItemOut]


class PackExportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_public_id: str = Field(min_length=1, max_length=36)
    idempotency_key: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )

    @field_validator("run_public_id")
    @classmethod
    def _run_id(cls, value: str) -> str:
        return _safe_id(value)


class PackExportOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_id: str
    pack_id: str
    run_public_id: str
    sha256: str
    size_bytes: int
    payload_count: int
    download_url: str
    download_expires_at: datetime
    replayed: bool
    created_at: datetime


class EvaluationReplayCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_public_id: str = Field(min_length=1, max_length=36)
    artifact_public_id: str = Field(min_length=1, max_length=36)
    idempotency_key: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )

    @field_validator("run_public_id", "artifact_public_id")
    @classmethod
    def _ids(cls, value: str) -> str:
        return _safe_id(value)


class EvaluationReplayOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    run_public_id: str
    artifact_public_id: str
    status: Literal["PENDING", "LEASED", "PUBLISHED", "FAILED"]
    replayed: bool
