from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


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
