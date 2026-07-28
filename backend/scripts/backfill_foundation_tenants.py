#!/usr/bin/env python3
"""Run one bounded DuckDock 2.0 Foundation tenant backfill batch.

Examples (from ``backend``):

    python scripts/backfill_foundation_tenants.py --dry-run --batch-size 500 --json
    python scripts/backfill_foundation_tenants.py --apply \
        --ack-write-quiescence --batch-size 500 \
        --checkpoint tenant-backfill.checkpoint.json

The command reads the database URL only from normal application settings.  It
never accepts a fallback/default Namespace because ambiguous rows must remain
visible as unresolved or conflict findings.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.services.tenant_backfill_service import (
    BackfillCheckpoint,
    run_tenant_backfill,
)


CHECKPOINT_SCHEMA_VERSION = 1
DEFAULT_BATCH_SIZE = 500
MINIMUM_ALEMBIC_REVISION = "20260717_0027"


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill one bounded batch of NULL Foundation tenant keys.",
    )
    parser.add_argument(
        "--batch-size",
        type=_positive_int,
        default=DEFAULT_BATCH_SIZE,
        help=f"maximum rows to scan in this invocation (default: {DEFAULT_BATCH_SIZE})",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="resolve and report without committing tenant assignments",
    )
    mode.add_argument(
        "--apply",
        dest="dry_run",
        action="store_false",
        help="commit safe resolved assignments for this bounded batch",
    )
    parser.add_argument(
        "--ack-write-quiescence",
        action="store_true",
        help="confirm AssetOwnership/RuntimeBinding and target relation writes are paused during apply",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        help="JSON cursor to load and atomically update after a successful batch",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="write deterministic JSON to stdout instead of the console report",
    )
    args = parser.parse_args(argv)
    if not args.dry_run and not args.ack_write_quiescence:
        parser.error("--apply requires --ack-write-quiescence")
    return args


def database_identity_sha256(database_url: str | None = None) -> str:
    """Hash a password-free physical database identity for cursor binding."""

    url = make_url(database_url or settings.DATABASE_URL)
    backend = url.get_backend_name()
    default_ports = {"mysql": 3306, "postgresql": 5432}
    identity = {
        "backend": backend,
        "host": (url.host or "").lower(),
        "port": url.port or default_ports.get(backend),
        "database": url.database or "",
    }
    canonical = json.dumps(identity, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def read_alembic_revision(db: Any) -> str:
    revision = await db.scalar(text("SELECT version_num FROM alembic_version"))
    if not isinstance(revision, str) or not revision:
        raise ValueError("database has no readable Alembic revision")
    if revision < MINIMUM_ALEMBIC_REVISION:
        raise ValueError(
            f"database Alembic revision {revision} is older than {MINIMUM_ALEMBIC_REVISION}"
        )
    return revision


def load_checkpoint(
    path: Path,
    *,
    dry_run: bool | None = None,
    expected_database_identity_sha256: str | None = None,
    expected_alembic_revision: str | None = None,
) -> BackfillCheckpoint | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read tenant backfill checkpoint: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise ValueError("unsupported tenant backfill checkpoint schema_version")
    expected_keys = {
        "schema_version",
        "mode",
        "database_identity_sha256",
        "alembic_revision",
        "target_type",
        "last_id",
    }
    if set(payload) != expected_keys:
        raise ValueError("invalid tenant backfill checkpoint fields")
    if dry_run is not None:
        expected_mode = "dry_run" if dry_run else "apply"
        if payload["mode"] != expected_mode:
            raise ValueError(
                f"checkpoint mode is {payload['mode']!r}, expected {expected_mode!r}"
            )
    database_identity = expected_database_identity_sha256 or database_identity_sha256()
    if payload["database_identity_sha256"] != database_identity:
        raise ValueError("checkpoint database identity does not match DATABASE_URL")
    alembic_revision = expected_alembic_revision or MINIMUM_ALEMBIC_REVISION
    if payload["alembic_revision"] != alembic_revision:
        raise ValueError("checkpoint Alembic revision does not match the database revision")
    return BackfillCheckpoint.from_dict(payload)


def save_checkpoint(
    path: Path,
    checkpoint: BackfillCheckpoint,
    *,
    dry_run: bool = False,
    current_database_identity_sha256: str | None = None,
    alembic_revision: str = MINIMUM_ALEMBIC_REVISION,
) -> None:
    """Durably write a checkpoint by replacing from the same directory."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "mode": "dry_run" if dry_run else "apply",
        "database_identity_sha256": current_database_identity_sha256 or database_identity_sha256(),
        "alembic_revision": alembic_revision,
        **checkpoint.to_dict(),
    }
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


async def async_main(
    argv: Sequence[str] | None = None,
    *,
    session_factory: Callable[[], Any] = AsyncSessionLocal,
    service_runner: Callable[..., Any] = run_tenant_backfill,
) -> int:
    args = parse_args(argv)
    database_identity = database_identity_sha256()

    async with session_factory() as db:
        try:
            alembic_revision = await read_alembic_revision(db)
            checkpoint = (
                load_checkpoint(
                    args.checkpoint,
                    dry_run=args.dry_run,
                    expected_database_identity_sha256=database_identity,
                    expected_alembic_revision=alembic_revision,
                )
                if args.checkpoint is not None
                else None
            )
            report = await service_runner(
                db,
                batch_size=args.batch_size,
                dry_run=args.dry_run,
                checkpoint=checkpoint,
            )
            if args.dry_run:
                await db.rollback()
            else:
                await db.commit()
        except BaseException:
            await db.rollback()
            raise

    if args.checkpoint is not None and report.checkpoint is not None:
        save_checkpoint(
            args.checkpoint,
            report.checkpoint,
            dry_run=args.dry_run,
            current_database_identity_sha256=database_identity,
            alembic_revision=alembic_revision,
        )
    print(report.to_json() if args.json else report.to_human())
    return report.exit_code


def main() -> int:
    try:
        return asyncio.run(async_main())
    except KeyboardInterrupt:
        return 130
    except ValueError as exc:
        print(f"tenant-backfill safety error: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        print(f"tenant-backfill failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
