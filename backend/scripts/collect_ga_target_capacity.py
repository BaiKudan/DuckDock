#!/usr/bin/env python3
"""Collect signed target load, database-growth, and cleanup evidence for GA."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

try:
    from scripts.ga_capacity_evidence import (
        CAPACITY_EVIDENCE_SCHEMA_VERSION,
        CAPACITY_POLICY_SCHEMA_VERSION,
        CLEANUP_RECEIPT_SIGNATURE_NAMESPACE,
        DIGEST_RE,
        EXERCISE_RE,
        GROWTH_RECEIPT_SIGNATURE_NAMESPACE,
        LOAD_REPORT_SIGNATURE_NAMESPACE,
        REQUIRED_COUNTERS,
        contains_secret_material_key,
        meaningful,
        sha256,
        validate_cleanup_receipt,
        validate_growth_receipt,
        validate_load_report,
    )
    from scripts.ga_release_identity import build_release_binding
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_capacity_evidence import (
        CAPACITY_EVIDENCE_SCHEMA_VERSION,
        CAPACITY_POLICY_SCHEMA_VERSION,
        CLEANUP_RECEIPT_SIGNATURE_NAMESPACE,
        DIGEST_RE,
        EXERCISE_RE,
        GROWTH_RECEIPT_SIGNATURE_NAMESPACE,
        LOAD_REPORT_SIGNATURE_NAMESPACE,
        REQUIRED_COUNTERS,
        contains_secret_material_key,
        meaningful,
        sha256,
        validate_cleanup_receipt,
        validate_growth_receipt,
        validate_load_report,
    )
    from ga_release_identity import build_release_binding


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} root must be an object")
    return value


def _exact_identity_list(value: Any) -> list[str] | None:
    if not (
        isinstance(value, list)
        and bool(value)
        and all(meaningful(identity) for identity in value)
        and len(value) == len(set(value))
    ):
        return None
    return [str(identity) for identity in value]


def _allowed_signer_bindings(path: Path) -> dict[str, set[str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read capacity allowed-signers: {exc}") from exc
    bindings: dict[str, set[str]] = {}
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        key_index = next(
            (
                index
                for index, field in enumerate(fields[1:], start=1)
                if field.startswith(("ssh-", "ecdsa-", "sk-"))
            ),
            None,
        )
        if key_index is None or key_index + 1 >= len(fields):
            raise ValueError(f"capacity allowed-signers line {line_number} has no public key")
        key_material = f"{fields[key_index]} {fields[key_index + 1]}"
        for principal in fields[0].split(","):
            if not meaningful(principal) or any(character in principal for character in "*?!"):
                raise ValueError(
                    f"capacity allowed-signers line {line_number} must use exact principals"
                )
            bindings.setdefault(principal, set()).add(key_material)
    if not bindings:
        raise ValueError("capacity allowed-signers contains no principals")
    key_owners: dict[str, set[str]] = {}
    for principal, keys in bindings.items():
        for key in keys:
            key_owners.setdefault(key, set()).add(principal)
    if any(len(owners) != 1 for owners in key_owners.values()):
        raise ValueError("capacity allowed-signers reuses a public key across identities")
    return bindings


def load_capacity_policy(
    path: Path,
    *,
    database_provider: str,
    load_identity: str,
    storage_identity: str,
    cleanup_identity: str,
) -> tuple[dict[str, Any], Path, str]:
    policy = _load_object(path, "capacity trust policy")
    expected_keys = {
        "schema_version",
        "policy_id",
        "organization",
        "allowed_signers_path",
        "allowed_signers_sha256",
        "approved_database_providers",
        "load_executor_identities",
        "storage_observer_identities",
        "cleanup_verifier_identities",
        "required_counters",
        "minimum_database_growth_bytes",
        "maximum_pending_outbox_events",
        "maximum_replica_lag_seconds",
    }
    providers = policy.get("approved_database_providers")
    provider_valid = (
        isinstance(providers, list)
        and bool(providers)
        and all(meaningful(provider) for provider in providers)
        and len(providers) == len(set(providers))
        and database_provider in providers
    )
    load_identities = _exact_identity_list(policy.get("load_executor_identities"))
    storage_identities = _exact_identity_list(policy.get("storage_observer_identities"))
    cleanup_identities = _exact_identity_list(policy.get("cleanup_verifier_identities"))
    identity_lists = (load_identities, storage_identities, cleanup_identities)
    all_identities = [identity for identities in identity_lists for identity in identities or []]
    thresholds_valid = (
        isinstance(policy.get("minimum_database_growth_bytes"), int)
        and not isinstance(policy.get("minimum_database_growth_bytes"), bool)
        and policy["minimum_database_growth_bytes"] > 0
        and isinstance(policy.get("maximum_pending_outbox_events"), int)
        and not isinstance(policy.get("maximum_pending_outbox_events"), bool)
        and policy["maximum_pending_outbox_events"] >= 0
        and isinstance(policy.get("maximum_replica_lag_seconds"), (int, float))
        and not isinstance(policy.get("maximum_replica_lag_seconds"), bool)
        and math.isfinite(float(policy["maximum_replica_lag_seconds"]))
        and policy["maximum_replica_lag_seconds"] >= 0
    )
    if not (
        set(policy) == expected_keys
        and policy.get("schema_version") == CAPACITY_POLICY_SCHEMA_VERSION
        and meaningful(policy.get("policy_id"))
        and meaningful(policy.get("organization"))
        and provider_valid
        and all(identity_lists)
        and len(all_identities) == len(set(all_identities))
        and load_identity in (load_identities or [])
        and storage_identity in (storage_identities or [])
        and cleanup_identity in (cleanup_identities or [])
        and policy.get("required_counters") == list(REQUIRED_COUNTERS)
        and thresholds_valid
    ):
        raise ValueError("capacity policy does not authorize the requested evidence roles")
    raw_trust_path = policy.get("allowed_signers_path")
    if not isinstance(raw_trust_path, str) or not raw_trust_path:
        raise ValueError("capacity policy has no allowed_signers_path")
    trust_path = Path(raw_trust_path).expanduser()
    if not trust_path.is_absolute():
        trust_path = (path.parent / trust_path).resolve()
    expected_digest = str(policy.get("allowed_signers_sha256", ""))
    if (
        not trust_path.is_file()
        or not DIGEST_RE.fullmatch(expected_digest)
        or sha256(trust_path) != expected_digest
    ):
        raise ValueError("capacity trust store is missing or digest-mismatched")
    bindings = _allowed_signer_bindings(trust_path)
    if set(bindings) != set(all_identities):
        raise ValueError("capacity trust principals do not exactly match policy identities")
    return policy, trust_path, sha256(path)


def verify_signature(
    path: Path,
    signature: Path,
    *,
    identity: str,
    allowed_signers: Path,
    namespace: str,
) -> None:
    try:
        payload = path.read_bytes()
        completed = subprocess.run(
            [
                "ssh-keygen",
                "-Y",
                "verify",
                "-f",
                str(allowed_signers),
                "-I",
                identity,
                "-n",
                namespace,
                "-s",
                str(signature),
            ],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except OSError as exc:
        raise ValueError(f"cannot verify signed capacity evidence: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stdout.decode("utf-8", errors="replace").strip()
        raise ValueError(f"invalid signed capacity evidence: {detail}")


def _signed_reference(path: Path, signature: Path, identity: str) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "sha256": sha256(path),
        "signature_path": str(signature.resolve()),
        "signer_identity": identity,
    }


def collect(args: argparse.Namespace) -> dict[str, Any]:
    policy, allowed_signers, policy_digest = load_capacity_policy(
        args.capacity_policy,
        database_provider=args.database_provider,
        load_identity=args.load_signer_identity,
        storage_identity=args.storage_signer_identity,
        cleanup_identity=args.cleanup_signer_identity,
    )
    load_report = _load_object(args.load_report, "target capacity load report")
    growth_receipt = _load_object(args.growth_receipt, "capacity growth receipt")
    cleanup_receipt = _load_object(args.cleanup_receipt, "capacity cleanup receipt")
    for path, signature, identity, namespace in (
        (
            args.load_report,
            args.load_signature,
            args.load_signer_identity,
            LOAD_REPORT_SIGNATURE_NAMESPACE,
        ),
        (
            args.growth_receipt,
            args.growth_signature,
            args.storage_signer_identity,
            GROWTH_RECEIPT_SIGNATURE_NAMESPACE,
        ),
        (
            args.cleanup_receipt,
            args.cleanup_signature,
            args.cleanup_signer_identity,
            CLEANUP_RECEIPT_SIGNATURE_NAMESPACE,
        ),
    ):
        verify_signature(
            path,
            signature,
            identity=identity,
            allowed_signers=allowed_signers,
            namespace=namespace,
        )
    load = validate_load_report(
        load_report,
        target_environment=args.target_environment,
        source_commit=args.source_commit,
        backend_image=args.backend_image,
        frontend_image=args.frontend_image,
        base_url=args.base_url,
        namespace_id=args.namespace_id,
        exercise_id=args.exercise_id,
    )
    growth = validate_growth_receipt(
        growth_receipt,
        load=load,
        target_environment=args.target_environment,
        source_commit=args.source_commit,
        backend_image=args.backend_image,
        frontend_image=args.frontend_image,
        namespace_id=args.namespace_id,
        exercise_id=args.exercise_id,
        database_provider=args.database_provider,
    )
    cleanup = validate_cleanup_receipt(
        cleanup_receipt,
        load=load,
        growth=growth,
        target_environment=args.target_environment,
        source_commit=args.source_commit,
        backend_image=args.backend_image,
        frontend_image=args.frontend_image,
        namespace_id=args.namespace_id,
        exercise_id=args.exercise_id,
    )
    growth_passed = (
        growth["counts_monotonic"] is True
        and growth["agent_runs_delta"] == load["materialized_runs"]
        and growth["tagged_agent_runs"] == load["materialized_runs"]
        and growth["audit_logs_delta"] >= load["materialized_runs"]
        and growth["outbox_events_delta"] >= load["materialized_runs"]
        and growth["database_growth_bytes"] >= policy["minimum_database_growth_bytes"]
        and growth["pending_outbox_events"] <= policy["maximum_pending_outbox_events"]
        and growth["replica_lag_seconds"] <= policy["maximum_replica_lag_seconds"]
    )
    load_passed = (
        load["sustained_seconds"] >= args.minimum_sustained_seconds
        and load["sustained_rps"] >= args.minimum_sustained_rps
        and load["materialized_runs"] >= args.minimum_materialized_runs
        and load["error_rate"] <= args.maximum_error_rate
        and load["write_p95_ms"] <= args.maximum_write_p95_ms
        and load["timeline_p95_ms"] <= args.maximum_timeline_p95_ms
    )
    passed = load_passed and growth_passed and cleanup["passed"] is True
    observed_at = datetime.now(timezone.utc).isoformat()
    report = {
        "schema_version": CAPACITY_EVIDENCE_SCHEMA_VERSION,
        **args.release_binding,
        "status": "PASSED" if passed else "BLOCKED",
        "passed": passed,
        "observed_at": observed_at,
        "base_url": args.base_url,
        "namespace_id": args.namespace_id,
        "database_provider": args.database_provider,
        "transport": "network HTTPS against target",
        "exercise": {
            "exercise_id": args.exercise_id,
            "run_tag": load["run_tag"],
            "started_at": load_report["started_at"],
            "finished_at": load_report["finished_at"],
            "completed_at": observed_at,
        },
        "requirements": {
            "minimum_sustained_seconds": args.minimum_sustained_seconds,
            "minimum_sustained_rps": args.minimum_sustained_rps,
            "minimum_materialized_runs": args.minimum_materialized_runs,
            "maximum_error_rate": args.maximum_error_rate,
            "maximum_write_p95_ms": args.maximum_write_p95_ms,
            "maximum_timeline_p95_ms": args.maximum_timeline_p95_ms,
        },
        "phases": load_report["phases"],
        "offered_runs": load["offered_runs"],
        "materialized_runs": load["materialized_runs"],
        "failure_count": load_report["failure_count"],
        "error_rate": load["error_rate"],
        "post_growth_timeline_query": load_report["post_growth_timeline_query"],
        "data_growth": {
            "agent_runs_delta": growth["agent_runs_delta"],
            "audit_logs_delta": growth["audit_logs_delta"],
            "outbox_events_delta": growth["outbox_events_delta"],
            "tagged_agent_runs": growth["tagged_agent_runs"],
            "database_growth_bytes": growth["database_growth_bytes"],
            "pending_outbox_events": growth["pending_outbox_events"],
            "replica_lag_seconds": growth["replica_lag_seconds"],
            "counts_monotonic": growth["counts_monotonic"],
        },
        "cleanup_verified": cleanup["passed"],
        "capacity_policy": {
            "path": str(args.capacity_policy.resolve()),
            "sha256": policy_digest,
            "policy_id": policy["policy_id"],
            "allowed_signers_path": str(allowed_signers.resolve()),
            "allowed_signers_sha256": sha256(allowed_signers),
        },
        "load_report": {
            **load_report,
            "signed_evidence": _signed_reference(
                args.load_report,
                args.load_signature,
                args.load_signer_identity,
            ),
        },
        "growth_receipt": {
            **growth_receipt,
            "signed_evidence": _signed_reference(
                args.growth_receipt,
                args.growth_signature,
                args.storage_signer_identity,
            ),
        },
        "cleanup_receipt": {
            **cleanup_receipt,
            "signed_evidence": _signed_reference(
                args.cleanup_receipt,
                args.cleanup_signature,
                args.cleanup_signer_identity,
            ),
        },
    }
    if contains_secret_material_key(report):
        raise ValueError("capacity evidence contains a forbidden credential field")
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-environment", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--backend-image", required=True)
    parser.add_argument("--frontend-image", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--namespace-id", type=int, required=True)
    parser.add_argument("--exercise-id", required=True)
    parser.add_argument("--database-provider", required=True)
    parser.add_argument("--capacity-policy", type=Path, required=True)
    parser.add_argument("--load-signer-identity", required=True)
    parser.add_argument("--storage-signer-identity", required=True)
    parser.add_argument("--cleanup-signer-identity", required=True)
    parser.add_argument("--load-report", type=Path, required=True)
    parser.add_argument("--load-signature", type=Path, required=True)
    parser.add_argument("--growth-receipt", type=Path, required=True)
    parser.add_argument("--growth-signature", type=Path, required=True)
    parser.add_argument("--cleanup-receipt", type=Path, required=True)
    parser.add_argument("--cleanup-signature", type=Path, required=True)
    parser.add_argument("--minimum-sustained-seconds", type=float, default=900)
    parser.add_argument("--minimum-sustained-rps", type=float, default=50)
    parser.add_argument("--minimum-materialized-runs", type=int, default=50_000)
    parser.add_argument("--maximum-error-rate", type=float, default=0.001)
    parser.add_argument("--maximum-write-p95-ms", type=float, default=1000)
    parser.add_argument("--maximum-timeline-p95-ms", type=float, default=250)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite-output", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.namespace_id < 1:
            raise ValueError("namespace ID must be positive")
        if not EXERCISE_RE.fullmatch(args.exercise_id):
            raise ValueError("exercise ID must be 8-64 safe characters")
        if len(
            {
                args.load_signer_identity,
                args.storage_signer_identity,
                args.cleanup_signer_identity,
            }
        ) != 3:
            raise ValueError("load, storage and cleanup signer identities must be distinct")
        if not all(
            meaningful(value)
            for value in (
                args.target_environment,
                args.database_provider,
                args.load_signer_identity,
                args.storage_signer_identity,
                args.cleanup_signer_identity,
            )
        ):
            raise ValueError("target, provider and identities must be non-placeholder")
        if not (
            args.base_url.startswith("https://")
            and "@" not in args.base_url
            and "?" not in args.base_url
            and "#" not in args.base_url
        ):
            raise ValueError("base URL must be a credential-free HTTPS origin")
        if min(
            args.minimum_sustained_seconds,
            args.minimum_sustained_rps,
            args.minimum_materialized_runs,
            args.maximum_write_p95_ms,
            args.maximum_timeline_p95_ms,
        ) <= 0 or args.maximum_error_rate < 0:
            raise ValueError("capacity thresholds must be positive and error rate non-negative")
        for path, label in (
            (args.capacity_policy, "capacity policy"),
            (args.load_report, "load report"),
            (args.load_signature, "load report signature"),
            (args.growth_receipt, "growth receipt"),
            (args.growth_signature, "growth receipt signature"),
            (args.cleanup_receipt, "cleanup receipt"),
            (args.cleanup_signature, "cleanup receipt signature"),
        ):
            if not path.is_file():
                raise ValueError(f"{label} does not exist")
        if args.output.exists() and not args.overwrite_output:
            raise ValueError("output already exists; choose a new path or pass --overwrite-output")
        args.release_binding = build_release_binding(
            scope="target-production",
            target_environment=args.target_environment,
            source_commit=args.source_commit,
            backend_image=args.backend_image,
            frontend_image=args.frontend_image,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = collect(args)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"Target capacity collection failed: {exc}", file=sys.stderr)
        return 3
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
