#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.contracts.openapi_v2 import render_openapi_v2_contract
from app.main import app


DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[2]
    / "specs"
    / "015-ga-candidate"
    / "contracts"
    / "openapi-v2.generated.json"
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export the frozen DuckDock API v2 contract")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    rendered = render_openapi_v2_contract(app)
    if args.check:
        if not args.output.exists() or args.output.read_bytes() != rendered:
            raise SystemExit(f"OpenAPI v2 contract drift detected: {args.output}")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(rendered)
    print(f"{hashlib.sha256(rendered).hexdigest()}  {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
