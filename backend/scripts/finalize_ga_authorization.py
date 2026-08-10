#!/usr/bin/env python3
"""Assemble four approvals and emit an authorization only after GA_AUTHORIZED."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

try:
    from scripts.ga_approval_campaign import (
        parse_rfc3339,
        sha256_path,
        validate_campaign_freeze,
    )
    from scripts.sign_ga_approval import APPROVAL_KEYS
    from scripts.verify_ga_production_authorization import (
        REQUIRED_APPROVAL_ROLES,
        evaluate,
        lint_authorization,
    )
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_approval_campaign import parse_rfc3339, sha256_path, validate_campaign_freeze
    from sign_ga_approval import APPROVAL_KEYS
    from verify_ga_production_authorization import (
        REQUIRED_APPROVAL_ROLES,
        evaluate,
        lint_authorization,
    )


FINALIZATION_RECEIPT_SCHEMA_VERSION = "duckdock-ga-authorization-finalization-v2"


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


def _approval_entry(path: Path, *, output_parent: Path) -> dict[str, Any]:
    approval = _load_object(path, f"approval entry {path}")
    if set(approval) != APPROVAL_KEYS:
        raise ValueError(f"approval entry has invalid fields: {path}")
    signature_raw = approval.get("signature_path")
    if not isinstance(signature_raw, str) or not signature_raw.strip():
        raise ValueError(f"approval entry has no signature path: {path}")
    signature = Path(signature_raw).expanduser()
    if not signature.is_absolute():
        signature = (path.parent / signature).resolve()
    if not signature.is_file():
        raise ValueError(f"approval signature is missing: {signature}")
    return {
        **approval,
        "signature_path": os.path.relpath(signature, output_parent),
    }


def finalize(
    args: argparse.Namespace,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    base_digest = _sha256(args.authorization)
    policy_digest = _sha256(args.approval_policy)
    campaign_freeze_digest = sha256_path(args.campaign_freeze)
    approval_entry_digests = [_sha256(path) for path in args.approval_entry]
    base = _load_object(args.authorization, "base GA authorization")
    lint_errors = lint_authorization(base)
    if lint_errors:
        raise ValueError("; ".join(lint_errors))
    if base.get("approvals") != []:
        raise ValueError("base authorization approvals must be empty before final assembly")
    if args.output.resolve().parent != args.authorization.resolve().parent:
        raise ValueError(
            "final authorization must stay beside the base file so relative evidence paths remain stable"
        )
    campaign = validate_campaign_freeze(
        args.campaign_freeze,
        authorization_path=args.authorization,
        approval_policy_path=args.approval_policy,
        now=current,
        require_execution_closure=not args.allow_legacy_unbound,
    )

    entry_records = [
        _approval_entry(path.resolve(), output_parent=args.output.resolve().parent)
        for path in args.approval_entry
    ]
    roles = [entry.get("role") for entry in entry_records]
    identities = [entry.get("identity") for entry in entry_records]
    if not (
        len(entry_records) == 4
        and set(roles) == REQUIRED_APPROVAL_ROLES
        and len(set(identities)) == 4
    ):
        raise ValueError("exactly four unique role and identity approval entries are required")
    if any(
        entry.get("campaign_id") != campaign["campaign_id"]
        or entry.get("campaign_freeze_sha256") != campaign["campaign_freeze_sha256"]
        for entry in entry_records
    ):
        raise ValueError("approval entries do not all belong to the frozen approval campaign")
    approval_times = [
        parse_rfc3339(entry.get("approved_at"), f"{entry.get('role')} approved_at")
        for entry in entry_records
    ]
    if any(
        approved_at < campaign["frozen_at"]
        or approved_at > campaign["approvals_expire_at"]
        for approved_at in approval_times
    ):
        raise ValueError("approval entry timestamp is outside the frozen campaign window")

    candidate = copy.deepcopy(base)
    candidate["approval_campaign"] = {
        "path": os.path.relpath(args.campaign_freeze.resolve(), args.output.resolve().parent),
        "sha256": campaign["campaign_freeze_sha256"],
        "campaign_id": campaign["campaign_id"],
    }
    candidate["approvals"] = entry_records
    result = evaluate(
        candidate,
        authorization_path=args.output.resolve(),
        approval_policy_path=args.approval_policy.resolve(),
        now=current,
    )
    if not (
        result.get("status") == "GA_AUTHORIZED"
        and result.get("campaign_stage") == "AUTHORIZED"
        and result.get("foundation_ready") is True
        and result.get("evidence_ready_for_approval") is True
        and result.get("approvals_complete") is True
        and result.get("block_count") == 0
        and result.get("failed_foundation_checks") == []
        and result.get("failed_evidence_checks") == []
        and result.get("failed_approval_checks") == []
        and result.get("next_action") == "archive_authorized_bundle"
    ):
        raise ValueError(
            "assembled authorization did not reach GA_AUTHORIZED; no final file was emitted"
        )
    if (
        _sha256(args.authorization) != base_digest
        or _sha256(args.approval_policy) != policy_digest
        or sha256_path(args.campaign_freeze) != campaign_freeze_digest
        or any(
            _sha256(path) != expected
            for path, expected in zip(
                args.approval_entry,
                approval_entry_digests,
                strict=True,
            )
        )
    ):
        raise ValueError("campaign inputs changed during final authorization assembly")

    receipt = {
        "schema_version": FINALIZATION_RECEIPT_SCHEMA_VERSION,
        "finalized_at": current.isoformat(),
        "base_authorization": {
            "path": str(args.authorization.resolve()),
            "sha256": base_digest,
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
            "schema_version": campaign["schema_version"],
        },
        "approval_entries": [
            {
                "path": str(path.resolve()),
                "sha256": entry_digest,
                "role": entry["role"],
                "identity": entry["identity"],
            }
            for path, entry, entry_digest in zip(
                args.approval_entry,
                entry_records,
                approval_entry_digests,
                strict=True,
            )
        ],
        "evaluation": result,
    }
    return candidate, receipt


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--campaign-freeze", type=Path, required=True)
    parser.add_argument(
        "--allow-legacy-unbound",
        action="store_true",
        help="validate historical v1 campaigns only; never use for a formal GA release",
    )
    parser.add_argument(
        "--approval-entry",
        type=Path,
        action="append",
        required=True,
        help="repeat exactly four times",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt-output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        for path, label in (
            (args.authorization, "base authorization"),
            (args.approval_policy, "approval policy"),
            (args.campaign_freeze, "approval campaign freeze"),
            *((path, "approval entry") for path in args.approval_entry),
        ):
            if not path.is_file():
                raise ValueError(f"{label} does not exist: {path}")
        if len(args.approval_entry) != 4:
            raise ValueError("--approval-entry must be provided exactly four times")
        if args.output.resolve() == args.authorization.resolve():
            raise ValueError("final output must not overwrite the unsigned base authorization")
        if args.output.resolve() == args.receipt_output.resolve():
            raise ValueError("authorization and receipt outputs must be distinct")
        if args.output.exists() or args.receipt_output.exists():
            raise ValueError("an output already exists; final authorization artifacts are immutable")
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        authorization, receipt = finalize(args)
        authorization_payload = json.dumps(authorization, indent=2, sort_keys=True) + "\n"
        _atomic_write(
            args.output,
            authorization_payload.encode("utf-8"),
        )
        try:
            persisted = _load_object(args.output, "persisted final authorization")
            persisted_result = evaluate(
                persisted,
                authorization_path=args.output.resolve(),
                approval_policy_path=args.approval_policy.resolve(),
            )
            if persisted_result.get("status") != "GA_AUTHORIZED":
                raise ValueError("persisted final authorization did not re-verify")
            receipt["evaluation"] = persisted_result
            receipt["authorization"] = {
                "path": str(args.output.resolve()),
                "sha256": _sha256(args.output),
            }
            receipt_payload = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
            _atomic_write(
                args.receipt_output,
                receipt_payload.encode("utf-8"),
            )
        except Exception:
            args.output.unlink(missing_ok=True)
            raise
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"GA authorization finalization failed: {exc}", file=sys.stderr)
        return 3
    print(receipt_payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
