from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from app.services.telemetry_ports import TraceConfirmationRequest
from app.services.telemetry_sinks.langfuse import (
    LangfuseTelemetrySinkPort,
    _langfuse_origin,
)


def _request(trace_id: str = "a" * 32) -> TraceConfirmationRequest:
    return TraceConfirmationRequest(
        run_public_id="run_langfuse",
        source_trace_id=trace_id,
        source_session_id="session-langfuse",
        started_at=datetime(2026, 7, 30, 8, tzinfo=timezone.utc),
        ended_at=datetime(2026, 7, 30, 8, 1, tzinfo=timezone.utc),
    )


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_langfuse_origin_accepts_base_or_otlp_endpoint() -> None:
    expected = "https://langfuse.example.test"
    assert _langfuse_origin(expected) == expected
    assert (
        _langfuse_origin(f"{expected}/api/public/otel")
        == expected
    )
    assert (
        _langfuse_origin(f"{expected}/api/public/otel/v1/traces")
        == expected
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.0.0.1:3000/api/public/otel",
        "https://user:secret@langfuse.example.test/api/public/otel",
        "https://langfuse.example.test/api/public/otel?token=secret",
    ],
)
def test_langfuse_origin_rejects_unsafe_endpoints(endpoint: str) -> None:
    with pytest.raises(ValueError):
        _langfuse_origin(endpoint)


@pytest.mark.asyncio
async def test_langfuse_confirmation_uses_bounded_v2_metadata_lookup(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "span-1",
                        "traceId": "a" * 32,
                        "projectId": "project-safe",
                    }
                ]
            },
        )

    monkeypatch.setattr(
        "app.services.ssrf._validated_addresses",
        lambda host, port: ["203.0.113.10"],
    )
    async with _client(handler) as client:
        port = LangfuseTelemetrySinkPort(
            endpoint="https://langfuse.example.test/api/public/otel",
            public_key="pk-safe",
            secret_key="sk-secret",
            client=client,
        )
        result = await port.confirm_trace(_request())

    assert result.confirmed is True
    assert result.external_trace_id == "a" * 32
    assert result.external_session_id == "session-langfuse"
    assert result.trace_url == (
        "https://langfuse.example.test/project/project-safe/traces/"
        + "a" * 32
    )
    outbound = captured["request"]
    assert isinstance(outbound, httpx.Request)
    assert outbound.url.path == "/api/public/v2/observations"
    assert outbound.url.params["fields"] == "core"
    assert outbound.url.params["traceId"] == "a" * 32
    assert outbound.url.params["limit"] == "1"
    assert "fromStartTime" in outbound.url.params
    assert "toStartTime" in outbound.url.params
    assert outbound.headers["authorization"].startswith("Basic ")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "error_code"),
    [
        (401, "provider_auth_failed"),
        (404, "provider_api_unsupported"),
        (429, "provider_rate_limited"),
        (503, "provider_unavailable"),
    ],
)
async def test_langfuse_confirmation_maps_provider_failures_to_safe_codes(
    monkeypatch,
    status_code: int,
    error_code: str,
) -> None:
    monkeypatch.setattr(
        "app.services.ssrf._validated_addresses",
        lambda host, port: ["203.0.113.10"],
    )
    async with _client(
        lambda request: httpx.Response(
            status_code,
            text="Authorization: secret-must-not-persist",
        )
    ) as client:
        result = await LangfuseTelemetrySinkPort(
            endpoint="https://langfuse.example.test",
            public_key="pk-safe",
            secret_key="sk-secret",
            client=client,
        ).confirm_trace(_request())
    assert result.confirmed is False
    assert result.error_code == error_code
    assert "secret" not in result.error_code


@pytest.mark.asyncio
async def test_langfuse_confirmation_rejects_invalid_or_missing_trace(
    monkeypatch,
) -> None:
    invalid = await LangfuseTelemetrySinkPort(
        endpoint="https://langfuse.example.test",
        public_key="pk-safe",
        secret_key="sk-secret",
    ).confirm_trace(_request("run-not-an-otel-trace-id"))
    assert invalid.error_code == "trace_id_invalid"

    monkeypatch.setattr(
        "app.services.ssrf._validated_addresses",
        lambda host, port: ["203.0.113.10"],
    )
    async with _client(
        lambda request: httpx.Response(200, json={"data": []})
    ) as client:
        missing = await LangfuseTelemetrySinkPort(
            endpoint="https://langfuse.example.test",
            public_key="pk-safe",
            secret_key="sk-secret",
            client=client,
        ).confirm_trace(_request())
    assert missing.error_code == "trace_not_found"
