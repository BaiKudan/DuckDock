"""OPS-05: structured JSON logging + request-id propagation.

Provides a lightweight ``logging.dictConfig`` that emits one JSON object per log line (no extra
dependencies — a small stdlib :class:`logging.Formatter` subclass does the encoding). A
``contextvars``-backed request id is woven into every record so a single request (HTTP) or a
single Celery task (``report_id``) can be traced across log lines.

Kept deliberately small: it does not replace handlers added elsewhere, and re-running
``configure_logging`` is idempotent (``dictConfig`` with ``disable_existing_loggers=False``).
"""
from __future__ import annotations

import contextvars
import datetime as _dt
import json
import logging
import logging.config
from typing import Any

# Request/task correlation id, propagated via contextvars so it is visible to every log record
# emitted while handling a request or running a task.
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "duckdock_request_id", default=None
)

# Reserved LogRecord attributes we never want to duplicate into the JSON "extra" bag.
_RESERVED_RECORD_KEYS = frozenset(
    vars(logging.makeLogRecord({})).keys()
    | {"message", "asctime", "request_id"}
)


def get_request_id() -> str | None:
    """Return the current request/task correlation id, if any."""
    return request_id_var.get()


def set_request_id(value: str | None) -> contextvars.Token:
    """Bind ``value`` as the current request/task correlation id; returns a reset token."""
    return request_id_var.set(value)


class RequestIdFilter(logging.Filter):
    """Stamp every record with the active request id (or ``-`` when unset)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "request_id", None):
            record.request_id = request_id_var.get() or "-"
        return True


class JsonFormatter(logging.Formatter):
    """Render a :class:`logging.LogRecord` as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": _dt.datetime.fromtimestamp(
                record.created, tz=_dt.timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None) or "-",
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        # Fold any structured `extra={...}` fields (e.g. report_id, task) into the payload.
        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_KEYS or key.startswith("_"):
                continue
            payload.setdefault(key, value)
        return json.dumps(payload, default=str, ensure_ascii=False)


def build_logging_config(level: str = "INFO") -> dict[str, Any]:
    """Return a ``logging.dictConfig`` dict emitting JSON lines stamped with the request id."""
    normalized = (level or "INFO").upper()
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "request_id": {
                "()": "app.core.logging.RequestIdFilter",
            },
        },
        "formatters": {
            "json": {
                "()": "app.core.logging.JsonFormatter",
            },
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "json",
                "filters": ["request_id"],
            },
        },
        "root": {
            "handlers": ["default"],
            "level": normalized,
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": normalized, "propagate": False},
            "uvicorn.error": {"handlers": ["default"], "level": normalized, "propagate": False},
            "uvicorn.access": {"handlers": ["default"], "level": normalized, "propagate": False},
        },
    }


def configure_logging(level: str = "INFO") -> None:
    """Apply the structured JSON logging config. Idempotent and safe to call at startup."""
    logging.config.dictConfig(build_logging_config(level))
