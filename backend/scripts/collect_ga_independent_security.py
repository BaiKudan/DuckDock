#!/usr/bin/env python3
"""Collect policy-bound, independently signed security assessment evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

try:
    from scripts.ga_release_identity import build_release_binding
    from scripts.ga_security_assessment import (
        ASSESSMENT_ENGAGEMENT_SIGNATURE_NAMESPACE,
        ASSESSMENT_EVIDENCE_SCHEMA_VERSION,
        ASSESSMENT_SIGNATURE_NAMESPACE,
        DIGEST_RE,
        meaningful,
        sha256,
        validate_assessment_engagement,
        validate_assessment_report,
    )
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_release_identity import build_release_binding
    from ga_security_assessment import (
        ASSESSMENT_ENGAGEMENT_SIGNATURE_NAMESPACE,
        ASSESSMENT_EVIDENCE_SCHEMA_VERSION,
        ASSESSMENT_SIGNATURE_NAMESPACE,
        DIGEST_RE,
        meaningful,
        sha256,
        validate_assessment_engagement,
        validate_assessment_report,
    )


APPROVAL_POLICY_SCHEMA_VERSION = "duckdock-ga-approval-policy-v2"
REQUIRED_APPROVAL_ROLES = {"Product", "Architecture", "Security", "Operations"}


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
        raise ValueError(f"cannot read release-authority allowed-signers: {exc}") from exc
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
                f"release-authority allowed-signers line {line_number} has no OpenSSH public key"
            )
        key_material = f"{fields[key_index]} {fields[key_index + 1]}"
        for principal in fields[0].split(","):
            if not meaningful(principal) or any(character in principal for character in "*?!"):
                raise ValueError(
                    f"release-authority allowed-signers line {line_number} must use exact principals"
                )
            bindings.setdefault(principal, set()).add(key_material)
    if not bindings:
        raise ValueError("release-authority allowed-signers contains no principals")
    key_owners: dict[str, set[str]] = {}
    for principal, keys in bindings.items():
        for key in keys:
            key_owners.setdefault(key, set()).add(principal)
    if any(len(owners) != 1 for owners in key_owners.values()):
        raise ValueError("release-authority allowed-signers reuses a public key across identities")
    return bindings


def load_release_authority(
    path: Path,
    *,
    provider: str,
    assessor_identity: str,
) -> tuple[dict[str, Any], Path, str]:
    policy = _load_object(path, "release-authority approval policy")
    expected_keys = {
        "schema_version",
        "policy_id",
        "organization",
        "allowed_signers_path",
        "allowed_signers_sha256",
        "roles",
        "independent_security_assessors",
    }
    roles = policy.get("roles")
    roles_valid = isinstance(roles, dict) and set(roles) == REQUIRED_APPROVAL_ROLES
    role_identities: list[str] = []
    if roles_valid:
        for role in REQUIRED_APPROVAL_ROLES:
            identities = _exact_identity_list(roles.get(role))
            if identities is None:
                roles_valid = False
                break
            role_identities.extend(identities)
    assessors = policy.get("independent_security_assessors")
    assessors_valid = isinstance(assessors, dict) and bool(assessors)
    assessor_identities: list[str] = []
    if assessors_valid:
        for configured_provider, raw_identities in assessors.items():
            identities = _exact_identity_list(raw_identities)
            if not meaningful(configured_provider) or identities is None:
                assessors_valid = False
                break
            assessor_identities.extend(identities)
    all_identities = role_identities + assessor_identities
    identities_exclusive = len(all_identities) == len(set(all_identities))
    if not (
        set(policy) == expected_keys
        and policy.get("schema_version") == APPROVAL_POLICY_SCHEMA_VERSION
        and meaningful(policy.get("policy_id"))
        and meaningful(policy.get("organization"))
        and roles_valid
        and assessors_valid
        and identities_exclusive
        and provider in assessors
        and assessor_identity in assessors.get(provider, [])
    ):
        raise ValueError("release-authority policy does not authorize this independent assessor")
    raw_trust_path = policy.get("allowed_signers_path")
    if not isinstance(raw_trust_path, str) or not raw_trust_path:
        raise ValueError("release-authority policy has no allowed_signers_path")
    trust_path = Path(raw_trust_path).expanduser()
    if not trust_path.is_absolute():
        trust_path = (path.parent / trust_path).resolve()
    expected_trust_digest = str(policy.get("allowed_signers_sha256", ""))
    if (
        not trust_path.is_file()
        or not DIGEST_RE.fullmatch(expected_trust_digest)
        or sha256(trust_path) != expected_trust_digest
    ):
        raise ValueError("release-authority trust store is missing or digest-mismatched")
    bindings = _allowed_signer_bindings(trust_path)
    if set(bindings) != set(all_identities):
        raise ValueError("release-authority trust principals do not exactly match the policy")
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
        raise ValueError(f"cannot verify independent assessment signature: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stdout.decode("utf-8", errors="replace").strip()
        raise ValueError(f"invalid independent assessment signature: {detail}")


def collect(args: argparse.Namespace) -> dict[str, Any]:
    policy, allowed_signers, policy_digest = load_release_authority(
        args.approval_policy,
        provider=args.provider,
        assessor_identity=args.assessor_signer_identity,
    )
    security_identities = set(policy["roles"]["Security"])
    if args.security_authorizer_identity not in security_identities:
        raise ValueError(
            "security engagement signer is not authorized for the Security role"
        )
    engagement = _load_object(
        args.assessment_engagement, "signed security assessment engagement"
    )
    verify_signature(
        args.assessment_engagement,
        args.engagement_signature,
        identity=args.security_authorizer_identity,
        allowed_signers=allowed_signers,
        namespace=ASSESSMENT_ENGAGEMENT_SIGNATURE_NAMESPACE,
    )
    engagement_derived = validate_assessment_engagement(
        engagement,
        expected_provider=args.provider,
        expected_assessor_identity=args.assessor_signer_identity,
        expected_security_authorizer_identity=args.security_authorizer_identity,
        target_environment=args.target_environment,
        source_commit=args.source_commit,
        backend_image=args.backend_image,
        frontend_image=args.frontend_image,
        contract_digest=args.contract_digest,
    )
    engagement_digest = sha256(args.assessment_engagement)

    assessment = _load_object(args.assessment_report, "signed security assessment")
    verify_signature(
        args.assessment_report,
        args.assessment_signature,
        identity=args.assessor_signer_identity,
        allowed_signers=allowed_signers,
        namespace=ASSESSMENT_SIGNATURE_NAMESPACE,
    )
    derived = validate_assessment_report(
        assessment,
        assessment_path=args.assessment_report,
        expected_provider=args.provider,
        expected_assessor_identity=args.assessor_signer_identity,
        target_environment=args.target_environment,
        source_commit=args.source_commit,
        backend_image=args.backend_image,
        frontend_image=args.frontend_image,
        contract_digest=args.contract_digest,
        expected_engagement_id=engagement_derived["engagement_id"],
        expected_engagement_sha256=engagement_digest,
        engagement_window_starts_at=engagement_derived["window_starts_at"],
        engagement_window_expires_at=engagement_derived["window_expires_at"],
        engagement_delete_by=engagement_derived["delete_by"],
        expected_scope=engagement_derived["scope"],
        expected_methodologies=engagement_derived["methodologies"],
        expected_source_cidrs=engagement_derived["source_cidrs"],
        expected_source_system_ids=engagement_derived["source_system_ids"],
        expected_test_account_ids=engagement_derived["test_account_ids"],
    )
    passed = (
        derived["open_critical"] == 0
        and derived["open_high"] == 0
        and derived["critical_high_retest_completed"] is True
    )
    observed_at = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": ASSESSMENT_EVIDENCE_SCHEMA_VERSION,
        **args.release_binding,
        "status": "PASS" if passed else "BLOCKED",
        "passed": passed,
        "observed_at": observed_at,
        "contract_digest": args.contract_digest,
        "independent": True,
        "provider": args.provider,
        "open_critical": derived["open_critical"],
        "open_high": derived["open_high"],
        "assessment": {
            "assessment_id": derived["assessment_id"],
            "engagement_id": engagement_derived["engagement_id"],
            "engagement_sha256": engagement_digest,
            "scope": derived["scope"],
            "methodologies": derived["methodologies"],
            "started_at": assessment["started_at"],
            "completed_at": assessment["completed_at"],
        },
        "findings": {
            "total_findings": derived["total_findings"],
            "open_by_severity": derived["open_by_severity"],
            "closed_by_severity": derived["closed_by_severity"],
            "critical_high_retest_completed": derived[
                "critical_high_retest_completed"
            ],
        },
        "release_authority": {
            "policy_id": policy["policy_id"],
            "policy_sha256": policy_digest,
            "allowed_signers_path": str(allowed_signers.resolve()),
            "allowed_signers_sha256": sha256(allowed_signers),
        },
        "authorized_engagement": {
            **engagement,
            "signed_evidence": {
                "path": str(args.assessment_engagement.resolve()),
                "sha256": engagement_digest,
                "signature_path": str(args.engagement_signature.resolve()),
                "signer_identity": args.security_authorizer_identity,
            },
        },
        "signed_assessment": {
            **assessment,
            "signed_evidence": {
                "path": str(args.assessment_report.resolve()),
                "sha256": sha256(args.assessment_report),
                "signature_path": str(args.assessment_signature.resolve()),
                "signer_identity": args.assessor_signer_identity,
            },
        },
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-environment", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--backend-image", required=True)
    parser.add_argument("--frontend-image", required=True)
    parser.add_argument("--contract-digest", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--security-authorizer-identity", required=True)
    parser.add_argument("--assessor-signer-identity", required=True)
    parser.add_argument("--assessment-engagement", type=Path, required=True)
    parser.add_argument("--engagement-signature", type=Path, required=True)
    parser.add_argument("--assessment-report", type=Path, required=True)
    parser.add_argument("--assessment-signature", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite-output", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not all(
            meaningful(value)
            for value in (
                args.target_environment,
                args.provider,
                args.security_authorizer_identity,
                args.assessor_signer_identity,
            )
        ):
            raise ValueError("target, provider and assessor identity must be non-placeholder")
        if not DIGEST_RE.fullmatch(args.contract_digest):
            raise ValueError("contract digest must be a 64-character lowercase SHA-256")
        for path, label in (
            (args.assessment_engagement, "assessment engagement"),
            (args.engagement_signature, "engagement signature"),
            (args.assessment_report, "assessment report"),
            (args.assessment_signature, "assessment signature"),
            (args.approval_policy, "approval policy"),
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
        print(f"Independent security collection failed: {exc}", file=sys.stderr)
        return 3
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
