from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.execution import (
    AgentRunStatus,
    AgentSessionStatus,
    ContentCaptureMode,
    TrustLevel,
    TrustSource,
)


TRACE_ID_PATTERN = r"^[0-9a-f]{32}$"
SPAN_ID_PATTERN = r"^[0-9a-f]{16}$"
ALLOWED_METADATA_KEYS = {
    "deployment.environment",
    "deployment.region",
    "harness.name",
    "harness.version",
    "service.name",
    "service.version",
    "operation.name",
    "error.category",
    "agent.mode",
}
TERMINAL_RUN_STATUSES = {
    AgentRunStatus.SUCCEEDED,
    AgentRunStatus.FAILED,
    AgentRunStatus.CANCELLED,
    AgentRunStatus.TIMED_OUT,
}
TERMINAL_SESSION_STATUSES = {
    AgentSessionStatus.ENDED,
    AgentSessionStatus.ABANDONED,
}
MetadataScalar = str | int | float | bool
MetadataValue = MetadataScalar | list[MetadataScalar]


def _validate_metadata(
    value: dict[str, MetadataValue] | None,
) -> dict[str, MetadataValue] | None:
    if value is None:
        return None
    if len(value) > 32:
        raise ValueError("metadata must contain at most 32 properties")
    unknown = sorted(set(value) - ALLOWED_METADATA_KEYS)
    if unknown:
        raise ValueError(f"metadata contains unknown keys: {unknown}")
    for key, item in value.items():
        if isinstance(item, str) and len(item) > 2000:
            raise ValueError(f"metadata string is too long: {key}")
        if isinstance(item, list):
            if len(item) > 100:
                raise ValueError(f"metadata list is too long: {key}")
            for child in item:
                if isinstance(child, str) and len(child) > 500:
                    raise ValueError(f"metadata list string is too long: {key}")
    if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > 16_384:
        raise ValueError("metadata exceeds 16 KiB")
    return value


class StrictExecutionEnvelope(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
    )

    @field_validator("started_at", "ended_at", check_fields=False)
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value

    @field_validator("metadata", check_fields=False)
    @classmethod
    def validate_metadata(
        cls,
        value: dict[str, MetadataValue] | None,
    ) -> dict[str, MetadataValue] | None:
        return _validate_metadata(value)


class SessionStartEnvelope(StrictExecutionEnvelope):
    external_session_id: str = Field(min_length=1, max_length=256)
    deployment_public_id: str | None = Field(default=None, min_length=1, max_length=36)
    work_trace_id: int | None = Field(default=None, ge=1)
    started_at: datetime
    sensitivity: Literal["RESTRICTED", "CONFIDENTIAL", "INTERNAL"] = "RESTRICTED"
    content_capture_mode: Literal[ContentCaptureMode.METADATA_ONLY] = (
        ContentCaptureMode.METADATA_ONLY
    )
    metadata: dict[str, MetadataValue] | None = None

    @field_validator("external_session_id")
    @classmethod
    def strip_external_session_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("external_session_id must not be blank")
        return value


class SessionCompleteEnvelope(StrictExecutionEnvelope):
    ended_at: datetime
    status: AgentSessionStatus
    run_count: int | None = Field(default=None, ge=0)
    error_count: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def terminal_status_only(self) -> "SessionCompleteEnvelope":
        if self.status not in TERMINAL_SESSION_STATUSES:
            raise ValueError("session completion status must be ENDED or ABANDONED")
        return self


class RunStartEnvelope(StrictExecutionEnvelope):
    external_run_id: str = Field(min_length=1, max_length=256)
    session_public_id: str | None = Field(default=None, min_length=1, max_length=36)
    deployment_public_id: str | None = Field(default=None, min_length=1, max_length=36)
    work_trace_id: int | None = Field(default=None, ge=1)
    otel_trace_id: str | None = Field(default=None, pattern=TRACE_ID_PATTERN)
    root_span_id: str | None = Field(default=None, pattern=SPAN_ID_PATTERN)
    attempt: int = Field(default=1, ge=1)
    source_schema: str = Field(min_length=1, max_length=100)
    source_schema_version: str = Field(min_length=1, max_length=50)
    started_at: datetime
    content_capture_mode: Literal[ContentCaptureMode.METADATA_ONLY] = (
        ContentCaptureMode.METADATA_ONLY
    )
    metadata: dict[str, MetadataValue] | None = None

    @field_validator("external_run_id", "source_schema", "source_schema_version")
    @classmethod
    def strip_identity(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("run identity fields must not be blank")
        return value


class RunCompleteEnvelope(StrictExecutionEnvelope):
    ended_at: datetime
    status: AgentRunStatus
    otel_trace_id: str | None = Field(default=None, pattern=TRACE_ID_PATTERN)
    root_span_id: str | None = Field(default=None, pattern=SPAN_ID_PATTERN)
    duration_ms: int | None = Field(default=None, ge=0)
    step_count: int | None = Field(default=None, ge=0)
    model_call_count: int | None = Field(default=None, ge=0)
    tool_call_count: int | None = Field(default=None, ge=0)
    input_token_count: int | None = Field(default=None, ge=0)
    output_token_count: int | None = Field(default=None, ge=0)
    error_type: str | None = Field(default=None, min_length=1, max_length=100)
    metadata: dict[str, MetadataValue] | None = None

    @model_validator(mode="after")
    def terminal_status_only(self) -> "RunCompleteEnvelope":
        if self.status not in TERMINAL_RUN_STATUSES:
            raise ValueError("run completion status must be terminal")
        return self


class ReporterSessionOut(BaseModel):
    """Metadata-only Reporter response; no idempotency hashes or raw content."""

    model_config = ConfigDict(extra="forbid")

    session_public_id: str
    external_session_id: str
    namespace_id: str
    runtime_instance_id: str
    deployment_public_id: str | None = None
    work_trace_id: str | None = None
    status: AgentSessionStatus
    sensitivity: str
    started_at: datetime
    ended_at: datetime | None = None
    run_count: int
    error_count: int
    content_capture_mode: ContentCaptureMode
    metadata: dict[str, MetadataValue] | None = None


class AgentRunOut(BaseModel):
    """Governed AgentRun projection shared by Reporter and management APIs."""

    model_config = ConfigDict(extra="forbid")

    run_public_id: str
    external_run_id: str
    namespace_id: str
    runtime_instance_id: str
    session_public_id: str | None = None
    deployment_public_id: str | None = None
    work_trace_id: str | None = None
    status: AgentRunStatus
    trust_level: TrustLevel
    trust_source: TrustSource
    source_schema: str
    source_schema_version: str
    normalizer_version: str
    otel_trace_id: str | None = None
    root_span_id: str | None = None
    attempt: int
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: int | None = None
    step_count: int | None = None
    model_call_count: int | None = None
    tool_call_count: int | None = None
    input_token_count: int | None = None
    output_token_count: int | None = None
    error_type: str | None = None
    content_capture_mode: ContentCaptureMode
    metadata: dict[str, MetadataValue] | None = None


class AgentRunListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AgentRunOut]
    next_cursor: str | None
