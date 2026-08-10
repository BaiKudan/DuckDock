#!/usr/bin/env python3
"""Run the read-only Foundation tenant contract preflight."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import text

from app.core.database import AsyncSessionLocal, engine
from app.services.tenant_contract_service import check_tenant_contract


MINIMUM_ALEMBIC_REVISION = "20260720_0028"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check whether Foundation tenant ownership is safe to contract.",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


async def read_alembic_revision(db: Any) -> str:
    revision = await db.scalar(text("SELECT version_num FROM alembic_version"))
    if not isinstance(revision, str) or revision < MINIMUM_ALEMBIC_REVISION:
        raise ValueError(
            f"database Alembic revision must be {MINIMUM_ALEMBIC_REVISION} or later"
        )
    return revision


async def async_main(
    argv: Sequence[str] | None = None,
    *,
    session_factory: Callable[[], Any] = AsyncSessionLocal,
    checker: Callable[..., Any] = check_tenant_contract,
) -> int:
    args = parse_args(argv)
    async with session_factory() as db:
        try:
            await read_alembic_revision(db)
            report = await checker(db)
        finally:
            await db.rollback()
    print(report.to_json() if args.json else report.to_human())
    return report.exit_code


def main() -> int:
    try:
        return asyncio.run(_run_and_dispose())
    except KeyboardInterrupt:
        return 130
    except ValueError as exc:
        print(f"tenant-contract safety error: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        print(f"tenant-contract failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 4


async def _run_and_dispose() -> int:
    try:
        return await async_main()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
