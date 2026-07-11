"""OPS-08 + OPS-05: celery reliability config and task-logging correlation context."""
from __future__ import annotations

from app.workers.celery_app import (
    BROKER_VISIBILITY_TIMEOUT,
    _bind_task_log_context,
    celery_app,
)
from app.core.logging import request_id_var


# ---------------------------------------------------------------------------
# OPS-08: tasks must ack-late, requeue on lost workers, and have a visibility timeout.
# ---------------------------------------------------------------------------


def test_task_acks_late_is_enabled():
    assert celery_app.conf.task_acks_late is True


def test_task_reject_on_worker_lost_is_enabled():
    assert celery_app.conf.task_reject_on_worker_lost is True


def test_broker_visibility_timeout_is_set():
    transport_options = celery_app.conf.broker_transport_options
    assert isinstance(transport_options, dict)
    assert transport_options.get("visibility_timeout") == BROKER_VISIBILITY_TIMEOUT
    assert BROKER_VISIBILITY_TIMEOUT > 0


def test_worker_soft_shutdown_window_is_set():
    assert celery_app.conf.worker_soft_shutdown_timeout == 60
    assert celery_app.conf.worker_enable_soft_shutdown_on_idle is True


# ---------------------------------------------------------------------------
# OPS-05: task prerun signal binds report_id (then request_id, then task_id) to log context.
# ---------------------------------------------------------------------------


def test_task_context_prefers_report_id():
    token = request_id_var.set(None)
    try:
        _bind_task_log_context(task_id="abc", kwargs={"report_id": "rep-7"})
        assert request_id_var.get() == "rep-7"
    finally:
        request_id_var.reset(token)


def test_task_context_falls_back_to_task_id():
    token = request_id_var.set(None)
    try:
        _bind_task_log_context(task_id="task-99", kwargs={})
        assert request_id_var.get() == "task-99"
    finally:
        request_id_var.reset(token)
