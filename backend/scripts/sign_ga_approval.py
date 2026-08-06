#!/usr/bin/env python3
"""Fail-closed signer for one organizational DuckDock GA approval."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

try:
    from scripts.ga_approval_campaign import sha256_path, validate_campaign_freeze
    from scripts.verify_ga_production_authorization import (
        APPROVAL_STATEMENT_SCHEMA_VERSION,
        REQUIRED_APPROVAL_ROLES,
        SCHEMA_VERSION,
        _approval_statement,
        evaluate,
        lint_authorization,
    )
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_approval_campaign import sha256_path, validate_campaign_freeze
    from verify_ga_production_authorization import (
        APPROVAL_STATEMENT_SCHEMA_VERSION,
        REQUIRED_APPROVAL_ROLES,
        SCHEMA_VERSION,
        _approval_statement,
        evaluate,
        lint_authorization,
    )


PREFLIGHT_RECEIPT_SCHEMA_VERSION = "duckdock-ga-approval-preflight-v2"
APPROVAL_SIGNATURE_NAMESPACE = "duckdock-ga"
APPROVAL_KEYS = {
    "campaign_id",
    "campaign_freeze_sha256",
    "role",
    "identity",
    "decision",
    "approved_at",
    "signed_digest",
    "signature_path",
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


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"approved-at is not RFC3339: {exc}") from exc
    if parsed.tzinfo is None:
        raise ValueError("approved-at must include a timezone")
    return parsed.astimezone(timezone.utc)


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


def _role_is_authorized(policy: dict[str, Any], role: str, identity: str) -> bool:
    roles = policy.get("roles")
    return (
        isinstance(roles, dict)
        and isinstance(roles.get(role), list)
        and identity in roles[role]
        and all(
            identity not in identities
            for other_role, identities in roles.items()
            if other_role != role and isinstance(identities, list)
        )
    )


def create_approval(
    args: argparse.Namespace,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    authorization_digest = _sha256(args.authorization)
    policy_digest = _sha256(args.approval_policy)
    campaign_freeze_digest = sha256_path(args.campaign_freeze)
    authorization = _load_object(args.authorization, "GA authorization")
    lint_errors = lint_authorization(authorization)
    if lint_errors:
        raise ValueError("; ".join(lint_errors))
    policy = _load_object(args.approval_policy, "approval policy")
    if not _role_is_authorized(policy, args.role, args.identity):
        raise ValueError("approval policy does not authorize this exact identity for this role")
    approvals = authorization.get("approvals")
    if not isinstance(approvals, list):
        raise ValueError("authorization approvals must be an array")
    if approvals:
        raise ValueError(
            "base authorization approvals must be empty so every signer evaluates the same campaign"
        )

    campaign = validate_campaign_freeze(
        args.campaign_freeze,
        authorization_path=args.authorization,
        approval_policy_path=args.approval_policy,
        now=current,
    )

    preflight = evaluate(
        authorization,
        authorization_path=args.authorization.resolve(),
        approval_policy_path=args.approval_policy.resolve(),
        now=current,
    )
    if not (
        preflight.get("status") == "AWAITING_EXTERNAL_APPROVALS"
        and preflight.get("campaign_stage") == "APPROVAL_COLLECTION"
        and preflight.get("foundation_ready") is True
        and preflight.get("evidence_ready_for_approval") is True
        and preflight.get("failed_foundation_checks") == []
        and preflight.get("failed_evidence_checks") == []
        and preflight.get("next_action") == "collect_organizational_approvals"
    ):
        raise ValueError(
            "authoritative preflight is not at APPROVAL_COLLECTION; signing is forbidden"
        )
    release_digest = str(preflight.get("release_digest", ""))
    if release_digest != campaign["release_digest"]:
        raise ValueError("current release digest does not match the approval campaign freeze")
    approved_at = args.approved_at or current.isoformat()
    approved_time = _parse_time(approved_at)
    if approved_time > current:
        raise ValueError("approved-at cannot be in the future")
    if approved_time < campaign["frozen_at"]:
        raise ValueError("approved-at cannot precede the approval campaign freeze")
    if approved_time > campaign["approvals_expire_at"]:
        raise ValueError("approved-at cannot exceed the approval campaign expiry")

    approval = {
        "campaign_id": campaign["campaign_id"],
        "campaign_freeze_sha256": campaign["campaign_freeze_sha256"],
        "role": args.role,
        "identity": args.identity,
        "decision": "APPROVED",
        "approved_at": approved_at,
        "signed_digest": release_digest,
        "signature_path": str(args.signature_output.resolve()),
    }
    statement = _approval_statement(approval, release_digest)
    expected_statement = (
        json.dumps(
            {
                "schema_version": APPROVAL_STATEMENT_SCHEMA_VERSION,
                "authorization_schema_version": SCHEMA_VERSION,
                "release_digest": release_digest,
                "campaign_id": campaign["campaign_id"],
                "campaign_freeze_sha256": campaign["campaign_freeze_sha256"],
                "role": args.role,
                "identity": args.identity,
                "decision": "APPROVED",
                "approved_at": approved_at,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")
    if statement != expected_statement:
        raise ValueError("approval statement canonicalization mismatch")

    with tempfile.TemporaryDirectory(prefix="duckdock-ga-approval-") as temporary_dir:
        statement_path = Path(temporary_dir) / "approval.json"
        statement_path.write_bytes(statement)
        try:
            completed = subprocess.run(
                [
                    "ssh-keygen",
                    "-Y",
                    "sign",
                    "-f",
                    str(args.key),
                    "-n",
                    APPROVAL_SIGNATURE_NAMESPACE,
                    str(statement_path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
        except OSError as exc:
            raise ValueError(f"cannot execute ssh-keygen: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stdout.decode("utf-8", errors="replace").strip()
            raise ValueError(f"cannot sign approval: {detail}")
        signature_path = Path(f"{statement_path}.sig")
        if not signature_path.is_file():
            raise ValueError("ssh-keygen did not create an approval signature")
        signature = signature_path.read_bytes()

        candidate = copy.deepcopy(authorization)
        candidate["approval_campaign"] = {
            "path": str(args.campaign_freeze.resolve()),
            "sha256": campaign["campaign_freeze_sha256"],
            "campaign_id": campaign["campaign_id"],
        }
        candidate_approval = {**approval, "signature_path": str(signature_path)}
        candidate["approvals"] = [*approvals, candidate_approval]
        candidate_result = evaluate(
            candidate,
            authorization_path=args.authorization.resolve(),
            approval_policy_path=args.approval_policy.resolve(),
            now=current,
        )
        approval_check = next(
            (
                check
                for check in candidate_result.get("checks", [])
                if check.get("key") == f"approval_{args.role}"
            ),
            None,
        )
        if not isinstance(approval_check, dict) or approval_check.get("passed") is not True:
            detail = approval_check.get("observed") if isinstance(approval_check, dict) else "missing"
            raise ValueError(f"new approval did not pass authoritative verification: {detail}")

    if (
        _sha256(args.authorization) != authorization_digest
        or _sha256(args.approval_policy) != policy_digest
        or sha256_path(args.campaign_freeze) != campaign_freeze_digest
    ):
        raise ValueError(
            "authorization, approval policy or campaign freeze changed while the approval was signing"
        )

    receipt = {
        "schema_version": PREFLIGHT_RECEIPT_SCHEMA_VERSION,
        "authorization": {
            "path": str(args.authorization.resolve()),
            "sha256": authorization_digest,
        },
        "approval_policy": {
            "path": str(args.approval_policy.resolve()),
            "sha256": policy_digest,
        },
        "campaign_freeze": {
            "path": str(args.campaign_freeze.resolve()),
            "sha256": campaign_freeze_digest,
            "campaign_id": campaign["campaign_id"],
            "frozen_at": campaign["freeze"]["frozen_at"],
            "approvals_expire_at": campaign["freeze"]["approvals_expire_at"],
        },
        "role": args.role,
        "identity": args.identity,
        "checked_at": current.isoformat(),
        "release_digest": release_digest,
        "evaluation": preflight,
        "approval_check": approval_check,
    }
    return approval, receipt, signature


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--campaign-freeze", type=Path, required=True)
    parser.add_argument("--role", choices=sorted(REQUIRED_APPROVAL_ROLES), required=True)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--approved-at")
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--signature-output", type=Path, required=True)
    parser.add_argument("--approval-output", type=Path, required=True)
    parser.add_argument("--preflight-output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        for path, label in (
            (args.authorization, "authorization"),
            (args.approval_policy, "approval policy"),
            (args.campaign_freeze, "approval campaign freeze"),
            (args.key, "private signing key"),
        ):
            if not path.is_file():
                raise ValueError(f"{label} does not exist")
        outputs = {
            args.signature_output.resolve(),
            args.approval_output.resolve(),
            args.preflight_output.resolve(),
        }
        if len(outputs) != 3:
            raise ValueError("signature, approval and preflight outputs must be distinct")
        if any(path.exists() for path in outputs):
            raise ValueError("an output already exists; approval artifacts are immutable")
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        approval, receipt, signature = create_approval(args)
        approval_payload = json.dumps(approval, indent=2, sort_keys=True) + "\n"
        receipt_payload = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
        created: list[Path] = []
        try:
            for path, payload in (
                (args.signature_output, signature),
                (args.approval_output, approval_payload.encode("utf-8")),
                (args.preflight_output, receipt_payload.encode("utf-8")),
            ):
                _atomic_write(path, payload)
                created.append(path)
        except Exception:
            for path in created:
                path.unlink(missing_ok=True)
            raise
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"GA approval signing failed: {exc}", file=sys.stderr)
        return 3
    print(approval_payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
