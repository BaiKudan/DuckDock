#!/usr/bin/env python3
"""Development-only webhook sink for Alertmanager firing/resolved receipts."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Any, Sequence


class ReceiptHandler(BaseHTTPRequestHandler):
    output_path: Path
    write_lock = Lock()

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok\n")

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400)
            return
        if length <= 0 or length > 1_048_576:
            self.send_error(413)
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_error(400)
            return
        receipt = _receipt(payload)
        with self.write_lock:
            with self.output_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(receipt, sort_keys=True) + "\n")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}\n')

    def log_message(self, format: str, *args: Any) -> None:
        return


def _receipt(payload: Any) -> dict[str, Any]:
    alerts = payload.get("alerts", []) if isinstance(payload, dict) else []
    projected = []
    for alert in alerts if isinstance(alerts, list) else []:
        if not isinstance(alert, dict):
            continue
        projected.append(
            {
                "status": alert.get("status"),
                "startsAt": alert.get("startsAt"),
                "endsAt": alert.get("endsAt"),
                "labels": alert.get("labels") if isinstance(alert.get("labels"), dict) else {},
            }
        )
    return {
        "schema_version": "duckdock-alert-delivery-receipt-v1",
        "received_at": datetime.now(timezone.utc).isoformat(),
        "group_status": payload.get("status") if isinstance(payload, dict) else None,
        "receiver": payload.get("receiver") if isinstance(payload, dict) else None,
        "alerts": projected,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18081)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.touch(exist_ok=True)
    ReceiptHandler.output_path = args.output
    server = ThreadingHTTPServer((args.host, args.port), ReceiptHandler)
    print(json.dumps({"ready": True, "host": args.host, "port": args.port, "output": str(args.output)}))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
