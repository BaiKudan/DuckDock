#!/usr/bin/env python3
"""Preflight global identity and public-key separation for all GA trust policies."""

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
    from scripts.verify_ga_production_authorization import (
        ALERTING_POLICY_SCHEMA_VERSION,
        APPROVAL_POLICY_SCHEMA_VERSION,
        CAPACITY_POLICY_SCHEMA_VERSION,
        NETWORK_POLICY_SCHEMA_VERSION,
        RECOVERY_POLICY_SCHEMA_VERSION,
        RELEASE_PROVENANCE_POLICY_SCHEMA_VERSION,
        REQUIRED_APPROVAL_ROLES,
        SECRETS_POLICY_SCHEMA_VERSION,
        STATE_POLICY_SCHEMA_VERSION,
        TLS_POLICY_SCHEMA_VERSION,
        _allowed_signer_bindings,
        _meaningful_string,
        _validate_global_trust_separation,
    )
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from verify_ga_production_authorization import (
        ALERTING_POLICY_SCHEMA_VERSION,
        APPROVAL_POLICY_SCHEMA_VERSION,
        CAPACITY_POLICY_SCHEMA_VERSION,
        NETWORK_POLICY_SCHEMA_VERSION,
        RECOVERY_POLICY_SCHEMA_VERSION,
        RELEASE_PROVENANCE_POLICY_SCHEMA_VERSION,
        REQUIRED_APPROVAL_ROLES,
        SECRETS_POLICY_SCHEMA_VERSION,
        STATE_POLICY_SCHEMA_VERSION,
        TLS_POLICY_SCHEMA_VERSION,
        _allowed_signer_bindings,
        _meaningful_string,
        _validate_global_trust_separation,
    )


MANIFEST_SCHEMA_VERSION = "duckdock-ga-trust-topology-manifest-v1"
RECEIPT_SCHEMA_VERSION = "duckdock-ga-trust-topology-verification-v1"
POLICY_SCHEMAS = {
    "approval": APPROVAL_POLICY_SCHEMA_VERSION,
    "release-provenance": RELEASE_PROVENANCE_POLICY_SCHEMA_VERSION,
    "tls": TLS_POLICY_SCHEMA_VERSION,
    "secrets": SECRETS_POLICY_SCHEMA_VERSION,
    "network": NETWORK_POLICY_SCHEMA_VERSION,
    "alerting": ALERTING_POLICY_SCHEMA_VERSION,
    "recovery": RECOVERY_POLICY_SCHEMA_VERSION,
    "capacity": CAPACITY_POLICY_SCHEMA_VERSION,
    "state-services": STATE_POLICY_SCHEMA_VERSION,
}
SIMPLE_ROLE_FIELDS = {
    "release-provenance": {"builder": "builder_identities"},
    "tls": {"probe-operator": "probe_operator_identities"},
    "secrets": {
        "provider": "provider_identities",
        "verifier": "verifier_identities",
    },
    "network": {"probe-operator": "probe_operator_identities"},
    "recovery": {
        "storage": "storage_identities",
        "restore-executor": "restore_executor_identities",
        "verifier": "verification_identities",
    },
    "capacity": {
        "load-executor": "load_executor_identities",
        "storage-observer": "storage_observer_identities",
        "cleanup-verifier": "cleanup_verifier_identities",
    },
    "state-services": {
        "provider": "provider_identities",
        "verifier": "verifier_identities",
    },
}


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


def _resolve_file(raw: Any, *, base: Path, label: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{label} must be a non-empty path")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = base.parent / path
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"{label} does not exist: {path}")
    return path


def _identity_set(value: Any, *, label: str) -> set[str]:
    if not (
        isinstance(value, list)
        and value
        and all(_meaningful_string(identity) for identity in value)
        and len(value) == len(set(value))
    ):
        raise ValueError(f"{label} must contain unique, non-placeholder identities")
    return {str(identity) for identity in value}


def _policy_assignments(name: str, policy: dict[str, Any]) -> dict[str, set[str]]:
    if name == "approval":
        roles = policy.get("roles")
        assessors = policy.get("independent_security_assessors")
        if not isinstance(roles, dict) or set(roles) != REQUIRED_APPROVAL_ROLES:
            raise ValueError("approval roles must contain the four required roles")
        if not isinstance(assessors, dict) or not assessors:
            raise ValueError("approval policy must configure independent assessors")
        if not all(_meaningful_string(provider) for provider in assessors):
            raise ValueError("approval assessor provider names must be non-placeholder strings")
        return {
            **{
                f"approver/{role}": _identity_set(identities, label=f"approval role {role}")
                for role, identities in roles.items()
            },
            **{
                f"security-assessor/{provider}": _identity_set(identities, label=f"assessor provider {provider}")
                for provider, identities in assessors.items()
                if _meaningful_string(provider)
            },
        }
    if name == "alerting":
        delivery = _identity_set(policy.get("delivery_identities"), label="alerting delivery identities")
        schedules = policy.get("oncall_schedules")
        if not isinstance(schedules, dict) or not schedules:
            raise ValueError("alerting oncall_schedules must be a non-empty object")
        if not all(_meaningful_string(schedule) for schedule in schedules):
            raise ValueError("alerting schedule names must be non-placeholder strings")
        oncall = set().union(
            *(
                _identity_set(identities, label=f"on-call schedule {schedule}")
                for schedule, identities in schedules.items()
                if _meaningful_string(schedule)
            )
        )
        if not oncall:
            raise ValueError("alerting on-call identities are empty")
        return {"delivery": delivery, "oncall": oncall}
    return {
        role: _identity_set(policy.get(field), label=f"{name} {role} identities")
        for role, field in SIMPLE_ROLE_FIELDS[name].items()
    }


def verify(manifest_path: Path, *, now: datetime | None = None) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    manifest_digest = _sha256(manifest_path)
    tracked_inputs = {manifest_path: manifest_digest}
    manifest = _load_object(manifest_path, "trust topology manifest")
    if set(manifest) != {"schema_version", "policies"}:
        raise ValueError("manifest must contain exactly schema_version and policies")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError(f"manifest schema_version must be {MANIFEST_SCHEMA_VERSION}")
    references = manifest.get("policies")
    if not isinstance(references, dict) or set(references) != set(POLICY_SCHEMAS):
        raise ValueError("manifest must reference exactly the nine GA trust policies")

    assignments: dict[str, dict[str, set[str]]] = {}
    trust_stores: dict[str, Path | None] = {}
    retained: dict[str, dict[str, Any]] = {}
    policy_ids: set[str] = set()
    for name in POLICY_SCHEMAS:
        reference = references[name]
        if not isinstance(reference, dict) or set(reference) != {"path", "sha256"}:
            raise ValueError(f"{name} reference must contain exactly path and sha256")
        policy_path = _resolve_file(reference.get("path"), base=manifest_path, label=f"{name} policy")
        policy_digest = _sha256(policy_path)
        if reference.get("sha256") != policy_digest:
            raise ValueError(f"{name} policy digest mismatch")
        policy = _load_object(policy_path, f"{name} policy")
        tracked_inputs[policy_path] = policy_digest
        if policy.get("schema_version") != POLICY_SCHEMAS[name]:
            raise ValueError(f"{name} policy schema mismatch")
        policy_id = policy.get("policy_id")
        if not _meaningful_string(policy_id) or policy_id in policy_ids:
            raise ValueError(f"{name} policy_id is missing, placeholder, or reused")
        policy_ids.add(str(policy_id))
        if not _meaningful_string(policy.get("organization")):
            raise ValueError(f"{name} policy organization is missing or placeholder")
        trust_store = _resolve_file(
            policy.get("allowed_signers_path"),
            base=policy_path,
            label=f"{name} allowed-signers",
        )
        trust_digest = _sha256(trust_store)
        if policy.get("allowed_signers_sha256") != trust_digest:
            raise ValueError(f"{name} allowed-signers digest mismatch")
        tracked_inputs[trust_store] = trust_digest
        role_assignments = _policy_assignments(name, policy)
        assignments[name] = role_assignments
        trust_stores[name] = trust_store
        bindings, binding_detail = _allowed_signer_bindings(trust_store)
        if bindings is None or set(bindings) != set().union(*role_assignments.values()):
            raise ValueError(f"{name} trust principals do not match configured identities: {binding_detail}")
        retained[name] = {
            "policy_id": policy_id,
            "policy": {"path": str(policy_path), "sha256": policy_digest},
            "allowed_signers": {"path": str(trust_store), "sha256": trust_digest},
            "roles": {role: sorted(identities) for role, identities in sorted(role_assignments.items())},
        }

    separated, detail = _validate_global_trust_separation(assignments, trust_stores)
    if not separated:
        raise ValueError(
            "global GA trust separation failed: " + json.dumps(detail, sort_keys=True, separators=(",", ":"))
        )
    if any(_sha256(path) != digest for path, digest in tracked_inputs.items()):
        raise ValueError("manifest, policy, or trust store changed during verification")
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "status": "PASS",
        "verified_at": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
        "manifest": {"path": str(manifest_path), "sha256": manifest_digest},
        "separation": detail,
        "policies": retained,
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


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if not args.manifest.is_file():
            raise ValueError(f"manifest does not exist: {args.manifest}")
        if args.output.exists():
            raise ValueError("output already exists; topology receipts are immutable")
        if args.output.resolve() == args.manifest.resolve():
            raise ValueError("output must not overwrite the manifest")
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        receipt = verify(args.manifest)
        payload = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
        _atomic_write(args.output, payload)
        persisted = _load_object(args.output, "persisted topology receipt")
        reverified = verify(
            args.manifest,
            now=datetime.fromisoformat(str(receipt["verified_at"])),
        )
        if persisted != receipt or reverified != receipt:
            raise ValueError("persisted topology receipt does not match verified result")
    except (OSError, UnicodeError, ValueError) as exc:
        args.output.unlink(missing_ok=True)
        print(f"GA trust topology verification failed: {exc}", file=sys.stderr)
        return 3
    print(payload.decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
