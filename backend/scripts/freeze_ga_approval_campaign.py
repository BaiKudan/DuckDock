#!/usr/bin/env python3
"""Freeze one approval-empty, evidence-ready DuckDock GA approval campaign."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

try:
    from scripts.close_ga_execution_campaign import verify_persisted_closure
    from scripts.ga_approval_campaign import (
        CAMPAIGN_FREEZE_SCHEMA_VERSION,
        LEGACY_CAMPAIGN_FREEZE_SCHEMA_VERSION,
        derive_campaign_id,
        parse_rfc3339,
        sha256_path,
        validate_campaign_freeze,
    )
    from scripts.verify_ga_production_authorization import evaluate, lint_authorization
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from close_ga_execution_campaign import verify_persisted_closure
    from ga_approval_campaign import (
        CAMPAIGN_FREEZE_SCHEMA_VERSION,
        LEGACY_CAMPAIGN_FREEZE_SCHEMA_VERSION,
        derive_campaign_id,
        parse_rfc3339,
        sha256_path,
        validate_campaign_freeze,
    )
    from verify_ga_production_authorization import evaluate, lint_authorization


MAX_CLOCK_SKEW = timedelta(minutes=5)
MAX_APPROVAL_WINDOW_HOURS = 72


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} root must be an object")
    return value


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


def freeze_campaign(
    args: argparse.Namespace,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    frozen_at = (
        parse_rfc3339(args.frozen_at, "frozen-at") if args.frozen_at else current
    )
    if frozen_at > current:
        raise ValueError("frozen-at cannot be in the future")
    if current - frozen_at > MAX_CLOCK_SKEW:
        raise ValueError("frozen-at cannot be more than five minutes before execution")
    expires_at = frozen_at + timedelta(hours=args.approval_window_hours)
    authorization_digest = sha256_path(args.authorization)
    policy_digest = sha256_path(args.approval_policy)
    authorization = _load_object(args.authorization, "GA authorization")
    lint_errors = lint_authorization(authorization)
    if lint_errors:
        raise ValueError("; ".join(lint_errors))
    if authorization.get("approvals") != []:
        raise ValueError("campaign authorization approvals must be empty at freeze time")
    if "approval_campaign" in authorization:
        raise ValueError("campaign authorization must not already contain a campaign reference")

    evaluation = evaluate(
        authorization,
        authorization_path=args.authorization.resolve(),
        approval_policy_path=args.approval_policy.resolve(),
        now=current,
    )
    if not (
        evaluation.get("status") == "AWAITING_EXTERNAL_APPROVALS"
        and evaluation.get("campaign_stage") == "APPROVAL_COLLECTION"
        and evaluation.get("foundation_ready") is True
        and evaluation.get("evidence_ready_for_approval") is True
        and evaluation.get("failed_foundation_checks") == []
        and evaluation.get("failed_evidence_checks") == []
        and evaluation.get("next_action") == "collect_organizational_approvals"
    ):
        raise ValueError("authorization is not ready for an approval campaign freeze")
    release_digest = str(evaluation.get("release_digest", ""))
    execution_closure_digest: str | None = None
    if args.execution_closure is not None:
        closure_validation = verify_persisted_closure(
            args.execution_closure,
            authorization_path=args.authorization,
            approval_policy_path=args.approval_policy,
            now=current,
        )
        if closure_validation.get("release_digest") != release_digest:
            raise ValueError("execution closure release digest differs from the authorization")
        execution_closure_digest = str(closure_validation["closure_sha256"])
    frozen_at_text = frozen_at.isoformat()
    expires_at_text = expires_at.isoformat()
    campaign_id = derive_campaign_id(
        authorization_sha256=authorization_digest,
        approval_policy_sha256=policy_digest,
        release_digest=release_digest,
        frozen_at=frozen_at_text,
        approvals_expire_at=expires_at_text,
        execution_closure_sha256=execution_closure_digest,
    )
    if (
        sha256_path(args.authorization) != authorization_digest
        or sha256_path(args.approval_policy) != policy_digest
        or (
            args.execution_closure is not None
            and sha256_path(args.execution_closure) != execution_closure_digest
        )
    ):
        raise ValueError(
            "authorization, approval policy or execution closure changed during campaign freeze"
        )
    receipt = {
        "schema_version": (
            CAMPAIGN_FREEZE_SCHEMA_VERSION
            if execution_closure_digest is not None
            else LEGACY_CAMPAIGN_FREEZE_SCHEMA_VERSION
        ),
        "campaign_id": campaign_id,
        "frozen_at": frozen_at_text,
        "approvals_expire_at": expires_at_text,
        "authorization": {
            "path": str(args.authorization.resolve()),
            "sha256": authorization_digest,
        },
        "approval_policy": {
            "path": str(args.approval_policy.resolve()),
            "sha256": policy_digest,
        },
        "release_digest": release_digest,
        "evaluation": evaluation,
    }
    if args.execution_closure is not None:
        receipt["execution_closure"] = {
            "path": str(args.execution_closure.resolve()),
            "sha256": execution_closure_digest,
        }
    return receipt


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--receipt-output", type=Path, required=True)
    binding = parser.add_mutually_exclusive_group(required=True)
    binding.add_argument(
        "--execution-closure",
        type=Path,
        help="persisted execution campaign closure required by the formal v2 profile",
    )
    binding.add_argument(
        "--allow-legacy-unbound",
        action="store_true",
        help="validate historical v1 campaigns only; never use for a formal GA release",
    )
    parser.add_argument("--frozen-at")
    parser.add_argument("--approval-window-hours", type=int, default=24)
    args = parser.parse_args(argv)
    try:
        inputs = [
            (args.authorization, "authorization"),
            (args.approval_policy, "approval policy"),
        ]
        if args.execution_closure is not None:
            inputs.append((args.execution_closure, "execution closure"))
        for path, label in inputs:
            if not path.is_file():
                raise ValueError(f"{label} does not exist: {path}")
        if not 1 <= args.approval_window_hours <= MAX_APPROVAL_WINDOW_HOURS:
            raise ValueError("approval window must be between 1 and 72 hours")
        if args.receipt_output.resolve() in {
            args.authorization.resolve(),
            args.approval_policy.resolve(),
            *(
                {args.execution_closure.resolve()}
                if args.execution_closure is not None
                else set()
            ),
        }:
            raise ValueError("campaign receipt must not overwrite an input")
        if args.receipt_output.exists():
            raise ValueError("campaign receipt already exists and is immutable")
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        receipt = freeze_campaign(args)
        payload = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
        _atomic_write(args.receipt_output, payload)
        try:
            validated = validate_campaign_freeze(
                args.receipt_output,
                authorization_path=args.authorization,
                approval_policy_path=args.approval_policy,
                now=datetime.now(timezone.utc),
                require_execution_closure=args.execution_closure is not None,
            )
            if validated["campaign_id"] != receipt["campaign_id"]:
                raise ValueError("persisted campaign receipt did not re-verify")
        except Exception:
            args.receipt_output.unlink(missing_ok=True)
            raise
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"GA approval campaign freeze failed: {exc}", file=sys.stderr)
        return 3
    print(payload.decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
