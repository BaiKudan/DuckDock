#!/usr/bin/env python3
"""Independently verify Security and Operations authorization for named GA phases."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

try:
    from scripts.ga_execution_authorization import json_payload, verify_authorization
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_execution_authorization import json_payload, verify_authorization


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--security-statement", type=Path, required=True)
    parser.add_argument("--security-signature", type=Path, required=True)
    parser.add_argument("--operations-statement", type=Path, required=True)
    parser.add_argument("--operations-signature", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        for path, label in (
            (args.campaign, "execution campaign"),
            (args.manifest, "execution authorization manifest"),
            (args.security_statement, "Security statement"),
            (args.security_signature, "Security signature"),
            (args.operations_statement, "Operations statement"),
            (args.operations_signature, "Operations signature"),
        ):
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"{label} must be a regular non-symlink file")
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        receipt = verify_authorization(
            args.campaign,
            args.manifest,
            {
                "Security": (args.security_statement, args.security_signature),
                "Operations": (args.operations_statement, args.operations_signature),
            },
        )
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"GA execution authorization verification failed: {exc}", file=sys.stderr)
        return 3
    print(json_payload(receipt).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
