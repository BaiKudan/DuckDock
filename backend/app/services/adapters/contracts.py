from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.models.control_plane import (
    AssetStatus,
    AssetType,
    Criticality,
    EvidenceSourceType,
    EvidenceVisibility,
    ProviderPrincipalType,
    RawRecordStream,
    RuntimeProvider,
    Sensitivity,
    TraceType,
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class AdapterConnectionResult:
    status: str
    message: str
    version: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AdapterCapabilities:
    provider: RuntimeProvider
    adapter_name: str
    asset_sync: bool | str
    principal_sync: bool | str
    worktrace_sync: bool | str
    artifact_sync: bool | str
    backup_import: bool | str
    backup_create: bool | str
    restore: bool | str
    browser_fallback: bool | str
    version: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "provider": self.provider.value,
            "adapter_name": self.adapter_name,
            "asset_sync": self.asset_sync,
            "principal_sync": self.principal_sync,
            "worktrace_sync": self.worktrace_sync,
            "artifact_sync": self.artifact_sync,
            "backup_import": self.backup_import,
            "backup_create": self.backup_create,
            "restore": self.restore,
            "browser_fallback": self.browser_fallback,
            "version": self.version,
        }


@dataclass(slots=True)
class RawAdapterRecord:
    stream: RawRecordStream
    payload: dict[str, Any]
    external_id: str | None = None
    normalized_type: str | None = None


@dataclass(slots=True)
class NormalizedPrincipal:
    external_id: str
    principal_type: ProviderPrincipalType = ProviderPrincipalType.USER
    display_name: str | None = None
    username: str | None = None
    email: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class NormalizedAsset:
    external_id: str | None
    asset_type: AssetType
    name: str
    description: str | None = None
    status: AssetStatus = AssetStatus.ACTIVE
    criticality: Criticality = Criticality.MEDIUM
    metadata: dict[str, Any] | None = None
    content_hash: str | None = None
    owner_external_id: str | None = None


@dataclass(slots=True)
class NormalizedWorkTrace:
    title: str
    external_session_id: str | None = None
    asset_external_id: str | None = None
    actor_external_id: str | None = None
    summary: str | None = None
    trace_type: TraceType = TraceType.SESSION
    started_at: datetime | None = None
    ended_at: datetime | None = None
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class NormalizedEvidence:
    summary: str
    source_type: EvidenceSourceType = EvidenceSourceType.API
    work_trace_external_session_id: str | None = None
    object_uri: str | None = None
    sha256: str | None = None
    confidence: float = 1.0
    visibility: EvidenceVisibility = EvidenceVisibility.NORMAL


@dataclass(slots=True)
class AdapterCollectionResult:
    adapter_name: str
    provider: RuntimeProvider
    capabilities: AdapterCapabilities
    raw_records: list[RawAdapterRecord] = field(default_factory=list)
    principals: list[NormalizedPrincipal] = field(default_factory=list)
    assets: list[NormalizedAsset] = field(default_factory=list)
    work_traces: list[NormalizedWorkTrace] = field(default_factory=list)
    evidence: list[NormalizedEvidence] = field(default_factory=list)
    cursor_updates: dict[RawRecordStream, dict[str, Any]] = field(default_factory=dict)
