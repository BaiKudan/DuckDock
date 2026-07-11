from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any

from app.models.control_plane import (
    AssetStatus,
    AssetType,
    Criticality,
    EvidenceSourceType,
    EvidenceVisibility,
    RawRecordStream,
    RuntimeInstance,
    Sensitivity,
    TraceType,
)
from app.services.adapters.contracts import (
    AdapterCapabilities,
    AdapterCollectionResult,
    NormalizedAsset,
    NormalizedEvidence,
    NormalizedPrincipal,
    NormalizedWorkTrace,
    RawAdapterRecord,
)


SUPPORTED_SCHEMA_VERSIONS = {"duckdock-pack-v1", "duckdock.report.v1"}


class ReportPackError(ValueError):
    pass


def normalize_duckdock_report_pack(
    *,
    content: bytes,
    runtime: RuntimeInstance,
    object_uri: str,
) -> tuple[dict[str, Any], AdapterCollectionResult]:
    files = _read_zip_files(content)
    manifest = _load_required_json(files, "manifest.json")
    schema_version = _string_or_none(manifest.get("schema_version")) or "duckdock-pack-v1"
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ReportPackError(f"Unsupported DuckDock report schema '{schema_version}'")

    capabilities = AdapterCapabilities(
        provider=runtime.provider,
        adapter_name="duckdock_reporter",
        asset_sync=True,
        principal_sync=True,
        worktrace_sync=True,
        artifact_sync=True,
        backup_import=True,
        backup_create=False,
        restore=False,
        browser_fallback=False,
        version=schema_version,
    )
    result = AdapterCollectionResult(
        adapter_name="duckdock_reporter",
        provider=runtime.provider,
        capabilities=capabilities,
    )
    result.raw_records.append(
        RawAdapterRecord(
            stream=RawRecordStream.BACKUP_MANIFEST,
            external_id=_string_or_none(manifest.get("report_id") or manifest.get("id")),
            payload={"object_uri": object_uri, **manifest},
            normalized_type="report_manifest",
        )
    )

    runtime_json = _load_optional_json(files, "runtime.json")
    if runtime_json is not None:
        result.raw_records.append(
            RawAdapterRecord(
                stream=RawRecordStream.CAPABILITY,
                external_id=_string_or_none(runtime_json.get("runtime_id") or runtime_json.get("id")),
                payload=runtime_json,
                normalized_type="runtime_metadata",
            )
        )

    for path, rows in _inventory_rows(files):
        kind = PurePosixPath(path).stem.lower()
        if kind in {"principals", "users", "members", "accounts"}:
            for row in rows:
                principal = _principal_from_item(row)
                result.principals.append(principal)
                result.raw_records.append(
                    RawAdapterRecord(
                        stream=RawRecordStream.PRINCIPAL,
                        external_id=principal.external_id,
                        payload=_with_pack_path(row, path),
                        normalized_type="provider_principal",
                    )
                )
        elif kind in {"sessions", "work_traces", "worktraces", "runs", "task_runs"}:
            for row in rows:
                trace = _work_trace_from_item(row)
                result.work_traces.append(trace)
                result.raw_records.append(
                    RawAdapterRecord(
                        stream=RawRecordStream.WORKTRACE,
                        external_id=trace.external_session_id,
                        payload=_with_pack_path(row, path),
                        normalized_type="work_trace",
                    )
                )
        elif kind in {"artifacts", "evidence", "evidence_items", "redaction"}:
            for row in rows:
                evidence = _evidence_from_item(row, default_object_uri=f"{object_uri}#{path}")
                result.evidence.append(evidence)
                result.raw_records.append(
                    RawAdapterRecord(
                        stream=RawRecordStream.EVIDENCE,
                        external_id=_string_or_none(row.get("id") or row.get("external_id") or row.get("sha256")),
                        payload=_with_pack_path(row, path),
                        normalized_type="evidence_item",
                    )
                )
        else:
            asset_type = _asset_type_for_inventory(kind)
            for row in rows:
                asset = _asset_from_item(row, asset_type=asset_type)
                result.assets.append(asset)
                result.raw_records.append(
                    RawAdapterRecord(
                        stream=RawRecordStream.ASSET,
                        external_id=asset.external_id,
                        payload=_with_pack_path(row, path),
                        normalized_type="ai_asset",
                    )
                )

    for path, data in sorted(files.items()):
        lower = path.lower()
        if not lower.startswith("summaries/") or not lower.endswith(".md"):
            continue
        text = data.decode("utf-8-sig", errors="replace")
        digest = hashlib.sha256(data).hexdigest()
        title = _first_markdown_heading(text) or PurePosixPath(path).name
        evidence = NormalizedEvidence(
            summary=f"DuckDock report summary: {title}",
            source_type=EvidenceSourceType.BACKUP_PACKAGE,
            object_uri=f"{object_uri}#{path}",
            sha256=digest,
            confidence=1.0,
            visibility=EvidenceVisibility.NORMAL,
        )
        result.evidence.append(evidence)
        result.raw_records.append(
            RawAdapterRecord(
                stream=RawRecordStream.EVIDENCE,
                external_id=digest,
                payload={"pack_path": path, "title": title, "sha256": digest},
                normalized_type="summary_markdown",
            )
        )

    collected_at = datetime.now(timezone.utc).isoformat()
    result.cursor_updates = {
        RawRecordStream.PRINCIPAL: {"last_collected_at": collected_at, "count": len(result.principals)},
        RawRecordStream.ASSET: {"last_collected_at": collected_at, "count": len(result.assets)},
        RawRecordStream.WORKTRACE: {"last_collected_at": collected_at, "count": len(result.work_traces)},
        RawRecordStream.EVIDENCE: {"last_collected_at": collected_at, "count": len(result.evidence)},
    }
    return manifest, result


def _read_zip_files(content: bytes) -> dict[str, bytes]:
    if len(content) < 4 or content[:4] != b"PK\x03\x04":
        raise ReportPackError("DuckDock report pack must be a zip file")
    files: dict[str, bytes] = {}
    with zipfile.ZipFile(BytesIO(content)) as archive:
        for name in archive.namelist():
            normalized = str(PurePosixPath(name))
            if normalized.endswith("/") or normalized.startswith("../") or "/../" in normalized:
                continue
            files[normalized] = archive.read(name)
    if not files:
        raise ReportPackError("DuckDock report pack is empty")
    return files


def _load_required_json(files: dict[str, bytes], path: str) -> dict[str, Any]:
    data = _load_optional_json(files, path)
    if data is None:
        raise ReportPackError(f"DuckDock report pack requires {path}")
    return data


def _load_optional_json(files: dict[str, bytes], path: str) -> dict[str, Any] | None:
    payload = files.get(path)
    if payload is None:
        return None
    data = json.loads(payload.decode("utf-8-sig"))
    if not isinstance(data, dict):
        raise ReportPackError(f"{path} must contain a JSON object")
    return data


def _inventory_rows(files: dict[str, bytes]) -> list[tuple[str, list[dict[str, Any]]]]:
    groups: list[tuple[str, list[dict[str, Any]]]] = []
    for path, data in sorted(files.items()):
        lower = path.lower()
        if not lower.startswith("inventory/") and not lower.startswith("evidence/"):
            continue
        if lower.endswith(".ndjson") or lower.endswith(".jsonl"):
            rows = _parse_ndjson(data, path)
        elif lower.endswith(".json") and path not in {"manifest.json", "runtime.json"}:
            rows = _parse_json_rows(data, path)
        else:
            continue
        if rows:
            groups.append((path, rows))
    return groups


def _parse_ndjson(data: bytes, path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(data.decode("utf-8-sig").splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        row = json.loads(text)
        if not isinstance(row, dict):
            raise ReportPackError(f"{path}:{line_no} must contain a JSON object")
        rows.append(row)
    return rows


def _parse_json_rows(data: bytes, path: str) -> list[dict[str, Any]]:
    value = json.loads(data.decode("utf-8-sig"))
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        value = value["items"]
    if not isinstance(value, list):
        raise ReportPackError(f"{path} must contain a JSON array or object with items")
    return [item for item in value if isinstance(item, dict)]


def _principal_from_item(item: dict[str, Any]) -> NormalizedPrincipal:
    external_id = _required_external_id(item, "principal")
    return NormalizedPrincipal(
        external_id=external_id,
        display_name=_string_or_none(item.get("display_name") or item.get("name") or item.get("full_name")),
        username=_string_or_none(item.get("username") or item.get("login")),
        email=_string_or_none(item.get("email") or item.get("mail")),
        metadata=_metadata(item, {"id", "external_id", "principal_id", "display_name", "name", "full_name", "username", "login", "email", "mail"}),
    )


def _asset_from_item(item: dict[str, Any], *, asset_type: AssetType) -> NormalizedAsset:
    inferred_type = _enum_value(AssetType, item.get("asset_type") or item.get("type"), asset_type)
    name = _string_or_none(item.get("name") or item.get("title") or item.get("display_name"))
    if not name:
        name = f"{inferred_type.value}-{item.get('id') or item.get('external_id') or 'unnamed'}"
    return NormalizedAsset(
        external_id=_string_or_none(item.get("external_id") or item.get("id") or item.get("asset_id") or item.get("skill_id")),
        asset_type=inferred_type,
        name=name,
        description=_string_or_none(item.get("description") or item.get("summary")),
        status=_enum_value(AssetStatus, item.get("status"), AssetStatus.ACTIVE),
        criticality=_enum_value(Criticality, item.get("criticality") or item.get("risk_level"), Criticality.MEDIUM),
        metadata=_metadata(item, {"id", "external_id", "asset_id", "skill_id", "asset_type", "type", "name", "title", "display_name", "description", "summary", "status", "criticality", "risk_level"}),
        content_hash=_string_or_none(item.get("content_hash") or item.get("sha256")),
        owner_external_id=_string_or_none(item.get("owner_external_id") or item.get("created_by") or item.get("creator_id") or item.get("owner_id")),
    )


def _work_trace_from_item(item: dict[str, Any]) -> NormalizedWorkTrace:
    return NormalizedWorkTrace(
        external_session_id=_string_or_none(item.get("external_session_id") or item.get("session_id") or item.get("run_id") or item.get("id")),
        asset_external_id=_string_or_none(item.get("asset_external_id") or item.get("asset_id") or item.get("skill_id")),
        actor_external_id=_string_or_none(item.get("actor_external_id") or item.get("user_id") or item.get("created_by")),
        title=_string_or_none(item.get("title") or item.get("name") or item.get("summary")) or "Untitled DuckDock report session",
        summary=_string_or_none(item.get("summary") or item.get("description")),
        trace_type=_enum_value(TraceType, item.get("trace_type") or item.get("type"), TraceType.SESSION),
        started_at=_parse_datetime(item.get("started_at") or item.get("created_at")),
        ended_at=_parse_datetime(item.get("ended_at") or item.get("finished_at")),
        sensitivity=_enum_value(Sensitivity, item.get("sensitivity"), Sensitivity.INTERNAL),
        metadata=_metadata(item, {"external_session_id", "session_id", "run_id", "id", "asset_external_id", "asset_id", "skill_id", "actor_external_id", "user_id", "created_by", "title", "name", "summary", "description", "trace_type", "type", "sensitivity", "started_at", "created_at", "ended_at", "finished_at"}),
    )


def _evidence_from_item(item: dict[str, Any], *, default_object_uri: str) -> NormalizedEvidence:
    return NormalizedEvidence(
        summary=_string_or_none(item.get("summary") or item.get("message") or item.get("title")) or "DuckDock report evidence",
        source_type=_enum_value(EvidenceSourceType, item.get("source_type"), EvidenceSourceType.BACKUP_PACKAGE),
        object_uri=_string_or_none(item.get("object_uri") or item.get("uri") or item.get("url")) or default_object_uri,
        sha256=_string_or_none(item.get("sha256") or item.get("content_hash")),
        confidence=float(item.get("confidence", 1.0)),
        visibility=_enum_value(EvidenceVisibility, item.get("visibility"), EvidenceVisibility.NORMAL),
    )


def _asset_type_for_inventory(kind: str) -> AssetType:
    mapping = {
        "skills": AssetType.SKILL,
        "skill": AssetType.SKILL,
        "agents": AssetType.AGENT,
        "agent": AssetType.AGENT,
        "prompts": AssetType.PROMPT,
        "prompt": AssetType.PROMPT,
        "workflows": AssetType.WORKFLOW,
        "workflow": AssetType.WORKFLOW,
        "tools": AssetType.TOOL,
        "tool": AssetType.TOOL,
        "memories": AssetType.KNOWLEDGE_BASE,
        "memory": AssetType.KNOWLEDGE_BASE,
        "knowledge_bases": AssetType.KNOWLEDGE_BASE,
        "mcp": AssetType.MCP,
        "scheduled_tasks": AssetType.SCHEDULED_TASK,
    }
    return mapping.get(kind, AssetType.OTHER)


def _enum_value(enum_cls, value: Any, default):
    if value is None:
        return default
    text = str(value).lower()
    for option in enum_cls:
        if option.value == text or option.name.lower() == text:
            return option
    return default


def _required_external_id(item: dict[str, Any], label: str) -> str:
    value = _string_or_none(item.get("external_id") or item.get("id") or item.get(f"{label}_id"))
    if not value:
        raise ReportPackError(f"{label} record requires id or external_id")
    return value


def _with_pack_path(item: dict[str, Any], path: str) -> dict[str, Any]:
    return {**item, "_duckdock_pack_path": path}


def _metadata(item: dict[str, Any], excluded: set[str]) -> dict[str, Any] | None:
    metadata = {key: value for key, value in item.items() if key not in excluded}
    return metadata or None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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


def _first_markdown_heading(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or None
    return None
