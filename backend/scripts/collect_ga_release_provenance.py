#!/usr/bin/env python3
"""Verify a signed 2.0.0 build report and assemble release provenance evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

try:
    from scripts.ga_release_provenance import (
        BUILD_SIGNATURE_NAMESPACE,
        DIGEST_RE,
        PROVENANCE_EVIDENCE_SCHEMA_VERSION,
        PROVENANCE_POLICY_SCHEMA_VERSION,
        REQUIRED_ARTIFACTS,
        contains_secret_material_key,
        meaningful,
        sha256,
        validate_build_report,
    )
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_release_provenance import (
        BUILD_SIGNATURE_NAMESPACE,
        DIGEST_RE,
        PROVENANCE_EVIDENCE_SCHEMA_VERSION,
        PROVENANCE_POLICY_SCHEMA_VERSION,
        REQUIRED_ARTIFACTS,
        contains_secret_material_key,
        meaningful,
        sha256,
        validate_build_report,
    )


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
        raise ValueError(f"cannot read build allowed-signers: {exc}") from exc
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
            raise ValueError(f"build allowed-signers line {line_number} has no public key")
        key_material = f"{fields[key_index]} {fields[key_index + 1]}"
        for principal in fields[0].split(","):
            if not meaningful(principal) or any(character in principal for character in "*?!"):
                raise ValueError(
                    f"build allowed-signers line {line_number} must use exact principals"
                )
            bindings.setdefault(principal, set()).add(key_material)
    if not bindings:
        raise ValueError("build allowed-signers contains no principals")
    key_owners: dict[str, set[str]] = {}
    for principal, keys in bindings.items():
        for key in keys:
            key_owners.setdefault(key, set()).add(principal)
    if any(len(owners) != 1 for owners in key_owners.values()):
        raise ValueError("build allowed-signers reuses a public key across identities")
    return bindings


def load_provenance_policy(
    path: Path,
    *,
    signer_identity: str,
) -> tuple[dict[str, Any], Path, dict[str, set[str]]]:
    policy = _load_object(path, "release provenance trust policy")
    expected_keys = {
        "schema_version",
        "policy_id",
        "organization",
        "allowed_signers_path",
        "allowed_signers_sha256",
        "builder_identities",
        "approved_source_repositories",
        "approved_builder_ids",
        "approved_workflow_refs",
        "required_artifacts",
    }
    identities = _exact_list(policy.get("builder_identities"))
    repositories = _exact_list(policy.get("approved_source_repositories"))
    builder_ids = _exact_list(policy.get("approved_builder_ids"))
    workflows = _exact_list(policy.get("approved_workflow_refs"))
    if not (
        set(policy) == expected_keys
        and policy.get("schema_version") == PROVENANCE_POLICY_SCHEMA_VERSION
        and meaningful(policy.get("policy_id"))
        and meaningful(policy.get("organization"))
        and identities
        and signer_identity in identities
        and repositories
        and builder_ids
        and workflows
        and policy.get("required_artifacts") == list(REQUIRED_ARTIFACTS)
    ):
        raise ValueError("release provenance policy does not authorize this exact build")
    raw_trust_path = policy.get("allowed_signers_path")
    if not meaningful(raw_trust_path):
        raise ValueError("release provenance policy has no allowed_signers_path")
    trust_path = Path(str(raw_trust_path)).expanduser()
    if not trust_path.is_absolute():
        trust_path = (path.parent / trust_path).resolve()
    expected_digest = str(policy.get("allowed_signers_sha256", ""))
    if not (
        trust_path.is_file()
        and DIGEST_RE.fullmatch(expected_digest)
        and sha256(trust_path) == expected_digest
    ):
        raise ValueError("release provenance trust store is missing or digest-mismatched")
    bindings = _allowed_signer_bindings(trust_path)
    if set(bindings) != set(identities):
        raise ValueError("build trust principals do not exactly match policy identities")
    return policy, trust_path, bindings


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
                BUILD_SIGNATURE_NAMESPACE,
                "-s",
                str(signature),
            ],
            input=path.read_bytes(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except OSError as exc:
        raise ValueError(f"cannot verify signed build report: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stdout.decode("utf-8", errors="replace").strip()
        raise ValueError(f"invalid signed build report: {detail}")


def collect(args: argparse.Namespace) -> dict[str, Any]:
    build_report = _load_object(args.build_report, "build provenance report")
    policy, allowed_signers, _ = load_provenance_policy(
        args.provenance_policy,
        signer_identity=args.build_signer_identity,
    )
    verify_signature(
        args.build_report,
        args.build_signature,
        identity=args.build_signer_identity,
        allowed_signers=allowed_signers,
    )
    if build_report.get("source_repository") not in set(
        policy["approved_source_repositories"]
    ):
        raise ValueError("build report uses an unapproved source repository")
    derived = validate_build_report(
        build_report,
        report_path=args.build_report,
        git_commit=args.git_commit,
        backend_image=args.backend_image,
        frontend_image=args.frontend_image,
        contract_digest=args.contract_digest,
        source_repository=str(build_report.get("source_repository", "")),
        approved_builder_ids=set(policy["approved_builder_ids"]),
        approved_workflow_refs=set(policy["approved_workflow_refs"]),
    )
    collected_at = datetime.now(timezone.utc)
    if not (
        derived["built_at"] <= collected_at
        and collected_at - derived["built_at"] <= timedelta(days=30)
    ):
        raise ValueError("signed release build is in the future or older than 30 days")
    report = {
        "schema_version": PROVENANCE_EVIDENCE_SCHEMA_VERSION,
        "release_version": "2.0.0",
        "git_ref": derived["git_ref"],
        "git_commit": derived["git_commit"],
        "contract_digest": derived["contract_digest"],
        "observed_at": collected_at.isoformat(),
        "built_at": build_report["build_finished_at"],
        "source_repository": derived["source_repository"],
        "source_tree_sha256": derived["source_tree_sha256"],
        "source_archive": derived["source_archive"],
        "builder": derived["builder"],
        "artifacts": derived["artifacts"],
        "provenance_policy": {
            "path": str(args.provenance_policy.resolve()),
            "sha256": sha256(args.provenance_policy),
            "policy_id": policy["policy_id"],
            "allowed_signers_path": str(allowed_signers.resolve()),
            "allowed_signers_sha256": sha256(allowed_signers),
        },
        "signed_build_report": {
            **build_report,
            "signed_evidence": {
                "path": str(args.build_report.resolve()),
                "sha256": sha256(args.build_report),
                "signature_path": str(args.build_signature.resolve()),
                "signer_identity": args.build_signer_identity,
            },
        },
    }
    if contains_secret_material_key(report):
        raise ValueError("release provenance evidence contains a forbidden credential field")
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--backend-image", required=True)
    parser.add_argument("--frontend-image", required=True)
    parser.add_argument("--contract-digest", required=True)
    parser.add_argument("--provenance-policy", type=Path, required=True)
    parser.add_argument("--build-signer-identity", required=True)
    parser.add_argument("--build-report", type=Path, required=True)
    parser.add_argument("--build-signature", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite-output", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not meaningful(args.build_signer_identity):
            raise ValueError("build signer identity must be non-placeholder")
        for path, label in (
            (args.provenance_policy, "release provenance policy"),
            (args.build_report, "build report"),
            (args.build_signature, "build signature"),
        ):
            if not path.is_file():
                raise ValueError(f"{label} does not exist")
        if args.output.exists() and not args.overwrite_output:
            raise ValueError("output already exists; choose a new evidence path or pass --overwrite-output")
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = collect(args)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"Release provenance collection failed: {exc}", file=sys.stderr)
        return 3
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
