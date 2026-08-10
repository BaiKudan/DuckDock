#!/usr/bin/env python3
"""One-shot Langfuse OTLP endpoint used by the local exporter smoke test."""

from __future__ import annotations

import base64
import gzip
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer


HOST = os.environ.get("DUCKDOCK_LANGFUSE_MOCK_HOST", "127.0.0.1")
PORT = int(os.environ.get("DUCKDOCK_LANGFUSE_MOCK_PORT", "14319"))
PUBLIC_KEY = os.environ.get(
    "DUCKDOCK_LANGFUSE_PUBLIC_KEY",
    "pk-shadow-export",
)
SECRET_KEY = os.environ.get(
    "DUCKDOCK_LANGFUSE_SECRET_KEY",
    "sk-shadow-export",
)
EXPECTED_AUTH = "Basic " + base64.b64encode(
    f"{PUBLIC_KEY}:{SECRET_KEY}".encode("utf-8")
).decode("ascii")
EXPECTED_MARKERS = (
    b"ns_openclaw_shadow_dev",
    b"rt_openclaw_shadow_dev",
    b"duckdock-genai-shadow-v1+otel-semconv-1.40.0",
    b"qwen-openinference-shadow",
)
FORBIDDEN_MARKERS = (
    b"SHADOW_SECRET_CANARY_DO_NOT_EXPORT",
    b"forged-namespace",
    b"forged-runtime",
    b"forged-client-model",
    b"private user request",
)


class MockServer(HTTPServer):
    error: str | None = None
    received: bool = False


class Handler(BaseHTTPRequestHandler):
    server: MockServer

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
        self.server.received = True
        content_length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(content_length)
        if self.headers.get("Content-Encoding") == "gzip":
            payload = gzip.decompress(payload)

        checks = {
            "path": self.path == "/api/public/otel/v1/traces",
            "basic_auth": self.headers.get("Authorization") == EXPECTED_AUTH,
            "ingestion_v4": (
                self.headers.get("x-langfuse-ingestion-version") == "4"
            ),
            "safe_metadata_present": all(
                marker in payload for marker in EXPECTED_MARKERS
            ),
            "secret_canary_absent": all(
                marker not in payload for marker in FORBIDDEN_MARKERS
            ),
        }
        if not all(checks.values()):
            self.server.error = json.dumps(checks, sort_keys=True)
            self.send_response(400)
        else:
            self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> int:
    server = MockServer((HOST, PORT), Handler)
    server.timeout = 20
    server.handle_request()
    if not server.received:
        print('{"error":"no_export_received"}')
        return 1
    if server.error is not None:
        print(server.error)
        return 1
    print(
        json.dumps(
            {
                "path": "/api/public/otel/v1/traces",
                "basic_auth": True,
                "ingestion_v4": True,
                "safe_metadata_present": True,
                "secret_canary_absent": True,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
