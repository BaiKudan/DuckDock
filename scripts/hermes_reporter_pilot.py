#!/usr/bin/env python3
"""Hermes-side DuckDock Reporter pilot.

This script is intentionally conservative: daily/weekly runs post a structured
JSON report directly to DuckDock, while explicit handover/audit runs can still
upload a duckdock-pack-v1.zip through the standard upload-session flow. It
never reads Hermes transcripts or prints the Reporter credential.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import socket
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_API_BASE = "http://localhost:8801/api/v1"
DEFAULT_HERMES_BASE = "http://127.0.0.1:50866"
DEFAULT_CONFIG = Path.home() / ".duckdock" / "reporter" / "hermes-pilot.json"
REPORTER_VERSION = "hermes-pilot-0.1.0"
DEFAULT_SCHEDULE = "0 16 * * 5"


class PilotError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_private_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def request_json(
    method: str,
    url: str,
    *,
    token: str | None = None,
    payload: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Accept", "application/json")
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise PilotError(f"{method} {url} failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise PilotError(f"{method} {url} failed: {exc.reason}") from exc
    return json.loads(raw) if raw.strip() else {}


def optional_json(url: str, *, timeout: int = 5) -> dict[str, Any] | list[Any] | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def upload_file(url: str, path: Path, content_type: str) -> None:
    data = path.read_bytes()
    request = urllib.request.Request(url, data=data, method="PUT")
    request.add_header("Content-Type", content_type)
    request.add_header("Content-Length", str(len(data)))
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            if response.status >= 400:
                raise PilotError(f"PUT {url} failed: HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise PilotError(f"PUT upload failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise PilotError(f"PUT upload failed: {exc.reason}") from exc


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def run_command(args: list[str], *, timeout: int = 10) -> tuple[int, str]:
    try:
        completed = subprocess.run(args, check=False, text=True, capture_output=True, timeout=timeout)
    except Exception as exc:
        return 1, str(exc)
    return completed.returncode, (completed.stdout + completed.stderr).strip()


def hermes_public_metadata(hermes_base: str) -> dict[str, Any]:
    model_info = optional_json(f"{hermes_base.rstrip('/')}/api/model/info")
    plugins = optional_json(f"{hermes_base.rstrip('/')}/api/dashboard/plugins")
    openapi = optional_json(f"{hermes_base.rstrip('/')}/openapi.json")
    return {
        "model_info": model_info if isinstance(model_info, dict) else None,
        "dashboard_plugins": plugins if isinstance(plugins, list) else [],
        "openapi_title": (openapi or {}).get("info", {}).get("title") if isinstance(openapi, dict) else None,
        "openapi_version": (openapi or {}).get("info", {}).get("version") if isinstance(openapi, dict) else None,
        "public_endpoint_status": {
            "model_info": isinstance(model_info, dict),
            "dashboard_plugins": isinstance(plugins, list),
            "openapi": isinstance(openapi, dict),
        },
    }


def hermes_cron_summary(hermes_cli: str | None) -> dict[str, Any]:
    if not hermes_cli:
        default_cli = Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "python"
        default_module = Path.home() / ".hermes" / "hermes-agent" / "hermes_cli" / "main.py"
        if default_cli.exists() and default_module.exists():
            args = [str(default_cli), "-m", "hermes_cli.main", "cron", "list"]
        else:
            return {"available": False, "output": "Hermes CLI not found"}
    else:
        args = hermes_cli.split() + ["cron", "list"]
    code, output = run_command(args)
    # Do not include arbitrary long output. The cron list command does not print
    # credentials, but keep the report bounded anyway.
    return {"available": code == 0, "output": output[:2000]}


def build_pack(
    *,
    output_dir: Path,
    config: dict[str, Any],
    hermes_base: str,
    report_type: str,
    period_start: str,
    period_end: str,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    public = hermes_public_metadata(hermes_base)
    cron = hermes_cron_summary(config.get("hermes_cli"))
    hostname_hash = stable_hash(socket.gethostname())
    username = config.get("owner_username") or os.environ.get("USER") or "unknown"
    user_hash = stable_hash(username)
    runtime_external_id = f"hermes-{hostname_hash}"
    runtime_id = int(config["runtime_id"])
    generated_at = iso_now()

    manifest = {
        "schema_version": "duckdock-pack-v1",
        "generated_at": generated_at,
        "runtime_id": runtime_external_id,
        "duckdock_runtime_id": runtime_id,
        "provider": "custom",
        "agent_kind": "hermes",
        "report_type": report_type,
        "period_start": period_start,
        "period_end": period_end,
        "actor_username": username,
        "collector": {"name": "duckdock_hermes_reporter_pilot", "version": REPORTER_VERSION, "mode": "summary_index"},
    }
    runtime = {
        "runtime_id": runtime_external_id,
        "duckdock_runtime_id": runtime_id,
        "provider": "custom",
        "agent_kind": "hermes",
        "name": config.get("runtime_name") or "Hermes Local Reporter Pilot",
        "deploy_type": "desktop",
        "host": {
            "os": platform.system().lower(),
            "machine": platform.machine(),
            "hostname_hash": hostname_hash,
            "username_hash": user_hash,
        },
        "app": {
            "name": public.get("openapi_title") or "Hermes Agent",
            "version": public.get("openapi_version"),
            "api_base": hermes_base,
        },
        "model": sanitize_model_info(public.get("model_info")),
        "public_endpoint_status": public.get("public_endpoint_status"),
        "collection_policy": {"include_raw": False, "respect_duckdockignore": True, "redaction": True},
    }

    principal_id = f"duckdock-user-{username}"
    principals = [
        {
            "id": principal_id,
            "type": "user",
            "display_name": username,
            "username": username,
        }
    ]
    agents = [
        {
            "id": f"{runtime_external_id}/agent/hermes",
            "asset_type": "agent",
            "name": "Hermes Agent Local Runtime",
            "description": "Local Hermes Agent desktop/runtime endpoint detected through the local OpenAPI server.",
            "owner_external_id": principal_id,
            "owner_username": username,
            "status": "active",
            "criticality": "high",
            "metadata": runtime,
        }
    ]
    tools = [
        {
            "id": f"{runtime_external_id}/tool/hermes-gateway",
            "asset_type": "tool",
            "name": "Hermes Gateway Service",
            "description": "Hermes local gateway process is available for runtime-side automation.",
            "owner_external_id": principal_id,
            "owner_username": username,
            "status": "active",
            "criticality": "medium",
            "metadata": {"cron_summary": cron},
        }
    ]
    for plugin in public.get("dashboard_plugins") or []:
        name = str(plugin.get("name") or "plugin")
        tools.append(
            {
                "id": f"{runtime_external_id}/plugin/{name}",
                "asset_type": "tool",
                "name": f"Hermes plugin: {name}",
                "description": str(plugin.get("description") or "")[:500],
                "owner_external_id": principal_id,
                "owner_username": username,
                "status": "active",
                "criticality": "medium",
                "metadata": {
                    "label": plugin.get("label"),
                    "version": plugin.get("version"),
                    "source": plugin.get("source"),
                    "has_api": plugin.get("has_api"),
                },
            }
        )

    scheduled_tasks = [
        {
            "id": f"{runtime_external_id}/scheduled-task/duckdock-reporter",
            "asset_type": "scheduled_task",
            "name": "DuckDock Hermes Reporter scheduled pilot",
            "description": "Hermes cron job or equivalent scheduler invokes the DuckDock Reporter pilot script.",
            "owner_external_id": principal_id,
            "owner_username": username,
            "status": "active" if config.get("hermes_cron_job_id") else "inactive",
            "criticality": "high",
            "schedule": config.get("schedule") or DEFAULT_SCHEDULE,
            "metadata": {
                "hermes_cron_job_id": config.get("hermes_cron_job_id"),
                "cron_list": cron,
            },
        }
    ]

    session_id = f"hermes-reporter-pilot-{utc_now().strftime('%Y%m%d%H%M%S')}"
    sessions = [
        {
            "id": session_id,
            "trace_type": "task_run",
            "title": "Hermes Reporter pilot upload",
            "summary": (
                "Local Hermes runtime produced a privacy-preserving DuckDock Pack, "
                "sent heartbeat, uploaded through a presigned session, and requested analysis."
            ),
            "asset_id": f"{runtime_external_id}/agent/hermes",
            "asset_external_id": f"{runtime_external_id}/agent/hermes",
            "actor_external_id": principal_id,
            "created_by": principal_id,
            "owner_username": username,
            "started_at": period_start,
            "ended_at": generated_at,
            "sensitivity": "internal",
        }
    ]
    evidence_rows = [
        {
            "id": f"{session_id}/hermes-openapi",
            "source_type": "backup_package",
            "summary": "Hermes local OpenAPI endpoint responded during Reporter pilot collection.",
            "object_uri": "duckdock-pack-v1.zip#runtime.json",
            "confidence": 1.0 if public["public_endpoint_status"]["openapi"] else 0.5,
            "visibility": "normal",
        },
        {
            "id": f"{session_id}/cron",
            "source_type": "backup_package",
            "summary": "Hermes cron status was sampled during Reporter pilot collection.",
            "object_uri": "duckdock-pack-v1.zip#inventory/scheduled_tasks.ndjson",
            "confidence": 0.8 if cron.get("available") else 0.4,
            "visibility": "normal",
        },
    ]
    weekly_report = (
        "# Hermes Reporter Pilot\n\n"
        f"- Generated at: {generated_at}\n"
        f"- DuckDock runtime id: {runtime_id}\n"
        f"- Hermes API: {hermes_base}\n"
        f"- Hermes version: {runtime['app'].get('version') or 'unknown'}\n"
        f"- Model provider: {(runtime.get('model') or {}).get('provider') or 'unknown'}\n"
        f"- Dashboard plugin count: {len(public.get('dashboard_plugins') or [])}\n"
        f"- Cron job id: {config.get('hermes_cron_job_id') or 'not recorded'}\n\n"
        "No raw conversations, local secrets, cookies, private keys, or arbitrary files were collected.\n"
    )

    files: dict[str, bytes] = {
        "manifest.json": json_bytes(manifest),
        "runtime.json": json_bytes(runtime),
        "inventory/principals.ndjson": ndjson_bytes(principals),
        "inventory/agents.ndjson": ndjson_bytes(agents),
        "inventory/tools.ndjson": ndjson_bytes(tools),
        "inventory/scheduled_tasks.ndjson": ndjson_bytes(scheduled_tasks),
        "inventory/sessions.ndjson": ndjson_bytes(sessions),
        "inventory/evidence.ndjson": ndjson_bytes(evidence_rows),
        "summaries/weekly-report.md": weekly_report.encode("utf-8"),
        "evidence/redaction-report.json": json_bytes(
            {
                "enabled": True,
                "rules_version": "duckdock-redaction-v1",
                "redacted_items": [],
                "notes": ["Summary/index collection only. No credential fields are included in the pack."],
            }
        ),
    }
    sha_lines = []
    for name, data in sorted(files.items()):
        sha_lines.append(f"{sha256_bytes(data)}  {name}")
    files["evidence/sha256sums.txt"] = ("\n".join(sha_lines) + "\n").encode("utf-8")

    pack_path = output_dir / "duckdock-pack-v1.zip"
    with zipfile.ZipFile(pack_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
    return pack_path


def sanitize_model_info(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    allowed = {
        "model": value.get("model"),
        "provider": value.get("provider"),
        "effective_context_length": value.get("effective_context_length"),
    }
    capabilities = value.get("capabilities")
    if isinstance(capabilities, dict):
        allowed["capabilities"] = {
            key: capabilities.get(key)
            for key in ("supports_tools", "supports_vision", "supports_reasoning", "context_window")
            if key in capabilities
        }
    return allowed


def json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def ndjson_bytes(rows: list[dict[str, Any]]) -> bytes:
    return "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows).encode("utf-8")


def enroll(args: argparse.Namespace) -> dict[str, Any]:
    api_base = args.api_base.rstrip("/")
    username = args.username or os.environ.get("DUCKDOCK_USERNAME")
    password = args.password or os.environ.get("DUCKDOCK_PASSWORD")
    if not username or not password:
        raise PilotError("DuckDock username/password are required for enroll")
    token_response = request_json(
        "POST",
        f"{api_base}/auth/login",
        payload={"username": username, "password": password},
    )
    access_token = token_response["access_token"]
    hermes_metadata = hermes_public_metadata(args.hermes_base)
    body = {
        "provider": "custom",
        "runtime_name": args.runtime_name,
        "device_id": args.device_id,
        "agent_kind": "hermes",
        "reporter_version": REPORTER_VERSION,
        "schedule_json": {"type": "hermes_cron", "schedule": args.schedule, "timezone": args.timezone},
        "metadata_json": {
            "agent_kind": "hermes",
            "hermes_base_url": args.hermes_base,
            "detected": {
                "openapi_title": hermes_metadata.get("openapi_title"),
                "openapi_version": hermes_metadata.get("openapi_version"),
                "model": sanitize_model_info(hermes_metadata.get("model_info")),
                "dashboard_plugin_count": len(hermes_metadata.get("dashboard_plugins") or []),
            },
        },
    }
    enrolled = request_json("POST", f"{api_base}/reporters/enroll", token=access_token, payload=body)
    runtime = enrolled["runtime"]
    credential = enrolled["credential"]
    config = {
        "api_base": api_base,
        "hermes_base": args.hermes_base,
        "runtime_id": runtime["id"],
        "runtime_name": runtime["name"],
        "credential_id": credential["id"],
        "token_prefix": credential["token_prefix"],
        "reporter_token": credential["token"],
        "device_id": args.device_id,
        "owner_username": username,
        "reporter_version": REPORTER_VERSION,
        "schedule": args.schedule,
        "timezone": args.timezone,
    }
    write_private_json(args.config, config)
    return {
        "status": "enrolled",
        "runtime_id": runtime["id"],
        "runtime_name": runtime["name"],
        "credential_id": credential["id"],
        "token_prefix": credential["token_prefix"],
        "config_path": str(args.config),
    }


def load_run_context(args: argparse.Namespace) -> tuple[dict[str, Any], str, str, int, str, datetime, datetime, str, str]:
    config = read_json(args.config)
    api_base = (args.api_base or config.get("api_base") or DEFAULT_API_BASE).rstrip("/")
    token = config.get("reporter_token") or os.environ.get("DUCKDOCK_REPORT_TOKEN")
    runtime_id = int(args.runtime_id or config.get("runtime_id"))
    if not token:
        raise PilotError("Reporter token is required")
    hermes_base = args.hermes_base or config.get("hermes_base") or DEFAULT_HERMES_BASE
    report_type = args.report_type
    period_end_dt = utc_now()
    period_start_dt = period_end_dt - timedelta(days=args.period_days)
    return config, api_base, token, runtime_id, hermes_base, period_start_dt, period_end_dt, report_type, args.trigger


def heartbeat_reporter(
    *,
    api_base: str,
    token: str,
    config: dict[str, Any],
    hermes_base: str,
    period_end_dt: datetime,
    trigger: str,
    capabilities: dict[str, Any],
) -> dict[str, Any]:
    next_run_at = (period_end_dt + timedelta(days=7)).isoformat()
    return request_json(
        "POST",
        f"{api_base}/reporters/heartbeat",
        token=token,
        payload={
            "device_id": config.get("device_id") or "hermes-reporter",
            "reporter_version": config.get("reporter_version") or REPORTER_VERSION,
            "agent_version": (hermes_public_metadata(hermes_base).get("openapi_version") or "unknown"),
            "status": "ok",
            "next_run_at": next_run_at,
            "schedule_json": {"type": "hermes_cron", "schedule": config.get("schedule") or DEFAULT_SCHEDULE},
            "capabilities_json": capabilities,
            "metadata_json": {"agent_kind": "hermes", "trigger": trigger},
        },
    )


def run_structured_report(args: argparse.Namespace) -> dict[str, Any]:
    config, api_base, token, runtime_id, hermes_base, period_start_dt, period_end_dt, report_type, trigger = load_run_context(args)
    period_start = period_start_dt.isoformat()
    period_end = period_end_dt.isoformat()
    heartbeat = heartbeat_reporter(
        api_base=api_base,
        token=token,
        config=config,
        hermes_base=hermes_base,
        period_end_dt=period_end_dt,
        trigger=trigger,
        capabilities={"structured_report": "duckdock-structured-report-v1", "collection_mode": "summary_index"},
    )
    public = hermes_public_metadata(hermes_base)
    cron = hermes_cron_summary(config.get("hermes_cli"))
    hostname_hash = stable_hash(socket.gethostname())
    runtime_external_id = f"hermes-{hostname_hash}"
    model = sanitize_model_info(public.get("model_info")) or {}
    plugin_assets = []
    for plugin in public.get("dashboard_plugins") or []:
        name = str(plugin.get("name") or "plugin")
        plugin_assets.append(
            {
                "external_id": f"{runtime_external_id}/plugin/{name}",
                "asset_type": "tool",
                "name": f"Hermes plugin: {name}",
                "description": str(plugin.get("description") or "")[:500],
                "criticality": "medium",
                "metadata_json": {
                    "label": plugin.get("label"),
                    "version": plugin.get("version"),
                    "source": plugin.get("source"),
                    "has_api": plugin.get("has_api"),
                },
            }
        )
    blockers = [] if cron.get("available") else ["Hermes cron status could not be sampled by the Reporter."]
    idempotency = args.idempotency_key or f"hermes-structured-{runtime_id}-{period_end_dt:%Y%m%d}-{report_type}"
    payload = {
        "runtime_id": runtime_id,
        "schema_version": "duckdock-structured-report-v1",
        "report_type": report_type,
        "title": f"Hermes {report_type} structured report",
        "summary": (
            "Hermes produced a schema-first structured report for DuckDock. "
            "This daily/weekly path sends summary fields only and does not upload a local evidence pack."
        ),
        "period_start": period_start,
        "period_end": period_end,
        "idempotency_key": idempotency,
        "sensitivity": "internal",
        "highlights": [
            f"Hermes local API reachable: {bool(public.get('openapi_title'))}",
            f"Model provider: {model.get('provider') or 'unknown'}",
            f"Dashboard plugin count: {len(public.get('dashboard_plugins') or [])}",
        ],
        "blockers": blockers,
        "next_actions": ["Keep the Hermes cron reporter enabled for lightweight timeline continuity."],
        "project_refs": ["duckdock"],
        "asset_refs": [
            {
                "external_id": f"{runtime_external_id}/agent/hermes",
                "asset_type": "agent",
                "name": "Hermes Agent Local Runtime",
                "description": "Local Hermes desktop/runtime endpoint reported through structured JSON.",
                "criticality": "high",
                "metadata_json": {"model": model, "api_base": hermes_base},
            },
            {
                "external_id": f"{runtime_external_id}/scheduled-task/duckdock-reporter",
                "asset_type": "scheduled_task",
                "name": "DuckDock Hermes Reporter scheduled task",
                "description": "Hermes cron invokes the DuckDock structured Reporter.",
                "criticality": "high",
                "metadata_json": {"hermes_cron_job_id": config.get("hermes_cron_job_id"), "cron": cron},
            },
            {
                "external_id": f"{runtime_external_id}/tool/hermes-gateway",
                "asset_type": "tool",
                "name": "Hermes Gateway Service",
                "description": "Hermes local gateway supports runtime-side automation.",
                "criticality": "medium",
            },
            *plugin_assets,
        ],
        "memory_candidates": [
            {
                "candidate_type": "project_context",
                "subject_type": "project",
                "subject_key": "duckdock",
                "title": "DuckDock structured Reporter path",
                "summary": "Daily and weekly Reporter runs should prefer schema-first JSON; evidence packs are reserved for handover or audit.",
                "confidence": 0.82,
                "sensitivity": "internal",
            }
        ],
        "handover_signals": [
            {
                "signal_type": "handover",
                "subject_type": "scheduled_task",
                "subject_key": f"{runtime_external_id}/scheduled-task/duckdock-reporter",
                "title": "Hermes Reporter schedule needs fallback ownership",
                "summary": "The recurring structured Reporter should have a fallback owner before employee or digital-worker handover.",
                "confidence": 0.76,
                "sensitivity": "internal",
            }
        ],
        "metadata_json": {"agent_kind": "hermes", "trigger": trigger, "cron_available": cron.get("available")},
    }
    submitted = request_json("POST", f"{api_base}/reports/structured", token=token, payload=payload)
    return {
        "status": submitted.get("status"),
        "mode": "structured",
        "runtime_id": runtime_id,
        "heartbeat": {"status": heartbeat.get("status"), "upload_recommended": heartbeat.get("upload_recommended")},
        "report_id": submitted.get("report_id"),
        "work_trace_id": (submitted.get("work_trace") or {}).get("id"),
        "asset_count": len(submitted.get("assets") or []),
        "memory_candidate_count": len(submitted.get("memory_candidates") or []),
        "token_prefix": config.get("token_prefix"),
        "credential_id": config.get("credential_id"),
    }


def run_pack_report(args: argparse.Namespace) -> dict[str, Any]:
    config, api_base, token, runtime_id, hermes_base, period_start_dt, period_end_dt, report_type, trigger = load_run_context(args)
    period_start = period_start_dt.isoformat()
    period_end = period_end_dt.isoformat()
    heartbeat = heartbeat_reporter(
        api_base=api_base,
        token=token,
        config=config,
        hermes_base=hermes_base,
        period_end_dt=period_end_dt,
        trigger=trigger,
        capabilities={"report_pack": "duckdock-pack-v1", "collection_mode": "summary_index"},
    )
    with tempfile.TemporaryDirectory(prefix="duckdock-hermes-reporter-") as tmp:
        output_dir = Path(tmp)
        pack_path = build_pack(
            output_dir=output_dir,
            config={**config, "runtime_id": runtime_id},
            hermes_base=hermes_base,
            report_type=report_type,
            period_start=period_start,
            period_end=period_end,
        )
        pack_sha = sha256_file(pack_path)
        pack_size = pack_path.stat().st_size
        idempotency = args.idempotency_key or f"hermes-pack-{runtime_id}-{period_start_dt:%Y%m%d%H%M%S}-{trigger}"
        session = request_json(
            "POST",
            f"{api_base}/reports/upload-sessions",
            token=token,
            payload={
                "runtime_id": runtime_id,
                "schema_version": "duckdock-pack-v1",
                "report_type": report_type,
                "filename": "duckdock-pack-v1.zip",
                "content_type": "application/zip",
                "expected_sha256": pack_sha,
                "expected_size_bytes": pack_size,
                "period_start": period_start,
                "period_end": period_end,
                "idempotency_key": idempotency,
                "metadata_json": {"agent_kind": "hermes", "trigger": trigger},
            },
        )
        if session.get("upload_url"):
            upload_file(session["upload_url"], pack_path, "application/zip")
        finalized = request_json(
            "POST",
            f"{api_base}/reports/{session['report_id']}/finalize",
            token=token,
            payload={
                "sha256": pack_sha,
                "size_bytes": pack_size,
                "manifest": {
                    "schema_version": "duckdock-pack-v1",
                    "runtime_id": runtime_id,
                    "provider": "custom",
                    "agent_kind": "hermes",
                    "report_type": report_type,
                },
            },
        )
    return {
        "status": "uploaded",
        "mode": "pack",
        "runtime_id": runtime_id,
        "heartbeat": {
            "status": heartbeat.get("status"),
            "upload_recommended": heartbeat.get("upload_recommended"),
        },
        "report_id": finalized.get("report_id"),
        "upload_status": finalized.get("status"),
        "object_key": finalized.get("object_key"),
        "pack_sha256": pack_sha,
        "pack_size": pack_size,
        "token_prefix": config.get("token_prefix"),
        "credential_id": config.get("credential_id"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DuckDock Hermes Reporter pilot")
    sub = parser.add_subparsers(dest="command", required=True)

    enroll_parser = sub.add_parser("enroll", help="Self-enroll this Hermes runtime and store a local Reporter config")
    enroll_parser.add_argument("--api-base", default=os.environ.get("DUCKDOCK_API_BASE", DEFAULT_API_BASE))
    enroll_parser.add_argument("--hermes-base", default=os.environ.get("HERMES_BASE_URL", DEFAULT_HERMES_BASE))
    enroll_parser.add_argument("--username", default=os.environ.get("DUCKDOCK_USERNAME"))
    enroll_parser.add_argument("--password", default=os.environ.get("DUCKDOCK_PASSWORD"))
    enroll_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    enroll_parser.add_argument("--runtime-name", default="Hermes Reporter")
    enroll_parser.add_argument("--device-id", default="hermes-reporter")
    enroll_parser.add_argument("--schedule", default=DEFAULT_SCHEDULE)
    enroll_parser.add_argument("--timezone", default=os.environ.get("TZ", "Asia/Shanghai"))

    def add_run_args(command_parser: argparse.ArgumentParser, *, default_report_type: str) -> None:
        command_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
        command_parser.add_argument("--api-base", default=os.environ.get("DUCKDOCK_API_BASE"))
        command_parser.add_argument("--hermes-base", default=os.environ.get("HERMES_BASE_URL"))
        command_parser.add_argument("--runtime-id", type=int)
        command_parser.add_argument("--report-type", default=os.environ.get("DUCKDOCK_REPORT_TYPE", default_report_type))
        command_parser.add_argument("--period-days", type=int, default=7)
        command_parser.add_argument("--trigger", default=os.environ.get("DUCKDOCK_REPORT_TRIGGER", "manual"))
        command_parser.add_argument("--idempotency-key", default=os.environ.get("DUCKDOCK_IDEMPOTENCY_KEY"))

    structured_parser = sub.add_parser("run-structured", help="Submit one lightweight Hermes structured daily/weekly report")
    add_run_args(structured_parser, default_report_type="weekly")

    pack_parser = sub.add_parser("run-pack", help="Build and upload one Hermes DuckDock Pack for handover/audit")
    add_run_args(pack_parser, default_report_type="hermes_handover_pack")

    run_parser = sub.add_parser("run", help="Legacy alias for run-pack")
    add_run_args(run_parser, default_report_type="hermes_handover_pack")
    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        argv = ["run-structured"]
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "enroll":
            result = enroll(args)
        elif args.command == "run-structured":
            result = run_structured_report(args)
        else:
            result = run_pack_report(args)
    except PilotError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
