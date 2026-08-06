#!/usr/bin/env python3
"""Assemble an approval-empty GA authorization from release-bound target evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

try:
    from scripts.ga_capacity_evidence import (
        validate_cleanup_receipt,
        validate_growth_receipt,
        validate_load_report,
    )
    from scripts.verify_ga_production_authorization import (
        SCHEMA_VERSION,
        evaluate,
        lint_authorization,
    )
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_capacity_evidence import (
        validate_cleanup_receipt,
        validate_growth_receipt,
        validate_load_report,
    )
    from verify_ga_production_authorization import SCHEMA_VERSION, evaluate, lint_authorization


REQUEST_SCHEMA_VERSION = "duckdock-ga-preapproval-assembly-request-v1"
RECEIPT_SCHEMA_VERSION = "duckdock-ga-preapproval-assembly-v1"
REQUEST_KEYS = {"schema_version", "release", "target", "evidence"}
RELEASE_KEYS = {
    "version",
    "git_commit",
    "backend_image",
    "frontend_image",
    "contract_digest",
    "provenance_evidence_path",
}
TARGET_KEYS = {
    "target_id",
    "environment",
    "deployment_mode",
    "public_base_url",
    "object_store_url",
    "fault_domains",
    "maximum_rpo_seconds",
    "maximum_rto_seconds",
    "minimum_sustained_rps",
}
CONTROL_NAMES = (
    "application_readiness",
    "tls",
    "secrets",
    "network",
    "alerting",
    "recovery",
    "capacity",
    "high_availability",
    "security_assessment",
)


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} root must be an object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _resolve_input(raw: Any, *, base: Path, label: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{label} must be a non-empty file path")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = base / path
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"{label} does not exist: {path}")
    return path


def _reference(path: Path, *, output_parent: Path, report: dict[str, Any]) -> dict[str, Any]:
    observed_at = report.get("observed_at")
    if not isinstance(observed_at, str) or not observed_at.strip():
        raise ValueError(f"evidence has no observed_at: {path}")
    return {
        "path": os.path.relpath(path, output_parent),
        "sha256": _sha256(path),
        "observed_at": observed_at,
    }


def _required(report: dict[str, Any], key: str, label: str) -> Any:
    if key not in report:
        raise ValueError(f"{label} evidence is missing {key}")
    return report[key]


def _project_capacity(
    report: dict[str, Any],
    *,
    report_path: Path,
    output_parent: Path,
    release: dict[str, Any],
    target: dict[str, Any],
    current: datetime,
) -> dict[str, Any]:
    exercise = _required(report, "exercise", "capacity")
    if not isinstance(exercise, dict):
        raise ValueError("capacity evidence exercise must be an object")
    namespace_id = _required(report, "namespace_id", "capacity")
    database_provider = _required(report, "database_provider", "capacity")
    if not isinstance(namespace_id, int) or isinstance(namespace_id, bool):
        raise ValueError("capacity namespace_id must be an integer")
    if not isinstance(database_provider, str):
        raise ValueError("capacity database_provider must be a string")

    raw_receipts: dict[str, dict[str, Any]] = {}
    for key in ("load_report", "growth_receipt", "cleanup_receipt"):
        embedded = _required(report, key, "capacity")
        if not isinstance(embedded, dict) or not isinstance(embedded.get("signed_evidence"), dict):
            raise ValueError(f"capacity {key} has no signed_evidence")
        signed = embedded["signed_evidence"]
        raw_path = _resolve_input(
            signed.get("path"),
            base=output_parent,
            label=f"capacity {key} raw evidence",
        )
        raw_receipts[key] = _load_object(raw_path, f"capacity {key} raw evidence")

    exercise_id = str(exercise.get("exercise_id", ""))
    load = validate_load_report(
        raw_receipts["load_report"],
        target_environment=str(target["target_id"]),
        source_commit=str(release["git_commit"]),
        backend_image=str(release["backend_image"]),
        frontend_image=str(release["frontend_image"]),
        base_url=str(target["public_base_url"]),
        namespace_id=namespace_id,
        exercise_id=exercise_id,
        now=current,
    )
    growth = validate_growth_receipt(
        raw_receipts["growth_receipt"],
        load=load,
        target_environment=str(target["target_id"]),
        source_commit=str(release["git_commit"]),
        backend_image=str(release["backend_image"]),
        frontend_image=str(release["frontend_image"]),
        namespace_id=namespace_id,
        exercise_id=exercise_id,
        database_provider=database_provider,
    )
    cleanup = validate_cleanup_receipt(
        raw_receipts["cleanup_receipt"],
        load=load,
        growth=growth,
        target_environment=str(target["target_id"]),
        source_commit=str(release["git_commit"]),
        backend_image=str(release["backend_image"]),
        frontend_image=str(release["frontend_image"]),
        namespace_id=namespace_id,
        exercise_id=exercise_id,
    )
    timeline_query = report.get("post_growth_timeline_query")
    if not isinstance(timeline_query, dict):
        raise ValueError("capacity post_growth_timeline_query must be an object")
    return {
        "status": _required(report, "status", "capacity"),
        "database_provider": database_provider,
        "sustained_seconds": load["sustained_seconds"],
        "sustained_rps": load["sustained_rps"],
        "materialized_runs": load["materialized_runs"],
        "error_rate": load["error_rate"],
        "write_p95_ms": load["write_p95_ms"],
        "timeline_p95_ms": load["timeline_p95_ms"],
        "post_growth_query_passed": timeline_query.get("passed"),
        "agent_runs_delta": growth["agent_runs_delta"],
        "audit_logs_delta": growth["audit_logs_delta"],
        "outbox_events_delta": growth["outbox_events_delta"],
        "database_growth_bytes": growth["database_growth_bytes"],
        "cleanup_verified": cleanup["passed"],
        "evidence": _reference(
            report_path,
            output_parent=output_parent,
            report=report,
        ),
    }


def _project_controls(
    reports: dict[str, dict[str, Any]],
    paths: dict[str, Path],
    *,
    output_parent: Path,
    release: dict[str, Any],
    target: dict[str, Any],
    current: datetime,
) -> dict[str, Any]:
    def evidence(name: str) -> dict[str, Any]:
        return _reference(paths[name], output_parent=output_parent, report=reports[name])

    application = reports["application_readiness"]
    tls = reports["tls"]
    secrets = reports["secrets"]
    network = reports["network"]
    alerting = reports["alerting"]
    recovery = reports["recovery"]
    high_availability = reports["high_availability"]
    security = reports["security_assessment"]
    ha_state_services = high_availability.get("state_services")
    ha_fault_injection = high_availability.get("fault_injection")
    if not isinstance(ha_state_services, dict):
        raise ValueError("high availability evidence state_services must be an object")
    if not isinstance(ha_fault_injection, dict):
        raise ValueError("high availability evidence fault_injection must be an object")
    security_images = security.get("images")
    if not isinstance(security_images, dict):
        raise ValueError("security assessment images must be an object")
    security_backend = security_images.get("backend")
    security_frontend = security_images.get("frontend")
    if not isinstance(security_backend, dict) or not isinstance(security_frontend, dict):
        raise ValueError("security assessment backend/frontend images must be objects")
    return {
        "application_readiness": {
            "status": _required(application, "status", "application readiness"),
            "pass_count": _required(application, "pass_count", "application readiness"),
            "block_count": _required(application, "block_count", "application readiness"),
            "contract_version": _required(
                application, "contract_version", "application readiness"
            ),
            "contract_digest": _required(
                application, "contract_digest", "application readiness"
            ),
            "database_revision": _required(
                application, "current_db_revision", "application readiness"
            ),
            "expected_database_revision": _required(
                application, "expected_db_revision", "application readiness"
            ),
            "evidence": evidence("application_readiness"),
        },
        "tls": {
            **{
                key: _required(tls, key, "TLS")
                for key in (
                    "status",
                    "negotiated_protocols",
                    "legacy_protocols_rejected",
                    "certificate_days_remaining",
                    "hostname_verified",
                    "hsts_max_age_seconds",
                )
            },
            "evidence": evidence("tls"),
        },
        "secrets": {
            **{
                key: _required(secrets, key, "secrets")
                for key in (
                    "status",
                    "provider",
                    "plaintext_env_persisted",
                    "rotation_tested",
                )
            },
            "evidence": evidence("secrets"),
        },
        "network": {
            **{
                key: _required(network, key, "network")
                for key in (
                    "status",
                    "public_tcp_ports",
                    "database_public",
                    "redis_public",
                    "object_store_direct_public",
                    "default_deny_ingress",
                    "egress_allowlist_enforced",
                )
            },
            "evidence": evidence("network"),
        },
        "alerting": {
            **{
                key: _required(alerting, key, "alerting")
                for key in (
                    "status",
                    "test_notification_delivered",
                    "resolved_notification_delivered",
                    "oncall_schedule",
                )
            },
            "evidence": evidence("alerting"),
        },
        "recovery": {
            **{
                key: _required(recovery, key, "recovery")
                for key in (
                    "status",
                    "offsite_media",
                    "encrypted",
                    "immutable_or_object_locked",
                    "rpo_seconds",
                    "rto_seconds",
                    "mysql_rows_verified",
                    "objects_verified",
                    "git_repositories_verified",
                )
            },
            "evidence": evidence("recovery"),
        },
        "capacity": _project_capacity(
            reports["capacity"],
            report_path=paths["capacity"],
            output_parent=output_parent,
            release=release,
            target=target,
            current=current,
        ),
        "high_availability": {
            "status": _required(high_availability, "status", "high availability"),
            "replica_counts": _required(
                high_availability, "replica_counts", "high availability"
            ),
            "fault_domains_exercised": _required(
                high_availability, "fault_domains_exercised", "high availability"
            ),
            **{
                key: _required(ha_state_services, key, "high availability state services")
                for key in (
                    "managed_mysql_ha",
                    "managed_redis_ha",
                    "object_store_ha",
                    "rwx_repository_storage_ha",
                )
            },
            **{
                key: _required(ha_fault_injection, key, "high availability fault injection")
                for key in (
                    "node_failover_passed",
                    "zone_failover_passed",
                    "beat_recovery_passed",
                )
            },
            "evidence": evidence("high_availability"),
        },
        "security_assessment": {
            **{
                key: _required(security, key, "security assessment")
                for key in (
                    "status",
                    "independent",
                    "provider",
                    "open_critical",
                    "open_high",
                    "source_commit",
                )
            },
            "backend_image": _required(
                security_backend, "name", "security backend image"
            ),
            "frontend_image": _required(
                security_frontend, "name", "security frontend image"
            ),
            "evidence": evidence("security_assessment"),
        },
    }


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise ValueError(f"output already exists: {path}") from exc
        temporary.unlink()
    finally:
        temporary.unlink(missing_ok=True)


def assemble(
    args: argparse.Namespace,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[Path, str]]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    request_digest = _sha256(args.request)
    policy_digest = _sha256(args.approval_policy)
    request = _load_object(args.request, "preapproval assembly request")
    if set(request) != REQUEST_KEYS or request.get("schema_version") != REQUEST_SCHEMA_VERSION:
        raise ValueError("preapproval assembly request schema or fields are invalid")
    release_request = request.get("release")
    target = request.get("target")
    evidence_request = request.get("evidence")
    if not isinstance(release_request, dict) or set(release_request) != RELEASE_KEYS:
        raise ValueError("preapproval release descriptor fields are invalid")
    if not isinstance(target, dict) or set(target) != TARGET_KEYS:
        raise ValueError("preapproval target descriptor fields are invalid")
    if not isinstance(evidence_request, dict) or set(evidence_request) != set(CONTROL_NAMES):
        raise ValueError("preapproval evidence must contain exactly the nine required controls")

    input_base = args.request.resolve().parent
    output_parent = args.output.resolve().parent
    provenance_path = _resolve_input(
        release_request["provenance_evidence_path"],
        base=input_base,
        label="release provenance evidence",
    )
    evidence_paths = {
        name: _resolve_input(
            evidence_request[name],
            base=input_base,
            label=f"{name} evidence",
        )
        for name in CONTROL_NAMES
    }
    tracked_paths = {
        args.request.resolve(): request_digest,
        args.approval_policy.resolve(): policy_digest,
        provenance_path: _sha256(provenance_path),
        **{path: _sha256(path) for path in evidence_paths.values()},
    }
    provenance = _load_object(provenance_path, "release provenance evidence")
    reports = {
        name: _load_object(path, f"{name} evidence")
        for name, path in evidence_paths.items()
    }
    release = {
        key: release_request[key]
        for key in (
            "version",
            "git_commit",
            "backend_image",
            "frontend_image",
            "contract_digest",
        )
    }
    release["provenance"] = _reference(
        provenance_path,
        output_parent=output_parent,
        report=provenance,
    )
    policy = _load_object(args.approval_policy, "approval policy")
    policy_id = policy.get("policy_id")
    if not isinstance(policy_id, str) or not policy_id.strip():
        raise ValueError("approval policy has no policy_id")
    authorization = {
        "schema_version": SCHEMA_VERSION,
        "release": release,
        "target": dict(target),
        "controls": _project_controls(
            reports,
            evidence_paths,
            output_parent=output_parent,
            release=release,
            target=target,
            current=current,
        ),
        "approval_policy": {
            "policy_id": policy_id,
            "sha256": policy_digest,
        },
        "approvals": [],
    }
    lint_errors = lint_authorization(authorization)
    if lint_errors:
        raise ValueError("; ".join(lint_errors))
    result = evaluate(
        authorization,
        authorization_path=args.output.resolve(),
        approval_policy_path=args.approval_policy.resolve(),
        now=current,
    )
    if not (
        result.get("status") == "AWAITING_EXTERNAL_APPROVALS"
        and result.get("campaign_stage") == "APPROVAL_COLLECTION"
        and result.get("foundation_ready") is True
        and result.get("evidence_ready_for_approval") is True
        and result.get("failed_foundation_checks") == []
        and result.get("failed_evidence_checks") == []
        and result.get("next_action") == "collect_organizational_approvals"
    ):
        raise ValueError("assembled authorization did not reach APPROVAL_COLLECTION")

    if any(_sha256(path) != digest for path, digest in tracked_paths.items()):
        raise ValueError("preapproval assembly input changed during evaluation")
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "assembled_at": current.isoformat(),
        "request": {"path": str(args.request.resolve()), "sha256": request_digest},
        "approval_policy": {
            "path": str(args.approval_policy.resolve()),
            "sha256": policy_digest,
            "policy_id": policy_id,
        },
        "release_provenance": {
            "path": str(provenance_path),
            "sha256": tracked_paths[provenance_path],
        },
        "evidence": {
            name: {"path": str(path), "sha256": tracked_paths[path]}
            for name, path in evidence_paths.items()
        },
        "evaluation": result,
    }
    return authorization, receipt, tracked_paths


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt-output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        for path, label in (
            (args.request, "assembly request"),
            (args.approval_policy, "approval policy"),
        ):
            if not path.is_file():
                raise ValueError(f"{label} does not exist: {path}")
        outputs = {args.output.resolve(), args.receipt_output.resolve()}
        if len(outputs) != 2:
            raise ValueError("authorization and receipt outputs must be distinct")
        if outputs.intersection({args.request.resolve(), args.approval_policy.resolve()}):
            raise ValueError("an output must not overwrite an assembly input")
        if any(path.exists() for path in outputs):
            raise ValueError("an output already exists; preapproval artifacts are immutable")
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        authorization, receipt, tracked_paths = assemble(args)
        authorization_payload = (
            json.dumps(authorization, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        _atomic_write(args.output, authorization_payload)
        try:
            persisted = _load_object(args.output, "persisted preapproval authorization")
            persisted_result = evaluate(
                persisted,
                authorization_path=args.output.resolve(),
                approval_policy_path=args.approval_policy.resolve(),
            )
            if not (
                persisted_result.get("campaign_stage") == "APPROVAL_COLLECTION"
                and persisted_result.get("evidence_ready_for_approval") is True
                and persisted_result.get("failed_foundation_checks") == []
                and persisted_result.get("failed_evidence_checks") == []
            ):
                raise ValueError("persisted preapproval authorization did not re-verify")
            if any(_sha256(path) != digest for path, digest in tracked_paths.items()):
                raise ValueError("preapproval assembly input changed before receipt emission")
            receipt["evaluation"] = persisted_result
            receipt["authorization"] = {
                "path": str(args.output.resolve()),
                "sha256": _sha256(args.output),
            }
            receipt_payload = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            )
            _atomic_write(args.receipt_output, receipt_payload)
        except Exception:
            args.output.unlink(missing_ok=True)
            raise
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"GA preapproval assembly failed: {exc}", file=sys.stderr)
        return 3
    print(receipt_payload.decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
