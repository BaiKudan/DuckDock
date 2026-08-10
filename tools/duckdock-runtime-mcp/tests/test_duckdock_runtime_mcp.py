from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "duckdock_runtime_mcp.py"
SPEC = importlib.util.spec_from_file_location("duckdock_runtime_mcp", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
duckdock_runtime_mcp = importlib.util.module_from_spec(SPEC)
sys.modules["duckdock_runtime_mcp"] = duckdock_runtime_mcp
SPEC.loader.exec_module(duckdock_runtime_mcp)


def make_server(tmp_path: Path):
    paths = duckdock_runtime_mcp.RuntimePaths(
        home=tmp_path,
        workbuddy_home=tmp_path / ".workbuddy",
        config_home=tmp_path / ".duckdock" / "runtime-mcp",
    )
    return duckdock_runtime_mcp.DuckDockRuntimeMcp(
        default_api_base="http://duckdock.test/api/v1",
        paths=paths,
    )


def test_tools_list_contains_reporter_lifecycle_tools(tmp_path: Path) -> None:
    server = make_server(tmp_path)
    response = server.handle_request({"jsonrpc": "2.0", "id": "1", "method": "tools/list"})
    assert response is not None
    tool_names = {item["name"] for item in response["result"]["tools"]}

    assert "duckdock.runtime.detect" in tool_names
    assert "duckdock.reporter.install" in tool_names
    assert "duckdock.reporter.configure" in tool_names
    assert "duckdock.reporter.dry_run" in tool_names
    assert "duckdock.reporter.run_structured" in tool_names
    assert "duckdock.execution.session_start" in tool_names
    assert "duckdock.execution.session_complete" in tool_names
    assert "duckdock.execution.run_start" in tool_names
    assert "duckdock.execution.run_complete" in tool_names
    assert "duckdock.execution.buffer_status" in tool_names
    assert "duckdock.execution.buffer_flush" in tool_names
    assert "duckdock.execution.buffer_discard" in tool_names
    assert "duckdock.reporter.schedule" in tool_names
    assert "duckdock.reporter.status" in tool_names
    assert "duckdock.reporter.pause" in tool_names
    assert "duckdock.reporter.uninstall" in tool_names


def test_configure_redacts_reporter_token_in_tool_result(tmp_path: Path) -> None:
    server = make_server(tmp_path)
    result = server.call_tool(
        "duckdock.reporter.configure",
        {
            "api_base": "http://duckdock.test/api/v1",
            "runtime_id": "32",
            "provider": "workbuddy",
            "reporter_token": "dkr_report_test",
        },
    )

    assert result["configured"] is True
    assert result["token_stored"] is True
    assert result["config"]["reporter_token"] == "<redacted>"

    stored = json.loads(server.paths.config_path.read_text(encoding="utf-8"))
    assert stored["reporter_token"] == "dkr_report_test"


def test_detect_reports_local_paths_without_requiring_workbuddy(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(duckdock_runtime_mcp, "command_lines", lambda: [])
    server = make_server(tmp_path)
    result = server.call_tool("duckdock.runtime.detect", {})

    assert result["provider"] == "workbuddy"
    assert result["workbuddy_home_exists"] is False
    assert result["reporter_skill_installed"] is False
    assert result["config_exists"] is False
    assert result["workbuddy_mcp"]["callable"] is False
    assert result["workbuddy_mcp_config"]["exists"] is False
    assert result["workbuddy_mcp_config"]["duckdock_runtime_registered"] is False


def test_detect_reports_workbuddy_mcp_registration_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(duckdock_runtime_mcp, "command_lines", lambda: [])
    workbuddy_home = tmp_path / ".workbuddy"
    workbuddy_home.mkdir()
    (workbuddy_home / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "connector-proxy": {"type": "http", "url": "http://127.0.0.1:54321/mcp"},
                    "duckdock-runtime": {
                        "command": "python",
                        "args": ["/repo/tools/duckdock-runtime-mcp/duckdock_runtime_mcp.py"],
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    server = make_server(tmp_path)

    result = server.call_tool("duckdock.runtime.detect", {})

    config = result["workbuddy_mcp_config"]
    assert config["exists"] is True
    assert config["readable"] is True
    assert config["server_names"] == ["connector-proxy", "duckdock-runtime"]
    assert config["duckdock_runtime_registered"] is True


def test_json_rpc_tools_call_returns_mcp_content(tmp_path: Path) -> None:
    server = make_server(tmp_path)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": "call-1",
            "method": "tools/call",
            "params": {
                "name": "duckdock.runtime.detect",
                "arguments": {},
            },
        }
    )

    assert response is not None
    assert response["id"] == "call-1"
    assert response["result"]["isError"] is False
    assert response["result"]["content"][0]["type"] == "text"


def test_run_structured_posts_heartbeat_and_structured_report_without_leaking_token(tmp_path: Path, monkeypatch) -> None:
    server = make_server(tmp_path)
    server.call_tool(
        "duckdock.reporter.configure",
        {
            "api_base": "http://duckdock.test/api/v1",
            "runtime_id": "32",
            "provider": "workbuddy",
            "reporter_token": "dkr_report_test",
        },
    )
    calls: list[tuple[str, str | None, dict]] = []

    def fake_post_json(url: str, *, token: str | None, payload: dict, timeout: int = 30) -> dict:
        calls.append((url, token, payload))
        if url.endswith("/reporters/heartbeat"):
            return {"status": "ok", "last_seen_at": "2030-01-01T00:00:00Z", "upload_recommended": False}
        if url.endswith("/reports/structured"):
            return {
                "report_id": "srpt_test",
                "status": "succeeded",
                "job": {"id": 7},
                "work_trace": {"id": 8},
                "assets": [{"id": 1}],
                "memory_candidates": [{"id": 2}, {"id": 3}],
            }
        raise AssertionError(url)

    monkeypatch.setattr(server, "_post_json", fake_post_json)

    result = server.call_tool("duckdock.reporter.run_structured", {"report_type": "daily"})

    assert result["submitted"] is True
    assert result["report_id"] == "srpt_test"
    assert result["collection_job_id"] == 7
    assert result["work_trace_id"] == 8
    assert result["asset_count"] == 1
    assert "dkr_report_test" not in json.dumps(result)
    assert [call[0] for call in calls] == [
        "http://duckdock.test/api/v1/reporters/heartbeat",
        "http://duckdock.test/api/v1/reports/structured",
    ]
    assert all(call[1] == "dkr_report_test" for call in calls)
    report_payload = calls[1][2]
    assert report_payload["schema_version"] == "duckdock-structured-report-v1"
    assert report_payload["report_type"] == "daily"
    assert report_payload["asset_refs"]


def test_workbuddy_payload_uses_hashed_device_id_without_raw_hostname(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(duckdock_runtime_mcp, "command_lines", lambda: [])
    monkeypatch.setattr(duckdock_runtime_mcp.socket, "gethostname", lambda: "private-hostname")
    monkeypatch.setattr(duckdock_runtime_mcp.platform, "node", lambda: "private-hostname")
    server = make_server(tmp_path)

    heartbeat = server._heartbeat_payload(report_type="daily")
    structured = server._structured_report_payload({"runtime_id": "32"}, report_type="daily")

    encoded = json.dumps({"heartbeat": heartbeat, "structured": structured}, ensure_ascii=False)
    assert heartbeat["device_id"].startswith("workbuddy-")
    assert heartbeat["device_id"] != "private-hostname-workbuddy"
    assert "private-hostname" not in encoded


def test_workbuddy_automation_prompt_defaults_to_structured_report(tmp_path: Path) -> None:
    server = make_server(tmp_path)
    prompt = server._workbuddy_prompt(
        {
            "api_base": "http://duckdock.test/api/v1",
            "runtime_id": "32",
            "provider": "workbuddy",
            "reporter_token": "dkr_report_test",
        },
        mode="weekly",
    )

    assert "duckdock.reporter.run_structured" in prompt
    assert "duckdock-structured-report-v1" in prompt
    assert "Create duckdock-pack-v1.zip" not in prompt
    assert "dkr_report_test" not in prompt


def test_execution_lifecycle_preserves_openclaw_run_session_trace_correlation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    server = make_server(tmp_path)
    server.call_tool(
        "duckdock.reporter.configure",
        {
            "api_base": "http://duckdock.test/api/v1",
            "runtime_id": "32",
            "provider": "openclaw",
            "reporter_token": "dkr_report_test",
        },
    )
    calls: list[tuple[str, str | None, dict, dict[str, str]]] = []

    def fake_post_json(
        url: str,
        *,
        token: str | None,
        payload: dict,
        timeout: int = 30,
        headers: dict[str, str] | None = None,
    ) -> dict:
        calls.append((url, token, payload, headers or {}))
        if url.endswith("/reporter/sessions"):
            return {
                "session_public_id": "ses_01",
                "external_session_id": "openclaw-session-01",
                "status": "ACTIVE",
                "started_at": payload["started_at"],
                "content_capture_mode": "metadata_only",
            }
        if url.endswith("/reporter/runs"):
            return {
                "run_public_id": "run_01",
                "external_run_id": "openclaw-run-01",
                "session_public_id": "ses_01",
                "status": "STARTED",
                "trust_level": "CHANNEL_AUTHENTICATED",
                "trust_source": "REPORTER",
                "source_schema": "openclaw",
                "source_schema_version": "2026.7.1",
                "normalizer_version": "duckdock-run-v1",
                "otel_trace_id": "a" * 32,
                "root_span_id": "b" * 16,
                "started_at": payload["started_at"],
                "content_capture_mode": "metadata_only",
            }
        if url.endswith("/reporter/runs/run_01/complete"):
            return {
                "run_public_id": "run_01",
                "external_run_id": "openclaw-run-01",
                "session_public_id": "ses_01",
                "status": "SUCCEEDED",
                "otel_trace_id": "a" * 32,
                "root_span_id": "b" * 16,
                "ended_at": payload["ended_at"],
                "input_token_count": 41,
                "output_token_count": 17,
            }
        if url.endswith("/reporter/sessions/ses_01/complete"):
            return {
                "session_public_id": "ses_01",
                "external_session_id": "openclaw-session-01",
                "status": "ENDED",
                "ended_at": payload["ended_at"],
                "run_count": 1,
                "error_count": 0,
            }
        raise AssertionError(url)

    monkeypatch.setattr(server, "_post_json", fake_post_json)
    started_at = "2026-07-30T08:00:00+08:00"
    ended_at = "2026-07-30T08:01:00+08:00"

    session = server.call_tool(
        "duckdock.execution.session_start",
        {
            "external_session_id": "openclaw-session-01",
            "started_at": started_at,
        },
    )
    run_args = {
        "external_run_id": "openclaw-run-01",
        "session_public_id": session["session_public_id"],
        "source_schema_version": "2026.7.1",
        "otel_trace_id": "a" * 32,
        "root_span_id": "b" * 16,
        "started_at": started_at,
    }
    run = server.call_tool("duckdock.execution.run_start", run_args)
    repeated = server.call_tool("duckdock.execution.run_start", run_args)
    completed_run = server.call_tool(
        "duckdock.execution.run_complete",
        {
            "run_public_id": run["run_public_id"],
            "status": "SUCCEEDED",
            "ended_at": ended_at,
            "otel_trace_id": "a" * 32,
            "root_span_id": "b" * 16,
            "input_token_count": 41,
            "output_token_count": 17,
        },
    )
    completed_session = server.call_tool(
        "duckdock.execution.session_complete",
        {
            "session_public_id": session["session_public_id"],
            "status": "ENDED",
            "ended_at": ended_at,
            "run_count": 1,
            "error_count": 0,
        },
    )

    assert run["external_run_id"] == "openclaw-run-01"
    assert run["otel_trace_id"] == "a" * 32
    assert repeated["run_public_id"] == run["run_public_id"]
    assert completed_run["status"] == "SUCCEEDED"
    assert completed_session["status"] == "ENDED"
    assert all(call[0].startswith("http://duckdock.test/api/v2/") for call in calls)
    assert all(call[1] == "dkr_report_test" for call in calls)
    assert len(calls) == 4
    assert server.execution_buffer.status()["ack_cursor"] == 4
    assert server.execution_buffer.status()["pending_items"] == 0
    run_payload = calls[1][2]
    assert run_payload["external_run_id"] == "openclaw-run-01"
    assert run_payload["session_public_id"] == "ses_01"
    assert run_payload["otel_trace_id"] == "a" * 32
    assert run_payload["content_capture_mode"] == "metadata_only"
    assert not {
        "namespace_id",
        "runtime_id",
        "trust_level",
        "trust_source",
    }.intersection(run_payload)
    assert "dkr_report_test" not in json.dumps(
        {
            "session": session,
            "run": run,
            "completed_run": completed_run,
            "completed_session": completed_session,
        }
    )


def test_execution_lifecycle_rejects_invalid_trace_and_naive_time(
    tmp_path: Path,
) -> None:
    server = make_server(tmp_path)
    server.call_tool(
        "duckdock.reporter.configure",
        {
            "api_base": "http://duckdock.test/api/v1",
            "runtime_id": "32",
            "provider": "openclaw",
            "reporter_token": "dkr_report_test",
        },
    )

    with pytest.raises(duckdock_runtime_mcp.ToolError):
        server.call_tool(
            "duckdock.execution.run_start",
            {
                "external_run_id": "openclaw-run-01",
                "source_schema_version": "2026.7.1",
                "otel_trace_id": "not-a-trace-id",
                "started_at": "2026-07-30T08:00:00+08:00",
            },
        )
    with pytest.raises(duckdock_runtime_mcp.ToolError):
        server.call_tool(
            "duckdock.execution.session_start",
            {
                "external_session_id": "openclaw-session-01",
                "started_at": "2026-07-30T08:00:00",
            },
        )


def test_execution_buffer_survives_restart_and_resolves_ordered_dependencies(
    tmp_path: Path,
    monkeypatch,
) -> None:
    server = make_server(tmp_path)
    server.call_tool(
        "duckdock.reporter.configure",
        {
            "api_base": "http://duckdock.test/api/v1",
            "runtime_id": "32",
            "provider": "openclaw",
            "reporter_token": "dkr_report_initial",
        },
    )

    def offline(*args, **kwargs):
        raise duckdock_runtime_mcp.PostError(
            "offline",
            safe_code="NETWORK_UNAVAILABLE",
            retryable=True,
        )

    monkeypatch.setattr(server, "_post_json", offline)
    started_at = "2026-07-30T08:00:00+08:00"
    ended_at = "2026-07-30T08:01:00+08:00"
    session = server.call_tool(
        "duckdock.execution.session_start",
        {
            "external_session_id": "openclaw-session-buffered",
            "started_at": started_at,
        },
    )
    run = server.call_tool(
        "duckdock.execution.run_start",
        {
            "external_run_id": "openclaw-run-buffered",
            "session_queue_sequence": session["queue_sequence"],
            "source_schema_version": "2026.7.1",
            "otel_trace_id": "a" * 32,
            "root_span_id": "b" * 16,
            "started_at": started_at,
        },
    )
    completed_run = server.call_tool(
        "duckdock.execution.run_complete",
        {
            "run_queue_sequence": run["queue_sequence"],
            "status": "SUCCEEDED",
            "ended_at": ended_at,
            "otel_trace_id": "a" * 32,
            "root_span_id": "b" * 16,
        },
    )
    completed_session = server.call_tool(
        "duckdock.execution.session_complete",
        {
            "session_queue_sequence": session["queue_sequence"],
            "status": "ENDED",
            "ended_at": ended_at,
            "run_count": 1,
            "error_count": 0,
        },
    )

    assert session["queued"] is True
    assert run["queued"] is True
    assert completed_run["queued"] is True
    assert completed_session["queued"] is True
    assert server.execution_buffer.status()["pending_items"] == 4
    persisted = server.paths.execution_buffer_path.read_bytes()
    wal_path = Path(f"{server.paths.execution_buffer_path}-wal")
    if wal_path.exists():
        persisted += wal_path.read_bytes()
    assert b"dkr_report_initial" not in persisted

    restarted = make_server(tmp_path)
    calls: list[tuple[str, str | None, dict]] = []

    def online(
        url: str,
        *,
        token: str | None,
        payload: dict,
        timeout: int = 30,
        headers: dict[str, str] | None = None,
    ) -> dict:
        calls.append((url, token, payload))
        if url.endswith("/reporter/sessions"):
            return {
                "session_public_id": "ses_buffered",
                "external_session_id": "openclaw-session-buffered",
                "status": "ACTIVE",
            }
        if url.endswith("/reporter/runs"):
            assert payload["session_public_id"] == "ses_buffered"
            return {
                "run_public_id": "run_buffered",
                "external_run_id": "openclaw-run-buffered",
                "session_public_id": "ses_buffered",
                "status": "STARTED",
            }
        if url.endswith("/reporter/runs/run_buffered/complete"):
            return {
                "run_public_id": "run_buffered",
                "status": "SUCCEEDED",
            }
        if url.endswith("/reporter/sessions/ses_buffered/complete"):
            return {
                "session_public_id": "ses_buffered",
                "status": "ENDED",
            }
        raise AssertionError(url)

    monkeypatch.setattr(restarted, "_post_json", online)
    mismatch = restarted.call_tool(
        "duckdock.execution.buffer_flush",
        {
            "runtime_id": "99",
            "reporter_token": "dkr_report_wrong_runtime",
            "max_items": 10,
        },
    )
    assert mismatch["processed_count"] == 0
    assert mismatch["stopped_reason"] == "RUNTIME_CREDENTIAL_MISMATCH"
    assert calls == []

    flushed = restarted.call_tool(
        "duckdock.execution.buffer_flush",
        {
            "reporter_token": "dkr_report_rotated",
            "max_items": 10,
        },
    )

    assert flushed["processed_count"] == 4
    assert flushed["stopped_reason"] is None
    assert flushed["buffer"]["ack_cursor"] == 4
    assert flushed["buffer"]["pending_items"] == 0
    assert [call[0] for call in calls] == [
        "http://duckdock.test/api/v2/reporter/sessions",
        "http://duckdock.test/api/v2/reporter/runs",
        "http://duckdock.test/api/v2/reporter/runs/run_buffered/complete",
        "http://duckdock.test/api/v2/reporter/sessions/ses_buffered/complete",
    ]
    assert all(call[1] == "dkr_report_rotated" for call in calls)


def test_execution_buffer_hard_limit_and_explicit_loss_marker(
    tmp_path: Path,
) -> None:
    buffer = duckdock_runtime_mcp.ExecutionBuffer(
        tmp_path / "buffer.sqlite3",
        max_items=2,
        max_bytes=10_000,
    )
    common = {
        "operation": "session.start",
        "runtime_id": "32",
        "api_base": "http://duckdock.test/api/v2",
        "path_template": "/reporter/sessions",
    }
    first = buffer.enqueue(
        **common,
        payload={"external_session_id": "session-1"},
        idempotency_key="buffer-key-0001",
    )
    second = buffer.enqueue(
        **common,
        payload={"external_session_id": "session-2"},
        idempotency_key="buffer-key-0002",
    )
    repeated = buffer.enqueue(
        **common,
        payload={"external_session_id": "session-2"},
        idempotency_key="buffer-key-0002",
    )

    assert repeated["sequence"] == second["sequence"]
    assert buffer.status()["high_water"] is True
    with pytest.raises(
        duckdock_runtime_mcp.ToolError,
        match="hard limit",
    ):
        buffer.enqueue(
            **common,
            payload={"external_session_id": "session-3"},
            idempotency_key="buffer-key-0003",
        )
    with pytest.raises(
        duckdock_runtime_mcp.ToolError,
        match="idempotency conflict",
    ):
        buffer.enqueue(
            **common,
            payload={"external_session_id": "changed"},
            idempotency_key="buffer-key-0002",
        )

    marker = buffer.discard(
        first_sequence=int(first["sequence"]),
        last_sequence=int(first["sequence"]),
        reason_code="operator-approved-test-loss",
    )
    status = buffer.status()

    assert marker["item_count"] == 1
    assert marker["completeness"] == "PARTIAL"
    assert marker["trust_state"] == "DECLARED_LOSS"
    assert status["ack_cursor"] == first["sequence"]
    assert status["pending_items"] == 1
    assert status["loss_markers"][0]["marker_id"] == marker["marker_id"]
    assert buffer.get(int(first["sequence"]))["payload_json"] == "{}"
