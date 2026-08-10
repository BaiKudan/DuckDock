from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import settings
from app.models.control_plane import Sensitivity
from app.models.telemetry import (
    AgentRunArtifactCompleteness,
    AgentRunArtifactKind,
    TelemetrySinkStatus,
    TraceBackendRefStatus,
)
from app.schemas.webhook import validate_external_url


_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_DB_CREDENTIAL_REF_PATTERN = re.compile(r"^db:[1-9][0-9]*$")
_EXTERNAL_CREDENTIAL_REF_PATTERN = re.compile(
    r"^(?:secret|vault)://[A-Za-z0-9][A-Za-z0-9._/-]*$"
)


def validate_internal_object_uri(value: str) -> str:
    uri = value.strip()
    if not uri or len(uri) > 512:
        raise ValueError("object_uri must contain at most 512 characters")
    if any(ord(char) < 32 for char in uri):
        raise ValueError("object_uri cannot contain control characters")

    if "://" in uri:
        parts = urlsplit(uri)
        if parts.scheme != "s3":
            raise ValueError("object_uri must use the internal s3 scheme")
        if parts.netloc != settings.MINIO_BUCKET:
            raise ValueError("object_uri must target the configured artifact bucket")
        if parts.query or parts.fragment or parts.username or parts.password:
            raise ValueError("object_uri cannot contain credentials, query, or fragment")
        object_key = parts.path.lstrip("/")
    else:
        if uri.startswith("/") or ":" in uri or "\\" in uri:
            raise ValueError("object_uri must be a relative internal object key")
        object_key = uri

    segments = [unquote(segment) for segment in object_key.split("/")]
    if not object_key or any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError("object_uri contains an invalid object-key segment")
    return uri


def validate_credential_reference(value: str) -> str:
    ref = value.strip()
    if (
        _DB_CREDENTIAL_REF_PATTERN.fullmatch(ref) is None
        and _EXTERNAL_CREDENTIAL_REF_PATTERN.fullmatch(ref) is None
    ):
        raise ValueError(
            "credential_ref must be a db:, secret://, or vault:// reference"
        )
    return ref


def validate_trace_url(value: str) -> str:
    url = value.strip()
    validate_external_url(url)
    parts = urlsplit(url)
    if parts.username or parts.password:
        raise ValueError("trace_url cannot contain embedded credentials")
    return url


class AgentRunArtifactCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: AgentRunArtifactKind
    schema_name: str = Field(min_length=1, max_length=100)
    schema_version: str = Field(min_length=1, max_length=50)
    object_uri: str = Field(min_length=1, max_length=512)
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0)
    sensitivity: Sensitivity = Sensitivity.RESTRICTED
    redaction_policy_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
    )
    completeness: AgentRunArtifactCompleteness = (
        AgentRunArtifactCompleteness.UNKNOWN
    )

    @field_validator("schema_name", "schema_version", "redaction_policy_version")
    @classmethod
    def _bounded_name(cls, value: str | None) -> str | None:
        if value is not None and _NAME_PATTERN.fullmatch(value) is None:
            raise ValueError("value contains unsupported characters")
        return value

    @field_validator("sha256")
    @classmethod
    def _sha256(cls, value: str) -> str:
        if _SHA256_PATTERN.fullmatch(value) is None:
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        return value

    @field_validator("object_uri")
    @classmethod
    def _object_uri(cls, value: str) -> str:
        return validate_internal_object_uri(value)


class AgentRunArtifactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    run_public_id: str
    kind: AgentRunArtifactKind
    schema_name: str
    schema_version: str
    object_uri: str
    sha256: str
    size_bytes: int
    sensitivity: Sensitivity
    redaction_policy_version: str | None
    completeness: AgentRunArtifactCompleteness
    created_at: datetime


class TelemetrySinkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: Literal["native", "otlp_http", "otlp_grpc", "custom"] = "custom"
    environment: str | None = Field(default=None, min_length=1, max_length=64)
    region: str | None = Field(default=None, min_length=1, max_length=64)
    timeout_ms: int = Field(default=15_000, ge=100, le=60_000)
    verify_tls: bool = True


class TelemetrySinkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(gt=0)
    provider: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    endpoint: str = Field(min_length=1, max_length=512)
    project_ref: str | None = Field(default=None, min_length=1, max_length=255)
    credential_ref: str = Field(min_length=1, max_length=255)
    config: TelemetrySinkConfig = Field(default_factory=TelemetrySinkConfig)

    @field_validator("provider", "name", "project_ref")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        if value is not None and _NAME_PATTERN.fullmatch(value) is None:
            raise ValueError("value contains unsupported characters")
        return value

    @field_validator("endpoint")
    @classmethod
    def _endpoint(cls, value: str) -> str:
        endpoint = value.strip()
        validate_external_url(endpoint)
        parts = urlsplit(endpoint)
        if parts.username or parts.password:
            raise ValueError("endpoint cannot contain embedded credentials")
        if parts.query or parts.fragment:
            raise ValueError("endpoint cannot contain query parameters or fragments")
        return endpoint

    @field_validator("credential_ref")
    @classmethod
    def _credential_ref(cls, value: str) -> str:
        return validate_credential_reference(value)


class TelemetrySinkUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str | None = Field(default=None, min_length=1, max_length=512)
    project_ref: str | None = Field(default=None, min_length=1, max_length=255)
    credential_ref: str | None = Field(default=None, min_length=1, max_length=255)
    config: TelemetrySinkConfig | None = None

    @field_validator("endpoint")
    @classmethod
    def _endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return TelemetrySinkCreate._endpoint(value)

    @field_validator("project_ref")
    @classmethod
    def _project_ref(cls, value: str | None) -> str | None:
        if value is not None and _NAME_PATTERN.fullmatch(value) is None:
            raise ValueError("value contains unsupported characters")
        return value

    @field_validator("credential_ref")
    @classmethod
    def _credential_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_credential_reference(value)


class TelemetrySinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    provider: str
    name: str
    endpoint: str
    project_ref: str | None
    credential_ref: str
    status: TelemetrySinkStatus
    config_json: dict | None
    created_at: datetime
    updated_at: datetime


class TraceBackendRefOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    namespace_id: int
    agent_run_id: int
    telemetry_sink_id: int
    external_trace_id: str
    external_session_id: str | None
    trace_url: str | None
    status: TraceBackendRefStatus
    last_confirmed_at: datetime | None
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime
