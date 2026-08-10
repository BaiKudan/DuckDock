#!/usr/bin/env python3
"""Collect release-bound GA readiness from a real target HTTPS endpoint."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

import httpx

try:
    from scripts.ga_release_identity import EVIDENCE_SCOPES, build_release_binding
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_release_identity import EVIDENCE_SCOPES, build_release_binding


SCHEMA_VERSION = "duckdock-ga-target-readiness-v1"
READINESS_PATH = "/api/v2/operations/ga-readiness"


def require_safe_base_url(base_url: str, *, allow_http_localhost: bool) -> str:
    parsed = urlparse(base_url)
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("base URL must be a credential-free origin")
    is_local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not (allow_http_localhost and is_local and parsed.scheme == "http"):
        raise ValueError("target readiness collection requires HTTPS")
    return f"{parsed.scheme}://{parsed.netloc}"


def _required_secret(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ValueError(f"required credential environment variable is missing: {name}")
    return value


def _readiness_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("GA readiness response must be a JSON object")
    required = {
        "profile_version",
        "contract_version",
        "contract_digest",
        "expected_db_revision",
        "current_db_revision",
        "status",
        "pass_count",
        "warn_count",
        "block_count",
        "checked_at",
        "checks",
    }
    missing = required - set(value)
    if missing:
        raise ValueError(f"GA readiness response missing fields: {', '.join(sorted(missing))}")
    if not isinstance(value.get("checks"), list):
        raise ValueError("GA readiness checks must be an array")
    return value


async def collect(
    args: argparse.Namespace,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    token = _required_secret(args.admin_token_env)
    client_options: dict[str, Any] = {
        "base_url": args.base_url,
        "timeout": httpx.Timeout(args.timeout),
        "follow_redirects": False,
        "verify": str(args.ca_file) if args.ca_file else True,
    }
    if transport is not None:
        client_options["transport"] = transport
    async with httpx.AsyncClient(**client_options) as client:
        response = await client.get(
            READINESS_PATH,
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        readiness = _readiness_object(response.json())
    observed_at = datetime.now(timezone.utc).isoformat()
    passed = (
        readiness.get("status") == "READY"
        and readiness.get("block_count") == 0
        and isinstance(readiness.get("checks"), list)
        and bool(readiness["checks"])
        and all(isinstance(item, dict) and item.get("status") == "PASS" for item in readiness["checks"])
    )
    return {
        "schema_version": SCHEMA_VERSION,
        **args.release_binding,
        **readiness,
        "status": readiness.get("status"),
        "passed": passed,
        "observed_at": observed_at,
        "base_url": args.base_url,
        "transport": (
            "network HTTPS against target" if args.base_url.startswith("https://") else "local HTTP validation"
        ),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--scope", choices=sorted(EVIDENCE_SCOPES), default="target-production")
    parser.add_argument("--target-environment", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--backend-image", required=True)
    parser.add_argument("--frontend-image", required=True)
    parser.add_argument("--admin-token-env", default="DUCKDOCK_GA_ADMIN_TOKEN")
    parser.add_argument("--allow-http-localhost", action="store_true")
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        args.base_url = require_safe_base_url(
            args.base_url,
            allow_http_localhost=args.allow_http_localhost,
        )
        if args.allow_http_localhost and args.scope != "local-validation":
            raise ValueError("--allow-http-localhost requires --scope local-validation")
        args.release_binding = build_release_binding(
            scope=args.scope,
            target_environment=args.target_environment,
            source_commit=args.source_commit,
            backend_image=args.backend_image,
            frontend_image=args.frontend_image,
        )
    except ValueError as exc:
        parser.error(str(exc))
    return args


async def async_main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = await collect(args)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["passed"] else 2


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return asyncio.run(async_main(argv))
    except (ValueError, httpx.HTTPError) as exc:
        print(f"Target readiness collection failed: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
