#!/usr/bin/env python3
"""Apply one explicit, versioned Foundation tenant remediation manifest."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from pydantic import ValidationError
from sqlalchemy import text

from app.core.database import AsyncSessionLocal, engine
from app.schemas.tenant_remediation import TenantRemediationManifest
from app.services.tenant_remediation_service import (
    TenantRemediationError,
    apply_tenant_remediation,
)


MINIMUM_ALEMBIC_REVISION = "20260720_0028"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate or atomically apply an explicit tenant remediation manifest.",
    )
    parser.add_argument("--manifest", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", dest="dry_run", action="store_true")
    mode.add_argument("--apply", dest="dry_run", action="store_false")
    parser.add_argument(
        "--ack-write-quiescence",
        action="store_true",
        help="confirm Foundation relationship writers are paused during apply",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not args.dry_run and not args.ack_write_quiescence:
        parser.error("--apply requires --ack-write-quiescence")
    return args


def load_manifest(path: Path) -> TenantRemediationManifest:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read tenant remediation manifest: {path}") from exc
    try:
        return TenantRemediationManifest.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"invalid tenant remediation manifest: {exc}") from exc


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
    service_runner: Callable[..., Any] = apply_tenant_remediation,
) -> int:
    args = parse_args(argv)
    manifest = load_manifest(args.manifest)
    async with session_factory() as db:
        try:
            await read_alembic_revision(db)
            report = await service_runner(
                db,
                manifest=manifest,
                dry_run=args.dry_run,
            )
            if args.dry_run:
                await db.rollback()
            else:
                await db.commit()
        except BaseException:
            await db.rollback()
            raise
    print(report.to_json() if args.json else report.to_human())
    return 0


def main() -> int:
    try:
        return asyncio.run(_run_and_dispose())
    except KeyboardInterrupt:
        return 130
    except TenantRemediationError as exc:
        print(f"tenant-remediation rejected: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"tenant-remediation safety error: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        print(f"tenant-remediation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 4


async def _run_and_dispose() -> int:
    try:
        return await async_main()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
