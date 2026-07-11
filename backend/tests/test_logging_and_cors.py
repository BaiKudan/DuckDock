"""SEC-04 + OPS-05: narrowed CORS allowlists, JSON logging config, request-id middleware."""
from __future__ import annotations

import json
import logging

from fastapi.middleware.cors import CORSMiddleware
from starlette.testclient import TestClient

from app.core.logging import (
    JsonFormatter,
    RequestIdFilter,
    build_logging_config,
    request_id_var,
)


def _cors_middleware_kwargs():
    from app.main import app

    for middleware in app.user_middleware:
        if middleware.cls is CORSMiddleware:
            return middleware.kwargs
    raise AssertionError("CORSMiddleware is not configured on the app")


# ---------------------------------------------------------------------------
# SEC-04: CORS method/header allowlists must not be wildcards.
# ---------------------------------------------------------------------------


def test_cors_methods_are_not_wildcard():
    kwargs = _cors_middleware_kwargs()
    methods = kwargs["allow_methods"]
    assert "*" not in methods
    # The verbs the SPA actually uses.
    for verb in ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
        assert verb in methods


def test_cors_headers_are_not_wildcard():
    kwargs = _cors_middleware_kwargs()
    headers = kwargs["allow_headers"]
    assert "*" not in headers
    assert "Authorization" in headers
    assert "Content-Type" in headers


def test_cors_credentials_and_origins_wiring_preserved():
    from app.core.config import settings

    kwargs = _cors_middleware_kwargs()
    assert kwargs["allow_credentials"] is True
    assert kwargs["allow_origins"] == settings.CORS_ORIGINS


# ---------------------------------------------------------------------------
# OPS-05: JSON logging config builds and the formatter emits valid JSON lines.
# ---------------------------------------------------------------------------


def test_logging_dictconfig_builds():
    config = build_logging_config("DEBUG")
    assert config["version"] == 1
    assert config["root"]["level"] == "DEBUG"
    # JSON formatter + request-id filter must be wired into the default handler.
    assert config["formatters"]["json"]["()"].endswith("JsonFormatter")
    assert "request_id" in config["handlers"]["default"]["filters"]


def test_json_formatter_emits_parseable_json_with_request_id():
    token = request_id_var.set("req-xyz")
    try:
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello %s",
            args=("world",),
            exc_info=None,
        )
        RequestIdFilter().filter(record)
        line = JsonFormatter().format(record)
    finally:
        request_id_var.reset(token)

    payload = json.loads(line)
    assert payload["message"] == "hello world"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.logger"
    assert payload["request_id"] == "req-xyz"


def test_json_formatter_folds_extra_fields():
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="x",
        args=(),
        exc_info=None,
    )
    record.report_id = "rep-42"  # type: ignore[attr-defined]
    payload = json.loads(JsonFormatter().format(record))
    assert payload["report_id"] == "rep-42"


# ---------------------------------------------------------------------------
# OPS-05: request-id middleware sets/propagates the X-Request-ID header.
# ---------------------------------------------------------------------------


def test_request_id_middleware_sets_response_header():
    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.headers.get("X-Request-ID")


def test_request_id_middleware_echoes_incoming_id():
    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/health", headers={"X-Request-ID": "incoming-123"})
    assert resp.headers.get("X-Request-ID") == "incoming-123"
