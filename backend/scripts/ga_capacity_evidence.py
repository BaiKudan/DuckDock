"""Strict contracts shared by the target capacity v3 collector and GA gate."""

from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


CAPACITY_EVIDENCE_SCHEMA_VERSION = "duckdock-target-capacity-gate-v3"
LOAD_REPORT_SCHEMA_VERSION = "duckdock-target-capacity-gate-v2"
CAPACITY_POLICY_SCHEMA_VERSION = "duckdock-ga-capacity-trust-policy-v1"
GROWTH_RECEIPT_SCHEMA_VERSION = "duckdock-ga-capacity-growth-receipt-v1"
CLEANUP_RECEIPT_SCHEMA_VERSION = "duckdock-ga-capacity-cleanup-receipt-v1"
LOAD_REPORT_SIGNATURE_NAMESPACE = "duckdock-capacity-load-report"
GROWTH_RECEIPT_SIGNATURE_NAMESPACE = "duckdock-capacity-growth-receipt"
CLEANUP_RECEIPT_SIGNATURE_NAMESPACE = "duckdock-capacity-cleanup-receipt"
REQUIRED_COUNTERS = (
    "agent_runs_rows",
    "audit_logs_rows",
    "outbox_events_rows",
    "tagged_agent_runs_rows",
)
MYSQL_SNAPSHOT_KEYS = {
    *REQUIRED_COUNTERS,
    "pending_outbox_events",
    "data_bytes",
    "index_bytes",
    "replica_lag_seconds",
}
EXERCISE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
RUN_TAG_RE = re.compile(r"^[0-9a-f]{12}$")
PLACEHOLDER_MARKERS = ("__CHANGE_ME", "example.invalid", "<", ">")


def meaningful(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not any(marker in value for marker in PLACEHOLDER_MARKERS)
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def contains_secret_material_key(value: Any) -> bool:
    forbidden = {
        "access_key",
        "api_key",
        "client_secret",
        "credential",
        "credential_value",
        "database_password",
        "password",
        "password_value",
        "plaintext",
        "plaintext_value",
        "private_key",
        "raw_secret",
        "reporter_token",
        "secret_value",
        "secret_values",
        "session_token",
        "token",
        "token_value",
        "user_token",
    }
    if isinstance(value, dict):
        return any(
            str(key).lower() in forbidden or contains_secret_material_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(contains_secret_material_key(item) for item in value)
    return False


def _strict_int(value: Any, *, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _number(value: Any, *, minimum: float = 0) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= minimum
    )


def _https_origin(value: Any) -> bool:
    if not meaningful(value):
        return False
    parsed = urlparse(str(value))
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


def _release_binding_valid(
    value: dict[str, Any],
    *,
    target_environment: str,
    source_commit: str,
    backend_image: str,
    frontend_image: str,
) -> bool:
    images = value.get("images")
    return (
        value.get("scope") == "target-production"
        and value.get("target_environment") == target_environment
        and value.get("source_commit") == source_commit
        and isinstance(images, dict)
        and set(images) == {"backend", "frontend"}
        and all(
            isinstance(images.get(component), dict)
            and set(images[component]) == {"name"}
            for component in ("backend", "frontend")
        )
        and images["backend"].get("name") == backend_image
        and images["frontend"].get("name") == frontend_image
    )


def _phase_valid(value: Any) -> bool:
    expected_keys = {
        "name",
        "target_rate",
        "duration_seconds",
        "request_count",
        "success_count",
        "failure_count",
        "elapsed_seconds",
        "completion_rate",
        "drain_seconds",
        "schedule_lag_ms",
        "p50_ms",
        "p95_ms",
        "p99_ms",
        "max_ms",
        "minimum_completion_rate",
        "maximum_p95_ms",
        "maximum_drain_seconds",
        "maximum_schedule_lag_ms",
        "error_codes",
        "passed",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        return False
    request_count = value.get("request_count")
    success_count = value.get("success_count")
    failure_count = value.get("failure_count")
    completion_rate = value.get("completion_rate")
    error_codes = value.get("error_codes")
    computed_completion = (
        success_count / request_count
        if _strict_int(request_count, minimum=1) and _strict_int(success_count)
        else -1
    )
    return (
        value.get("name") in {"sustained", "burst"}
        and _number(value.get("target_rate"), minimum=0.001)
        and _number(value.get("duration_seconds"), minimum=0.001)
        and _strict_int(request_count, minimum=1)
        and _strict_int(success_count)
        and _strict_int(failure_count)
        and request_count == success_count + failure_count
        and _number(value.get("elapsed_seconds"))
        and _number(completion_rate)
        and abs(float(completion_rate) - computed_completion) <= 1e-9
        and all(
            _number(value.get(key))
            for key in (
                "drain_seconds",
                "schedule_lag_ms",
                "p50_ms",
                "p95_ms",
                "p99_ms",
                "max_ms",
                "minimum_completion_rate",
                "maximum_p95_ms",
                "maximum_drain_seconds",
                "maximum_schedule_lag_ms",
            )
        )
        and isinstance(error_codes, dict)
        and all(meaningful(key) and _strict_int(count) for key, count in error_codes.items())
        and sum(error_codes.values()) == failure_count
        and value.get("passed") is True
        and failure_count == 0
        and float(value["p95_ms"]) <= float(value["maximum_p95_ms"])
        and float(value["drain_seconds"]) <= float(value["maximum_drain_seconds"])
        and float(value["schedule_lag_ms"]) <= float(value["maximum_schedule_lag_ms"])
        and float(completion_rate) >= float(value["minimum_completion_rate"])
    )


def validate_load_report(
    report: Any,
    *,
    target_environment: str,
    source_commit: str,
    backend_image: str,
    frontend_image: str,
    base_url: str,
    namespace_id: int,
    exercise_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(report, dict):
        raise ValueError("capacity load report must be a JSON object")
    expected_keys = {
        "schema_version",
        "scope",
        "target_environment",
        "source_commit",
        "images",
        "status",
        "observed_at",
        "base_url",
        "namespace_id",
        "exercise_id",
        "run_tag",
        "transport",
        "started_at",
        "finished_at",
        "phases",
        "offered_runs",
        "materialized_runs",
        "minimum_materialized_runs",
        "failure_count",
        "error_rate",
        "maximum_error_rate",
        "idempotency",
        "post_growth_timeline_query",
        "passed",
        "cleanup_required",
    }
    started_at = parse_time(report.get("started_at"))
    finished_at = parse_time(report.get("finished_at"))
    observed_at = parse_time(report.get("observed_at"))
    current = now or datetime.now(timezone.utc)
    phases = report.get("phases")
    phase_names = [phase.get("name") for phase in phases or [] if isinstance(phase, dict)]
    offered = report.get("offered_runs")
    materialized = report.get("materialized_runs")
    failure_count = report.get("failure_count")
    idempotency = report.get("idempotency")
    timeline = report.get("post_growth_timeline_query")
    if not (
        set(report) == expected_keys
        and report.get("schema_version") == LOAD_REPORT_SCHEMA_VERSION
        and _release_binding_valid(
            report,
            target_environment=target_environment,
            source_commit=source_commit,
            backend_image=backend_image,
            frontend_image=frontend_image,
        )
        and report.get("status") == "PASSED"
        and report.get("passed") is True
        and _https_origin(report.get("base_url"))
        and str(report.get("base_url", "")).rstrip("/") == base_url.rstrip("/")
        and report.get("namespace_id") == namespace_id
        and report.get("exercise_id") == exercise_id
        and bool(EXERCISE_RE.fullmatch(exercise_id))
        and bool(RUN_TAG_RE.fullmatch(str(report.get("run_tag", ""))))
        and report.get("transport") == "network HTTPS against target"
        and started_at is not None
        and finished_at is not None
        and observed_at == finished_at
        and started_at < finished_at <= current
        and isinstance(phases, list)
        and len(phases) == 2
        and phase_names == ["sustained", "burst"]
        and all(_phase_valid(phase) for phase in phases)
        and _strict_int(offered, minimum=1)
        and offered == sum(phase["request_count"] for phase in phases)
        and _strict_int(materialized, minimum=1)
        and materialized == sum(phase["success_count"] for phase in phases)
        and _strict_int(report.get("minimum_materialized_runs"), minimum=1)
        and materialized >= report["minimum_materialized_runs"]
        and _strict_int(failure_count)
        and failure_count == offered - materialized
        and _number(report.get("error_rate"))
        and abs(float(report["error_rate"]) - failure_count / offered) <= 1e-12
        and _number(report.get("maximum_error_rate"))
        and float(report["error_rate"]) <= float(report["maximum_error_rate"])
        and isinstance(idempotency, dict)
        and set(idempotency)
        == {"same_key_same_payload_status", "same_key_different_payload_status", "passed"}
        and idempotency.get("same_key_same_payload_status") == 201
        and idempotency.get("same_key_different_payload_status") == 409
        and idempotency.get("passed") is True
        and isinstance(timeline, dict)
        and set(timeline)
        == {
            "sample_count",
            "success_count",
            "p50_ms",
            "p95_ms",
            "p99_ms",
            "maximum_p95_ms",
            "passed",
        }
        and _strict_int(timeline.get("sample_count"), minimum=1)
        and timeline.get("success_count") == timeline.get("sample_count")
        and all(_number(timeline.get(key)) for key in ("p50_ms", "p95_ms", "p99_ms", "maximum_p95_ms"))
        and float(timeline["p95_ms"]) <= float(timeline["maximum_p95_ms"])
        and timeline.get("passed") is True
        and meaningful(report.get("cleanup_required"))
        and not contains_secret_material_key(report)
    ):
        raise ValueError("capacity load report is not a complete passing target HTTPS exercise")
    sustained = phases[0]
    return {
        "started_at": started_at,
        "finished_at": finished_at,
        "run_tag": report["run_tag"],
        "offered_runs": offered,
        "materialized_runs": materialized,
        "error_rate": float(report["error_rate"]),
        "sustained_seconds": float(sustained["duration_seconds"]),
        "sustained_rps": float(sustained["target_rate"]),
        "write_p95_ms": float(sustained["p95_ms"]),
        "timeline_p95_ms": float(timeline["p95_ms"]),
    }


def _snapshot(value: Any, *, phase: str) -> tuple[dict[str, Any], datetime] | None:
    if not isinstance(value, dict) or set(value) != {
        "phase",
        "snapshot_id",
        "captured_at",
        "query_sha256",
        "mysql",
    }:
        return None
    captured_at = parse_time(value.get("captured_at"))
    mysql = value.get("mysql")
    if not (
        value.get("phase") == phase
        and meaningful(value.get("snapshot_id"))
        and captured_at is not None
        and bool(DIGEST_RE.fullmatch(str(value.get("query_sha256", ""))))
        and isinstance(mysql, dict)
        and set(mysql) == MYSQL_SNAPSHOT_KEYS
        and all(_strict_int(mysql.get(counter)) for counter in REQUIRED_COUNTERS)
        and _strict_int(mysql.get("pending_outbox_events"))
        and _strict_int(mysql.get("data_bytes"))
        and _strict_int(mysql.get("index_bytes"))
        and _number(mysql.get("replica_lag_seconds"))
    ):
        return None
    return mysql, captured_at


def validate_growth_receipt(
    receipt: Any,
    *,
    load: dict[str, Any],
    target_environment: str,
    source_commit: str,
    backend_image: str,
    frontend_image: str,
    namespace_id: int,
    exercise_id: str,
    database_provider: str,
) -> dict[str, Any]:
    if not isinstance(receipt, dict):
        raise ValueError("capacity growth receipt must be a JSON object")
    expected_keys = {
        "schema_version",
        "exercise_id",
        "target_environment",
        "namespace_id",
        "run_tag",
        "source_commit",
        "images",
        "database_provider",
        "provider_receipt_id",
        "baseline",
        "post_growth",
        "completed_at",
    }
    baseline_result = _snapshot(receipt.get("baseline"), phase="baseline")
    post_result = _snapshot(receipt.get("post_growth"), phase="post-growth")
    completed_at = parse_time(receipt.get("completed_at"))
    if not (
        set(receipt) == expected_keys
        and receipt.get("schema_version") == GROWTH_RECEIPT_SCHEMA_VERSION
        and receipt.get("exercise_id") == exercise_id
        and receipt.get("target_environment") == target_environment
        and receipt.get("namespace_id") == namespace_id
        and receipt.get("run_tag") == load.get("run_tag")
        and receipt.get("source_commit") == source_commit
        and _release_binding_valid(
            {"scope": "target-production", **receipt},
            target_environment=target_environment,
            source_commit=source_commit,
            backend_image=backend_image,
            frontend_image=frontend_image,
        )
        and receipt.get("database_provider") == database_provider
        and meaningful(receipt.get("provider_receipt_id"))
        and baseline_result is not None
        and post_result is not None
        and completed_at is not None
        and not contains_secret_material_key(receipt)
    ):
        raise ValueError("capacity growth receipt is not release-bound or structurally complete")
    baseline, baseline_at = baseline_result
    post, post_at = post_result
    load_started = load["started_at"]
    load_finished = load["finished_at"]
    if not (baseline_at <= load_started < load_finished <= post_at <= completed_at):
        raise ValueError("capacity storage snapshots do not bracket the load exercise")
    deltas = {
        counter: post[counter] - baseline[counter]
        for counter in REQUIRED_COUNTERS[:3]
    }
    database_growth_bytes = (
        post["data_bytes"]
        + post["index_bytes"]
        - baseline["data_bytes"]
        - baseline["index_bytes"]
    )
    return {
        "baseline_captured_at": baseline_at,
        "post_growth_captured_at": post_at,
        "completed_at": completed_at,
        "agent_runs_delta": deltas["agent_runs_rows"],
        "audit_logs_delta": deltas["audit_logs_rows"],
        "outbox_events_delta": deltas["outbox_events_rows"],
        "tagged_agent_runs": post["tagged_agent_runs_rows"],
        "database_growth_bytes": database_growth_bytes,
        "pending_outbox_events": post["pending_outbox_events"],
        "replica_lag_seconds": float(post["replica_lag_seconds"]),
        "counts_monotonic": all(delta >= 0 for delta in deltas.values()),
    }


def validate_cleanup_receipt(
    receipt: Any,
    *,
    load: dict[str, Any],
    growth: dict[str, Any],
    target_environment: str,
    source_commit: str,
    backend_image: str,
    frontend_image: str,
    namespace_id: int,
    exercise_id: str,
) -> dict[str, Any]:
    if not isinstance(receipt, dict):
        raise ValueError("capacity cleanup receipt must be a JSON object")
    expected_keys = {
        "schema_version",
        "exercise_id",
        "target_environment",
        "namespace_id",
        "run_tag",
        "source_commit",
        "images",
        "cleanup_receipt_id",
        "namespace_deleted",
        "credentials",
        "remaining_rows",
        "completed_at",
    }
    credentials = receipt.get("credentials")
    remaining = receipt.get("remaining_rows")
    completed_at = parse_time(receipt.get("completed_at"))
    structurally_valid = (
        set(receipt) == expected_keys
        and receipt.get("schema_version") == CLEANUP_RECEIPT_SCHEMA_VERSION
        and receipt.get("exercise_id") == exercise_id
        and receipt.get("target_environment") == target_environment
        and receipt.get("namespace_id") == namespace_id
        and receipt.get("run_tag") == load.get("run_tag")
        and receipt.get("source_commit") == source_commit
        and _release_binding_valid(
            {"scope": "target-production", **receipt},
            target_environment=target_environment,
            source_commit=source_commit,
            backend_image=backend_image,
            frontend_image=frontend_image,
        )
        and meaningful(receipt.get("cleanup_receipt_id"))
        and isinstance(receipt.get("namespace_deleted"), bool)
        and isinstance(credentials, dict)
        and set(credentials) == {"reporter_credential_revoked", "user_credential_revoked"}
        and all(isinstance(value, bool) for value in credentials.values())
        and isinstance(remaining, dict)
        and set(remaining) == set(REQUIRED_COUNTERS)
        and all(_strict_int(remaining.get(counter)) for counter in REQUIRED_COUNTERS)
        and completed_at is not None
        and completed_at >= growth["completed_at"]
        and not contains_secret_material_key(receipt)
    )
    if not structurally_valid:
        raise ValueError("capacity cleanup receipt is not release-bound or structurally complete")
    passed = (
        receipt.get("namespace_deleted") is True
        and credentials.get("reporter_credential_revoked") is True
        and credentials.get("user_credential_revoked") is True
        and all(remaining[counter] == 0 for counter in REQUIRED_COUNTERS)
    )
    return {"completed_at": completed_at, "passed": passed}
