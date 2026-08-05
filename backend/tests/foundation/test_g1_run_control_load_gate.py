"""Deterministic contracts for the G1 Run-control-envelope load gate."""

from __future__ import annotations

import asyncio

import pytest

from scripts.g1_run_control_load_gate import (
    LoadPhase,
    PhaseResult,
    build_run_start_request,
    percentile,
    require_isolated_database,
    run_load_phase,
)


def test_gate_refuses_a_non_test_database() -> None:
    with pytest.raises(ValueError, match="isolated test/perf database"):
        require_isolated_database(
            "mysql+aiomysql://user:secret@mysql:3306/duckdock"
        )

    assert (
        require_isolated_database(
            "mysql+aiomysql://user:secret@mysql:3306/duckdock_test"
        )
        == "duckdock_test"
    )
    assert (
        require_isolated_database(
            "mysql+aiomysql://user:secret@mysql:3306/duckdock_g1_perf"
        )
        == "duckdock_g1_perf"
    )


def test_percentile_uses_nearest_rank_without_hiding_tail_latency() -> None:
    values = [1.0, 2.0, 3.0, 100.0]

    assert percentile(values, 50) == 2.0
    assert percentile(values, 95) == 100.0
    assert percentile(values, 99) == 100.0


def test_phase_result_requires_zero_errors_rate_and_latency_budget() -> None:
    passing = PhaseResult(
        name="sustained",
        target_rate=100,
        duration_seconds=1,
        request_count=100,
        success_count=100,
        failure_count=0,
        elapsed_seconds=1,
        completion_rate=100,
        drain_seconds=0,
        schedule_lag_ms=0,
        p50_ms=10,
        p95_ms=20,
        p99_ms=30,
        max_ms=40,
        minimum_completion_rate=95,
        maximum_p95_ms=1000,
        maximum_drain_seconds=1,
        maximum_schedule_lag_ms=100,
        error_codes={},
    )

    assert passing.passed is True
    assert PhaseResult(
        **{
            **passing.as_dict(),
            "failure_count": 1,
            "success_count": 99,
        }
    ).passed is False
    assert PhaseResult(
        **{
            **passing.as_dict(),
            "completion_rate": 94.9,
        }
    ).passed is False
    assert PhaseResult(
        **{
            **passing.as_dict(),
            "p95_ms": 1000.1,
        }
    ).passed is False
    assert PhaseResult(
        **{
            **passing.as_dict(),
            "schedule_lag_ms": 100.1,
        }
    ).passed is False

    burst = PhaseResult(
        **{
            **passing.as_dict(),
            "name": "burst",
            "target_rate": 500,
            "completion_rate": 300,
            "minimum_completion_rate": 0,
            "p95_ms": 800,
            "drain_seconds": 0.5,
            "maximum_p95_ms": 2000,
            "maximum_drain_seconds": 2,
        }
    )
    assert burst.passed is True


def test_run_start_request_is_metadata_only_and_has_no_caller_identity() -> None:
    request = build_run_start_request("gate-run", "sustained", 7)

    assert request.idempotency_key == "g1-sustained-gate-run-7"
    assert request.body["external_run_id"] == "g1-sustained-gate-run-7"
    assert request.body["content_capture_mode"] == "metadata_only"
    assert {
        "namespace_id",
        "runtime_id",
        "trust_level",
        "prompt",
        "completion",
        "messages",
        "tool_arguments",
        "tool_result",
    }.isdisjoint(request.body)


async def test_load_scheduler_offers_every_request_and_reports_failures() -> None:
    seen: list[int] = []

    async def operation(index: int) -> int:
        seen.append(index)
        await asyncio.sleep(0)
        return 201 if index != 2 else 409

    result = await run_load_phase(
        LoadPhase(
            name="unit",
            target_rate=100,
            duration_seconds=0.05,
            minimum_completion_ratio=0,
            maximum_p95_ms=10_000,
            maximum_drain_seconds=10,
        ),
        operation,
    )

    assert sorted(seen) == list(range(5))
    assert result.request_count == 5
    assert result.success_count == 4
    assert result.failure_count == 1
    assert result.error_codes == {"http_409": 1}
    assert result.passed is False
