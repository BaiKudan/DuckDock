from __future__ import annotations

import asyncio
import logging
import socket
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.adapters.evaluation.langfuse import LangfuseAnnotationQueueAdapter
from app.core.config import settings
from app.services.evaluation_annotation_service import (
    acknowledge_annotation_dispatch,
    fail_annotation_dispatch,
    lease_annotation_dispatch,
    list_due_annotation_dispatch_ids,
    load_annotation_dispatch_request,
)
from app.services.langfuse_service import langfuse_service
from app.workers.celery_app import celery_app


logger = logging.getLogger(__name__)


def enqueue_annotation_dispatch(dispatch_public_id: str) -> None:
    celery_app.send_task(
        "sync_annotation_dispatch",
        kwargs={"dispatch_public_id": dispatch_public_id},
        task_id=f"annotation-dispatch:{dispatch_public_id}",
    )


@celery_app.task(name="dispatch_due_annotation_syncs", ignore_result=True)
def dispatch_due_annotation_syncs() -> dict[str, int]:
    return asyncio.run(_dispatch_due_annotation_syncs())


async def _dispatch_due_annotation_syncs() -> dict[str, int]:
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as db:
            dispatch_ids = await list_due_annotation_dispatch_ids(
                db,
                limit=settings.ANNOTATION_QUEUE_DISPATCH_BATCH_SIZE,
            )
        for public_id in dispatch_ids:
            await asyncio.to_thread(enqueue_annotation_dispatch, public_id)
    finally:
        await engine.dispose()
    return {"dispatched": len(dispatch_ids)}


@celery_app.task(name="sync_annotation_dispatch", ignore_result=True)
def sync_annotation_dispatch(*, dispatch_public_id: str) -> dict[str, object]:
    return asyncio.run(_sync_annotation_dispatch(dispatch_public_id))


async def _sync_annotation_dispatch(
    dispatch_public_id: str,
) -> dict[str, object]:
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    worker_id = f"{socket.gethostname()}:{uuid.uuid4().hex[:12]}"
    try:
        async with session_factory() as db:
            lease = await lease_annotation_dispatch(
                db,
                dispatch_public_id=dispatch_public_id,
                worker_id=worker_id,
                lease_seconds=settings.ANNOTATION_QUEUE_LEASE_SECONDS,
                max_attempts=settings.ANNOTATION_QUEUE_MAX_ATTEMPTS,
            )
            await db.commit()
        if lease is None:
            return {
                "leased": False,
                "dispatch_public_id": dispatch_public_id,
            }
        try:
            async with session_factory() as db:
                request = await load_annotation_dispatch_request(
                    db,
                    dispatch_public_id=dispatch_public_id,
                    worker_id=worker_id,
                )
            client = langfuse_service.client()
            if client is None:
                raise RuntimeError("Langfuse annotation provider is disabled")
            adapter = LangfuseAnnotationQueueAdapter(client=client)
            descriptor = await adapter.get_queue(
                queue_ref=request.provider_queue_ref
            )
            if tuple(sorted(descriptor.score_config_ids)) != (
                request.expected_score_config_ids
            ):
                raise RuntimeError(
                    "Langfuse annotation queue score configs changed"
                )
            snapshots = await adapter.ensure_observations(
                queue_ref=request.provider_queue_ref,
                observation_refs=request.observation_refs,
            )
            async with session_factory() as db:
                acknowledged = await acknowledge_annotation_dispatch(
                    db,
                    dispatch_public_id=dispatch_public_id,
                    worker_id=worker_id,
                    snapshots=snapshots,
                )
                await db.commit()
        except Exception as exc:
            error_code = (
                "annotation_queue_config_changed"
                if "score configs changed" in str(exc)
                else "annotation_queue_provider_failed"
            )
            async with session_factory() as db:
                state = await fail_annotation_dispatch(
                    db,
                    dispatch_public_id=dispatch_public_id,
                    worker_id=worker_id,
                    error_code=error_code,
                    max_attempts=settings.ANNOTATION_QUEUE_MAX_ATTEMPTS,
                    base_retry_seconds=(
                        settings.ANNOTATION_QUEUE_BASE_RETRY_SECONDS
                    ),
                    max_retry_seconds=(
                        settings.ANNOTATION_QUEUE_MAX_RETRY_SECONDS
                    ),
                )
                await db.commit()
            logger.warning(
                "Annotation queue sync failed public_id=%s state=%s code=%s",
                dispatch_public_id,
                state.value if state is not None else None,
                error_code,
            )
            return {
                "leased": True,
                "completed": False,
                "status": state.value if state is not None else None,
            }
        return {
            "leased": True,
            "completed": acknowledged is not None,
            "status": (
                acknowledged.status.value
                if acknowledged is not None
                else "STALE"
            ),
        }
    finally:
        await engine.dispose()
