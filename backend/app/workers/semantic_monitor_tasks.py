from __future__ import annotations

import asyncio
import logging
import socket
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.services.evaluation_semantic_monitor_service import (
    execute_evaluation_semantic_monitor_run,
    fail_evaluation_semantic_monitor_run,
    lease_evaluation_semantic_monitor_run,
    list_due_evaluation_semantic_monitor_run_ids,
    queue_due_evaluation_semantic_monitors,
)
from app.workers.celery_app import celery_app


logger = logging.getLogger(__name__)


def enqueue_semantic_monitor_run(run_public_id: str) -> None:
    celery_app.send_task(
        "run_semantic_monitor",
        kwargs={"run_public_id": run_public_id},
        task_id=f"semantic-monitor:{run_public_id}",
    )


@celery_app.task(name="dispatch_due_semantic_monitors", ignore_result=True)
def dispatch_due_semantic_monitors() -> dict[str, int]:
    return asyncio.run(_dispatch_due_semantic_monitors())


async def _dispatch_due_semantic_monitors() -> dict[str, int]:
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as db:
            queued = await queue_due_evaluation_semantic_monitors(
                db,
                limit=settings.SEMANTIC_MONITOR_DISPATCH_BATCH_SIZE,
            )
            await db.commit()
        async with session_factory() as db:
            run_ids = await list_due_evaluation_semantic_monitor_run_ids(
                db,
                limit=settings.SEMANTIC_MONITOR_DISPATCH_BATCH_SIZE,
            )
        for public_id in run_ids:
            await asyncio.to_thread(enqueue_semantic_monitor_run, public_id)
    finally:
        await engine.dispose()
    return {"queued": len(queued), "dispatched": len(run_ids)}


@celery_app.task(name="run_semantic_monitor", ignore_result=True)
def run_semantic_monitor(*, run_public_id: str) -> dict[str, object]:
    return asyncio.run(_run_semantic_monitor(run_public_id))


def _execution_error_code(exc: Exception) -> str:
    name = exc.__class__.__name__.lower()
    if "provider" in name or "langfuse" in name or "embedding" in name:
        return "semantic_monitor_provider_failed"
    if "state" in name or "conflict" in name or "notfound" in name:
        return "semantic_monitor_pins_invalid"
    return "semantic_monitor_execution_failed"


async def _run_semantic_monitor(run_public_id: str) -> dict[str, object]:
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    worker_id = f"{socket.gethostname()}:{uuid.uuid4().hex[:12]}"
    try:
        async with session_factory() as db:
            lease = await lease_evaluation_semantic_monitor_run(
                db,
                run_public_id=run_public_id,
                worker_id=worker_id,
                lease_seconds=settings.SEMANTIC_MONITOR_LEASE_SECONDS,
                max_attempts=settings.SEMANTIC_MONITOR_MAX_ATTEMPTS,
            )
            await db.commit()
        if lease is None:
            return {"leased": False, "run_public_id": run_public_id}
        try:
            async with session_factory() as db:
                completed = await execute_evaluation_semantic_monitor_run(
                    db,
                    run_public_id=run_public_id,
                    worker_id=worker_id,
                )
                await db.commit()
        except Exception as exc:
            error_code = _execution_error_code(exc)
            async with session_factory() as db:
                status = await fail_evaluation_semantic_monitor_run(
                    db,
                    run_public_id=run_public_id,
                    worker_id=worker_id,
                    error_code=error_code,
                    max_attempts=settings.SEMANTIC_MONITOR_MAX_ATTEMPTS,
                    base_retry_seconds=settings.SEMANTIC_MONITOR_BASE_RETRY_SECONDS,
                    max_retry_seconds=settings.SEMANTIC_MONITOR_MAX_RETRY_SECONDS,
                )
                await db.commit()
            logger.warning(
                "Semantic monitor failed public_id=%s state=%s code=%s error=%s",
                run_public_id,
                status.value if status is not None else None,
                error_code,
                exc.__class__.__name__,
            )
            return {
                "leased": True,
                "completed": False,
                "status": status.value if status is not None else None,
            }
        return {
            "leased": True,
            "completed": completed is not None,
            "status": completed.status.value if completed is not None else "STALE",
            "outcome": (
                completed.outcome.value
                if completed is not None and completed.outcome is not None
                else None
            ),
        }
    finally:
        await engine.dispose()
