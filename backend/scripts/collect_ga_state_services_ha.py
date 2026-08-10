#!/usr/bin/env python3
"""Combine provider and independent-verifier receipts into state-service HA evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

try:
    from scripts.ga_execution_phase_start import verify_runtime_entry
    from scripts.ga_release_identity import build_release_binding
    from scripts.ga_state_services_evidence import (
        DIGEST_RE,
        EXERCISE_RE,
        PROVIDER_SIGNATURE_NAMESPACE,
        REQUIRED_SERVICES,
        STATE_EVIDENCE_SCHEMA_VERSION,
        STATE_POLICY_SCHEMA_VERSION,
        VERIFICATION_SIGNATURE_NAMESPACE,
        build_service_projection,
        contains_secret_material_key,
        meaningful,
        validate_provider_receipt,
        validate_verification_receipt,
    )
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_execution_phase_start import verify_runtime_entry
    from ga_release_identity import build_release_binding
    from ga_state_services_evidence import (
        DIGEST_RE,
        EXERCISE_RE,
        PROVIDER_SIGNATURE_NAMESPACE,
        REQUIRED_SERVICES,
        STATE_EVIDENCE_SCHEMA_VERSION,
        STATE_POLICY_SCHEMA_VERSION,
        VERIFICATION_SIGNATURE_NAMESPACE,
        build_service_projection,
        contains_secret_material_key,
        meaningful,
        validate_provider_receipt,
        validate_verification_receipt,
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} root must be an object")
    return value


def _exact_list(value: Any) -> list[str] | None:
    if not (
        isinstance(value, list)
        and value
        and all(meaningful(item) for item in value)
        and len(value) == len(set(value))
    ):
        return None
    return [str(item) for item in value]


def _allowed_signer_bindings(path: Path) -> dict[str, set[str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read state-services allowed-signers: {exc}") from exc
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
            raise ValueError(
                f"state-services allowed-signers line {line_number} has no public key"
            )
        key_material = f"{fields[key_index]} {fields[key_index + 1]}"
        for principal in fields[0].split(","):
            if not meaningful(principal) or any(character in principal for character in "*?!"):
                raise ValueError(
                    f"state-services allowed-signers line {line_number} must use exact principals"
                )
            bindings.setdefault(principal, set()).add(key_material)
    if not bindings:
        raise ValueError("state-services allowed-signers contains no principals")
    key_owners: dict[str, set[str]] = {}
    for principal, keys in bindings.items():
        for key in keys:
            key_owners.setdefault(key, set()).add(principal)
    if any(len(owners) != 1 for owners in key_owners.values()):
        raise ValueError("state-services allowed-signers reuses a public key across identities")
    return bindings


def load_state_services_policy(
    path: Path,
    *,
    provider_identity: str,
    verifier_identity: str,
) -> tuple[dict[str, Any], Path, str, dict[str, set[str]]]:
    policy = _load_object(path, "state-services trust policy")
    expected_keys = {
        "schema_version",
        "policy_id",
        "organization",
        "allowed_signers_path",
        "allowed_signers_sha256",
        "provider_identities",
        "verifier_identities",
        "required_services",
        "approved_providers",
    }
    provider_identities = _exact_list(policy.get("provider_identities"))
    verifier_identities = _exact_list(policy.get("verifier_identities"))
    providers = policy.get("approved_providers")
    approved_providers: dict[str, set[str]] = {}
    if isinstance(providers, dict) and set(providers) == set(REQUIRED_SERVICES):
        for name, values in providers.items():
            exact = _exact_list(values)
            if exact:
                approved_providers[name] = set(exact)
    if not (
        set(policy) == expected_keys
        and policy.get("schema_version") == STATE_POLICY_SCHEMA_VERSION
        and meaningful(policy.get("policy_id"))
        and meaningful(policy.get("organization"))
        and provider_identities
        and verifier_identities
        and not set(provider_identities).intersection(verifier_identities)
        and provider_identity in provider_identities
        and verifier_identity in verifier_identities
        and policy.get("required_services") == list(REQUIRED_SERVICES)
        and set(approved_providers) == set(REQUIRED_SERVICES)
    ):
        raise ValueError("state-services policy does not authorize distinct evidence roles")
    raw_trust_path = policy.get("allowed_signers_path")
    if not isinstance(raw_trust_path, str) or not raw_trust_path:
        raise ValueError("state-services policy has no allowed_signers_path")
    trust_path = Path(raw_trust_path).expanduser()
    if not trust_path.is_absolute():
        trust_path = (path.parent / trust_path).resolve()
    expected_digest = str(policy.get("allowed_signers_sha256", ""))
    if (
        not trust_path.is_file()
        or not DIGEST_RE.fullmatch(expected_digest)
        or sha256(trust_path) != expected_digest
    ):
        raise ValueError("state-services trust store is missing or digest-mismatched")
    bindings = _allowed_signer_bindings(trust_path)
    if set(bindings) != set(provider_identities).union(verifier_identities):
        raise ValueError("state-services trust principals do not exactly match policy identities")
    return policy, trust_path, sha256(path), approved_providers


def verify_signature(
    path: Path,
    signature: Path,
    *,
    identity: str,
    allowed_signers: Path,
    namespace: str,
) -> None:
    try:
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
            input=path.read_bytes(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except OSError as exc:
        raise ValueError(f"cannot verify signed state-services receipt: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stdout.decode("utf-8", errors="replace").strip()
        raise ValueError(f"invalid signed state-services receipt: {detail}")


def collect(args: argparse.Namespace) -> dict[str, Any]:
    provider_receipt = _load_object(args.provider_receipt, "provider failover receipt")
    verification_receipt = _load_object(
        args.verification_receipt,
        "independent state verification receipt",
    )
    policy, allowed_signers, policy_digest, approved_providers = load_state_services_policy(
        args.state_services_policy,
        provider_identity=args.provider_signer_identity,
        verifier_identity=args.verifier_signer_identity,
    )
    verify_signature(
        args.provider_receipt,
        args.provider_signature,
        identity=args.provider_signer_identity,
        allowed_signers=allowed_signers,
        namespace=PROVIDER_SIGNATURE_NAMESPACE,
    )
    verify_signature(
        args.verification_receipt,
        args.verification_signature,
        identity=args.verifier_signer_identity,
        allowed_signers=allowed_signers,
        namespace=VERIFICATION_SIGNATURE_NAMESPACE,
    )
    provider = validate_provider_receipt(
        provider_receipt,
        target_environment=args.target_environment,
        source_commit=args.source_commit,
        backend_image=args.backend_image,
        frontend_image=args.frontend_image,
        exercise_id=args.exercise_id,
        approved_providers=approved_providers,
    )
    verification = validate_verification_receipt(
        verification_receipt,
        target_environment=args.target_environment,
        source_commit=args.source_commit,
        backend_image=args.backend_image,
        frontend_image=args.frontend_image,
        exercise_id=args.exercise_id,
        provider_services=provider["services"],
    )
    collected_at = datetime.now(timezone.utc)
    if not (
        provider["observed_at"] <= verification["observed_at"] <= collected_at
        and verification["observed_at"] - provider["observed_at"] <= timedelta(hours=4)
        and collected_at - verification["observed_at"] <= timedelta(minutes=5)
    ):
        raise ValueError("state-services receipt collection timeline is invalid or stale")
    services = build_service_projection(provider["services"], verification["services"])
    report = {
        "schema_version": STATE_EVIDENCE_SCHEMA_VERSION,
        **args.release_binding,
        "status": "PASS",
        "passed": True,
        "observed_at": collected_at.isoformat(),
        "provider_observed_at": provider_receipt["observed_at"],
        "verification_observed_at": verification_receipt["observed_at"],
        "exercise_id": args.exercise_id,
        "managed_mysql_ha": True,
        "managed_redis_ha": True,
        "object_store_ha": True,
        "rwx_repository_storage_ha": True,
        "failover_exercised": True,
        "data_integrity_passed": True,
        "services": services,
        "state_services_policy": {
            "path": str(args.state_services_policy.resolve()),
            "sha256": policy_digest,
            "policy_id": policy["policy_id"],
            "allowed_signers_path": str(allowed_signers.resolve()),
            "allowed_signers_sha256": sha256(allowed_signers),
        },
        "provider_receipt": {
            **provider_receipt,
            "signed_evidence": {
                "path": str(args.provider_receipt.resolve()),
                "sha256": sha256(args.provider_receipt),
                "signature_path": str(args.provider_signature.resolve()),
                "signer_identity": args.provider_signer_identity,
            },
        },
        "verification_receipt": {
            **verification_receipt,
            "signed_evidence": {
                "path": str(args.verification_receipt.resolve()),
                "sha256": sha256(args.verification_receipt),
                "signature_path": str(args.verification_signature.resolve()),
                "signer_identity": args.verifier_signer_identity,
            },
        },
    }
    if contains_secret_material_key(report):
        raise ValueError("state-services HA evidence contains a forbidden credential field")
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-environment", required=True)
    parser.add_argument("--execution-campaign", type=Path, required=True)
    parser.add_argument("--phase-action-id", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--backend-image", required=True)
    parser.add_argument("--frontend-image", required=True)
    parser.add_argument("--exercise-id", required=True)
    parser.add_argument("--state-services-policy", type=Path, required=True)
    parser.add_argument("--provider-signer-identity", required=True)
    parser.add_argument("--verifier-signer-identity", required=True)
    parser.add_argument("--provider-receipt", type=Path, required=True)
    parser.add_argument("--provider-signature", type=Path, required=True)
    parser.add_argument("--verification-receipt", type=Path, required=True)
    parser.add_argument("--verification-signature", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite-output", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not EXERCISE_RE.fullmatch(args.exercise_id):
            raise ValueError("exercise ID must be 8-64 safe characters")
        if not meaningful(args.provider_signer_identity) or not meaningful(
            args.verifier_signer_identity
        ):
            raise ValueError("state-services signer identities must be non-placeholder")
        if args.provider_signer_identity == args.verifier_signer_identity:
            raise ValueError("provider and verifier signer identities must differ")
        for path, label in (
            (args.state_services_policy, "state-services policy"),
            (args.provider_receipt, "provider receipt"),
            (args.provider_signature, "provider signature"),
            (args.verification_receipt, "verification receipt"),
            (args.verification_signature, "verification signature"),
        ):
            if not path.is_file():
                raise ValueError(f"{label} does not exist")
        if args.output.exists() and not args.overwrite_output:
            raise ValueError("output already exists; choose a new evidence path or pass --overwrite-output")
        args.release_binding = build_release_binding(
            scope="target-production",
            target_environment=args.target_environment,
            source_commit=args.source_commit,
            backend_image=args.backend_image,
            frontend_image=args.frontend_image,
        )
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        verify_runtime_entry(
            args.execution_campaign,
            phase_id="state_services",
            action_id=args.phase_action_id,
            release_binding=args.release_binding,
            target_environment=args.target_environment,
        )
        report = collect(args)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"State-services HA collection failed: {exc}", file=sys.stderr)
        return 3
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
