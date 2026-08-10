import os

from celery import Celery
from celery.signals import task_prerun, worker_process_init
from celery.utils.log import get_task_logger

from app.core.config import settings
from app.core.logging import configure_logging, set_request_id

# OPS-08: visibility timeout (seconds) for re-delivering tasks that an unacked worker dropped.
# Reuse the analysis job-lease horizon so a reaped lease and a redelivered task agree.
BROKER_VISIBILITY_TIMEOUT = settings.ANALYSIS_JOB_LEASE_SECONDS

celery_app = Celery(
    "duckdock",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.workers.analysis_tasks",
        "app.workers.scan_tasks",
        "app.workers.clinic_tasks",
        "app.workers.lifecycle_tasks",
        "app.workers.webhook_tasks",
        "app.workers.report_upload_tasks",
        "app.workers.agent_insight_tasks",
        "app.workers.outbox_dispatcher",
        "app.workers.evaluation_tasks",
        "app.workers.annotation_tasks",
        "app.workers.semantic_monitor_tasks",
    ],
)

celery_settings = dict(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    # L4-06: give workers a bounded soft-shutdown window before a container stop turns hard.
    worker_soft_shutdown_timeout=60,
    worker_enable_soft_shutdown_on_idle=True,
    # OPS-08: ack a task only after it finishes, and requeue tasks orphaned by a lost worker.
    # Safe because PUSH/ingest tasks are idempotent, so a redelivery cannot double-apply.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    broker_transport_options={"visibility_timeout": BROKER_VISIBILITY_TIMEOUT},
    beat_schedule={
        "reap-expired-analysis-jobs": {
            "task": "reap_expired_analysis_jobs",
            "schedule": 60.0,
        },
        # L0-OPS-RETENTION-BEAT: periodically apply retention policies so GC isn't
        # left to manual admin POSTs. Period configurable via RETENTION_GC_SCHEDULE_SECONDS.
        "run-retention-gc": {
            "task": "run_retention_gc",
            "schedule": settings.RETENTION_GC_SCHEDULE_SECONDS,
        },
        "dispatch-transactional-outbox": {
            "task": "dispatch_outbox",
            "schedule": settings.OUTBOX_DISPATCH_SCHEDULE_SECONDS,
        },
        "dispatch-due-evaluations": {
            "task": "dispatch_due_evaluations",
            "schedule": settings.EVALUATION_DISPATCH_SCHEDULE_SECONDS,
        },
        "dispatch-due-annotation-syncs": {
            "task": "dispatch_due_annotation_syncs",
            "schedule": settings.ANNOTATION_QUEUE_DISPATCH_SCHEDULE_SECONDS,
        },
        "dispatch-due-semantic-monitors": {
            "task": "dispatch_due_semantic_monitors",
            "schedule": settings.SEMANTIC_MONITOR_DISPATCH_SCHEDULE_SECONDS,
        },
    },
)
if os.name == "nt":
    celery_settings["worker_pool"] = "solo"

celery_app.conf.update(**celery_settings)

task_logger = get_task_logger(__name__)


@worker_process_init.connect
def _init_worker_logging(**_kwargs) -> None:
    """OPS-05: emit structured JSON logs from worker processes too."""
    configure_logging(settings.LOG_LEVEL)


@task_prerun.connect
def _bind_task_log_context(task_id=None, task=None, args=None, kwargs=None, **_extra) -> None:
    """OPS-05: weave the task's report_id (and request id where available) into log context.

    Many ingest/analysis tasks are keyed by ``report_id``; binding it (falling back to the
    celery ``task_id``) lets every log line for a task be correlated downstream.
    """
    kwargs = kwargs or {}
    correlation = (
        kwargs.get("report_id")
        or kwargs.get("request_id")
        or task_id
    )
    if correlation is not None:
        set_request_id(str(correlation))
