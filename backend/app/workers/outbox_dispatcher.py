from __future__ import annotations

import asyncio
import logging
import socket
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.services.compatibility_reconciliation_service import (
    process_compatibility_outbox_event,
)
from app.services.outbox_dispatcher_service import dispatch_outbox_once
from app.workers.celery_app import celery_app
from app.workers.evaluation_tasks import enqueue_evaluation


logger = logging.getLogger(__name__)


class CeleryOutboxPublisher:
    async def publish(
        self,
        *,
        event_id: str,
        event_type: str,
        schema_version: str,
        payload: dict,
    ) -> None:
        await asyncio.to_thread(
            celery_app.send_task,
            "consume_outbox_event",
            kwargs={
                "event_id": event_id,
                "event_type": event_type,
                "schema_version": schema_version,
                "payload": payload,
            },
            task_id=event_id,
        )


@celery_app.task(name="dispatch_outbox", ignore_result=True)
def dispatch_outbox() -> dict[str, int]:
    return asyncio.run(_dispatch_outbox())


async def _dispatch_outbox() -> dict[str, int]:
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    worker_id = f"{socket.gethostname()}:{uuid.uuid4().hex[:12]}"
    try:
        result = await dispatch_outbox_once(
            session_factory,
            publisher=CeleryOutboxPublisher(),
            worker_id=worker_id,
            batch_size=settings.OUTBOX_BATCH_SIZE,
            lease_seconds=settings.OUTBOX_LEASE_SECONDS,
            max_attempts=settings.OUTBOX_MAX_ATTEMPTS,
            base_retry_seconds=settings.OUTBOX_BASE_RETRY_SECONDS,
            max_retry_seconds=settings.OUTBOX_MAX_RETRY_SECONDS,
        )
    finally:
        await engine.dispose()
    logger.info(
        "Outbox dispatch completed worker_id=%s leased=%s published=%s "
        "retry_scheduled=%s failed=%s",
        worker_id,
        result.leased,
        result.published,
        result.retry_scheduled,
        result.failed,
    )
    return {
        "leased": result.leased,
        "published": result.published,
        "retry_scheduled": result.retry_scheduled,
        "failed": result.failed,
    }


@celery_app.task(name="consume_outbox_event", ignore_result=True)
def consume_outbox_event(
    *,
    event_id: str,
    event_type: str,
    schema_version: str,
    payload: dict,
) -> None:
    if event_type == "EvaluationQueued":
        evaluation_public_id = payload.get("evaluation_public_id")
        if not isinstance(evaluation_public_id, str):
            raise ValueError(
                "EvaluationQueued payload is missing evaluation_public_id"
            )
        enqueue_evaluation(evaluation_public_id)
        logger.info(
            "Evaluation queued event accepted event_id=%s public_id=%s",
            event_id,
            evaluation_public_id,
        )
        return
    result = asyncio.run(
        _consume_outbox_event(
            event_id=event_id,
        )
    )
    logger.info(
        "Outbox event accepted event_id=%s event_type=%s schema_version=%s "
        "handled=%s replayed=%s",
        event_id,
        event_type,
        schema_version,
        result["handled"],
        result["replayed"],
    )


async def _consume_outbox_event(
    *,
    event_id: str,
) -> dict[str, bool]:
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as db:
            result = await process_compatibility_outbox_event(
                db,
                event_id=event_id,
            )
            await db.commit()
    finally:
        await engine.dispose()
    return {
        "handled": result.handled,
        "replayed": result.replayed,
    }
