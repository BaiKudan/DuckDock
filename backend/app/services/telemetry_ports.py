from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol


class TelemetryPortUnavailable(RuntimeError):
    pass


class TrajectoryCodecUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TraceConfirmationRequest:
    run_public_id: str
    source_trace_id: str
    source_session_id: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TraceConfirmation:
    confirmed: bool
    external_trace_id: str
    external_session_id: str | None = None
    trace_url: str | None = None
    error_code: str | None = None


class TelemetrySinkPort(Protocol):
    async def confirm_trace(
        self,
        request: TraceConfirmationRequest,
    ) -> TraceConfirmation: ...


class TrajectoryCodecPort(Protocol):
    def encode(self, document: Mapping[str, Any]) -> bytes: ...

    def decode(self, payload: bytes) -> dict[str, Any]: ...


class AttestationVerifierPort(Protocol):
    async def verify(self, proof: dict[str, Any]) -> bool: ...


class NullTelemetrySinkPort:
    async def confirm_trace(
        self,
        request: TraceConfirmationRequest,
    ) -> TraceConfirmation:
        return TraceConfirmation(
            confirmed=False,
            external_trace_id=request.source_trace_id,
            error_code="telemetry_sink_not_configured",
        )


class NullTrajectoryCodec:
    def encode(self, document: Mapping[str, Any]) -> bytes:
        raise TrajectoryCodecUnavailable("trajectory codec is not configured")

    def decode(self, payload: bytes) -> dict[str, Any]:
        raise TrajectoryCodecUnavailable("trajectory codec is not configured")


class NullAttestationVerifier:
    async def verify(self, proof: dict[str, Any]) -> bool:
        return False
