#!/usr/bin/env python3
"""Sign one exact role statement for a campaign-bound GA execution authorization."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

try:
    from scripts.ga_execution_authorization import (
        REQUIRED_SIGNER_ROLES,
        atomic_write,
        create_statement,
        json_payload,
        load_object,
        sha256,
        sign_payload,
        verify_signature,
    )
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_execution_authorization import (
        REQUIRED_SIGNER_ROLES,
        atomic_write,
        create_statement,
        json_payload,
        load_object,
        sha256,
        sign_payload,
        verify_signature,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--role", choices=REQUIRED_SIGNER_ROLES, required=True)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--statement-output", type=Path, required=True)
    parser.add_argument("--signature-output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        for path, label in (
            (args.campaign, "execution campaign"),
            (args.manifest, "execution authorization manifest"),
            (args.approval_policy, "approval policy"),
            (args.key, "signing key"),
        ):
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"{label} must be a regular non-symlink file")
        outputs = {args.statement_output.resolve(), args.signature_output.resolve()}
        if len(outputs) != 2 or any(path.exists() for path in outputs):
            raise ValueError("statement and signature outputs must be distinct and immutable")
        campaign = load_object(args.campaign, "execution campaign")
        artifacts = campaign.get("artifacts")
        slug = args.role.lower()
        if not isinstance(artifacts, dict) or outputs != {
            Path(str(artifacts.get(f"execution_authorization_{slug}_statement"))).resolve(),
            Path(str(artifacts.get(f"execution_authorization_{slug}_signature"))).resolve(),
        }:
            raise ValueError("statement outputs must equal the campaign-planned artifact paths")
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    created: list[Path] = []
    try:
        tracked = {
            args.campaign.resolve(): sha256(args.campaign),
            args.manifest.resolve(): sha256(args.manifest),
            args.approval_policy.resolve(): sha256(args.approval_policy),
        }
        statement, trust_path = create_statement(
            args.campaign,
            args.manifest,
            role=args.role,
            identity=args.identity,
            approval_policy_path=args.approval_policy,
        )
        policy = load_object(args.approval_policy, "approval policy")
        expected_trust_digest = policy.get("allowed_signers_sha256")
        if expected_trust_digest != sha256(trust_path):
            raise ValueError("approval allowed-signers changed before signing")
        tracked[trust_path.resolve()] = str(expected_trust_digest)
        payload = json_payload(statement)
        signature = sign_payload(payload, key=args.key)
        atomic_write(args.statement_output, payload)
        created.append(args.statement_output)
        atomic_write(args.signature_output, signature)
        created.append(args.signature_output)
        if load_object(args.statement_output, "persisted execution authorization statement") != statement:
            raise ValueError("persisted execution authorization statement changed")
        verify_signature(
            args.statement_output,
            args.signature_output,
            identity=args.identity,
            allowed_signers=trust_path,
        )
        if any(sha256(path) != digest for path, digest in tracked.items()):
            raise ValueError("campaign, manifest, or policy changed during signing")
    except (OSError, UnicodeError, ValueError) as exc:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        print(f"GA execution authorization signing failed: {exc}", file=sys.stderr)
        return 3
    print(payload.decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
