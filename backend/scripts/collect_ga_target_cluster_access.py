#!/usr/bin/env python3
"""Collect and sign the campaign-bound live Kubernetes RBAC profile."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

try:
    from scripts.ga_target_cluster_access import persist_target_cluster_access
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_target_cluster_access import persist_target_cluster_access


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--operations-identity", required=True)
    parser.add_argument("--key", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = persist_target_cluster_access(
            args.campaign,
            operations_identity=args.operations_identity,
            key=args.key,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"Target cluster access collection failed: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
