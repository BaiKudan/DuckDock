from __future__ import annotations

from types import SimpleNamespace

import pytest

import scripts.g2_target_capacity_gate as target_gate
from scripts.g2_target_capacity_gate import parse_args, require_safe_target, run_gate


COMMIT = "a" * 40
BACKEND_IMAGE = f"registry.example.com/duckdock/backend@sha256:{'b' * 64}"
FRONTEND_IMAGE = f"registry.example.com/duckdock/frontend@sha256:{'c' * 64}"


def test_target_capacity_gate_requires_credential_free_https_origin() -> None:
    assert (
        require_safe_target("https://duckdock.example.com", allow_http_localhost=False)
        == "https://duckdock.example.com"
    )

    with pytest.raises(ValueError, match="requires HTTPS"):
        require_safe_target("http://duckdock.example.com", allow_http_localhost=False)
    with pytest.raises(ValueError, match="credential-free"):
        require_safe_target("https://user:secret@duckdock.example.com", allow_http_localhost=False)
    with pytest.raises(ValueError, match="credential-free"):
        require_safe_target("https://duckdock.example.com?token=secret", allow_http_localhost=False)


def test_local_http_requires_an_explicit_validation_override() -> None:
    with pytest.raises(ValueError, match="requires HTTPS"):
        require_safe_target("http://127.0.0.1:8801", allow_http_localhost=False)

    assert require_safe_target("http://127.0.0.1:8801", allow_http_localhost=True) == "http://127.0.0.1:8801"


@pytest.mark.asyncio
async def test_capacity_report_v2_retains_release_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, _path, *, json, **_kwargs) -> FakeResponse:
            status = 409 if str(json.get("external_run_id", "")).endswith("-conflict") else 201
            return FakeResponse(status)

    async def fake_load_phase(phase, operation):
        await operation(0)
        return SimpleNamespace(
            passed=True,
            success_count=phase.request_count,
            as_dict=lambda: {
                "name": phase.name,
                "duration_seconds": phase.duration_seconds,
                "target_rate": phase.target_rate,
                "p95_ms": 1,
            },
        )

    async def fake_query_probe(*_args, **_kwargs):
        return {"p95_ms": 1, "passed": True}

    monkeypatch.setattr(target_gate.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(target_gate, "run_load_phase", fake_load_phase)
    monkeypatch.setattr(target_gate, "_query_probe", fake_query_probe)
    monkeypatch.setenv("DUCKDOCK_CAPACITY_REPORTER_TOKEN", "reporter-test-token")
    monkeypatch.setenv("DUCKDOCK_CAPACITY_USER_TOKEN", "user-test-token")
    args = parse_args(
        [
            "--base-url",
            "http://127.0.0.1:8801",
            "--target-environment",
            "local-capacity",
            "--acknowledge-target-mutation",
            "local-capacity",
            "--scope",
            "local-validation",
            "--source-commit",
            COMMIT,
            "--backend-image",
            BACKEND_IMAGE,
            "--frontend-image",
            FRONTEND_IMAGE,
            "--namespace-id",
            "1",
            "--allow-http-localhost",
            "--sustained-rate",
            "1",
            "--sustained-seconds",
            "1",
            "--burst-rate",
            "1",
            "--burst-seconds",
            "1",
            "--minimum-materialized-runs",
            "1",
        ]
    )

    exit_code, report = await run_gate(args)

    assert exit_code == 0
    assert report["schema_version"] == "duckdock-target-capacity-gate-v2"
    assert report["status"] == "PASSED"
    assert report["scope"] == "local-validation"
    assert report["source_commit"] == COMMIT
    assert report["images"]["backend"]["name"] == BACKEND_IMAGE
    assert report["observed_at"] == report["finished_at"]
