#!/usr/bin/env python3
"""Run the GA capacity/data-growth gate through a real target HTTPS endpoint.

This script intentionally mutates a dedicated performance namespace. It never
creates or prints credentials, and it refuses plain HTTP except an explicitly
allowed localhost validation target.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

import httpx

try:
    from scripts.g1_run_control_load_gate import LoadPhase, build_run_start_request, run_load_phase
    from scripts.ga_release_identity import EVIDENCE_SCOPES, build_release_binding
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from g1_run_control_load_gate import LoadPhase, build_run_start_request, run_load_phase
    from ga_release_identity import EVIDENCE_SCOPES, build_release_binding


EXERCISE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")


def require_safe_target(base_url: str, *, allow_http_localhost: bool) -> str:
    parsed = urlparse(base_url)
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("base URL must be a credential-free origin")
    is_local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not (allow_http_localhost and is_local and parsed.scheme == "http"):
        raise ValueError("target capacity gate requires HTTPS")
    return f"{parsed.scheme}://{parsed.netloc}"


def _secret_from_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ValueError(f"required credential environment variable is missing: {name}")
    return value


async def _query_probe(
    client: httpx.AsyncClient,
    *,
    user_token: str,
    namespace_id: int,
    samples: int,
    maximum_p95_ms: float,
) -> dict[str, Any]:
    from time import perf_counter

    from scripts.g1_run_control_load_gate import percentile

    end = datetime.now(timezone.utc) + timedelta(minutes=1)
    start = end - timedelta(days=31)
    latencies: list[float] = []
    statuses: list[int] = []
    for _ in range(samples):
        started = perf_counter()
        response = await client.get(
            "/api/v2/agent-runs",
            headers={"Authorization": f"Bearer {user_token}"},
            params={
                "namespace_id": namespace_id,
                "started_after": start.isoformat(),
                "started_before": end.isoformat(),
                "limit": 50,
            },
        )
        latencies.append((perf_counter() - started) * 1000)
        statuses.append(response.status_code)
    p95 = round(percentile(latencies, 95), 3)
    passed = all(status == 200 for status in statuses) and p95 <= maximum_p95_ms
    return {
        "sample_count": samples,
        "success_count": sum(status == 200 for status in statuses),
        "p50_ms": round(percentile(latencies, 50), 3),
        "p95_ms": p95,
        "p99_ms": round(percentile(latencies, 99), 3),
        "maximum_p95_ms": maximum_p95_ms,
        "passed": passed,
    }


async def run_gate(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    base_url = require_safe_target(args.base_url, allow_http_localhost=args.allow_http_localhost)
    if args.acknowledge_target_mutation != args.target_environment:
        raise ValueError("--acknowledge-target-mutation must exactly match --target-environment")
    reporter_token = _secret_from_env(args.reporter_token_env)
    user_token = _secret_from_env(args.user_token_env)
    run_tag = uuid.uuid4().hex[:12]
    phases = (
        LoadPhase(
            name="sustained",
            target_rate=args.sustained_rate,
            duration_seconds=args.sustained_seconds,
            minimum_completion_ratio=args.minimum_completion_ratio,
            maximum_p95_ms=args.sustained_maximum_p95_ms,
            maximum_drain_seconds=args.sustained_maximum_drain_seconds,
            maximum_schedule_lag_ms=args.maximum_schedule_lag_ms,
        ),
        LoadPhase(
            name="burst",
            target_rate=args.burst_rate,
            duration_seconds=args.burst_seconds,
            minimum_completion_ratio=0,
            maximum_p95_ms=args.burst_maximum_p95_ms,
            maximum_drain_seconds=args.burst_maximum_drain_seconds,
            maximum_schedule_lag_ms=args.maximum_schedule_lag_ms,
        ),
    )
    started_at = datetime.now(timezone.utc)
    requests: dict[tuple[str, int], Any] = {}
    results = []
    limits = httpx.Limits(max_connections=args.maximum_connections, max_keepalive_connections=args.maximum_connections)
    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(args.request_timeout_seconds),
        limits=limits,
        follow_redirects=False,
    ) as client:
        for phase in phases:

            async def operation(index: int, *, current_phase: LoadPhase = phase) -> int:
                request = build_run_start_request(run_tag, current_phase.name, index)
                requests[(current_phase.name, index)] = request
                response = await client.post(
                    "/api/v2/reporter/runs",
                    headers={
                        "Authorization": f"Bearer {reporter_token}",
                        "Idempotency-Key": request.idempotency_key,
                    },
                    json=request.body,
                )
                return response.status_code

            results.append(await run_load_phase(phase, operation))

        first = requests[("sustained", 0)]
        replay = await client.post(
            "/api/v2/reporter/runs",
            headers={
                "Authorization": f"Bearer {reporter_token}",
                "Idempotency-Key": first.idempotency_key,
            },
            json=first.body,
        )
        conflict = await client.post(
            "/api/v2/reporter/runs",
            headers={
                "Authorization": f"Bearer {reporter_token}",
                "Idempotency-Key": first.idempotency_key,
            },
            json={**first.body, "external_run_id": f"{first.body['external_run_id']}-conflict"},
        )
        timeline = await _query_probe(
            client,
            user_token=user_token,
            namespace_id=args.namespace_id,
            samples=args.timeline_query_samples,
            maximum_p95_ms=args.timeline_maximum_p95_ms,
        )

    offered = sum(phase.request_count for phase in phases)
    succeeded = sum(result.success_count for result in results)
    failure_count = offered - succeeded
    error_rate = failure_count / offered if offered else 1.0
    passed = (
        all(result.passed for result in results)
        and succeeded >= args.minimum_materialized_runs
        and error_rate <= args.maximum_error_rate
        and replay.status_code == 201
        and conflict.status_code == 409
        and timeline["passed"]
    )
    finished_at = datetime.now(timezone.utc)
    payload = {
        "schema_version": "duckdock-target-capacity-gate-v2",
        **args.release_binding,
        "status": "PASSED" if passed else "BLOCKED",
        "observed_at": finished_at.isoformat(),
        "base_url": base_url,
        "namespace_id": args.namespace_id,
        "exercise_id": args.exercise_id,
        "run_tag": run_tag,
        "transport": "network HTTPS against target" if base_url.startswith("https://") else "local HTTP validation",
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "phases": [{**result.as_dict(), "passed": result.passed} for result in results],
        "offered_runs": offered,
        "materialized_runs": succeeded,
        "minimum_materialized_runs": args.minimum_materialized_runs,
        "failure_count": failure_count,
        "error_rate": error_rate,
        "maximum_error_rate": args.maximum_error_rate,
        "idempotency": {
            "same_key_same_payload_status": replay.status_code,
            "same_key_different_payload_status": conflict.status_code,
            "passed": replay.status_code == 201 and conflict.status_code == 409,
        },
        "post_growth_timeline_query": timeline,
        "passed": passed,
        "cleanup_required": (
            "sign the database growth receipt, delete the dedicated performance namespace, "
            "revoke both tokens, then run collect_ga_target_capacity.py"
        ),
    }
    return (0 if passed else 2), payload


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--target-environment", required=True)
    parser.add_argument("--scope", choices=sorted(EVIDENCE_SCOPES), default="target-production")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--backend-image", required=True)
    parser.add_argument("--frontend-image", required=True)
    parser.add_argument("--acknowledge-target-mutation", required=True)
    parser.add_argument("--namespace-id", type=int, required=True)
    parser.add_argument("--exercise-id", required=True)
    parser.add_argument("--reporter-token-env", default="DUCKDOCK_CAPACITY_REPORTER_TOKEN")
    parser.add_argument("--user-token-env", default="DUCKDOCK_CAPACITY_USER_TOKEN")
    parser.add_argument("--allow-http-localhost", action="store_true")
    parser.add_argument("--sustained-rate", type=float, default=50)
    parser.add_argument("--sustained-seconds", type=float, default=900)
    parser.add_argument("--burst-rate", type=float, default=100)
    parser.add_argument("--burst-seconds", type=float, default=60)
    parser.add_argument("--minimum-completion-ratio", type=float, default=0.95)
    parser.add_argument("--minimum-materialized-runs", type=int, default=50_000)
    parser.add_argument("--maximum-error-rate", type=float, default=0.001)
    parser.add_argument("--sustained-maximum-p95-ms", type=float, default=1000)
    parser.add_argument("--sustained-maximum-drain-seconds", type=float, default=2)
    parser.add_argument("--burst-maximum-p95-ms", type=float, default=2000)
    parser.add_argument("--burst-maximum-drain-seconds", type=float, default=5)
    parser.add_argument("--maximum-schedule-lag-ms", type=float, default=100)
    parser.add_argument("--timeline-query-samples", type=int, default=100)
    parser.add_argument("--timeline-maximum-p95-ms", type=float, default=250)
    parser.add_argument("--request-timeout-seconds", type=float, default=30)
    parser.add_argument("--maximum-connections", type=int, default=500)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.namespace_id < 1:
        parser.error("namespace ID must be positive")
    if args.sustained_rate <= 0 or args.burst_rate <= 0:
        parser.error("rates must be positive")
    if args.sustained_seconds <= 0 or args.burst_seconds <= 0:
        parser.error("durations must be positive")
    if args.minimum_materialized_runs < 1:
        parser.error("minimum materialized runs must be positive")
    if not EXERCISE_RE.fullmatch(args.exercise_id):
        parser.error("exercise ID must be 8-64 safe characters")
    if args.allow_http_localhost and args.scope != "local-validation":
        parser.error("--allow-http-localhost requires --scope local-validation")
    try:
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
    exit_code, payload = await run_gate(args)
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return asyncio.run(async_main(argv))
    except (ValueError, httpx.HTTPError) as exc:
        print(f"Target capacity gate failed: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
