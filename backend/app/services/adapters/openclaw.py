from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from io import BytesIO
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
from app.services.adapters.base import BaseRuntimeAdapter
from app.services.adapters.contracts import (
    AdapterCapabilities,
    AdapterCollectionResult,
    AdapterConnectionResult,
    NormalizedAsset,
    NormalizedEvidence,
    NormalizedPrincipal,
    NormalizedWorkTrace,
    RawAdapterRecord,
)


class OpenClawAdapter(BaseRuntimeAdapter):
    adapter_name = "openclaw"

    async def test_connection(self) -> AdapterConnectionResult:
        metadata = self.runtime.metadata_json or {}
        version = str(metadata.get("version")) if metadata.get("version") else None
        return AdapterConnectionResult(
            status="ok",
            message="OpenClaw adapter is available. Live API handshake can be enabled after credentials are configured.",
            version=version,
            details={"base_url": self.runtime.base_url, "credential_ref": self.runtime.credential_ref},
        )

    async def list_capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            provider=RuntimeProvider.OPENCLAW,
            adapter_name=self.adapter_name,
            asset_sync=True,
            principal_sync=True,
            worktrace_sync=True,
            artifact_sync=True,
            backup_import=True,
            backup_create=True,
            restore="manual",
            browser_fallback=False,
            version=(self.runtime.metadata_json or {}).get("version"),
        )

    async def collect(self, job) -> AdapterCollectionResult:
        metadata = self.runtime.metadata_json or {}
        payload = metadata.get("sample_collection") or metadata.get("backup_payload") or {}
        result = normalize_openclaw_payload(
            payload=payload,
            adapter_name=self.adapter_name,
            source="runtime_metadata",
        )
        result.capabilities = await self.list_capabilities()
        return result


def parse_openclaw_backup_bytes(content: bytes, filename: str | None = None) -> dict[str, Any]:
    lower = (filename or "").lower()
    if lower.endswith(".zip") or _looks_like_zip(content):
        return _parse_zip_backup(content)
    text = content.decode("utf-8-sig")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("OpenClaw backup JSON root must be an object")
    return payload


def normalize_openclaw_payload(
    payload: dict[str, Any],
    adapter_name: str = "openclaw",
    source: str = "backup_package",
) -> AdapterCollectionResult:
    capabilities = AdapterCapabilities(
        provider=RuntimeProvider.OPENCLAW,
        adapter_name=adapter_name,
        asset_sync=True,
        principal_sync=True,
        worktrace_sync=True,
        artifact_sync=True,
        backup_import=True,
        backup_create=True,
        restore="manual",
        browser_fallback=False,
        version=_string_or_none(payload.get("version") or payload.get("openclaw_version")),
    )
    result = AdapterCollectionResult(
        adapter_name=adapter_name,
        provider=RuntimeProvider.OPENCLAW,
        capabilities=capabilities,
    )
    result.raw_records.append(
        RawAdapterRecord(
            stream=RawRecordStream.BACKUP_MANIFEST,
            external_id=_string_or_none(payload.get("id") or payload.get("backup_id") or payload.get("export_id")),
            payload={"source": source, "keys": sorted(payload.keys()), "version": capabilities.version},
            normalized_type="backup_manifest",
        )
    )

    for item in _items(payload, "principals", "users", "members", "accounts"):
        principal = _principal_from_item(item)
        result.principals.append(principal)
        result.raw_records.append(
            RawAdapterRecord(
                stream=RawRecordStream.PRINCIPAL,
                external_id=principal.external_id,
                payload=item,
                normalized_type="provider_principal",
            )
        )

    for item in _items(payload, "assets", "skills", "agents", "prompts", "workflows", "tools", "knowledge_bases"):
        asset = _asset_from_item(item)
        result.assets.append(asset)
        result.raw_records.append(
            RawAdapterRecord(
                stream=RawRecordStream.ASSET,
                external_id=asset.external_id,
                payload=item,
                normalized_type="ai_asset",
            )
        )

    for item in _items(payload, "work_traces", "worktraces", "sessions", "runs", "task_runs"):
        trace = _work_trace_from_item(item)
        result.work_traces.append(trace)
        result.raw_records.append(
            RawAdapterRecord(
                stream=RawRecordStream.WORKTRACE,
                external_id=trace.external_session_id,
                payload=item,
                normalized_type="work_trace",
            )
        )

    for item in _items(payload, "evidence", "evidence_items", "audit_logs"):
        evidence = _evidence_from_item(item)
        result.evidence.append(evidence)
        result.raw_records.append(
            RawAdapterRecord(
                stream=RawRecordStream.EVIDENCE,
                external_id=_string_or_none(item.get("id") or item.get("external_id") or item.get("sha256")),
                payload=item,
                normalized_type="evidence_item",
            )
        )

    collected_at = datetime.now(timezone.utc).isoformat()
    result.cursor_updates = {
        RawRecordStream.PRINCIPAL: {"last_collected_at": collected_at, "count": len(result.principals)},
        RawRecordStream.ASSET: {"last_collected_at": collected_at, "count": len(result.assets)},
        RawRecordStream.WORKTRACE: {"last_collected_at": collected_at, "count": len(result.work_traces)},
        RawRecordStream.EVIDENCE: {"last_collected_at": collected_at, "count": len(result.evidence)},
    }
    return result


def _parse_zip_backup(content: bytes) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    with zipfile.ZipFile(BytesIO(content)) as archive:
        for name in archive.namelist():
            lower = name.lower()
            if lower.endswith("/") or not lower.endswith(".json"):
                continue
            with archive.open(name) as fp:
                data = json.loads(fp.read().decode("utf-8-sig"))
            key = lower.rsplit("/", 1)[-1].replace(".json", "")
            if key in {"manifest", "backup", "export"} and isinstance(data, dict):
                payload.update(data)
            else:
                payload[_normalize_key(key)] = data
    if not payload:
        raise ValueError("OpenClaw backup zip does not contain JSON records")
    return payload


def _looks_like_zip(content: bytes) -> bool:
    return len(content) >= 4 and content[:4] == b"PK\x03\x04"


def _items(payload: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, dict) and isinstance(value.get("items"), list):
            value = value["items"]
        if isinstance(value, list):
            rows.extend([item for item in value if isinstance(item, dict)])
    return rows


def _principal_from_item(item: dict[str, Any]) -> NormalizedPrincipal:
    external_id = _required_external_id(item, "principal")
    principal_type = _enum_value(
        ProviderPrincipalType,
        item.get("principal_type") or item.get("type"),
        ProviderPrincipalType.USER,
    )
    return NormalizedPrincipal(
        external_id=external_id,
        principal_type=principal_type,
        display_name=_string_or_none(item.get("display_name") or item.get("name") or item.get("full_name")),
        username=_string_or_none(item.get("username") or item.get("login")),
        email=_string_or_none(item.get("email") or item.get("mail")),
        metadata=_metadata(item, {"id", "external_id", "principal_id", "type", "principal_type", "display_name", "name", "full_name", "username", "login", "email", "mail"}),
    )


def _asset_from_item(item: dict[str, Any]) -> NormalizedAsset:
    asset_type = _enum_value(AssetType, item.get("asset_type") or item.get("type"), _infer_asset_type(item))
    status = _enum_value(AssetStatus, item.get("status"), AssetStatus.ACTIVE)
    criticality = _enum_value(Criticality, item.get("criticality") or item.get("risk_level"), Criticality.MEDIUM)
    name = _string_or_none(item.get("name") or item.get("title") or item.get("display_name"))
    if not name:
        name = f"{asset_type.value}-{item.get('id') or item.get('external_id') or 'unnamed'}"
    return NormalizedAsset(
        external_id=_string_or_none(item.get("external_id") or item.get("id") or item.get("asset_id") or item.get("skill_id")),
        asset_type=asset_type,
        name=name,
        description=_string_or_none(item.get("description") or item.get("summary")),
        status=status,
        criticality=criticality,
        metadata=_metadata(item, {"id", "external_id", "asset_id", "skill_id", "asset_type", "type", "name", "title", "display_name", "description", "summary", "status", "criticality", "risk_level"}),
        content_hash=_string_or_none(item.get("content_hash") or item.get("sha256")),
        owner_external_id=_string_or_none(item.get("owner_external_id") or item.get("created_by") or item.get("creator_id") or item.get("owner_id")),
    )


def _work_trace_from_item(item: dict[str, Any]) -> NormalizedWorkTrace:
    trace_type = _enum_value(TraceType, item.get("trace_type") or item.get("type"), TraceType.SESSION)
    sensitivity = _enum_value(Sensitivity, item.get("sensitivity"), Sensitivity.INTERNAL)
    title = _string_or_none(item.get("title") or item.get("name") or item.get("summary")) or "Untitled OpenClaw session"
    return NormalizedWorkTrace(
        external_session_id=_string_or_none(item.get("external_session_id") or item.get("session_id") or item.get("run_id") or item.get("id")),
        asset_external_id=_string_or_none(item.get("asset_external_id") or item.get("asset_id") or item.get("skill_id")),
        actor_external_id=_string_or_none(item.get("actor_external_id") or item.get("user_id") or item.get("created_by")),
        title=title,
        summary=_string_or_none(item.get("summary") or item.get("description")),
        trace_type=trace_type,
        started_at=_parse_datetime(item.get("started_at") or item.get("created_at")),
        ended_at=_parse_datetime(item.get("ended_at") or item.get("finished_at")),
        sensitivity=sensitivity,
        metadata=_metadata(item, {"external_session_id", "session_id", "run_id", "id", "asset_external_id", "asset_id", "skill_id", "actor_external_id", "user_id", "created_by", "title", "name", "summary", "description", "trace_type", "type", "sensitivity", "started_at", "created_at", "ended_at", "finished_at"}),
    )


def _evidence_from_item(item: dict[str, Any]) -> NormalizedEvidence:
    source_type = _enum_value(EvidenceSourceType, item.get("source_type"), EvidenceSourceType.BACKUP_PACKAGE)
    visibility = _enum_value(EvidenceVisibility, item.get("visibility"), EvidenceVisibility.NORMAL)
    summary = _string_or_none(item.get("summary") or item.get("message") or item.get("title")) or "OpenClaw backup evidence"
    return NormalizedEvidence(
        summary=summary,
        source_type=source_type,
        object_uri=_string_or_none(item.get("object_uri") or item.get("uri") or item.get("url")),
        sha256=_string_or_none(item.get("sha256")),
        confidence=float(item.get("confidence", 1.0)),
        visibility=visibility,
    )


def _infer_asset_type(item: dict[str, Any]) -> AssetType:
    if "skill_id" in item or item.get("kind") == "skill":
        return AssetType.SKILL
    if "agent_id" in item or item.get("kind") == "agent":
        return AssetType.AGENT
    return AssetType.OTHER


def _required_external_id(item: dict[str, Any], label: str) -> str:
    value = _string_or_none(item.get("external_id") or item.get("id") or item.get(f"{label}_id"))
    if not value:
        raise ValueError(f"{label} record requires id or external_id")
    return value


def _enum_value(enum_cls, value: Any, default):
    if value is None:
        return default
    text = str(value).lower()
    for option in enum_cls:
        if option.value == text or option.name.lower() == text:
            return option
    return default


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _metadata(item: dict[str, Any], excluded: set[str]) -> dict[str, Any] | None:
    metadata = {key: value for key, value in item.items() if key not in excluded}
    return metadata or None


def _parse_datetime(value: Any) -> datetime | None:
    text = _string_or_none(value)
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _normalize_key(key: str) -> str:
    mapping = {
        "skill": "skills",
        "agent": "agents",
        "prompt": "prompts",
        "workflow": "workflows",
        "session": "sessions",
        "run": "runs",
        "user": "users",
        "principal": "principals",
    }
    return mapping.get(key, key)
