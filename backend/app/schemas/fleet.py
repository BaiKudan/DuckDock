from __future__ import annotations

import base64
import re
from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from app.models.control_plane import RuntimeProvider, RuntimeStatus
from app.models.fleet import AdapterConfigDrift, AdapterProfile


_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]*$")
_CAPABILITY = re.compile(r"^[a-z][a-z0-9._-]*$")
_NONCE = re.compile(r"^[A-Za-z0-9_-]{22,171}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AdapterDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter_id: str = Field(min_length=1, max_length=100)
    adapter_version: str = Field(min_length=1, max_length=50)
    profile: AdapterProfile
    protocol: Literal["duckdock-adapter"] = "duckdock-adapter"
    protocol_version: Literal["1.0"] = "1.0"
    source_schema: str = Field(min_length=1, max_length=100)
    source_schema_version: str = Field(min_length=1, max_length=50)
    capabilities: list[str] = Field(min_length=1, max_length=16)
    content_capture_modes: list[Literal["metadata_only"]] = Field(
        min_length=1,
        max_length=1,
    )
    instance_id: str = Field(min_length=1, max_length=128)
    boot_id: str = Field(min_length=1, max_length=128)
    client_nonce: str = Field(min_length=22, max_length=171)
    client_time: datetime
    claimed_capability_level: Literal[
        "DD-C0",
        "DD-C1",
        "DD-C2",
        "DD-C3",
    ] = "DD-C0"
    config_fingerprint: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )

    @field_validator(
        "adapter_id",
        "adapter_version",
        "source_schema",
        "source_schema_version",
        "instance_id",
        "boot_id",
    )
    @classmethod
    def _safe_names(cls, value: str) -> str:
        stripped = value.strip()
        if _SAFE_NAME.fullmatch(stripped) is None:
            raise ValueError("value contains unsupported characters")
        return stripped

    @field_validator("capabilities")
    @classmethod
    def _capabilities(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("capabilities must be unique")
        if any(_CAPABILITY.fullmatch(item) is None for item in value):
            raise ValueError("capability name is invalid")
        return sorted(value)

    @field_validator("client_nonce")
    @classmethod
    def _nonce(cls, value: str) -> str:
        if _NONCE.fullmatch(value) is None:
            raise ValueError("client_nonce must be base64url")
        try:
            raw = base64.urlsafe_b64decode(
                value + "=" * (-len(value) % 4)
            )
        except ValueError as exc:
            raise ValueError("client_nonce must be base64url") from exc
        if len(raw) < 16:
            raise ValueError("client_nonce must contain at least 128 bits")
        return value

    @field_validator("client_time")
    @classmethod
    def _aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("client_time must include a timezone")
        return value

    @field_validator("config_fingerprint")
    @classmethod
    def _fingerprint(cls, value: str | None) -> str | None:
        if value is not None and _SHA256.fullmatch(value) is None:
            raise ValueError(
                "config_fingerprint must be lowercase SHA-256"
            )
        return value


class AdapterHandshakeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    handshake_id: str
    protocol_version: Literal["1.0"]
    runtime_public_id: str
    profile: AdapterProfile
    accepted_capabilities: list[str]
    rejected_capabilities: list[str]
    claimed_capability_level: str
    certified_capability_level: str
    content_capture_mode: Literal["metadata_only"]
    clock_skew_degraded: bool
    limits: dict[str, int]
    idempotency: dict[str, int | str]
    server_time: datetime
    expires_at: datetime


class AdapterHeartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    handshake_id: str = Field(
        min_length=35,
        max_length=35,
        pattern=r"^hs_[0-9a-f]{32}$",
    )
    boot_id: str = Field(min_length=1, max_length=128)
    status: Literal["ok", "degraded", "error"] = "ok"
    accepted_capabilities: list[str] = Field(
        min_length=0,
        max_length=16,
    )
    config_fingerprint: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )
    collector_status: Literal[
        "healthy",
        "degraded",
        "unavailable",
        "not_applicable",
    ] | None = None
    collector_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
    )

    @field_validator("boot_id", "collector_version")
    @classmethod
    def _safe_names(cls, value: str | None) -> str | None:
        if value is not None and _SAFE_NAME.fullmatch(value) is None:
            raise ValueError("value contains unsupported characters")
        return value

    @field_validator("accepted_capabilities")
    @classmethod
    def _capabilities(cls, value: list[str]) -> list[str]:
        return AdapterDescriptor._capabilities(value)

    @field_validator("config_fingerprint")
    @classmethod
    def _fingerprint(cls, value: str | None) -> str | None:
        return AdapterDescriptor._fingerprint(value)


class AdapterHeartbeatOut(BaseModel):
    handshake_id: str
    status: Literal["ACTIVE", "DEGRADED", "EXPIRED"]
    config_drift: AdapterConfigDrift
    rehandshake_required: bool
    last_heartbeat_at: datetime
    expires_at: datetime


class FleetHeartbeatOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    config_drift: AdapterConfigDrift
    collector_status: str | None
    collector_version: str | None
    observed_at: datetime


class FleetRuntimeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_public_id: str
    provider: RuntimeProvider
    name: str
    runtime_status: RuntimeStatus
    profile: AdapterProfile | None
    adapter_id: str | None
    adapter_version: str | None
    certified_capability_level: str | None
    accepted_capabilities: list[str]
    rejected_capabilities: list[str]
    handshake_status: Literal[
        "ACTIVE",
        "DEGRADED",
        "EXPIRED",
        "SUPERSEDED",
        "NONE",
    ]
    heartbeat_state: Literal[
        "HEALTHY",
        "STALE",
        "NEVER",
        "ERROR",
    ]
    last_heartbeat_at: datetime | None
    handshake_expires_at: datetime | None
    config_drift: AdapterConfigDrift | None
    collector_status: str | None
    collector_version: str | None
    heartbeat_history: list[FleetHeartbeatOut]
    namespace_telemetry_sink_count: int
    namespace_active_telemetry_sink_count: int
    latest_run_at: datetime | None
    pending_pack_import_count: int
    quarantined_item_count: int


class FleetSummaryOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int
    runtime_count: int
    healthy_count: int
    stale_count: int
    degraded_count: int
    drifted_count: int
    runtimes: list[FleetRuntimeOut]
