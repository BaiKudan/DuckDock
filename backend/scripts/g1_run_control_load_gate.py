#!/usr/bin/env python3
"""Run the G1 Run-control-envelope load gate against isolated real MySQL.

The gate exercises the complete FastAPI request path, Reporter credential
authentication, metadata-only validation, AgentRun/audit/Outbox persistence,
transaction commit, idempotent replay, and conflict handling. It intentionally
uses an isolated database and refuses the normal DuckDock application database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import sys
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import httpx
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

import app.models  # noqa: F401 - register complete SQLAlchemy metadata
from app.core.config import settings
from app.core.database import AsyncSessionLocal, Base, engine
from app.core.api_token_security import hash_reporter_token_secret
from app.main import app
from app.models.audit import AuditLog
from app.models.control_plane import (
    ReporterCredential,
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
)
from app.models.execution import AgentRun
from app.models.namespace import Namespace
from app.models.outbox import OutboxEvent
from app.models.user import SystemRole, User
from app.services.outbox_event_service import AGENT_RUN_REGISTERED
from app.services.report_upload_service import generate_runtime_report_token
from app.services.reporter_identity_service import EXECUTION_WRITE_SCOPE


@dataclass(frozen=True, slots=True)
class LoadPhase:
    name: str
    target_rate: float
    duration_seconds: float
    minimum_completion_ratio: float = 0.95
    maximum_p95_ms: float = 1000
    maximum_drain_seconds: float = 1
    maximum_schedule_lag_ms: float = 100

    @property
    def request_count(self) -> int:
        return max(1, int(round(self.target_rate * self.duration_seconds)))


@dataclass(frozen=True, slots=True)
class PhaseResult:
    name: str
    target_rate: float
    duration_seconds: float
    request_count: int
    success_count: int
    failure_count: int
    elapsed_seconds: float
    completion_rate: float
    drain_seconds: float
    schedule_lag_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    minimum_completion_rate: float
    maximum_p95_ms: float
    maximum_drain_seconds: float
    maximum_schedule_lag_ms: float
    error_codes: dict[str, int]

    @property
    def passed(self) -> bool:
        return (
            self.failure_count == 0
            and self.success_count == self.request_count
            and self.completion_rate >= self.minimum_completion_rate
            and self.p95_ms <= self.maximum_p95_ms
            and self.drain_seconds <= self.maximum_drain_seconds
            and self.schedule_lag_ms
            <= self.maximum_schedule_lag_ms
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RunStartRequest:
    idempotency_key: str
    body: dict[str, Any]


def require_isolated_database(database_url: str) -> str:
    url = make_url(database_url)
    database = (url.database or "").lower()
    if url.get_backend_name() != "mysql" or not any(
        marker in database for marker in ("test", "perf")
    ):
        raise ValueError(
            "G1 load gate requires an isolated test/perf database"
        )
    return database


def percentile(values: Sequence[float], rank: int) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, math.ceil((rank / 100) * len(ordered)) - 1)
    return float(ordered[index])


def build_run_start_request(
    run_tag: str,
    phase_name: str,
    index: int,
) -> RunStartRequest:
    identity = f"g1-{phase_name}-{run_tag}-{index}"
    return RunStartRequest(
        idempotency_key=identity,
        body={
            "external_run_id": identity,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "source_schema": "duckdock-g1-load-gate",
            "source_schema_version": "1.0",
            "content_capture_mode": "metadata_only",
            "metadata": {
                "agent.mode": "g1-load-gate",
                "operation.name": f"{phase_name}.run.start",
            },
        },
    )


async def run_load_phase(
    phase: LoadPhase,
    operation: Callable[[int], Awaitable[int]],
) -> PhaseResult:
    loop = asyncio.get_running_loop()
    started = loop.time()
    latencies_ms: list[float] = []
    statuses: list[int | str] = []

    async def invoke(index: int) -> None:
        request_started = perf_counter()
        try:
            statuses.append(await operation(index))
        except Exception as exc:
            statuses.append(f"exception_{type(exc).__name__}")
        finally:
            latencies_ms.append(
                (perf_counter() - request_started) * 1000
            )

    tasks: list[asyncio.Task[None]] = []
    for index in range(phase.request_count):
        due_at = started + (index / phase.target_rate)
        delay = due_at - loop.time()
        if delay > 0:
            await asyncio.sleep(delay)
        tasks.append(asyncio.create_task(invoke(index)))
    scheduled_at = loop.time()
    await asyncio.gather(*tasks)
    finished = loop.time()

    success_count = sum(status == 201 for status in statuses)
    errors = Counter(
        f"http_{status}" if isinstance(status, int) else status
        for status in statuses
        if status != 201
    )
    elapsed = max(finished - started, 0.000_001)
    offered_window_end = started + phase.duration_seconds
    final_request_due_at = started + (
        (phase.request_count - 1) / phase.target_rate
    )
    minimum_rate = phase.target_rate * phase.minimum_completion_ratio
    return PhaseResult(
        name=phase.name,
        target_rate=phase.target_rate,
        duration_seconds=phase.duration_seconds,
        request_count=phase.request_count,
        success_count=success_count,
        failure_count=len(statuses) - success_count,
        elapsed_seconds=round(elapsed, 6),
        completion_rate=round(phase.request_count / elapsed, 3),
        drain_seconds=round(max(0, finished - offered_window_end), 6),
        schedule_lag_ms=round(
            max(0, scheduled_at - final_request_due_at) * 1000,
            3,
        ),
        p50_ms=round(percentile(latencies_ms, 50), 3),
        p95_ms=round(percentile(latencies_ms, 95), 3),
        p99_ms=round(percentile(latencies_ms, 99), 3),
        max_ms=round(max(latencies_ms, default=0), 3),
        minimum_completion_rate=round(minimum_rate, 3),
        maximum_p95_ms=phase.maximum_p95_ms,
        maximum_drain_seconds=phase.maximum_drain_seconds,
        maximum_schedule_lag_ms=phase.maximum_schedule_lag_ms,
        error_codes=dict(sorted(errors.items())),
    )


async def reset_database() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)


async def drop_database_tables() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)


async def seed_reporter() -> str:
    prefix, secret, token = generate_runtime_report_token()
    async with AsyncSessionLocal() as db:
        user = User(
            username="g1-load-gate",
            email="g1-load-gate@example.test",
            hashed_password="unused",
            system_role=SystemRole.USER,
        )
        db.add(user)
        await db.flush()
        namespace = Namespace(
            name="g1-load-gate",
            owner_id=user.id,
        )
        db.add(namespace)
        await db.flush()
        runtime = RuntimeInstance(
            namespace_id=namespace.id,
            provider=RuntimeProvider.CUSTOM,
            name="G1 isolated load gate",
            deploy_type=RuntimeDeployType.PRIVATE,
        )
        db.add(runtime)
        await db.flush()
        db.add(
            ReporterCredential(
                runtime_id=runtime.id,
                user_id=user.id,
                device_id="g1-load-gate",
                name="G1 Load Gate Reporter",
                token_prefix=prefix,
                token_hash=hash_reporter_token_secret(secret),
                scopes=[EXECUTION_WRITE_SCOPE],
            )
        )
        await db.commit()
    return token


async def verify_materialization(expected_count: int) -> dict[str, int]:
    async with AsyncSessionLocal() as db:
        return {
            "agent_runs": int(
                await db.scalar(select(func.count(AgentRun.id))) or 0
            ),
            "agent_run_registered_events": int(
                await db.scalar(
                    select(func.count(OutboxEvent.id)).where(
                        OutboxEvent.event_type == AGENT_RUN_REGISTERED
                    )
                )
                or 0
            ),
            "agent_run_started_audits": int(
                await db.scalar(
                    select(func.count(AuditLog.id)).where(
                        AuditLog.action == "agent_run.started"
                    )
                )
                or 0
            ),
            "expected": expected_count,
        }


def _phase_payload(result: PhaseResult) -> dict[str, Any]:
    return {**result.as_dict(), "passed": result.passed}


async def run_gate(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    database = require_isolated_database(settings.DATABASE_URL)
    if not args.reset_test_database:
        raise ValueError("--reset-test-database is required")

    await reset_database()
    token = await seed_reporter()
    run_tag = uuid.uuid4().hex[:8]
    requests: dict[tuple[str, int], RunStartRequest] = {}
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

    headers = {"Authorization": f"Bearer {token}"}
    results: list[PhaseResult] = []
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://g1-load-gate.local",
        timeout=httpx.Timeout(args.request_timeout_seconds),
    ) as client:
        for phase in phases:
            async def operation(
                index: int,
                *,
                current_phase: LoadPhase = phase,
            ) -> int:
                request = build_run_start_request(
                    run_tag,
                    current_phase.name,
                    index,
                )
                requests[(current_phase.name, index)] = request
                response = await client.post(
                    "/api/v2/reporter/runs",
                    headers={
                        **headers,
                        "Idempotency-Key": request.idempotency_key,
                    },
                    json=request.body,
                )
                return response.status_code

            results.append(await run_load_phase(phase, operation))

        first = requests[(phases[0].name, 0)]
        replay = await client.post(
            "/api/v2/reporter/runs",
            headers={
                **headers,
                "Idempotency-Key": first.idempotency_key,
            },
            json=first.body,
        )
        conflict_body = {
            **first.body,
            "external_run_id": f"{first.body['external_run_id']}-conflict",
        }
        conflict = await client.post(
            "/api/v2/reporter/runs",
            headers={
                **headers,
                "Idempotency-Key": first.idempotency_key,
            },
            json=conflict_body,
        )

    expected_count = sum(phase.request_count for phase in phases)
    materialized = await verify_materialization(expected_count)
    materialization_passed = (
        materialized["agent_runs"] == expected_count
        and materialized["agent_run_registered_events"] == expected_count
        and materialized["agent_run_started_audits"] == expected_count
    )
    idempotency_passed = (
        replay.status_code == 201
        and conflict.status_code == 409
    )
    passed = (
        all(result.passed for result in results)
        and materialization_passed
        and idempotency_passed
    )
    payload = {
        "gate": "G1 Run control envelope",
        "database": database,
        "transport": "FastAPI ASGI HTTP with real MySQL",
        "phases": [_phase_payload(result) for result in results],
        "materialization": {
            **materialized,
            "passed": materialization_passed,
        },
        "idempotency": {
            "same_key_same_payload_status": replay.status_code,
            "same_key_different_payload_status": conflict.status_code,
            "passed": idempotency_passed,
        },
        "passed": passed,
    }
    return (0 if passed else 2), payload


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Exercise G1 Run-control-envelope throughput through FastAPI "
            "against an isolated real-MySQL database."
        )
    )
    parser.add_argument("--reset-test-database", action="store_true")
    parser.add_argument("--keep-database", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--sustained-rate", type=float, default=100)
    parser.add_argument("--sustained-seconds", type=float, default=5)
    parser.add_argument("--burst-rate", type=float, default=500)
    parser.add_argument("--burst-seconds", type=float, default=1)
    parser.add_argument(
        "--minimum-completion-ratio",
        type=float,
        default=0.95,
    )
    parser.add_argument(
        "--sustained-maximum-p95-ms",
        type=float,
        default=1000,
    )
    parser.add_argument(
        "--sustained-maximum-drain-seconds",
        type=float,
        default=1,
    )
    parser.add_argument(
        "--burst-maximum-p95-ms",
        type=float,
        default=2000,
    )
    parser.add_argument(
        "--burst-maximum-drain-seconds",
        type=float,
        default=2,
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=float,
        default=30,
    )
    parser.add_argument(
        "--maximum-schedule-lag-ms",
        type=float,
        default=100,
    )
    args = parser.parse_args(argv)
    if args.sustained_rate <= 0 or args.burst_rate <= 0:
        parser.error("rates must be positive")
    if args.sustained_seconds <= 0 or args.burst_seconds <= 0:
        parser.error("durations must be positive")
    if not 0 < args.minimum_completion_ratio <= 1:
        parser.error("minimum completion ratio must be in (0, 1]")
    return args


async def async_main(
    argv: Sequence[str] | None = None,
) -> int:
    args = parse_args(argv)
    require_isolated_database(settings.DATABASE_URL)
    if not args.reset_test_database:
        raise ValueError("--reset-test-database is required")
    engine.sync_engine.echo = False
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    exit_code = 4
    payload: dict[str, Any] = {}
    try:
        exit_code, payload = await run_gate(args)
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            for phase in payload["phases"]:
                print(
                    f"{phase['name']}: passed={phase['passed']} "
                    f"requests={phase['request_count']} "
                    f"completion_rate={phase['completion_rate']} req/s "
                    f"p95={phase['p95_ms']} ms "
                    f"drain={phase['drain_seconds']} s "
                    f"schedule_lag={phase['schedule_lag_ms']} ms"
                )
            print(
                f"materialization: passed="
                f"{payload['materialization']['passed']} "
                f"runs={payload['materialization']['agent_runs']}"
            )
            print(
                f"idempotency: passed={payload['idempotency']['passed']}"
            )
            print(f"G1 Run control envelope: passed={payload['passed']}")
        return exit_code
    finally:
        if args.reset_test_database and not args.keep_database:
            await drop_database_tables()
        await engine.dispose()


def main() -> int:
    try:
        return asyncio.run(async_main())
    except KeyboardInterrupt:
        return 130
    except ValueError as exc:
        print(f"G1 load gate safety error: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        print(
            f"G1 load gate failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
