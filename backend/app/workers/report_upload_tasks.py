import asyncio

from app.services.report_upload_service import ingest_report_upload_session
from app.workers.celery_app import celery_app
from app.workers.db import worker_db_session


@celery_app.task(name="ingest_report_upload_session", bind=True, max_retries=2)
def ingest_report_upload_session_task(self, report_id: str):
    try:
        asyncio.run(_async_ingest(report_id))
    except Exception as exc:
        raise self.retry(exc=exc, countdown=30)


async def _async_ingest(report_id: str):
    async with worker_db_session() as db:
        await ingest_report_upload_session(db, report_id=report_id)
        await db.commit()
