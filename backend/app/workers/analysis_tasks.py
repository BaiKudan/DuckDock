import asyncio

from app.services.analysis_service import reap_expired_analysis_jobs
from app.workers.celery_app import celery_app
from app.workers.db import worker_db_session


@celery_app.task(name="reap_expired_analysis_jobs")
def reap_expired_analysis_jobs_task():
    asyncio.run(_async_reap_expired_analysis_jobs())


async def _async_reap_expired_analysis_jobs():
    async with worker_db_session() as db:
        await reap_expired_analysis_jobs(db)
        await db.commit()
