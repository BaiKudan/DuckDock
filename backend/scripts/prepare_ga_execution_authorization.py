#!/usr/bin/env python3
"""Prepare the immutable manifest for Security/Operations GA execution approval."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

try:
    from scripts.ga_execution_authorization import (
        atomic_write,
        json_payload,
        load_object,
        prepare_manifest,
        validate_manifest,
    )
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_execution_authorization import (
        atomic_write,
        json_payload,
        load_object,
        prepare_manifest,
        validate_manifest,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.campaign.is_symlink() or not args.campaign.is_file():
            raise ValueError("execution campaign must be a regular non-symlink file")
        if args.output.exists():
            raise ValueError("execution authorization manifests are immutable")
        campaign = load_object(args.campaign, "execution campaign")
        artifacts = campaign.get("artifacts")
        planned = artifacts.get("execution_authorization_manifest") if isinstance(artifacts, dict) else None
        if not isinstance(planned, str) or args.output.resolve() != Path(planned).resolve():
            raise ValueError("manifest output must equal the campaign-planned artifact path")
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        manifest = prepare_manifest(args.campaign)
        payload = json_payload(manifest)
        atomic_write(args.output, payload)
        _, persisted = validate_manifest(
            args.campaign,
            args.output,
        )
        if persisted != manifest:
            raise ValueError("persisted execution authorization manifest did not re-verify")
    except (OSError, UnicodeError, ValueError) as exc:
        args.output.unlink(missing_ok=True)
        print(f"GA execution authorization preparation failed: {exc}", file=sys.stderr)
        return 3
    print(payload.decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
