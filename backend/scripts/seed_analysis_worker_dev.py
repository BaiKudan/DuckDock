#!/usr/bin/env python3
"""Ensure the configured Analysis Worker token exists in a DEBUG database."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings
from app.core.database import AsyncSessionLocal, engine
from app.core.security import hash_password, verify_password
from app.models.control_plane import AnalysisWorker, AnalysisWorkerStatus


TOKEN_PREFIX_RE = re.compile(r"^[0-9a-f]{8}$")


def _token_parts(token: str) -> tuple[str, str]:
    parts = token.split("_", 3)
    if (
        len(parts) != 4
        or parts[:2] != ["dkr", "worker"]
        or TOKEN_PREFIX_RE.fullmatch(parts[2]) is None
        or len(parts[3]) < 16
        or any(character.isspace() for character in parts[3])
    ):
        raise ValueError("DUCKDOCK_ANALYSIS_WORKER_TOKEN must be a valid analysis worker token")
    return parts[2], parts[3]


async def ensure_dev_analysis_worker(
    db: AsyncSession,
    *,
    token: str,
    name: str,
) -> tuple[AnalysisWorker, bool]:
    prefix, secret = _token_parts(token)
    normalized_name = name.strip()
    if not normalized_name or len(normalized_name) > 128:
        raise ValueError("analysis worker name must contain 1 to 128 characters")
    worker = await db.scalar(
        select(AnalysisWorker).where(AnalysisWorker.token_prefix == prefix)
    )
    if worker is not None:
        if not verify_password(secret, worker.token_hash):
            raise ValueError("analysis worker token prefix already belongs to another token")
        if worker.status != AnalysisWorkerStatus.ACTIVE:
            raise ValueError("analysis worker token belongs to a disabled or stale worker")
        worker.name = normalized_name
        return worker, False
    worker = AnalysisWorker(
        name=normalized_name,
        worker_key=f"ocw_dev_{prefix}",
        token_prefix=prefix,
        token_hash=hash_password(secret),
        status=AnalysisWorkerStatus.ACTIVE,
        capabilities_json={"bootstrap": "debug-env"},
        created_by=None,
    )
    db.add(worker)
    await db.flush()
    return worker, True


async def bootstrap() -> dict[str, Any]:
    if not settings.DEBUG:
        raise ValueError("refusing to bootstrap an Analysis Worker while DEBUG=false")
    token = os.environ.get("DUCKDOCK_ANALYSIS_WORKER_TOKEN", "")
    name = os.environ.get(
        "DUCKDOCK_ANALYSIS_WORKER_NAME",
        "DuckDock Compose Analysis Worker",
    )
    async with AsyncSessionLocal() as db:
        worker, created = await ensure_dev_analysis_worker(
            db,
            token=token,
            name=name,
        )
        await db.commit()
        return {
            "status": "created" if created else "verified",
            "worker_id": worker.id,
            "worker_key": worker.worker_key,
        }


async def async_main() -> int:
    engine.echo = False
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    try:
        result = await bootstrap()
    except (OSError, ValueError) as exc:
        print(f"Analysis Worker dev bootstrap failed: {exc}", file=sys.stderr)
        return 3
    finally:
        await engine.dispose()
    print(json.dumps(result, sort_keys=True))
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
