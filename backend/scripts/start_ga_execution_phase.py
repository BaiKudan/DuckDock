#!/usr/bin/env python3
"""Start one risky GA campaign phase through its signed execution interlock."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

try:
    from scripts.ga_execution_phase_start import RISKY_PHASE_IDS, persist_phase_start
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_execution_phase_start import RISKY_PHASE_IDS, persist_phase_start


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--phase-id", choices=RISKY_PHASE_IDS, required=True)
    parser.add_argument("--action-id", required=True)
    parser.add_argument(
        "--action-description",
        required=True,
        help="single-line, secret-free description of the reviewed command or external action",
    )
    parser.add_argument("--operations-identity", required=True)
    parser.add_argument("--key", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.campaign.is_symlink() or not args.campaign.is_file():
            raise ValueError("campaign must be a regular non-symlink file")
        if args.key.is_symlink() or not args.key.is_file():
            raise ValueError("Operations signing key must be a regular non-symlink file")
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        receipt = persist_phase_start(
            args.campaign,
            phase_id=args.phase_id,
            action_id=args.action_id,
            action_description=args.action_description,
            operations_identity=args.operations_identity,
            key=args.key,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"GA execution phase start failed: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(receipt, indent=2, sort_keys=True) + "\n", end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
