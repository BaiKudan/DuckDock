#!/usr/bin/env python3
"""Two-path OTLP JSON mock for the Generic bridge live smoke."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer


HOST = os.environ.get("DUCKDOCK_GENERIC_MOCK_HOST", "0.0.0.0")
PORT = int(os.environ.get("DUCKDOCK_GENERIC_MOCK_PORT", "14329"))
SINK_PUBLIC_ID = "tsk_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
EXPECTED_ALL = {
    "/provider/v1/traces": "Bearer provider-dev-token",
    (
        "/api/v2/reporter/telemetry-sinks/"
        f"{SINK_PUBLIC_ID}/v1/traces"
    ): "Bearer reporter-dev-token",
}
MODE = os.environ.get("DUCKDOCK_GENERIC_MOCK_EXPECT", "both")
if MODE == "provider":
    EXPECTED = {
        path: auth
        for path, auth in EXPECTED_ALL.items()
        if path == "/provider/v1/traces"
    }
elif MODE == "duckdock":
    EXPECTED = {
        path: auth
        for path, auth in EXPECTED_ALL.items()
        if path != "/provider/v1/traces"
    }
elif MODE == "both":
    EXPECTED = EXPECTED_ALL
else:
    raise ValueError("DUCKDOCK_GENERIC_MOCK_EXPECT must be both/provider/duckdock")
REQUIRED_MARKERS = (
    b"generic-run-smoke-001",
    b"ns_generic_smoke",
    b"rt_generic_smoke",
    b"duckdock-generic-otlp-v1",
    b"qwen-generic-safe",
    b"duckdock.generic.agent.operation",
)
FORBIDDEN_MARKERS = (
    b"GENERIC_SECRET_CANARY_DO_NOT_EXPORT",
    b"forged-namespace",
    b"forged-runtime",
    b"PRODUCER_ATTESTED",
    b"private user request",
    b"tool.arguments",
    b"tool.result",
    b"gen_ai.input.messages",
)


class MockServer(HTTPServer):
    errors: list[dict[str, object]]
    received: set[str]

    def __init__(self, server_address, handler_class) -> None:
        super().__init__(server_address, handler_class)
        self.errors = []
        self.received = set()


class Handler(BaseHTTPRequestHandler):
    server: MockServer

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length)
        expected_auth = EXPECTED.get(self.path)
        checks = {
            "known_path": expected_auth is not None,
            "authorization": (
                expected_auth is not None
                and self.headers.get("Authorization") == expected_auth
            ),
            "json_content_type": (
                self.headers.get("Content-Type", "").split(";", 1)[0]
                == "application/json"
            ),
            "uncompressed": self.headers.get("Content-Encoding") in (
                None,
                "",
                "identity",
            ),
            "safe_metadata_present": all(
                marker in payload for marker in REQUIRED_MARKERS
            ),
            "content_and_forgery_absent": all(
                marker not in payload for marker in FORBIDDEN_MARKERS
            ),
        }
        if not all(checks.values()):
            self.server.errors.append(
                {"path": self.path, "checks": checks}
            )
            self.send_response(400)
        else:
            self.server.received.add(self.path)
            self.send_response(200)
        response = b"{}"
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> int:
    server = MockServer((HOST, PORT), Handler)
    server.timeout = 30
    while len(server.received) < len(EXPECTED) and not server.errors:
        server.handle_request()
        if not server.received and not server.errors:
            break
    if server.errors:
        print(json.dumps({"errors": server.errors}, sort_keys=True))
        return 1
    if server.received != set(EXPECTED):
        print(
            json.dumps(
                {
                    "error": "missing_export",
                    "received_paths": sorted(server.received),
                },
                sort_keys=True,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "authenticated_paths": sorted(server.received),
                "content_and_forgery_absent": True,
                "safe_metadata_present": True,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
