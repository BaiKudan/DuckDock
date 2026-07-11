from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import platform
import re
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SERVER_NAME = "duckdock-runtime-mcp"
SERVER_VERSION = "0.1.0"
DEFAULT_REPORTER_SKILL = "duckdock/duckdock-reporter"
DEFAULT_REPORTER_DIR = "duckdock-reporter"
DEFAULT_RRULE = "FREQ=WEEKLY;BYDAY=FR;BYHOUR=16;BYMINUTE=0;BYSECOND=0"


class ToolError(Exception):
    pass


@dataclass
class RuntimePaths:
    home: Path
    workbuddy_home: Path
    config_home: Path

    @classmethod
    def from_env(cls) -> "RuntimePaths":
        home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home())).expanduser()
        workbuddy_home = Path(os.environ.get("WORKBUDDY_HOME", str(home / ".workbuddy"))).expanduser()
        config_home = Path(
            os.environ.get("DUCKDOCK_RUNTIME_MCP_HOME", str(home / ".duckdock" / "runtime-mcp"))
        ).expanduser()
        return cls(home=home, workbuddy_home=workbuddy_home, config_home=config_home)

    @property
    def config_path(self) -> Path:
        return self.config_home / "config.json"

    @property
    def reporter_skill_dir(self) -> Path:
        return self.workbuddy_home / "skills" / DEFAULT_REPORTER_DIR


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_iso_after(seconds: int) -> str:
    return (datetime.now().astimezone() + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def redact_text(value: str) -> str:
    value = re.sub(r"dkr_report_[A-Za-z0-9_.\-]+", "dkr_report_<redacted>", value)
    value = re.sub(r"Bearer\s+[A-Za-z0-9_.\-]+", "Bearer <redacted>", value)
    value = re.sub(r'("reporter_token"\s*:\s*")[^"]+(")', r'\1<redacted>\2', value)
    return value


def redact_config(config: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(config)
    if redacted.get("reporter_token"):
        redacted["reporter_token"] = "<redacted>"
    return redacted


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_private_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def ensure_inside(child: Path, parent: Path) -> None:
    child_resolved = child.resolve()
    parent_resolved = parent.resolve()
    if child_resolved != parent_resolved and parent_resolved not in child_resolved.parents:
        raise ToolError(f"Refusing to operate outside allowed directory: {child}")


def fetch_json(url: str, headers: dict[str, str] | None = None, timeout: int = 30) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def download_file(url: str, target: Path, headers: dict[str, str] | None = None, timeout: int = 60) -> None:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        with target.open("wb") as handle:
            shutil.copyfileobj(response, handle)


def post_json(url: str, *, token: str | None, payload: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ToolError(f"POST {url} failed: HTTP {exc.code}: {redact_text(detail)}") from exc
    except urllib.error.URLError as exc:
        raise ToolError(f"POST {url} failed: {exc.reason}") from exc
    return json.loads(raw) if raw.strip() else {}


def safe_extract_tar_gz(archive_path: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    target_resolved = target_dir.resolve()
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            member_target = (target_dir / member.name).resolve()
            if member_target != target_resolved and target_resolved not in member_target.parents:
                raise ToolError(f"Unsafe archive path: {member.name}")
        archive.extractall(target_dir)


def find_skill_root(extracted_dir: Path) -> Path:
    candidates = [path.parent for path in extracted_dir.rglob("SKILL.md")]
    if not candidates:
        raise ToolError("Downloaded reporter bundle does not contain SKILL.md")
    candidates.sort(key=lambda item: len(item.parts))
    return candidates[0]


def command_lines() -> list[str]:
    if platform.system().lower().startswith("win"):
        script = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.CommandLine -like '*connector-proxy*' -or "
            "$_.CommandLine -like '*--mcp-config*' -or $_.Name -like '*WorkBuddy*' } | "
            "Select-Object CommandLine | ConvertTo-Json -Depth 2"
        )
        try:
            raw = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", script],
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=10,
            )
            if not raw.strip():
                return []
            data = json.loads(raw)
            if isinstance(data, dict):
                data = [data]
            return [str(item.get("CommandLine") or "") for item in data]
        except Exception:
            return []
    try:
        raw = subprocess.check_output(["ps", "axo", "command"], text=True, errors="ignore", timeout=10)
        return [line for line in raw.splitlines() if "WorkBuddy" in line or "connector-proxy" in line]
    except Exception:
        return []


class WorkBuddyMcpClient:
    def __init__(self, url: str, bearer_token: str):
        self.url = url
        self.bearer_token = bearer_token
        self.session_id: str | None = None
        self.initialized = False

    @classmethod
    def discover(cls, paths: RuntimePaths) -> tuple["WorkBuddyMcpClient | None", dict[str, Any]]:
        mcp_config = paths.workbuddy_home / ".mcp.json"
        discovered: dict[str, Any] = {
            "mcp_config_exists": mcp_config.exists(),
            "endpoint_found": False,
            "bearer_token_found": False,
        }

        url: str | None = None
        if mcp_config.exists():
            try:
                config = read_json(mcp_config)
                server = (config.get("mcpServers") or {}).get("connector-proxy") or {}
                url = server.get("url")
            except Exception:
                url = None

        joined = "\n".join(command_lines())
        if url is None:
            match = re.search(r"http://127\.0\.0\.1:\d+/mcp", joined)
            url = match.group(0) if match else None
        token_match = re.search(r"Bearer ([A-Za-z0-9_.\-]+)", joined)
        token = token_match.group(1) if token_match else None

        discovered["endpoint_found"] = bool(url)
        discovered["bearer_token_found"] = bool(token)
        if not url or not token:
            return None, discovered
        return cls(url=url, bearer_token=token), discovered

    def _decode_response(self, response: Any) -> dict[str, Any]:
        body = response.read().decode("utf-8", errors="ignore")
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            data_lines = [line[5:].strip() for line in body.splitlines() if line.startswith("data:")]
            return json.loads(data_lines[-1]) if data_lines else {}
        return json.loads(body) if body.strip() else {}

    def rpc(self, method: str, params: dict[str, Any] | None = None, *, notify: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not notify:
            payload["id"] = str(uuid.uuid4())
        if params is not None:
            payload["params"] = params
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.bearer_token}",
        }
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            session_id = response.headers.get("mcp-session-id")
            if session_id:
                self.session_id = session_id
            return self._decode_response(response)

    def ensure_initialized(self) -> None:
        if self.initialized:
            return
        self.rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )
        try:
            self.rpc("notifications/initialized", notify=True)
        except Exception:
            pass
        self.initialized = True

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.ensure_initialized()
        return self.rpc("tools/call", {"name": name, "arguments": arguments})


def collect_nested_json(value: Any) -> list[Any]:
    found: list[Any] = []
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except Exception:
                return found
            found.append(parsed)
            found.extend(collect_nested_json(parsed))
        return found
    if isinstance(value, dict):
        for item in value.values():
            found.extend(collect_nested_json(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(collect_nested_json(item))
    return found


def extract_automations(response: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = [response]
    candidates.extend(collect_nested_json(response))
    for candidate in candidates:
        if isinstance(candidate, dict) and isinstance(candidate.get("automations"), list):
            return candidate["automations"]
    return []


def inspect_workbuddy_mcp_config(paths: RuntimePaths) -> dict[str, Any]:
    mcp_config = paths.workbuddy_home / ".mcp.json"
    info = {
        "path": str(mcp_config),
        "exists": mcp_config.exists(),
        "server_names": [],
        "duckdock_runtime_registered": False,
    }
    if not mcp_config.exists():
        return info
    try:
        servers = read_json(mcp_config).get("mcpServers") or {}
    except Exception:
        return {**info, "readable": False}
    if not isinstance(servers, dict):
        return {**info, "readable": True}
    names = sorted(str(name) for name in servers)
    registered = False
    for name, server in servers.items():
        haystack = f"{name} {json.dumps(server, ensure_ascii=False)}".lower()
        if "duckdock-runtime" in haystack or "duckdock_runtime_mcp.py" in haystack:
            registered = True
            break
    return {
        **info,
        "readable": True,
        "server_names": names,
        "duckdock_runtime_registered": registered,
    }


class DuckDockRuntimeMcp:
    def __init__(self, *, default_api_base: str | None = None, paths: RuntimePaths | None = None):
        self.default_api_base = default_api_base or os.environ.get("DUCKDOCK_API_BASE")
        self.paths = paths or RuntimePaths.from_env()

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "duckdock.runtime.detect",
                "description": "Detect the local WorkBuddy runtime, DuckDock Reporter skill, local config, and callable WorkBuddy MCP bridge.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "duckdock.reporter.install",
                "description": "Install or update duckdock/duckdock-reporter from DuckDock private Skills Registry into the local WorkBuddy skills directory.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "api_base": {"type": "string", "description": "DuckDock API base, for example https://duckdock.example.com/api/v1."},
                        "registry_index_url": {"type": "string", "description": "Optional full registry index URL."},
                        "skill": {"type": "string", "description": "Skill id. Defaults to duckdock/duckdock-reporter."},
                        "overwrite": {"type": "boolean", "description": "Replace existing local reporter skill. Defaults to true."},
                    },
                },
            },
            {
                "name": "duckdock.reporter.configure",
                "description": "Persist DuckDock runtime configuration for local Reporter deployment. Reporter token is stored locally only when provided.",
                "inputSchema": {
                    "type": "object",
                    "required": ["api_base", "runtime_id"],
                    "properties": {
                        "api_base": {"type": "string"},
                        "runtime_id": {"type": "string"},
                        "provider": {"type": "string", "enum": ["workbuddy", "openclaw", "arkclaw", "jvs", "custom"]},
                        "reporter_token": {"type": "string", "description": "Runtime-scoped DuckDock Reporter token."},
                        "store_reporter_token": {"type": "boolean", "description": "Defaults to true when reporter_token is provided."},
                    },
                },
            },
            {
                "name": "duckdock.reporter.dry_run",
                "description": "Create a one-time WorkBuddy automation that runs a structured DuckDock Reporter validation.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "api_base": {"type": "string"},
                        "runtime_id": {"type": "string"},
                        "reporter_token": {"type": "string"},
                        "cwd": {"type": "string", "description": "Work directory for WorkBuddy automation. Defaults to current process cwd."},
                        "delay_seconds": {"type": "number", "description": "Delay before the one-time automation runs. Defaults to 30."},
                    },
                },
            },
            {
                "name": "duckdock.reporter.run_structured",
                "description": "Submit one privacy-preserving duckdock-structured-report-v1 report from the local WorkBuddy runtime.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "api_base": {"type": "string"},
                        "runtime_id": {"type": "string"},
                        "reporter_token": {"type": "string"},
                        "report_type": {"type": "string", "description": "daily or weekly. Defaults to daily."},
                    },
                },
            },
            {
                "name": "duckdock.reporter.schedule",
                "description": "Create a recurring WorkBuddy automation for periodic structured DuckDock Reporter self-reporting.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "api_base": {"type": "string"},
                        "runtime_id": {"type": "string"},
                        "reporter_token": {"type": "string"},
                        "rrule": {"type": "string", "description": "RFC 5545 RRULE. Defaults to every Friday at 16:00."},
                        "cwd": {"type": "string"},
                    },
                },
            },
            {
                "name": "duckdock.reporter.status",
                "description": "Return local Reporter install/config status and WorkBuddy automation summaries.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "duckdock.reporter.pause",
                "description": "Pause DuckDock Reporter WorkBuddy automations for the current runtime or all DuckDock Reporter automations.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "runtime_id": {"type": "string"},
                    },
                },
            },
            {
                "name": "duckdock.reporter.uninstall",
                "description": "Remove local DuckDock Reporter config and optionally remove skill files and WorkBuddy automations.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "runtime_id": {"type": "string"},
                        "remove_skill": {"type": "boolean", "description": "Defaults to true."},
                        "delete_automations": {"type": "boolean", "description": "Defaults to false; pause is safer."},
                    },
                },
            },
        ]

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = arguments or {}
        handlers = {
            "duckdock.runtime.detect": self.detect,
            "duckdock.reporter.install": self.install_reporter,
            "duckdock.reporter.configure": self.configure_reporter,
            "duckdock.reporter.dry_run": self.dry_run,
            "duckdock.reporter.run_structured": self.run_structured,
            "duckdock.reporter.schedule": self.schedule,
            "duckdock.reporter.status": self.status,
            "duckdock.reporter.pause": self.pause,
            "duckdock.reporter.uninstall": self.uninstall,
        }
        handler = handlers.get(name)
        if handler is None:
            raise ToolError(f"Unknown tool: {name}")
        return handler(args)

    def detect(self, _: dict[str, Any] | None = None) -> dict[str, Any]:
        client, mcp = WorkBuddyMcpClient.discover(self.paths)
        mcp_config = inspect_workbuddy_mcp_config(self.paths)
        return {
            "provider": "workbuddy",
            "supported": True,
            "workbuddy_home": str(self.paths.workbuddy_home),
            "workbuddy_home_exists": self.paths.workbuddy_home.exists(),
            "workbuddy_process_running": any("WorkBuddy" in line for line in command_lines()),
            "reporter_skill_dir": str(self.paths.reporter_skill_dir),
            "reporter_skill_installed": (self.paths.reporter_skill_dir / "SKILL.md").exists(),
            "config_path": str(self.paths.config_path),
            "config_exists": self.paths.config_path.exists(),
            "workbuddy_mcp": {
                **mcp,
                "callable": client is not None,
            },
            "workbuddy_mcp_config": mcp_config,
        }

    def _api_base(self, args: dict[str, Any]) -> str:
        api_base = args.get("api_base") or self.default_api_base
        if not api_base:
            config = self._load_config(required=False)
            api_base = config.get("api_base") if config else None
        if not api_base:
            raise ToolError("api_base is required. Example: https://duckdock.example.com/api/v1")
        return str(api_base).rstrip("/")

    def _registry_index_url(self, args: dict[str, Any]) -> str:
        if args.get("registry_index_url"):
            return str(args["registry_index_url"])
        api_base = self._api_base(args)
        return f"{api_base}/registry/index?include_urls=true&namespace=duckdock"

    def install_reporter(self, args: dict[str, Any]) -> dict[str, Any]:
        skill = str(args.get("skill") or DEFAULT_REPORTER_SKILL)
        overwrite = bool(args.get("overwrite", True))
        namespace, skill_name = self._split_skill(skill)
        registry_index_url = self._registry_index_url(args)

        index = fetch_json(registry_index_url)
        item = self._find_registry_item(index, namespace=namespace, skill_name=skill_name)
        artifact_url = item.get("artifact_url")
        if not artifact_url:
            raise ToolError("Registry item does not include artifact_url. Use include_urls=true.")

        with tempfile.TemporaryDirectory(prefix="duckdock-runtime-mcp-") as temp_name:
            temp_dir = Path(temp_name)
            archive_path = temp_dir / "bundle.tar.gz"
            extracted_dir = temp_dir / "extracted"
            download_file(artifact_url, archive_path)
            safe_extract_tar_gz(archive_path, extracted_dir)
            skill_root = find_skill_root(extracted_dir)

            skills_dir = self.paths.workbuddy_home / "skills"
            destination = skills_dir / DEFAULT_REPORTER_DIR
            ensure_inside(destination, skills_dir)
            if destination.exists() and overwrite:
                shutil.rmtree(destination)
            if destination.exists() and not overwrite:
                return {
                    "installed": True,
                    "changed": False,
                    "reason": "Reporter skill already exists and overwrite=false.",
                    "destination": str(destination),
                    "tag": item.get("tag"),
                }
            skills_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(skill_root, destination, dirs_exist_ok=True)

        return {
            "installed": True,
            "changed": True,
            "destination": str(self.paths.reporter_skill_dir),
            "tag": item.get("tag"),
            "artifact_sha256": item.get("artifact_sha256"),
            "artifact_size_bytes": item.get("artifact_size_bytes"),
        }

    def configure_reporter(self, args: dict[str, Any]) -> dict[str, Any]:
        api_base = self._api_base(args)
        runtime_id = str(args.get("runtime_id") or "").strip()
        if not runtime_id:
            raise ToolError("runtime_id is required")
        provider = str(args.get("provider") or "workbuddy")
        token = args.get("reporter_token")
        store_token = bool(args.get("store_reporter_token", bool(token)))
        config = {
            "schema_version": "duckdock-runtime-mcp-config/v1",
            "api_base": api_base,
            "runtime_id": runtime_id,
            "provider": provider,
            "updated_at": utc_now_iso(),
        }
        if token and store_token:
            config["reporter_token"] = str(token)
        write_private_json(self.paths.config_path, config)
        return {
            "configured": True,
            "config_path": str(self.paths.config_path),
            "config": redact_config(config),
            "token_stored": bool(token and store_token),
        }

    def dry_run(self, args: dict[str, Any]) -> dict[str, Any]:
        config = self._merged_config(args)
        delay_seconds = int(args.get("delay_seconds") or 30)
        scheduled_at = local_iso_after(max(5, delay_seconds))
        name = f"DuckDock Reporter dry-run - runtime {config['runtime_id']}"
        prompt = self._workbuddy_prompt(config, mode="dry-run")
        result = self._automation_update(
            {
                "mode": "create",
                "name": name,
                "prompt": prompt,
                "scheduleType": "once",
                "scheduledAt": scheduled_at,
                "cwds": str(args.get("cwd") or os.getcwd()),
                "status": "ACTIVE",
                "modelIsThinking": True,
            }
        )
        return {
            "created": True,
            "automation_name": name,
            "scheduled_at": scheduled_at,
            "workbuddy_response": self._safe_response(result),
        }

    def run_structured(self, args: dict[str, Any]) -> dict[str, Any]:
        config = self._merged_config(args)
        report_type = str(args.get("report_type") or "daily")
        if report_type not in {"daily", "weekly"}:
            report_type = "daily"
        token = str(config["reporter_token"])
        api_base = str(config["api_base"]).rstrip("/")
        heartbeat = self._post_json(
            f"{api_base}/reporters/heartbeat",
            token=token,
            payload=self._heartbeat_payload(report_type=report_type),
        )
        report = self._post_json(
            f"{api_base}/reports/structured",
            token=token,
            payload=self._structured_report_payload(config, report_type=report_type),
            timeout=60,
        )
        return {
            "submitted": True,
            "runtime_id": config["runtime_id"],
            "heartbeat": {
                "status": heartbeat.get("status"),
                "last_seen_at": heartbeat.get("last_seen_at"),
                "upload_recommended": heartbeat.get("upload_recommended"),
            },
            "report_id": report.get("report_id"),
            "status": report.get("status"),
            "collection_job_id": (report.get("job") or {}).get("id") if isinstance(report.get("job"), dict) else None,
            "work_trace_id": (report.get("work_trace") or {}).get("id") if isinstance(report.get("work_trace"), dict) else None,
            "asset_count": len(report.get("assets") or []),
            "memory_candidate_count": len(report.get("memory_candidates") or []),
        }

    def schedule(self, args: dict[str, Any]) -> dict[str, Any]:
        config = self._merged_config(args)
        rrule = str(args.get("rrule") or DEFAULT_RRULE)
        name = f"DuckDock Reporter weekly - runtime {config['runtime_id']}"
        prompt = self._workbuddy_prompt(config, mode="weekly")
        result = self._automation_update(
            {
                "mode": "create",
                "name": name,
                "prompt": prompt,
                "scheduleType": "recurring",
                "rrule": rrule,
                "validFrom": datetime.now().astimezone().isoformat(timespec="seconds"),
                "cwds": str(args.get("cwd") or os.getcwd()),
                "status": "ACTIVE",
                "modelIsThinking": True,
            }
        )
        return {
            "created": True,
            "automation_name": name,
            "rrule": rrule,
            "workbuddy_response": self._safe_response(result),
        }

    def status(self, _: dict[str, Any] | None = None) -> dict[str, Any]:
        config = self._load_config(required=False)
        automations = self._list_automations(safe=True)
        return {
            "detect": self.detect({}),
            "config": redact_config(config) if config else None,
            "automations": automations,
        }

    def pause(self, args: dict[str, Any]) -> dict[str, Any]:
        runtime_id = args.get("runtime_id") or self._runtime_id_from_config()
        targets = self._matching_automations(runtime_id=runtime_id)
        updated = []
        for target in targets:
            automation_id = target.get("id")
            if not automation_id:
                continue
            updated.append(self._safe_response(self._automation_update({"mode": "update", "id": automation_id, "status": "PAUSED"})))
        return {"paused": len(updated), "targets": targets, "workbuddy_responses": updated}

    def uninstall(self, args: dict[str, Any]) -> dict[str, Any]:
        runtime_id = args.get("runtime_id") or self._runtime_id_from_config()
        remove_skill = bool(args.get("remove_skill", True))
        delete_automations = bool(args.get("delete_automations", False))

        targets = self._matching_automations(runtime_id=runtime_id)
        automation_results = []
        for target in targets:
            automation_id = target.get("id")
            if not automation_id:
                continue
            mode = "delete" if delete_automations else "update"
            payload = {"mode": mode, "id": automation_id}
            if not delete_automations:
                payload["status"] = "PAUSED"
            automation_results.append(self._safe_response(self._automation_update(payload)))

        config_removed = False
        if self.paths.config_path.exists():
            self.paths.config_path.unlink()
            config_removed = True

        skill_removed = False
        if remove_skill and self.paths.reporter_skill_dir.exists():
            ensure_inside(self.paths.reporter_skill_dir, self.paths.workbuddy_home / "skills")
            shutil.rmtree(self.paths.reporter_skill_dir)
            skill_removed = True

        return {
            "config_removed": config_removed,
            "skill_removed": skill_removed,
            "automations_touched": len(automation_results),
            "automation_action": "delete" if delete_automations else "pause",
            "workbuddy_responses": automation_results,
        }

    def _split_skill(self, skill: str) -> tuple[str, str]:
        if "/" not in skill:
            raise ToolError("Skill must use namespace/name format, for example duckdock/duckdock-reporter")
        namespace, skill_name = skill.split("/", 1)
        return namespace, skill_name

    def _find_registry_item(self, index: dict[str, Any], *, namespace: str, skill_name: str) -> dict[str, Any]:
        for item in index.get("items", []):
            if item.get("namespace") == namespace and item.get("skill") == skill_name:
                return item
        raise ToolError(f"Skill not found in registry index: {namespace}/{skill_name}")

    def _load_config(self, *, required: bool) -> dict[str, Any] | None:
        if not self.paths.config_path.exists():
            if required:
                raise ToolError("Reporter is not configured. Call duckdock.reporter.configure first.")
            return None
        return read_json(self.paths.config_path)

    def _merged_config(self, args: dict[str, Any]) -> dict[str, Any]:
        config = self._load_config(required=False) or {}
        merged = dict(config)
        for key in ("api_base", "runtime_id", "reporter_token", "provider"):
            if args.get(key):
                merged[key] = str(args[key])
        if not merged.get("api_base"):
            merged["api_base"] = self._api_base(args)
        if not merged.get("runtime_id"):
            raise ToolError("runtime_id is required or must be configured first")
        if not merged.get("reporter_token"):
            raise ToolError("reporter_token is required or must be configured first")
        merged.setdefault("provider", "workbuddy")
        return merged

    def _post_json(
        self,
        url: str,
        *,
        token: str | None,
        payload: dict[str, Any],
        timeout: int = 30,
    ) -> dict[str, Any]:
        return post_json(url, token=token, payload=payload, timeout=timeout)

    def _heartbeat_payload(self, *, report_type: str) -> dict[str, Any]:
        facts = self._workbuddy_facts()
        return {
            "device_id": facts["device_id"],
            "reporter_version": f"{SERVER_NAME}-{SERVER_VERSION}",
            "agent_version": facts["app"]["version"],
            "status": "ok",
            "schedule_json": {"mode": "structured", "report_type": report_type},
            "capabilities_json": {
                "structured_report": True,
                "pack_report": False,
                "workbuddy_mcp_callable": facts["mcp"]["callable"],
            },
            "metadata_json": {
                "source": SERVER_NAME,
                "app": facts["app"],
                "mcp": facts["mcp"],
                "privacy": "summary_index_only",
            },
        }

    def _structured_report_payload(self, config: dict[str, Any], *, report_type: str) -> dict[str, Any]:
        facts = self._workbuddy_facts()
        now = datetime.now(timezone.utc)
        period_start = now - (timedelta(days=7) if report_type == "weekly" else timedelta(days=1))
        app_version = facts["app"]["version"] or "unknown"
        asset_refs = self._workbuddy_asset_refs(facts)
        return {
            "runtime_id": int(config["runtime_id"]),
            "schema_version": "duckdock-structured-report-v1",
            "report_type": report_type,
            "title": f"WorkBuddy structured {report_type} report",
            "summary": (
                f"WorkBuddy {app_version} submitted a privacy-preserving structured Reporter snapshot. "
                f"Detected {facts['process_count']} WorkBuddy-related processes, "
                f"{len(facts['marketplaces'])} plugin marketplaces, and {len(facts['connectors'])} connector definitions."
            ),
            "period_start": period_start.isoformat(),
            "period_end": now.isoformat(),
            "idempotency_key": f"workbuddy-runtime-mcp-{report_type}-{now.strftime('%Y%m%d%H%M%S')}",
            "highlights": [
                "WorkBuddy MCP bridge is callable." if facts["mcp"]["callable"] else "WorkBuddy is installed but MCP bridge was not callable.",
                "Reporter credential stayed inside the local MCP config and was not placed in the prompt.",
                "Daily/weekly reporting used the lightweight structured endpoint instead of MinIO pack upload.",
            ],
            "blockers": [] if facts["mcp"]["callable"] else ["WorkBuddy MCP bridge was not callable during local detection."],
            "next_actions": [
                "Create a recurring WorkBuddy automation that calls duckdock.reporter.run_structured.",
                "Reserve duckdock-pack-v1 uploads for handover or audit evidence packages.",
            ],
            "project_refs": ["duckdock", "workbuddy"],
            "asset_refs": asset_refs,
            "memory_candidates": [
                {
                    "candidate_type": "project_context",
                    "subject_type": "project",
                    "subject_key": "workbuddy-reporter",
                    "title": "WorkBuddy structured Reporter path",
                    "summary": (
                        "WorkBuddy can report summary/index metadata through DuckDock Runtime MCP without exposing "
                        "Reporter credentials to model prompts."
                    ),
                    "confidence": 0.82,
                }
            ],
            "handover_signals": [
                {
                    "signal_type": "handover",
                    "subject_type": "agent",
                    "subject_key": "workbuddy/local-runtime",
                    "title": "WorkBuddy Reporter automation needs an operational owner",
                    "summary": (
                        "The structured Reporter should have a named fallback owner and credential rotation procedure "
                        "before it becomes part of long-running employee/agent governance."
                    ),
                    "confidence": 0.78,
                },
                {
                    "signal_type": "risk",
                    "subject_type": "credential",
                    "subject_key": "duckdock-runtime-mcp-config",
                    "title": "Reporter credential is stored locally",
                    "summary": "The local config is chmod 0600, but production rollout should use runtime or enterprise secret storage where available.",
                    "confidence": 0.72,
                },
            ],
            "metadata_json": {
                "source": SERVER_NAME,
                "workbuddy_facts": facts,
                "privacy": "no raw transcripts, cookies, screenshots, or arbitrary user directories",
            },
        }

    def _workbuddy_facts(self) -> dict[str, Any]:
        app_path = Path("/Applications/WorkBuddy.app")
        marketplaces = self._child_dir_names(self.paths.workbuddy_home / "plugins" / "marketplaces", limit=12)
        connectors = self._child_dir_names(self.paths.workbuddy_home / "connectors-marketplace" / "connectors", limit=20)
        skills = self._child_dir_names(self.paths.workbuddy_home / "skills", limit=20)
        process_lines = command_lines()
        client, mcp = WorkBuddyMcpClient.discover(self.paths)
        mcp_config = inspect_workbuddy_mcp_config(self.paths)
        hostname_hash = hashlib.sha256((socket.gethostname() or platform.node() or "workbuddy").encode("utf-8")).hexdigest()[:16]
        return {
            "device_id": f"workbuddy-{hostname_hash}",
            "app": {
                "name": "WorkBuddy",
                "path": str(app_path),
                "installed": app_path.exists(),
                "version": self._workbuddy_version(app_path),
            },
            "host": {
                "os": platform.platform(),
                "machine": platform.machine(),
                "hostname_hash": hostname_hash,
            },
            "process_count": len(process_lines),
            "mcp": {**mcp, "callable": client is not None},
            "mcp_config": mcp_config,
            "marketplaces": marketplaces,
            "connectors": connectors,
            "skills": skills,
        }

    def _workbuddy_asset_refs(self, facts: dict[str, Any]) -> list[dict[str, Any]]:
        assets = [
            {
                "external_id": "workbuddy/local-runtime",
                "asset_type": "agent",
                "name": "WorkBuddy Local Runtime",
                "criticality": "high",
                "description": f"WorkBuddy desktop runtime {facts['app']['version'] or 'unknown'} on this device.",
            },
            {
                "external_id": "workbuddy/codebuddy-cli",
                "asset_type": "tool",
                "name": "CodeBuddy CLI",
                "criticality": "medium",
                "description": "WorkBuddy non-interactive CLI/MCP execution surface for Reporter automation.",
            },
            {
                "external_id": "workbuddy/mcp-connector-proxy",
                "asset_type": "mcp",
                "name": "WorkBuddy MCP Connector Proxy",
                "criticality": "medium",
                "description": "Local connector-proxy MCP bridge used to create or run DuckDock Reporter automation.",
            },
        ]
        for name in facts.get("connectors", [])[:8]:
            assets.append(
                {
                    "external_id": f"workbuddy/connector/{name}",
                    "asset_type": "tool",
                    "name": f"WorkBuddy connector: {name}",
                    "criticality": "low",
                    "description": "Connector definition discovered in WorkBuddy local connector marketplace.",
                }
            )
        return assets

    def _workbuddy_version(self, app_path: Path) -> str | None:
        plist_path = app_path / "Contents" / "Info.plist"
        try:
            data = plistlib.loads(plist_path.read_bytes())
        except Exception:
            return None
        value = data.get("CFBundleShortVersionString") or data.get("CFBundleVersion")
        return str(value) if value else None

    def _child_dir_names(self, path: Path, *, limit: int) -> list[str]:
        if not path.exists():
            return []
        return sorted(item.name for item in path.iterdir() if item.is_dir())[:limit]

    def _runtime_id_from_config(self) -> str | None:
        config = self._load_config(required=False)
        return str(config.get("runtime_id")) if config and config.get("runtime_id") else None

    def _automation_update(self, arguments: dict[str, Any]) -> dict[str, Any]:
        client, discovered = WorkBuddyMcpClient.discover(self.paths)
        if client is None:
            raise ToolError(f"WorkBuddy MCP bridge is not callable: {discovered}")
        return client.call_tool("automation_update", arguments)

    def _list_automations(self, *, safe: bool) -> list[dict[str, Any]]:
        try:
            response = self._automation_update({"mode": "list"})
            automations = extract_automations(response)
        except Exception:
            return []
        if not safe:
            return automations
        return [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "status": item.get("status"),
                "scheduleType": item.get("scheduleType"),
                "scheduledAt": item.get("scheduledAt"),
            }
            for item in automations
        ]

    def _matching_automations(self, *, runtime_id: str | None) -> list[dict[str, Any]]:
        automations = self._list_automations(safe=True)
        result = []
        for item in automations:
            name = str(item.get("name") or "")
            if not name.startswith("DuckDock Reporter"):
                continue
            if runtime_id and f"runtime {runtime_id}" not in name:
                continue
            result.append(item)
        return result

    def _workbuddy_prompt(self, config: dict[str, Any], *, mode: str) -> str:
        action = "one structured dry-run validation" if mode == "dry-run" else "a scheduled structured self-report"
        report_type = "daily" if mode == "dry-run" else "weekly"
        return f"""Run DuckDock Reporter {action} from this WorkBuddy runtime.

Configuration:
- DuckDock API base: {config["api_base"]}
- Runtime ID: {config["runtime_id"]}
- Provider: {config.get("provider", "workbuddy")}
- Local config path: {self.paths.config_path}
- Reporter skill path: {self.paths.reporter_skill_dir}

Rules:
1. Call the local MCP tool duckdock.reporter.run_structured with report_type="{report_type}".
2. Let the MCP tool read the Reporter token from the local config path. Do not print the token in the final answer, logs, or generated markdown.
3. Collect only DuckDock-required asset indexes, skill/agent/prompt/workflow metadata, memory indexes, session summaries, artifact indexes, hashes, and evidence summaries.
4. Do not upload raw private conversation transcripts, secrets, cookies, screenshots, or arbitrary user directories.
5. Daily/weekly reporting must use duckdock-structured-report-v1. Use duckdock-pack-v1 only for explicit handover or audit evidence packages.
6. Return only report_id, status, runtime_id, work_trace_id, asset_count, memory_candidate_count, and any non-secret error message.
"""

    def _safe_response(self, response: dict[str, Any]) -> dict[str, Any]:
        return json.loads(redact_text(json.dumps(response, ensure_ascii=False)))

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        request_id = request.get("id")
        is_notification = request_id is None
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": request.get("params", {}).get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                }
            elif method == "notifications/initialized":
                return None
            elif method == "tools/list":
                result = {"tools": self.tool_definitions()}
            elif method == "tools/call":
                params = request.get("params") or {}
                tool_result = self.call_tool(params.get("name"), params.get("arguments") or {})
                result = {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(tool_result, ensure_ascii=False, indent=2),
                        }
                    ],
                    "isError": False,
                }
            else:
                raise ToolError(f"Unsupported method: {method}")
            if is_notification:
                return None
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as exc:
            if is_notification:
                return None
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": str(exc)},
            }

    def run_stdio(self) -> None:
        for line in sys.stdin:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                request = json.loads(stripped)
            except json.JSONDecodeError as exc:
                response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
            else:
                response = self.handle_request(request)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description="DuckDock Runtime MCP Adapter")
    parser.add_argument("--api-base", default=None, help="Default DuckDock API base URL.")
    parser.add_argument("--self-test", action="store_true", help="Print local detection JSON and exit.")
    args = parser.parse_args()
    server = DuckDockRuntimeMcp(default_api_base=args.api_base)
    if args.self_test:
        print(json.dumps(server.detect({}), ensure_ascii=False, indent=2))
        return
    server.run_stdio()


if __name__ == "__main__":
    main()
