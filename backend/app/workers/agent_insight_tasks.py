import asyncio

from app.services.agent_overview_service import run_agent_insight_job
from app.workers.celery_app import celery_app
from app.workers.db import worker_db_session


@celery_app.task(name="run_agent_insight_job", bind=True, max_retries=1)
def run_agent_insight_job_task(self, job_id: int):
    try:
        asyncio.run(_async_run(job_id))
    except Exception as exc:
        raise self.retry(exc=exc, countdown=10)


async def _async_run(job_id: int) -> None:
    async with worker_db_session() as db:
        await run_agent_insight_job(db, job_id=job_id)
        await db.commit()
