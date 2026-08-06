#!/usr/bin/env python3
"""Verify a signed external TLS probe and build target GA TLS evidence."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

try:
    from scripts.ga_release_identity import build_release_binding
    from scripts.ga_tls_evidence import (
        DIGEST_RE,
        EXERCISE_RE,
        TLS_EVIDENCE_SCHEMA_VERSION,
        TLS_POLICY_SCHEMA_VERSION,
        TLS_SIGNATURE_NAMESPACE,
        contains_secret_material_key,
        meaningful,
        validate_tls_probe_report,
    )
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_release_identity import build_release_binding
    from ga_tls_evidence import (
        DIGEST_RE,
        EXERCISE_RE,
        TLS_EVIDENCE_SCHEMA_VERSION,
        TLS_POLICY_SCHEMA_VERSION,
        TLS_SIGNATURE_NAMESPACE,
        contains_secret_material_key,
        meaningful,
        validate_tls_probe_report,
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


def _exact_identity_list(value: Any) -> list[str] | None:
    if not (
        isinstance(value, list)
        and bool(value)
        and all(meaningful(item) for item in value)
        and len(value) == len(set(value))
    ):
        return None
    return [str(item) for item in value]


def _allowed_signer_bindings(path: Path) -> dict[str, set[str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read TLS allowed-signers: {exc}") from exc
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
            raise ValueError(f"TLS allowed-signers line {line_number} has no public key")
        key_material = f"{fields[key_index]} {fields[key_index + 1]}"
        for principal in fields[0].split(","):
            if not meaningful(principal) or any(character in principal for character in "*?!"):
                raise ValueError(
                    f"TLS allowed-signers line {line_number} must use exact principals"
                )
            bindings.setdefault(principal, set()).add(key_material)
    if not bindings:
        raise ValueError("TLS allowed-signers contains no principals")
    key_owners: dict[str, set[str]] = {}
    for principal, keys in bindings.items():
        for key in keys:
            key_owners.setdefault(key, set()).add(principal)
    if any(len(owners) != 1 for owners in key_owners.values()):
        raise ValueError("TLS allowed-signers reuses a public key across identities")
    return bindings


def _approved_networks(value: Any) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network] | None:
    if not isinstance(value, list) or not value or len(value) != len(set(value)):
        return None
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    try:
        for item in value:
            if not meaningful(item):
                return None
            network = ipaddress.ip_network(str(item), strict=True)
            if not network.network_address.is_global:
                return None
            networks.append(network)
    except ValueError:
        return None
    return networks


def load_tls_policy(
    path: Path,
    *,
    signer_identity: str,
    probe_id: str,
    vantage_id: str,
    source_ip: str,
) -> tuple[dict[str, Any], Path, str]:
    policy = _load_object(path, "TLS trust policy")
    expected_keys = {
        "schema_version",
        "policy_id",
        "organization",
        "allowed_signers_path",
        "allowed_signers_sha256",
        "probe_operator_identities",
        "approved_probe_ids",
        "approved_vantage_ids",
        "approved_vantage_classes",
        "approved_source_cidrs",
    }
    identities = _exact_identity_list(policy.get("probe_operator_identities"))
    probe_ids = _exact_identity_list(policy.get("approved_probe_ids"))
    vantage_ids = _exact_identity_list(policy.get("approved_vantage_ids"))
    networks = _approved_networks(policy.get("approved_source_cidrs"))
    try:
        source_address = ipaddress.ip_address(source_ip)
    except ValueError:
        source_address = None
    if not (
        set(policy) == expected_keys
        and policy.get("schema_version") == TLS_POLICY_SCHEMA_VERSION
        and meaningful(policy.get("policy_id"))
        and meaningful(policy.get("organization"))
        and identities
        and signer_identity in identities
        and probe_ids
        and probe_id in probe_ids
        and vantage_ids
        and vantage_id in vantage_ids
        and policy.get("approved_vantage_classes") == ["external-internet"]
        and networks
        and source_address is not None
        and any(source_address in network for network in networks)
    ):
        raise ValueError("TLS policy does not authorize the probe identity or external vantage")
    raw_trust_path = policy.get("allowed_signers_path")
    if not isinstance(raw_trust_path, str) or not raw_trust_path:
        raise ValueError("TLS policy has no allowed_signers_path")
    trust_path = Path(raw_trust_path).expanduser()
    if not trust_path.is_absolute():
        trust_path = (path.parent / trust_path).resolve()
    expected_digest = str(policy.get("allowed_signers_sha256", ""))
    if (
        not trust_path.is_file()
        or not DIGEST_RE.fullmatch(expected_digest)
        or sha256(trust_path) != expected_digest
    ):
        raise ValueError("TLS trust store is missing or digest-mismatched")
    bindings = _allowed_signer_bindings(trust_path)
    if set(bindings) != set(identities):
        raise ValueError("TLS trust principals do not exactly match policy identities")
    return policy, trust_path, sha256(path)


def verify_signature(
    path: Path,
    signature: Path,
    *,
    identity: str,
    allowed_signers: Path,
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
                TLS_SIGNATURE_NAMESPACE,
                "-s",
                str(signature),
            ],
            input=path.read_bytes(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except OSError as exc:
        raise ValueError(f"cannot verify signed TLS probe: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stdout.decode("utf-8", errors="replace").strip()
        raise ValueError(f"invalid signed TLS probe: {detail}")


def collect(args: argparse.Namespace) -> dict[str, Any]:
    raw_report = _load_object(args.probe_report, "TLS probe report")
    raw_probe = raw_report.get("probe") if isinstance(raw_report.get("probe"), dict) else {}
    policy, allowed_signers, policy_digest = load_tls_policy(
        args.tls_policy,
        signer_identity=args.probe_signer_identity,
        probe_id=str(raw_probe.get("probe_id", "")),
        vantage_id=str(raw_probe.get("vantage_id", "")),
        source_ip=str(raw_probe.get("source_ip", "")),
    )
    verify_signature(
        args.probe_report,
        args.probe_signature,
        identity=args.probe_signer_identity,
        allowed_signers=allowed_signers,
    )
    derived = validate_tls_probe_report(
        raw_report,
        target_environment=args.target_environment,
        source_commit=args.source_commit,
        backend_image=args.backend_image,
        frontend_image=args.frontend_image,
        application_url=args.application_url,
        object_store_url=args.object_store_url,
        exercise_id=args.exercise_id,
    )
    collected_at = datetime.now(timezone.utc)
    if (collected_at - derived["observed_at"]).total_seconds() > 300:
        raise ValueError("signed TLS probe must be collected within five minutes")
    report = {
        "schema_version": TLS_EVIDENCE_SCHEMA_VERSION,
        **args.release_binding,
        "status": "PASS",
        "passed": True,
        "observed_at": collected_at.isoformat(),
        "probe_observed_at": raw_report["observed_at"],
        "exercise_id": args.exercise_id,
        "probe": derived["probe"],
        "application_url": args.application_url,
        "object_store_url": args.object_store_url,
        "negotiated_protocols": derived["negotiated_protocols"],
        "certificate_days_remaining": derived["certificate_days_remaining"],
        "hostname_verified": derived["hostname_verified"],
        "legacy_protocols_rejected": derived["legacy_protocols_rejected"],
        "hsts_max_age_seconds": derived["hsts_max_age_seconds"],
        "endpoints": raw_report["endpoints"],
        "tls_policy": {
            "path": str(args.tls_policy.resolve()),
            "sha256": policy_digest,
            "policy_id": policy["policy_id"],
            "allowed_signers_path": str(allowed_signers.resolve()),
            "allowed_signers_sha256": sha256(allowed_signers),
        },
        "signed_probe": {
            **raw_report,
            "signed_evidence": {
                "path": str(args.probe_report.resolve()),
                "sha256": sha256(args.probe_report),
                "signature_path": str(args.probe_signature.resolve()),
                "signer_identity": args.probe_signer_identity,
            },
        },
    }
    if contains_secret_material_key(report):
        raise ValueError("TLS evidence contains a forbidden credential field")
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-environment", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--backend-image", required=True)
    parser.add_argument("--frontend-image", required=True)
    parser.add_argument("--application-url", required=True)
    parser.add_argument("--object-store-url", required=True)
    parser.add_argument("--exercise-id", required=True)
    parser.add_argument("--tls-policy", type=Path, required=True)
    parser.add_argument("--probe-signer-identity", required=True)
    parser.add_argument("--probe-report", type=Path, required=True)
    parser.add_argument("--probe-signature", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite-output", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not EXERCISE_RE.fullmatch(args.exercise_id):
            raise ValueError("exercise ID must be 8-64 safe characters")
        if not meaningful(args.probe_signer_identity):
            raise ValueError("probe signer identity must be non-placeholder")
        for path, label in (
            (args.tls_policy, "TLS policy"),
            (args.probe_report, "probe report"),
            (args.probe_signature, "probe signature"),
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
        print(f"Target TLS collection failed: {exc}", file=sys.stderr)
        return 3
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
