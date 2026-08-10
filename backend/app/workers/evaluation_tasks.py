from __future__ import annotations

import asyncio
import logging
import socket
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.adapters.evaluation.langfuse import (
    build_langfuse_experiment_runner,
)
from app.adapters.evaluation.replay import DatasetReplayTargetAdapter
from app.core.config import settings
from app.services.evaluation_execution_service import (
    acknowledge_evaluation,
    fail_evaluation,
    lease_evaluation,
    list_due_evaluation_ids,
    load_evaluation_run_request,
    renew_evaluation_lease,
)
from app.services.langfuse_service import langfuse_service
from app.workers.celery_app import celery_app


logger = logging.getLogger(__name__)


def enqueue_evaluation(evaluation_public_id: str) -> None:
    celery_app.send_task(
        "run_evaluation",
        kwargs={"evaluation_public_id": evaluation_public_id},
        task_id=f"evaluation:{evaluation_public_id}",
    )


@celery_app.task(name="dispatch_due_evaluations", ignore_result=True)
def dispatch_due_evaluations() -> dict[str, int]:
    return asyncio.run(_dispatch_due_evaluations())


async def _dispatch_due_evaluations() -> dict[str, int]:
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as db:
            evaluation_ids = await list_due_evaluation_ids(
                db,
                limit=settings.EVALUATION_DISPATCH_BATCH_SIZE,
            )
        for public_id in evaluation_ids:
            await asyncio.to_thread(enqueue_evaluation, public_id)
    finally:
        await engine.dispose()
    return {"dispatched": len(evaluation_ids)}


@celery_app.task(name="run_evaluation", ignore_result=True)
def run_evaluation(*, evaluation_public_id: str) -> dict[str, object]:
    return asyncio.run(_run_evaluation(evaluation_public_id))


async def _heartbeat(
    session_factory,
    *,
    evaluation_public_id: str,
    worker_id: str,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(
                stop.wait(),
                timeout=settings.EVALUATION_HEARTBEAT_SECONDS,
            )
            return
        except asyncio.TimeoutError:
            pass
        async with session_factory() as db:
            renewed = await renew_evaluation_lease(
                db,
                evaluation_public_id=evaluation_public_id,
                worker_id=worker_id,
                lease_seconds=settings.EVALUATION_LEASE_SECONDS,
            )
            await db.commit()
        if not renewed:
            return


def _runner_error_code(exc: Exception) -> str:
    name = exc.__class__.__name__.lower()
    if "configuration" in name:
        return "evaluation_configuration_error"
    if "langfuse" in name:
        return "langfuse_experiment_failed"
    if "deepeval" in name:
        return "deepeval_execution_failed"
    return "evaluation_runner_failed"


async def _run_evaluation(
    evaluation_public_id: str,
) -> dict[str, object]:
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    worker_id = f"{socket.gethostname()}:{uuid.uuid4().hex[:12]}"
    stop = asyncio.Event()
    heartbeat_task = None
    try:
        async with session_factory() as db:
            lease = await lease_evaluation(
                db,
                evaluation_public_id=evaluation_public_id,
                worker_id=worker_id,
                lease_seconds=settings.EVALUATION_LEASE_SECONDS,
                max_attempts=settings.EVALUATION_MAX_ATTEMPTS,
            )
            await db.commit()
        if lease is None:
            return {"leased": False, "evaluation_public_id": evaluation_public_id}

        heartbeat_task = asyncio.create_task(
            _heartbeat(
                session_factory,
                evaluation_public_id=evaluation_public_id,
                worker_id=worker_id,
                stop=stop,
            )
        )
        try:
            async with session_factory() as db:
                request = await load_evaluation_run_request(
                    db,
                    evaluation_public_id=evaluation_public_id,
                    worker_id=worker_id,
                )
            client = langfuse_service.client()
            if client is None:
                raise RuntimeError("Langfuse evaluation provider is disabled")
            runner = build_langfuse_experiment_runner(
                compatibility_profile=(
                    settings.LANGFUSE_COMPATIBILITY_PROFILE
                ),
                client=client,
                target_adapter=DatasetReplayTargetAdapter(),
                max_concurrency=settings.EVALUATION_MAX_CONCURRENCY,
            )
            summary = await runner.run(request=request)
            async with session_factory() as db:
                acknowledged_status = await acknowledge_evaluation(
                    db,
                    evaluation_public_id=evaluation_public_id,
                    worker_id=worker_id,
                    summary=summary,
                )
                await db.commit()
        except Exception as exc:
            async with session_factory() as db:
                state = await fail_evaluation(
                    db,
                    evaluation_public_id=evaluation_public_id,
                    worker_id=worker_id,
                    error_code=_runner_error_code(exc),
                    max_attempts=settings.EVALUATION_MAX_ATTEMPTS,
                    base_retry_seconds=settings.EVALUATION_BASE_RETRY_SECONDS,
                    max_retry_seconds=settings.EVALUATION_MAX_RETRY_SECONDS,
                )
                await db.commit()
            logger.warning(
                "Evaluation execution failed public_id=%s state=%s code=%s",
                evaluation_public_id,
                state.value if state is not None else None,
                _runner_error_code(exc),
            )
            return {
                "leased": True,
                "completed": False,
                "status": state.value if state is not None else None,
            }
        else:
            return {
                "leased": True,
                "completed": acknowledged_status is not None,
                "status": (
                    acknowledged_status.value
                    if acknowledged_status is not None
                    else "STALE"
                ),
            }
    finally:
        stop.set()
        if heartbeat_task is not None:
            heartbeat_result = await asyncio.gather(
                heartbeat_task,
                return_exceptions=True,
            )
            if isinstance(heartbeat_result[0], BaseException):
                logger.warning(
                    "Evaluation heartbeat failed public_id=%s error=%s",
                    evaluation_public_id,
                    heartbeat_result[0].__class__.__name__,
                )
        await engine.dispose()
