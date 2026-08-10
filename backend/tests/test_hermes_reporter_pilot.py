from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "hermes_reporter_pilot.py"
)
SPEC = importlib.util.spec_from_file_location(
    "hermes_reporter_pilot",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
pilot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pilot)


def test_agent_loop_uses_native_hermes_profile_and_server_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_request_json(
        method: str,
        url: str,
        *,
        token: str | None = None,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        calls.append(
            {
                "method": method,
                "url": url,
                "token": token,
                "payload": payload or {},
                "headers": headers or {},
                "timeout": timeout,
            }
        )
        if url.endswith("/reporter/handshakes"):
            return {
                "handshake_id": "hs_" + "1" * 32,
                "accepted_capabilities": [
                    "run_control",
                    "session_control",
                ],
                "certified_capability_level": "DD-C1",
            }
        if url.endswith("/reporter/heartbeats"):
            return {"status": "ACTIVE"}
        if url.endswith("/reporter/sessions"):
            return {
                "session_public_id": "ses_" + "2" * 32,
                "status": "OPEN",
            }
        if url.endswith("/reporter/runs"):
            return {
                "run_public_id": "run_" + "3" * 32,
                "status": "STARTED",
            }
        if "/reporter/runs/" in url and url.endswith("/complete"):
            return {
                "run_public_id": "run_" + "3" * 32,
                "status": "SUCCEEDED",
                "trust_level": "CHANNEL_AUTHENTICATED",
                "trust_source": "REPORTER",
                "content_capture_mode": "metadata_only",
            }
        if "/reporter/sessions/" in url and url.endswith("/complete"):
            return {
                "session_public_id": "ses_" + "2" * 32,
                "status": "ENDED",
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(pilot, "request_json", fake_request_json)
    monkeypatch.setattr(
        pilot,
        "utc_now",
        lambda: datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(pilot, "hermes_boot_id", lambda: "hermes-boot")

    state = pilot.start_agent_loop(
        config={
            "runtime_id": 9,
            "runtime_external_id": "hermes-runtime",
        },
        api_base="http://localhost:8801/api/v1",
        token="secret-reporter-token",
        operation="hermes-custom-structured-report",
        source_schema="custom-structured-v1",
        trigger="cron",
    )
    result = pilot.finish_agent_loop(
        state,
        token="secret-reporter-token",
        succeeded=True,
    )

    handshake_payload = calls[0]["payload"]
    assert handshake_payload["profile"] == "hermes-reporter"
    assert handshake_payload["adapter_id"] == "hermes-reporter-pilot"
    assert handshake_payload["adapter_version"] == "hermes-pilot-0.2.0"
    assert handshake_payload["source_schema"] == "custom-structured-v1"
    assert sorted(handshake_payload["capabilities"]) == [
        "run_control",
        "session_control",
    ]
    assert handshake_payload["content_capture_modes"] == [
        "metadata_only"
    ]

    run_start = calls[3]
    assert run_start["payload"]["source_schema"] == "custom-structured-v1"
    assert run_start["payload"]["metadata"]["operation.name"] == (
        "hermes-custom-structured-report"
    )
    assert run_start["headers"]["Idempotency-Key"].endswith("-run-start")

    run_complete = calls[4]
    assert "duration_ms" not in run_complete["payload"]
    assert "error_type" not in run_complete["payload"]
    assert run_complete["payload"]["status"] == "SUCCEEDED"
    assert result == {
        "profile": "hermes-reporter",
        "handshake_id": "hs_" + "1" * 32,
        "certified_capability_level": "DD-C1",
        "heartbeat_status": "ACTIVE",
        "session_public_id": "ses_" + "2" * 32,
        "session_status": "ENDED",
        "run_public_id": "run_" + "3" * 32,
        "run_status": "SUCCEEDED",
        "trust_level": "CHANNEL_AUTHENTICATED",
        "trust_source": "REPORTER",
        "content_capture_mode": "metadata_only",
    }
    assert "secret-reporter-token" not in repr(state)
    assert "secret-reporter-token" not in repr(result)


def test_failed_agent_loop_is_explicit_and_never_sends_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads: list[dict[str, Any]] = []

    def fake_request_json(
        method: str,
        url: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del method
        payloads.append(kwargs["payload"])
        if "/runs/" in url:
            return {
                "run_public_id": "run_" + "3" * 32,
                "status": "FAILED",
                "trust_level": "CHANNEL_AUTHENTICATED",
                "trust_source": "REPORTER",
                "content_capture_mode": "metadata_only",
            }
        return {
            "session_public_id": "ses_" + "2" * 32,
            "status": "ABANDONED",
        }

    monkeypatch.setattr(pilot, "request_json", fake_request_json)
    state = {
        "api_v2_base": "http://localhost:8801/api/v2",
        "identity": "hermes-test",
        "operation": "hermes-pack-report",
        "handshake": {
            "handshake_id": "hs_" + "1" * 32,
            "certified_capability_level": "DD-C1",
        },
        "heartbeat": {"status": "ACTIVE"},
        "session": {"session_public_id": "ses_" + "2" * 32},
        "run": {"run_public_id": "run_" + "3" * 32},
    }

    result = pilot.finish_agent_loop(
        state,
        token="secret-reporter-token",
        succeeded=False,
    )

    assert payloads[0]["status"] == "FAILED"
    assert payloads[0]["error_type"] == "reporter_failure"
    assert "duration_ms" not in payloads[0]
    assert payloads[1]["status"] == "ABANDONED"
    assert payloads[1]["error_count"] == 1
    assert result["run_status"] == "FAILED"


def test_finish_agent_loop_ends_after_server_canonicalized_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads: list[dict[str, Any]] = []

    def fake_request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        del method
        payloads.append(kwargs["payload"])
        if "/runs/" in url:
            return {
                "run_public_id": "run_" + "3" * 32,
                "status": "SUCCEEDED",
                "trust_level": "CHANNEL_AUTHENTICATED",
                "trust_source": "REPORTER",
                "content_capture_mode": "metadata_only",
            }
        return {
            "session_public_id": "ses_" + "2" * 32,
            "status": "ENDED",
        }

    monkeypatch.setattr(pilot, "request_json", fake_request_json)
    monkeypatch.setattr(
        pilot,
        "utc_now",
        lambda: datetime(2026, 8, 5, 1, 13, 6, 900000, tzinfo=timezone.utc),
    )
    state = {
        "api_v2_base": "http://localhost:8801/api/v2",
        "identity": "hermes-clock-boundary",
        "operation": "hermes-structured-report",
        "handshake": {
            "handshake_id": "hs_" + "1" * 32,
            "certified_capability_level": "DD-C1",
        },
        "heartbeat": {"status": "ACTIVE"},
        "session": {
            "session_public_id": "ses_" + "2" * 32,
            "started_at": "2026-08-05T01:13:07Z",
        },
        "run": {
            "run_public_id": "run_" + "3" * 32,
            "started_at": "2026-08-05T01:13:07Z",
        },
    }

    pilot.finish_agent_loop(state, token="secret-reporter-token", succeeded=True)

    ended_at = datetime.fromisoformat(payloads[0]["ended_at"])
    assert ended_at > datetime(2026, 8, 5, 1, 13, 7, tzinfo=timezone.utc)
    assert payloads[1]["ended_at"] == payloads[0]["ended_at"]


def test_api_v2_base_requires_the_v1_reporter_base() -> None:
    assert pilot.api_v2_base("http://localhost:8801/api/v1/") == (
        "http://localhost:8801/api/v2"
    )
    with pytest.raises(pilot.PilotError):
        pilot.api_v2_base("http://localhost:8801/api")


def test_hermes_boot_id_parses_structured_pid_file_and_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    hermes_dir = tmp_path / ".hermes"
    hermes_dir.mkdir()
    (hermes_dir / "gateway.pid").write_text(
        (
            '{"pid": 44534, "kind": "hermes-gateway", '
            '"argv": "a-very-long-command-line-that-must-not-leak", '
            '"start_time": 1785219683.46}'
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(pilot.Path, "home", lambda: tmp_path)

    boot_id = pilot.hermes_boot_id()

    assert boot_id.startswith("hermes-gateway-44534-")
    assert len(boot_id) <= 128
    assert "command-line" not in boot_id
