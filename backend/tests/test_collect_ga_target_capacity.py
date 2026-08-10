from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.collect_ga_target_capacity as target_capacity
from scripts.ga_capacity_evidence import (
    CAPACITY_POLICY_SCHEMA_VERSION,
    CLEANUP_RECEIPT_SCHEMA_VERSION,
    CLEANUP_RECEIPT_SIGNATURE_NAMESPACE,
    GROWTH_RECEIPT_SCHEMA_VERSION,
    GROWTH_RECEIPT_SIGNATURE_NAMESPACE,
    LOAD_REPORT_SCHEMA_VERSION,
    LOAD_REPORT_SIGNATURE_NAMESPACE,
    REQUIRED_COUNTERS,
)
from scripts.ga_release_identity import build_release_binding


COMMIT = "a" * 40
BACKEND_IMAGE = f"registry.example.com/duckdock/backend@sha256:{'b' * 64}"
FRONTEND_IMAGE = f"registry.example.com/duckdock/frontend@sha256:{'c' * 64}"
TARGET = "customer-production"
BASE_URL = "https://duckdock.example.com"
NAMESPACE_ID = 321
EXERCISE_ID = "capacity-ga-20260806"
DATABASE_PROVIDER = "Managed MySQL"
LOAD_IDENTITY = "capacity-load@example.com"
STORAGE_IDENTITY = "capacity-dba@example.com"
CLEANUP_IDENTITY = "capacity-cleanup@example.com"


def _generate_key(path: Path) -> None:
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)],
        check=True,
    )


def _sign(path: Path, key: Path, namespace: str) -> Path:
    subprocess.run(
        ["ssh-keygen", "-q", "-Y", "sign", "-f", str(key), "-n", namespace, str(path)],
        check=True,
    )
    return Path(f"{path}.sig")


def _phase(name: str, rate: float, duration: float, requests: int, p95: float) -> dict:
    return {
        "name": name,
        "target_rate": rate,
        "duration_seconds": duration,
        "request_count": requests,
        "success_count": requests,
        "failure_count": 0,
        "elapsed_seconds": duration,
        "completion_rate": 1.0,
        "drain_seconds": 0.1,
        "schedule_lag_ms": 2.0,
        "p50_ms": p95 / 2,
        "p95_ms": p95,
        "p99_ms": p95 * 1.2,
        "max_ms": p95 * 1.5,
        "minimum_completion_rate": 0.95 if name == "sustained" else 0,
        "maximum_p95_ms": 1000 if name == "sustained" else 2000,
        "maximum_drain_seconds": 2 if name == "sustained" else 5,
        "maximum_schedule_lag_ms": 100,
        "error_codes": {},
        "passed": True,
    }


def _load_report(now: datetime) -> dict:
    started_at = now - timedelta(minutes=20)
    finished_at = now - timedelta(minutes=3)
    phases = [
        _phase("sustained", 50, 900, 45_000, 50),
        _phase("burst", 100, 60, 6_000, 100),
    ]
    return {
        "schema_version": LOAD_REPORT_SCHEMA_VERSION,
        "scope": "target-production",
        "target_environment": TARGET,
        "source_commit": COMMIT,
        "images": {
            "backend": {"name": BACKEND_IMAGE},
            "frontend": {"name": FRONTEND_IMAGE},
        },
        "status": "PASSED",
        "observed_at": finished_at.isoformat(),
        "base_url": BASE_URL,
        "namespace_id": NAMESPACE_ID,
        "exercise_id": EXERCISE_ID,
        "run_tag": "abcdef123456",
        "transport": "network HTTPS against target",
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "phases": phases,
        "offered_runs": 51_000,
        "materialized_runs": 51_000,
        "minimum_materialized_runs": 50_000,
        "failure_count": 0,
        "error_rate": 0.0,
        "maximum_error_rate": 0.001,
        "idempotency": {
            "same_key_same_payload_status": 201,
            "same_key_different_payload_status": 409,
            "passed": True,
        },
        "post_growth_timeline_query": {
            "sample_count": 100,
            "success_count": 100,
            "p50_ms": 15,
            "p95_ms": 30,
            "p99_ms": 40,
            "maximum_p95_ms": 250,
            "passed": True,
        },
        "passed": True,
        "cleanup_required": "run signed capacity cleanup collection",
    }


def _growth_receipt(now: datetime, *, agent_runs_delta: int = 51_000) -> dict:
    baseline_mysql = {
        "agent_runs_rows": 100,
        "audit_logs_rows": 200,
        "outbox_events_rows": 200,
        "tagged_agent_runs_rows": 0,
        "pending_outbox_events": 0,
        "data_bytes": 10_000_000,
        "index_bytes": 2_000_000,
        "replica_lag_seconds": 0,
    }
    post_mysql = {
        "agent_runs_rows": baseline_mysql["agent_runs_rows"] + agent_runs_delta,
        "audit_logs_rows": baseline_mysql["audit_logs_rows"] + 51_000,
        "outbox_events_rows": baseline_mysql["outbox_events_rows"] + 51_000,
        "tagged_agent_runs_rows": agent_runs_delta,
        "pending_outbox_events": 0,
        "data_bytes": 20_000_000,
        "index_bytes": 7_000_000,
        "replica_lag_seconds": 2,
    }
    return {
        "schema_version": GROWTH_RECEIPT_SCHEMA_VERSION,
        "exercise_id": EXERCISE_ID,
        "target_environment": TARGET,
        "namespace_id": NAMESPACE_ID,
        "run_tag": "abcdef123456",
        "source_commit": COMMIT,
        "images": {
            "backend": {"name": BACKEND_IMAGE},
            "frontend": {"name": FRONTEND_IMAGE},
        },
        "database_provider": DATABASE_PROVIDER,
        "provider_receipt_id": "mysql-growth-receipt-001",
        "baseline": {
            "phase": "baseline",
            "snapshot_id": "mysql-snapshot-before-001",
            "captured_at": (now - timedelta(minutes=21)).isoformat(),
            "query_sha256": "1" * 64,
            "mysql": baseline_mysql,
        },
        "post_growth": {
            "phase": "post-growth",
            "snapshot_id": "mysql-snapshot-after-001",
            "captured_at": (now - timedelta(minutes=2)).isoformat(),
            "query_sha256": "2" * 64,
            "mysql": post_mysql,
        },
        "completed_at": (now - timedelta(minutes=1, seconds=30)).isoformat(),
    }


def _cleanup_receipt(now: datetime, *, passed: bool = True) -> dict:
    return {
        "schema_version": CLEANUP_RECEIPT_SCHEMA_VERSION,
        "exercise_id": EXERCISE_ID,
        "target_environment": TARGET,
        "namespace_id": NAMESPACE_ID,
        "run_tag": "abcdef123456",
        "source_commit": COMMIT,
        "images": {
            "backend": {"name": BACKEND_IMAGE},
            "frontend": {"name": FRONTEND_IMAGE},
        },
        "cleanup_receipt_id": "capacity-cleanup-001",
        "namespace_deleted": passed,
        "credentials": {
            "reporter_credential_revoked": passed,
            "user_credential_revoked": passed,
        },
        "remaining_rows": {
            counter: 0 if passed else 1 for counter in REQUIRED_COUNTERS
        },
        "completed_at": (now - timedelta(minutes=1)).isoformat(),
    }


def _material(
    tmp_path: Path,
    *,
    agent_runs_delta: int = 51_000,
    cleanup_passed: bool = True,
    reused_role_key: bool = False,
) -> tuple[SimpleNamespace, Path, Path, Path]:
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen is required for signed capacity evidence tests")
    load_key = tmp_path / "load_key"
    storage_key = tmp_path / "storage_key"
    cleanup_key = tmp_path / "cleanup_key"
    _generate_key(load_key)
    _generate_key(storage_key)
    if not reused_role_key:
        _generate_key(cleanup_key)
    public_keys = {
        LOAD_IDENTITY: load_key.with_suffix(".pub").read_text(encoding="utf-8").strip(),
        STORAGE_IDENTITY: storage_key.with_suffix(".pub").read_text(encoding="utf-8").strip(),
        CLEANUP_IDENTITY: (
            storage_key.with_suffix(".pub").read_text(encoding="utf-8").strip()
            if reused_role_key
            else cleanup_key.with_suffix(".pub").read_text(encoding="utf-8").strip()
        ),
    }
    trust_path = tmp_path / "capacity.allowed-signers"
    trust_path.write_text(
        "".join(f"{identity} {public}\n" for identity, public in public_keys.items()),
        encoding="utf-8",
    )
    policy = {
        "schema_version": CAPACITY_POLICY_SCHEMA_VERSION,
        "policy_id": "duckdock-target-capacity-authority",
        "organization": "DuckDock Test Operations",
        "allowed_signers_path": str(trust_path),
        "allowed_signers_sha256": hashlib.sha256(trust_path.read_bytes()).hexdigest(),
        "approved_database_providers": [DATABASE_PROVIDER],
        "load_executor_identities": [LOAD_IDENTITY],
        "storage_observer_identities": [STORAGE_IDENTITY],
        "cleanup_verifier_identities": [CLEANUP_IDENTITY],
        "required_counters": list(REQUIRED_COUNTERS),
        "minimum_database_growth_bytes": 5_000_000,
        "maximum_pending_outbox_events": 100,
        "maximum_replica_lag_seconds": 30,
    }
    policy_path = tmp_path / "capacity-policy.json"
    policy_path.write_text(json.dumps(policy, sort_keys=True) + "\n", encoding="utf-8")
    now = datetime.now(timezone.utc)
    load_path = tmp_path / "load.json"
    growth_path = tmp_path / "growth.json"
    cleanup_path = tmp_path / "cleanup.json"
    load_path.write_text(json.dumps(_load_report(now), sort_keys=True) + "\n", encoding="utf-8")
    growth_path.write_text(
        json.dumps(_growth_receipt(now, agent_runs_delta=agent_runs_delta), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    cleanup_path.write_text(
        json.dumps(_cleanup_receipt(now, passed=cleanup_passed), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    load_signature = _sign(load_path, load_key, LOAD_REPORT_SIGNATURE_NAMESPACE)
    growth_signature = _sign(growth_path, storage_key, GROWTH_RECEIPT_SIGNATURE_NAMESPACE)
    cleanup_signature = _sign(
        cleanup_path,
        storage_key if reused_role_key else cleanup_key,
        CLEANUP_RECEIPT_SIGNATURE_NAMESPACE,
    )
    args = SimpleNamespace(
        target_environment=TARGET,
        source_commit=COMMIT,
        backend_image=BACKEND_IMAGE,
        frontend_image=FRONTEND_IMAGE,
        base_url=BASE_URL,
        namespace_id=NAMESPACE_ID,
        exercise_id=EXERCISE_ID,
        database_provider=DATABASE_PROVIDER,
        capacity_policy=policy_path,
        load_signer_identity=LOAD_IDENTITY,
        storage_signer_identity=STORAGE_IDENTITY,
        cleanup_signer_identity=CLEANUP_IDENTITY,
        load_report=load_path,
        load_signature=load_signature,
        growth_receipt=growth_path,
        growth_signature=growth_signature,
        cleanup_receipt=cleanup_path,
        cleanup_signature=cleanup_signature,
        minimum_sustained_seconds=900,
        minimum_sustained_rps=50,
        minimum_materialized_runs=50_000,
        maximum_error_rate=0.001,
        maximum_write_p95_ms=1000,
        maximum_timeline_p95_ms=250,
        output=tmp_path / "capacity-v3.json",
        release_binding=build_release_binding(
            scope="target-production",
            target_environment=TARGET,
            source_commit=COMMIT,
            backend_image=BACKEND_IMAGE,
            frontend_image=FRONTEND_IMAGE,
        ),
    )
    return args, load_key, storage_key, cleanup_key


def test_collects_signed_load_growth_and_cleanup_evidence(tmp_path: Path) -> None:
    args, _, _, _ = _material(tmp_path)

    report = target_capacity.collect(args)

    assert report["status"] == "PASSED"
    assert report["materialized_runs"] == 51_000
    assert report["data_growth"]["agent_runs_delta"] == 51_000
    assert report["data_growth"]["database_growth_bytes"] == 15_000_000
    assert report["cleanup_verified"] is True


def test_rejects_load_report_signed_by_storage_observer(tmp_path: Path) -> None:
    args, _, storage_key, _ = _material(tmp_path)
    args.load_signature.unlink()
    args.load_signature = _sign(
        args.load_report,
        storage_key,
        LOAD_REPORT_SIGNATURE_NAMESPACE,
    )

    with pytest.raises(ValueError, match="invalid signed capacity evidence"):
        target_capacity.collect(args)


def test_rejects_public_key_reuse_between_capacity_roles(tmp_path: Path) -> None:
    args, _, _, _ = _material(tmp_path, reused_role_key=True)

    with pytest.raises(ValueError, match="reuses a public key"):
        target_capacity.collect(args)


def test_blocks_when_signed_database_rows_do_not_reconcile(tmp_path: Path) -> None:
    args, _, _, _ = _material(tmp_path, agent_runs_delta=50_999)

    report = target_capacity.collect(args)

    assert report["status"] == "BLOCKED"
    assert report["passed"] is False
    assert report["data_growth"]["agent_runs_delta"] == 50_999


def test_blocks_when_signed_cleanup_is_incomplete(tmp_path: Path) -> None:
    args, _, _, _ = _material(tmp_path, cleanup_passed=False)

    report = target_capacity.collect(args)

    assert report["status"] == "BLOCKED"
    assert report["cleanup_verified"] is False


def test_rejects_secret_material_field_in_growth_receipt(tmp_path: Path) -> None:
    args, _, storage_key, _ = _material(tmp_path)
    receipt = json.loads(args.growth_receipt.read_text(encoding="utf-8"))
    receipt["database_password"] = "must-not-be-retained"
    args.growth_receipt.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
    args.growth_signature.unlink()
    args.growth_signature = _sign(
        args.growth_receipt,
        storage_key,
        GROWTH_RECEIPT_SIGNATURE_NAMESPACE,
    )

    with pytest.raises(ValueError, match="growth receipt is not release-bound"):
        target_capacity.collect(args)
