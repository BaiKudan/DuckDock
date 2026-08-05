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
import sqlite3
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
SERVER_VERSION = "0.3.0"
DEFAULT_REPORTER_SKILL = "duckdock/duckdock-reporter"
DEFAULT_REPORTER_DIR = "duckdock-reporter"
DEFAULT_RRULE = "FREQ=WEEKLY;BYDAY=FR;BYHOUR=16;BYMINUTE=0;BYSECOND=0"
DEFAULT_EXECUTION_BUFFER_MAX_ITEMS = 10_000
DEFAULT_EXECUTION_BUFFER_MAX_BYTES = 64 * 1024 * 1024
EXECUTION_BUFFER_HIGH_WATER_RATIO = 0.8
EXECUTION_BUFFER_TERMINAL_STATES = {
    "ACKED",
    "REJECTED",
    "DISCARDED",
}


class ToolError(Exception):
    pass


class PostError(ToolError):
    def __init__(
        self,
        message: str,
        *,
        safe_code: str,
        retryable: bool,
        status_code: int | None = None,
    ):
        super().__init__(message)
        self.safe_code = safe_code
        self.retryable = retryable
        self.status_code = status_code


class BufferDependencyPending(ToolError):
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

    @property
    def execution_buffer_path(self) -> Path:
        return self.config_home / "execution-buffer.sqlite3"


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


def post_json(
    url: str,
    *,
    token: str | None,
    payload: dict[str, Any],
    timeout: int = 30,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    request_headers.update(headers or {})
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        retryable = exc.code in {408, 425, 429} or exc.code >= 500
        raise PostError(
            f"POST {url} failed: HTTP {exc.code}: {redact_text(detail)}",
            safe_code=f"HTTP_{exc.code}",
            retryable=retryable,
            status_code=exc.code,
        ) from exc
    except urllib.error.URLError as exc:
        raise PostError(
            f"POST {url} failed: {redact_text(str(exc.reason))}",
            safe_code="NETWORK_UNAVAILABLE",
            retryable=True,
        ) from exc
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PostError(
            f"POST {url} returned invalid JSON",
            safe_code="INVALID_RESPONSE",
            retryable=True,
        ) from exc


class ExecutionBuffer:
    """Crash-safe, metadata-only ordered queue for execution control envelopes."""

    def __init__(
        self,
        path: Path,
        *,
        max_items: int = DEFAULT_EXECUTION_BUFFER_MAX_ITEMS,
        max_bytes: int = DEFAULT_EXECUTION_BUFFER_MAX_BYTES,
    ):
        if max_items < 1:
            raise ToolError("execution buffer max_items must be positive")
        if max_bytes < 1024:
            raise ToolError("execution buffer max_bytes must be at least 1024")
        self.path = path
        self.max_items = max_items
        self.max_bytes = max_bytes
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(
                stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
            )
        except OSError:
            pass
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS queue_items (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation TEXT NOT NULL,
                    runtime_id TEXT NOT NULL,
                    api_base TEXT NOT NULL,
                    path_template TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    payload_bytes INTEGER NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    dependency_sequence INTEGER,
                    dependency_response_field TEXT,
                    dependency_target TEXT,
                    state TEXT NOT NULL DEFAULT 'PENDING'
                        CHECK (
                            state IN (
                                'PENDING',
                                'ACKED',
                                'REJECTED',
                                'DISCARDED'
                            )
                        ),
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at TEXT,
                    last_error_code TEXT,
                    ack_response_json TEXT,
                    created_at TEXT NOT NULL,
                    acknowledged_at TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_execution_buffer_state_sequence
                    ON queue_items (state, sequence);
                CREATE TABLE IF NOT EXISTS buffer_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    ack_cursor INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS loss_markers (
                    marker_id TEXT PRIMARY KEY,
                    first_sequence INTEGER NOT NULL,
                    last_sequence INTEGER NOT NULL,
                    item_count INTEGER NOT NULL,
                    reason_code TEXT NOT NULL,
                    completeness TEXT NOT NULL,
                    trust_state TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO buffer_state (
                    singleton,
                    ack_cursor,
                    updated_at
                ) VALUES (1, 0, ?)
                """,
                (utc_now_iso(),),
            )
        try:
            self.path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    @staticmethod
    def _canonical_json(value: dict[str, Any]) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _assert_metadata_only(payload: dict[str, Any]) -> None:
        forbidden_keys = {
            "authorization",
            "cookie",
            "password",
            "reporter_token",
            "secret",
            "token",
        }

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    normalized = str(key).lower().replace("-", "_")
                    if normalized in forbidden_keys:
                        raise ToolError(
                            "execution buffer refuses credential-bearing payloads"
                        )
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
            elif isinstance(value, str):
                lowered = value.lower()
                if "bearer " in lowered or "dkr_report_" in lowered:
                    raise ToolError(
                        "execution buffer refuses credential-bearing payloads"
                    )

        walk(payload)

    @staticmethod
    def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {key: row[key] for key in row.keys()}

    def enqueue(
        self,
        *,
        operation: str,
        runtime_id: str,
        api_base: str,
        path_template: str,
        payload: dict[str, Any],
        idempotency_key: str,
        dependency_sequence: int | None = None,
        dependency_response_field: str | None = None,
        dependency_target: str | None = None,
    ) -> dict[str, Any]:
        self._assert_metadata_only(payload)
        canonical = self._canonical_json(payload)
        payload_bytes = len(canonical.encode("utf-8"))
        digest_input = self._canonical_json(
            {
                "api_base": api_base,
                "operation": operation,
                "runtime_id": runtime_id,
                "path_template": path_template,
                "payload": payload,
                "dependency_sequence": dependency_sequence,
                "dependency_response_field": dependency_response_field,
                "dependency_target": dependency_target,
            }
        )
        payload_sha256 = hashlib.sha256(
            digest_input.encode("utf-8")
        ).hexdigest()

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT *
                FROM queue_items
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if existing["payload_sha256"] != payload_sha256:
                    raise ToolError(
                        "execution buffer idempotency conflict: "
                        "same key has a different envelope"
                    )
                return self._row_dict(existing)

            pressure = connection.execute(
                """
                SELECT
                    COUNT(*) AS item_count,
                    COALESCE(SUM(payload_bytes), 0) AS byte_count
                FROM queue_items
                WHERE state = 'PENDING'
                """
            ).fetchone()
            assert pressure is not None
            if (
                int(pressure["item_count"]) + 1 > self.max_items
                or int(pressure["byte_count"]) + payload_bytes
                > self.max_bytes
            ):
                raise ToolError(
                    "execution buffer hard limit reached; "
                    "no item was discarded or accepted"
                )

            cursor = connection.execute(
                """
                INSERT INTO queue_items (
                    operation,
                    runtime_id,
                    api_base,
                    path_template,
                    payload_json,
                    payload_sha256,
                    payload_bytes,
                    idempotency_key,
                    dependency_sequence,
                    dependency_response_field,
                    dependency_target,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation,
                    runtime_id,
                    api_base,
                    path_template,
                    canonical,
                    payload_sha256,
                    payload_bytes,
                    idempotency_key,
                    dependency_sequence,
                    dependency_response_field,
                    dependency_target,
                    utc_now_iso(),
                ),
            )
            sequence = int(cursor.lastrowid)
            row = connection.execute(
                "SELECT * FROM queue_items WHERE sequence = ?",
                (sequence,),
            ).fetchone()
            assert row is not None
            return self._row_dict(row)

    def get(self, sequence: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM queue_items WHERE sequence = ?",
                (sequence,),
            ).fetchone()
        return self._row_dict(row) if row is not None else None

    def pending(self, *, limit: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM queue_items
                WHERE state = 'PENDING'
                ORDER BY sequence ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_dict(row) for row in rows]

    def materialize(
        self,
        item: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        path = str(item["path_template"])
        payload = json.loads(str(item["payload_json"]))
        dependency_sequence = item.get("dependency_sequence")
        if dependency_sequence is None:
            return path, payload

        dependency = self.get(int(dependency_sequence))
        if dependency is None:
            raise BufferDependencyPending("buffer dependency is missing")
        if dependency["state"] != "ACKED":
            raise BufferDependencyPending(
                f"buffer dependency {dependency_sequence} is "
                f"{dependency['state']}"
            )
        response = json.loads(str(dependency["ack_response_json"] or "{}"))
        response_field = str(item["dependency_response_field"])
        resolved = response.get(response_field)
        if not isinstance(resolved, str) or not resolved:
            raise BufferDependencyPending(
                f"buffer dependency {dependency_sequence} has no "
                f"{response_field}"
            )
        dependency_target = str(item["dependency_target"])
        if dependency_target.startswith("payload."):
            payload[dependency_target.removeprefix("payload.")] = resolved
        elif dependency_target.startswith("path."):
            path = path.replace(
                "{" + dependency_target.removeprefix("path.") + "}",
                resolved,
            )
        else:
            raise ToolError("unsupported execution buffer dependency target")
        return path, payload

    def record_retry(
        self,
        sequence: int,
        *,
        error_code: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE queue_items
                SET attempt_count = attempt_count + 1,
                    last_attempt_at = ?,
                    last_error_code = ?
                WHERE sequence = ? AND state = 'PENDING'
                """,
                (utc_now_iso(), error_code[:100], sequence),
            )

    def acknowledge(
        self,
        sequence: int,
        response: dict[str, Any],
    ) -> None:
        self._assert_metadata_only(response)
        response_json = self._canonical_json(response)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE queue_items
                SET state = 'ACKED',
                    attempt_count = attempt_count + 1,
                    last_attempt_at = ?,
                    last_error_code = NULL,
                    ack_response_json = ?,
                    acknowledged_at = ?
                WHERE sequence = ? AND state = 'PENDING'
                """,
                (
                    utc_now_iso(),
                    response_json,
                    utc_now_iso(),
                    sequence,
                ),
            )
            self._advance_ack_cursor(connection)

    def reject(
        self,
        sequence: int,
        *,
        error_code: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE queue_items
                SET state = 'REJECTED',
                    attempt_count = attempt_count + 1,
                    last_attempt_at = ?,
                    last_error_code = ?,
                    acknowledged_at = ?
                WHERE sequence = ? AND state = 'PENDING'
                """,
                (
                    utc_now_iso(),
                    error_code[:100],
                    utc_now_iso(),
                    sequence,
                ),
            )
            self._advance_ack_cursor(connection)

    def discard(
        self,
        *,
        first_sequence: int,
        last_sequence: int,
        reason_code: str,
    ) -> dict[str, Any]:
        if first_sequence < 1 or last_sequence < first_sequence:
            raise ToolError("invalid execution buffer discard range")
        reason = reason_code.strip()
        if (
            len(reason) < 4
            or len(reason) > 200
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._:/-]*", reason)
            is None
        ):
            raise ToolError(
                "discard reason must be 4-200 safe characters"
            )

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT sequence
                FROM queue_items
                WHERE state = 'PENDING'
                  AND sequence BETWEEN ? AND ?
                ORDER BY sequence
                """,
                (first_sequence, last_sequence),
            ).fetchall()
            if not rows:
                raise ToolError(
                    "discard range contains no pending execution envelopes"
                )
            actual_first = int(rows[0]["sequence"])
            actual_last = int(rows[-1]["sequence"])
            marker_id = f"loss_{uuid.uuid4().hex}"
            created_at = utc_now_iso()
            connection.execute(
                """
                UPDATE queue_items
                SET state = 'DISCARDED',
                    payload_json = '{}',
                    payload_bytes = 2,
                    last_error_code = 'OPERATOR_DISCARD',
                    acknowledged_at = ?
                WHERE state = 'PENDING'
                  AND sequence BETWEEN ? AND ?
                """,
                (created_at, first_sequence, last_sequence),
            )
            connection.execute(
                """
                INSERT INTO loss_markers (
                    marker_id,
                    first_sequence,
                    last_sequence,
                    item_count,
                    reason_code,
                    completeness,
                    trust_state,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, 'PARTIAL', 'DECLARED_LOSS', ?)
                """,
                (
                    marker_id,
                    actual_first,
                    actual_last,
                    len(rows),
                    reason,
                    created_at,
                ),
            )
            self._advance_ack_cursor(connection)
        return {
            "marker_id": marker_id,
            "first_sequence": actual_first,
            "last_sequence": actual_last,
            "item_count": len(rows),
            "reason_code": reason,
            "completeness": "PARTIAL",
            "trust_state": "DECLARED_LOSS",
            "created_at": created_at,
        }

    @staticmethod
    def _advance_ack_cursor(connection: sqlite3.Connection) -> None:
        state_row = connection.execute(
            "SELECT ack_cursor FROM buffer_state WHERE singleton = 1"
        ).fetchone()
        cursor = int(state_row["ack_cursor"]) if state_row else 0
        rows = connection.execute(
            """
            SELECT sequence, state
            FROM queue_items
            WHERE sequence > ?
            ORDER BY sequence
            """,
            (cursor,),
        ).fetchall()
        for row in rows:
            if row["state"] not in EXECUTION_BUFFER_TERMINAL_STATES:
                break
            cursor = int(row["sequence"])
        connection.execute(
            """
            UPDATE buffer_state
            SET ack_cursor = ?, updated_at = ?
            WHERE singleton = 1
            """,
            (cursor, utc_now_iso()),
        )

    def status(self) -> dict[str, Any]:
        with self._connect() as connection:
            state_rows = connection.execute(
                """
                SELECT
                    state,
                    COUNT(*) AS item_count,
                    COALESCE(SUM(payload_bytes), 0) AS byte_count,
                    COALESCE(SUM(attempt_count), 0) AS retry_count
                FROM queue_items
                GROUP BY state
                """
            ).fetchall()
            oldest = connection.execute(
                """
                SELECT created_at
                FROM queue_items
                WHERE state = 'PENDING'
                ORDER BY sequence
                LIMIT 1
                """
            ).fetchone()
            last_error = connection.execute(
                """
                SELECT last_error_code
                FROM queue_items
                WHERE last_error_code IS NOT NULL
                ORDER BY sequence DESC
                LIMIT 1
                """
            ).fetchone()
            cursor_row = connection.execute(
                "SELECT ack_cursor FROM buffer_state WHERE singleton = 1"
            ).fetchone()
            loss_rows = connection.execute(
                """
                SELECT *
                FROM loss_markers
                ORDER BY created_at DESC
                LIMIT 20
                """
            ).fetchall()

        counts = {
            str(row["state"]): {
                "items": int(row["item_count"]),
                "bytes": int(row["byte_count"]),
                "attempts": int(row["retry_count"]),
            }
            for row in state_rows
        }
        pending = counts.get(
            "PENDING",
            {"items": 0, "bytes": 0, "attempts": 0},
        )
        oldest_age_seconds: int | None = None
        if oldest is not None:
            created_at = datetime.fromisoformat(str(oldest["created_at"]))
            oldest_age_seconds = max(
                0,
                int(
                    (
                        datetime.now(timezone.utc)
                        - created_at.astimezone(timezone.utc)
                    ).total_seconds()
                ),
            )
        pressure_ratio = max(
            pending["items"] / self.max_items,
            pending["bytes"] / self.max_bytes,
        )
        return {
            "schema_version": "duckdock-execution-buffer/v1",
            "path": str(self.path),
            "ack_cursor": (
                int(cursor_row["ack_cursor"]) if cursor_row else 0
            ),
            "pending_items": pending["items"],
            "pending_bytes": pending["bytes"],
            "retry_count": pending["attempts"],
            "oldest_pending_age_seconds": oldest_age_seconds,
            "last_error_code": (
                str(last_error["last_error_code"])
                if last_error is not None
                else None
            ),
            "max_items": self.max_items,
            "max_bytes": self.max_bytes,
            "pressure_ratio": round(pressure_ratio, 6),
            "high_water": (
                pressure_ratio >= EXECUTION_BUFFER_HIGH_WATER_RATIO
            ),
            "hard_limit": pressure_ratio >= 1,
            "state_counts": counts,
            "loss_markers": [
                self._row_dict(row) for row in loss_rows
            ],
        }


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
        self.execution_buffer = ExecutionBuffer(
            self.paths.execution_buffer_path,
            max_items=int(
                os.environ.get(
                    "DUCKDOCK_EXECUTION_BUFFER_MAX_ITEMS",
                    DEFAULT_EXECUTION_BUFFER_MAX_ITEMS,
                )
            ),
            max_bytes=int(
                os.environ.get(
                    "DUCKDOCK_EXECUTION_BUFFER_MAX_BYTES",
                    DEFAULT_EXECUTION_BUFFER_MAX_BYTES,
                )
            ),
        )

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
                "name": "duckdock.execution.session_start",
                "description": "Open a governed DuckDock AgentSession for an OpenClaw-like runtime before its first run.",
                "inputSchema": {
                    "type": "object",
                    "required": ["external_session_id", "started_at"],
                    "properties": {
                        "api_base": {"type": "string"},
                        "reporter_token": {"type": "string"},
                        "external_session_id": {"type": "string"},
                        "deployment_public_id": {"type": "string"},
                        "started_at": {"type": "string", "description": "Timezone-aware ISO 8601 event timestamp."},
                        "sensitivity": {"type": "string", "enum": ["RESTRICTED", "CONFIDENTIAL", "INTERNAL"]},
                        "idempotency_key": {"type": "string"},
                    },
                },
            },
            {
                "name": "duckdock.execution.session_complete",
                "description": "Close a governed DuckDock AgentSession without sending transcript content.",
                "inputSchema": {
                    "type": "object",
                    "required": ["status", "ended_at"],
                    "properties": {
                        "api_base": {"type": "string"},
                        "reporter_token": {"type": "string"},
                        "session_public_id": {
                            "type": "string",
                            "description": "Server id, or use session_queue_sequence while offline.",
                        },
                        "session_queue_sequence": {
                            "type": "integer",
                            "minimum": 1,
                        },
                        "ended_at": {"type": "string", "description": "Timezone-aware ISO 8601 event timestamp."},
                        "status": {"type": "string", "enum": ["ENDED", "ABANDONED"]},
                        "run_count": {"type": "integer", "minimum": 0},
                        "error_count": {"type": "integer", "minimum": 0},
                        "idempotency_key": {"type": "string"},
                    },
                },
            },
            {
                "name": "duckdock.execution.run_start",
                "description": "Open a governed AgentRun and bind its stable external run/session ids to the OTLP trace id.",
                "inputSchema": {
                    "type": "object",
                    "required": ["external_run_id", "source_schema_version", "started_at"],
                    "properties": {
                        "api_base": {"type": "string"},
                        "reporter_token": {"type": "string"},
                        "external_run_id": {"type": "string"},
                        "session_public_id": {"type": "string"},
                        "session_queue_sequence": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "Durable session-start sequence to resolve before replay.",
                        },
                        "deployment_public_id": {"type": "string"},
                        "otel_trace_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
                        "root_span_id": {"type": "string", "pattern": "^[0-9a-f]{16}$"},
                        "attempt": {"type": "integer", "minimum": 1},
                        "source_schema": {"type": "string", "description": "Defaults to configured runtime provider."},
                        "source_schema_version": {"type": "string"},
                        "started_at": {"type": "string", "description": "Timezone-aware ISO 8601 event timestamp."},
                        "idempotency_key": {"type": "string"},
                    },
                },
            },
            {
                "name": "duckdock.execution.run_complete",
                "description": "Close a governed AgentRun with metadata-only aggregate counters and the same OTLP correlation ids.",
                "inputSchema": {
                    "type": "object",
                    "required": ["status", "ended_at"],
                    "properties": {
                        "api_base": {"type": "string"},
                        "reporter_token": {"type": "string"},
                        "run_public_id": {
                            "type": "string",
                            "description": "Server id, or use run_queue_sequence while offline.",
                        },
                        "run_queue_sequence": {
                            "type": "integer",
                            "minimum": 1,
                        },
                        "ended_at": {"type": "string", "description": "Timezone-aware ISO 8601 event timestamp."},
                        "status": {"type": "string", "enum": ["SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"]},
                        "otel_trace_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
                        "root_span_id": {"type": "string", "pattern": "^[0-9a-f]{16}$"},
                        "duration_ms": {"type": "integer", "minimum": 0},
                        "step_count": {"type": "integer", "minimum": 0},
                        "model_call_count": {"type": "integer", "minimum": 0},
                        "tool_call_count": {"type": "integer", "minimum": 0},
                        "input_token_count": {"type": "integer", "minimum": 0},
                        "output_token_count": {"type": "integer", "minimum": 0},
                        "error_type": {"type": "string"},
                        "idempotency_key": {"type": "string"},
                    },
                },
            },
            {
                "name": "duckdock.execution.buffer_status",
                "description": "Inspect the durable execution-envelope buffer, ack cursor, pressure, retry state, and declared loss markers.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "duckdock.execution.buffer_flush",
                "description": "Replay pending execution envelopes in durable queue order using the current Reporter credential.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "api_base": {"type": "string"},
                        "runtime_id": {"type": "string"},
                        "reporter_token": {"type": "string"},
                        "max_items": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 1000,
                        },
                    },
                },
            },
            {
                "name": "duckdock.execution.buffer_discard",
                "description": "Explicitly discard an exact pending sequence range and emit a durable PARTIAL/DECLARED_LOSS marker.",
                "inputSchema": {
                    "type": "object",
                    "required": [
                        "first_sequence",
                        "last_sequence",
                        "reason_code",
                        "confirm",
                    ],
                    "properties": {
                        "first_sequence": {
                            "type": "integer",
                            "minimum": 1,
                        },
                        "last_sequence": {
                            "type": "integer",
                            "minimum": 1,
                        },
                        "reason_code": {
                            "type": "string",
                            "minLength": 4,
                            "maxLength": 200,
                        },
                        "confirm": {
                            "type": "boolean",
                            "description": "Must be true; no automatic discard exists.",
                        },
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
            "duckdock.execution.session_start": self.start_execution_session,
            "duckdock.execution.session_complete": self.complete_execution_session,
            "duckdock.execution.run_start": self.start_execution_run,
            "duckdock.execution.run_complete": self.complete_execution_run,
            "duckdock.execution.buffer_status": self.execution_buffer_status,
            "duckdock.execution.buffer_flush": self.flush_execution_buffer,
            "duckdock.execution.buffer_discard": self.discard_execution_buffer,
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

    def start_execution_session(self, args: dict[str, Any]) -> dict[str, Any]:
        config = self._merged_config(args)
        external_session_id = self._required_text(
            args,
            "external_session_id",
        )
        payload: dict[str, Any] = {
            "external_session_id": external_session_id,
            "started_at": self._timestamp(args.get("started_at"), required=True),
            "sensitivity": str(args.get("sensitivity") or "RESTRICTED"),
            "content_capture_mode": "metadata_only",
            "metadata": {
                "harness.name": str(config.get("provider") or "openclaw"),
                "service.name": "openclaw",
            },
        }
        self._copy_present(
            args,
            payload,
            "deployment_public_id",
        )
        response = self._post_execution(
            config,
            operation="session.start",
            path="/reporter/sessions",
            payload=payload,
            idempotency_key=self._idempotency_key(
                args,
                operation="session.start",
                identity=external_session_id,
            ),
        )
        return self._execution_result(
            response,
            fields=(
                "session_public_id",
                "external_session_id",
                "status",
                "started_at",
                "content_capture_mode",
            ),
        )

    def complete_execution_session(
        self,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        config = self._merged_config(args)
        session_public_id, session_queue_sequence = (
            self._resource_or_dependency(
                args,
                resource_key="session_public_id",
                dependency_key="session_queue_sequence",
            )
        )
        session_status = str(args.get("status") or "")
        if session_status not in {"ENDED", "ABANDONED"}:
            raise ToolError("status must be ENDED or ABANDONED")
        payload: dict[str, Any] = {
            "ended_at": self._timestamp(args.get("ended_at"), required=True),
            "status": session_status,
        }
        self._copy_present(args, payload, "run_count", "error_count")
        response = self._post_execution(
            config,
            operation="session.complete",
            path=(
                f"/reporter/sessions/{session_public_id}/complete"
                if session_public_id
                else "/reporter/sessions/{session_public_id}/complete"
            ),
            payload=payload,
            idempotency_key=self._idempotency_key(
                args,
                operation="session.complete",
                identity=(
                    f"{session_public_id or f'queue-{session_queue_sequence}'}:"
                    f"{session_status}"
                ),
            ),
            dependency_sequence=session_queue_sequence,
            dependency_response_field=(
                "session_public_id"
                if session_queue_sequence is not None
                else None
            ),
            dependency_target=(
                "path.session_public_id"
                if session_queue_sequence is not None
                else None
            ),
        )
        return self._execution_result(
            response,
            fields=(
                "session_public_id",
                "external_session_id",
                "status",
                "ended_at",
                "run_count",
                "error_count",
            ),
        )

    def start_execution_run(self, args: dict[str, Any]) -> dict[str, Any]:
        config = self._merged_config(args)
        external_run_id = self._required_text(args, "external_run_id")
        source_schema_version = self._required_text(
            args,
            "source_schema_version",
        )
        payload: dict[str, Any] = {
            "external_run_id": external_run_id,
            "attempt": int(args.get("attempt") or 1),
            "source_schema": str(
                args.get("source_schema")
                or config.get("provider")
                or "openclaw"
            ),
            "source_schema_version": source_schema_version,
            "started_at": self._timestamp(args.get("started_at"), required=True),
            "content_capture_mode": "metadata_only",
            "metadata": {
                "harness.name": str(config.get("provider") or "openclaw"),
                "service.name": "openclaw",
            },
        }
        session_queue_sequence = self._optional_positive_sequence(
            args.get("session_queue_sequence"),
            key="session_queue_sequence",
        )
        if (
            args.get("session_public_id") is not None
            and session_queue_sequence is not None
        ):
            raise ToolError(
                "provide session_public_id or session_queue_sequence, not both"
            )
        self._copy_present(
            args,
            payload,
            "session_public_id",
            "deployment_public_id",
            "otel_trace_id",
            "root_span_id",
        )
        self._validate_trace_fields(payload)
        response = self._post_execution(
            config,
            operation="run.start",
            path="/reporter/runs",
            payload=payload,
            idempotency_key=self._idempotency_key(
                args,
                operation="run.start",
                identity=(
                    f"{external_run_id}:{payload['attempt']}:"
                    f"{payload.get('otel_trace_id') or ''}"
                ),
            ),
            dependency_sequence=session_queue_sequence,
            dependency_response_field=(
                "session_public_id"
                if session_queue_sequence is not None
                else None
            ),
            dependency_target=(
                "payload.session_public_id"
                if session_queue_sequence is not None
                else None
            ),
        )
        return self._execution_result(
            response,
            fields=(
                "run_public_id",
                "external_run_id",
                "session_public_id",
                "status",
                "trust_level",
                "trust_source",
                "source_schema",
                "source_schema_version",
                "normalizer_version",
                "otel_trace_id",
                "root_span_id",
                "started_at",
                "content_capture_mode",
            ),
        )

    def complete_execution_run(
        self,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        config = self._merged_config(args)
        run_public_id, run_queue_sequence = self._resource_or_dependency(
            args,
            resource_key="run_public_id",
            dependency_key="run_queue_sequence",
        )
        run_status = str(args.get("status") or "")
        if run_status not in {
            "SUCCEEDED",
            "FAILED",
            "CANCELLED",
            "TIMED_OUT",
        }:
            raise ToolError(
                "status must be SUCCEEDED, FAILED, CANCELLED, or TIMED_OUT"
            )
        payload: dict[str, Any] = {
            "ended_at": self._timestamp(args.get("ended_at"), required=True),
            "status": run_status,
        }
        self._copy_present(
            args,
            payload,
            "otel_trace_id",
            "root_span_id",
            "duration_ms",
            "step_count",
            "model_call_count",
            "tool_call_count",
            "input_token_count",
            "output_token_count",
            "error_type",
        )
        self._validate_trace_fields(payload)
        response = self._post_execution(
            config,
            operation="run.complete",
            path=(
                f"/reporter/runs/{run_public_id}/complete"
                if run_public_id
                else "/reporter/runs/{run_public_id}/complete"
            ),
            payload=payload,
            idempotency_key=self._idempotency_key(
                args,
                operation="run.complete",
                identity=(
                    f"{run_public_id or f'queue-{run_queue_sequence}'}:"
                    f"{run_status}:"
                    f"{payload.get('ended_at')}"
                ),
            ),
            dependency_sequence=run_queue_sequence,
            dependency_response_field=(
                "run_public_id"
                if run_queue_sequence is not None
                else None
            ),
            dependency_target=(
                "path.run_public_id"
                if run_queue_sequence is not None
                else None
            ),
        )
        return self._execution_result(
            response,
            fields=(
                "run_public_id",
                "external_run_id",
                "session_public_id",
                "status",
                "otel_trace_id",
                "root_span_id",
                "ended_at",
                "duration_ms",
                "step_count",
                "model_call_count",
                "tool_call_count",
                "input_token_count",
                "output_token_count",
                "error_type",
            ),
        )

    def execution_buffer_status(
        self,
        _: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.execution_buffer.status()

    def flush_execution_buffer(
        self,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        config = self._merged_config(args)
        max_items = int(args.get("max_items") or 100)
        if max_items < 1 or max_items > 1000:
            raise ToolError("max_items must be between 1 and 1000")
        result = self._flush_execution_buffer(
            config,
            max_items=max_items,
        )
        return {
            **result,
            "buffer": self.execution_buffer.status(),
        }

    def discard_execution_buffer(
        self,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        if args.get("confirm") is not True:
            raise ToolError(
                "confirm=true is required; the buffer never auto-discards"
            )
        marker = self.execution_buffer.discard(
            first_sequence=self._required_positive_int(
                args,
                "first_sequence",
            ),
            last_sequence=self._required_positive_int(
                args,
                "last_sequence",
            ),
            reason_code=self._required_text(args, "reason_code"),
        )
        return {
            "discarded": True,
            "loss_marker": marker,
            "buffer": self.execution_buffer.status(),
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
            "execution_buffer": self.execution_buffer.status(),
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
            "execution_buffer_preserved": True,
            "execution_buffer": self.execution_buffer.status(),
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
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return post_json(
            url,
            token=token,
            payload=payload,
            timeout=timeout,
            headers=headers,
        )

    def _post_execution(
        self,
        config: dict[str, Any],
        *,
        operation: str,
        path: str,
        payload: dict[str, Any],
        idempotency_key: str,
        dependency_sequence: int | None = None,
        dependency_response_field: str | None = None,
        dependency_target: str | None = None,
    ) -> dict[str, Any]:
        api_base = self._execution_api_base(str(config["api_base"]))
        item = self.execution_buffer.enqueue(
            operation=operation,
            runtime_id=str(config["runtime_id"]),
            api_base=api_base,
            path_template=path,
            payload=payload,
            idempotency_key=idempotency_key,
            dependency_sequence=dependency_sequence,
            dependency_response_field=dependency_response_field,
            dependency_target=dependency_target,
        )
        sequence = int(item["sequence"])
        if item["state"] == "ACKED":
            return json.loads(str(item["ack_response_json"] or "{}"))
        if item["state"] in {"REJECTED", "DISCARDED"}:
            raise ToolError(
                f"execution buffer item {sequence} is {item['state']}"
            )

        self._flush_execution_buffer(config, max_items=100)
        current = self.execution_buffer.get(sequence)
        if current is None:
            raise ToolError("execution buffer item disappeared")
        if current["state"] == "ACKED":
            return json.loads(str(current["ack_response_json"] or "{}"))
        if current["state"] == "REJECTED":
            raise ToolError(
                f"execution write permanently rejected: "
                f"{current['last_error_code']}"
            )
        return {
            "_duckdock_buffer": {
                "submitted": False,
                "queued": True,
                "queue_sequence": sequence,
                "idempotency_key": idempotency_key,
                "state": current["state"],
                "attempt_count": int(current["attempt_count"]),
                "last_error_code": current["last_error_code"],
                "ack_cursor": self.execution_buffer.status()["ack_cursor"],
            }
        }

    def _flush_execution_buffer(
        self,
        config: dict[str, Any],
        *,
        max_items: int,
    ) -> dict[str, Any]:
        processed: list[dict[str, Any]] = []
        stopped_reason: str | None = None
        for item in self.execution_buffer.pending(limit=max_items):
            sequence = int(item["sequence"])
            if str(item["runtime_id"]) != str(config["runtime_id"]):
                self.execution_buffer.record_retry(
                    sequence,
                    error_code="RUNTIME_CREDENTIAL_MISMATCH",
                )
                stopped_reason = "RUNTIME_CREDENTIAL_MISMATCH"
                break
            try:
                path, payload = self.execution_buffer.materialize(item)
            except BufferDependencyPending:
                self.execution_buffer.record_retry(
                    sequence,
                    error_code="DEPENDENCY_PENDING",
                )
                stopped_reason = "DEPENDENCY_PENDING"
                break
            try:
                response = self._post_json(
                    f"{item['api_base']}{path}",
                    token=str(config["reporter_token"]),
                    payload=payload,
                    headers={
                        "Idempotency-Key": str(
                            item["idempotency_key"]
                        )
                    },
                )
            except PostError as exc:
                if exc.retryable:
                    self.execution_buffer.record_retry(
                        sequence,
                        error_code=exc.safe_code,
                    )
                    stopped_reason = exc.safe_code
                    break
                self.execution_buffer.reject(
                    sequence,
                    error_code=exc.safe_code,
                )
                processed.append(
                    {
                        "sequence": sequence,
                        "state": "REJECTED",
                        "error_code": exc.safe_code,
                    }
                )
            except ToolError:
                self.execution_buffer.record_retry(
                    sequence,
                    error_code="ADAPTER_UNAVAILABLE",
                )
                stopped_reason = "ADAPTER_UNAVAILABLE"
                break
            else:
                self.execution_buffer.acknowledge(sequence, response)
                processed.append(
                    {
                        "sequence": sequence,
                        "state": "ACKED",
                    }
                )
        return {
            "processed": processed,
            "processed_count": len(processed),
            "stopped_reason": stopped_reason,
        }

    @staticmethod
    def _execution_api_base(api_base: str) -> str:
        base = api_base.rstrip("/")
        if base.endswith("/api/v1"):
            return f"{base[:-len('/api/v1')]}/api/v2"
        if base.endswith("/api/v2"):
            return base
        if re.search(r"/api/v[0-9]+$", base):
            raise ToolError("execution lifecycle requires DuckDock API v2")
        return f"{base}/api/v2"

    @staticmethod
    def _required_text(args: dict[str, Any], key: str) -> str:
        value = str(args.get(key) or "").strip()
        if not value:
            raise ToolError(f"{key} is required")
        return value

    @staticmethod
    def _optional_positive_sequence(
        value: Any,
        *,
        key: str,
    ) -> int | None:
        if value is None:
            return None
        try:
            sequence = int(value)
        except (TypeError, ValueError) as exc:
            raise ToolError(f"{key} must be a positive integer") from exc
        if sequence < 1:
            raise ToolError(f"{key} must be a positive integer")
        return sequence

    @classmethod
    def _required_positive_int(
        cls,
        args: dict[str, Any],
        key: str,
    ) -> int:
        value = cls._optional_positive_sequence(args.get(key), key=key)
        if value is None:
            raise ToolError(f"{key} is required")
        return value

    @classmethod
    def _resource_or_dependency(
        cls,
        args: dict[str, Any],
        *,
        resource_key: str,
        dependency_key: str,
    ) -> tuple[str | None, int | None]:
        resource = str(args.get(resource_key) or "").strip() or None
        dependency = cls._optional_positive_sequence(
            args.get(dependency_key),
            key=dependency_key,
        )
        if resource is None and dependency is None:
            raise ToolError(
                f"{resource_key} or {dependency_key} is required"
            )
        if resource is not None and dependency is not None:
            raise ToolError(
                f"provide {resource_key} or {dependency_key}, not both"
            )
        return resource, dependency

    @staticmethod
    def _timestamp(value: Any, *, required: bool = False) -> str:
        if value is None:
            if required:
                raise ToolError("timezone-aware event timestamp is required")
            return utc_now_iso()
        text = str(value).strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ToolError("timestamp must use ISO 8601 format") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ToolError("timestamp must include a timezone")
        return parsed.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _copy_present(
        source: dict[str, Any],
        target: dict[str, Any],
        *keys: str,
    ) -> None:
        for key in keys:
            if source.get(key) is not None:
                target[key] = source[key]

    @staticmethod
    def _validate_trace_fields(payload: dict[str, Any]) -> None:
        trace_id = payload.get("otel_trace_id")
        if trace_id is not None and re.fullmatch(
            r"[0-9a-f]{32}",
            str(trace_id),
        ) is None:
            raise ToolError("otel_trace_id must be 32 lowercase hex characters")
        span_id = payload.get("root_span_id")
        if span_id is not None and re.fullmatch(
            r"[0-9a-f]{16}",
            str(span_id),
        ) is None:
            raise ToolError("root_span_id must be 16 lowercase hex characters")

    @staticmethod
    def _idempotency_key(
        args: dict[str, Any],
        *,
        operation: str,
        identity: str,
    ) -> str:
        explicit = str(args.get("idempotency_key") or "").strip()
        if explicit:
            if re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", explicit) is None:
                raise ToolError(
                    "idempotency_key must be 8-128 safe characters"
                )
            return explicit
        digest = hashlib.sha256(
            f"{operation}:{identity}".encode("utf-8")
        ).hexdigest()[:32]
        return f"dd.{operation}.{digest}"

    @staticmethod
    def _execution_result(
        response: dict[str, Any],
        *,
        fields: tuple[str, ...],
    ) -> dict[str, Any]:
        buffered = response.get("_duckdock_buffer")
        if isinstance(buffered, dict):
            return buffered
        result = {
            key: response.get(key)
            for key in fields
            if response.get(key) is not None
        }
        return {"submitted": True, **result}

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
