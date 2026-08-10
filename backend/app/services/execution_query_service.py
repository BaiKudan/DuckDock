"""Tenant-scoped, bounded management projections for AgentSession/AgentRun."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.deployment import AgentDeployment
from app.models.execution import AgentRun, AgentRunStatus, AgentSession


MAX_RUN_QUERY_WINDOW = timedelta(days=31)


class ExecutionQueryError(ValueError):
    pass


class ExecutionCursorError(ExecutionQueryError):
    pass


class ExecutionTimeWindowError(ExecutionQueryError):
    pass


@dataclass(frozen=True, slots=True)
class AgentSessionProjection:
    session: AgentSession
    deployment_public_id: str | None


@dataclass(frozen=True, slots=True)
class AgentRunProjection:
    run: AgentRun
    session_public_id: str | None
    deployment_public_id: str | None


@dataclass(frozen=True, slots=True)
class AgentRunPage:
    items: list[AgentRunProjection]
    next_cursor: str | None


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ExecutionTimeWindowError(
            "AgentRun time bounds must include a timezone"
        )
    return value.astimezone(timezone.utc)


def validate_run_time_window(
    *,
    started_after: datetime,
    started_before: datetime,
) -> tuple[datetime, datetime]:
    after = _aware_utc(started_after)
    before = _aware_utc(started_before)
    if after >= before:
        raise ExecutionTimeWindowError(
            "started_after must be earlier than started_before"
        )
    if before - after > MAX_RUN_QUERY_WINDOW:
        raise ExecutionTimeWindowError(
            "AgentRun query window cannot exceed 31 days"
        )
    return after, before


def _cursor_fingerprint(
    *,
    namespace_id: int,
    runtime_id: int | None,
    deployment_public_id: str | None,
    status: AgentRunStatus | None,
    started_after: datetime,
    started_before: datetime,
) -> str:
    payload = {
        "deployment_public_id": deployment_public_id,
        "namespace_id": namespace_id,
        "runtime_id": runtime_id,
        "started_after": started_after.isoformat(timespec="microseconds"),
        "started_before": started_before.isoformat(timespec="microseconds"),
        "status": status.value if status is not None else None,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except Exception as exc:
        raise ExecutionCursorError("AgentRun cursor is invalid") from exc


def _encode_cursor(
    *,
    run: AgentRun,
    fingerprint: str,
) -> str:
    started_at = run.started_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    payload = json.dumps(
        {
            "fingerprint": fingerprint,
            "id": run.id,
            "started_at": started_at.astimezone(timezone.utc).isoformat(
                timespec="microseconds"
            ),
            "version": 1,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    signature = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).digest()
    return f"{_b64encode(payload)}.{_b64encode(signature)}"


def _decode_cursor(
    cursor: str,
    *,
    fingerprint: str,
) -> tuple[datetime, int]:
    try:
        payload_part, signature_part = cursor.split(".", 1)
        payload = _b64decode(payload_part)
        signature = _b64decode(signature_part)
        expected_signature = hmac.new(
            settings.SECRET_KEY.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise ExecutionCursorError("AgentRun cursor is invalid")
        decoded = json.loads(payload)
        if decoded.get("version") != 1:
            raise ExecutionCursorError("AgentRun cursor version is invalid")
        if decoded.get("fingerprint") != fingerprint:
            raise ExecutionCursorError(
                "AgentRun cursor does not match the current filters"
            )
        started_at = datetime.fromisoformat(decoded["started_at"])
        started_at = _aware_utc(started_at)
        row_id = int(decoded["id"])
        if row_id < 1:
            raise ValueError("invalid id")
        return started_at, row_id
    except ExecutionCursorError:
        raise
    except Exception as exc:
        raise ExecutionCursorError("AgentRun cursor is invalid") from exc


async def get_session_projection(
    db: AsyncSession,
    session: AgentSession,
) -> AgentSessionProjection:
    deployment_public_id = None
    if session.deployment_id is not None:
        deployment_public_id = await db.scalar(
            select(AgentDeployment.public_id).where(
                AgentDeployment.id == session.deployment_id
            )
        )
    return AgentSessionProjection(
        session=session,
        deployment_public_id=deployment_public_id,
    )


def _run_projection(row) -> AgentRunProjection:
    return AgentRunProjection(
        run=row[0],
        session_public_id=row.session_public_id,
        deployment_public_id=row.deployment_public_id,
    )


def _run_projection_statement():
    return (
        select(
            AgentRun,
            AgentSession.public_id.label("session_public_id"),
            AgentDeployment.public_id.label("deployment_public_id"),
        )
        .outerjoin(AgentSession, AgentSession.id == AgentRun.session_id)
        .outerjoin(AgentDeployment, AgentDeployment.id == AgentRun.deployment_id)
    )


async def get_run_projection(
    db: AsyncSession,
    *,
    public_id: str,
    namespace_id: int | None = None,
) -> AgentRunProjection | None:
    statement = _run_projection_statement().where(
        AgentRun.public_id == public_id
    )
    if namespace_id is not None:
        statement = statement.where(AgentRun.namespace_id == namespace_id)
    row = (await db.execute(statement)).first()
    if row is None:
        return None
    return _run_projection(row)


async def list_run_projections(
    db: AsyncSession,
    *,
    namespace_id: int,
    started_after: datetime,
    started_before: datetime,
    runtime_id: int | None = None,
    deployment_public_id: str | None = None,
    status: AgentRunStatus | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> AgentRunPage:
    after, before = validate_run_time_window(
        started_after=started_after,
        started_before=started_before,
    )
    fingerprint = _cursor_fingerprint(
        namespace_id=namespace_id,
        runtime_id=runtime_id,
        deployment_public_id=deployment_public_id,
        status=status,
        started_after=after,
        started_before=before,
    )

    deployment_id = None
    if deployment_public_id is not None:
        deployment_id = await db.scalar(
            select(AgentDeployment.id).where(
                AgentDeployment.namespace_id == namespace_id,
                AgentDeployment.public_id == deployment_public_id,
            )
        )
        if deployment_id is None:
            return AgentRunPage(items=[], next_cursor=None)

    statement = _run_projection_statement().where(
        AgentRun.namespace_id == namespace_id,
        AgentRun.started_at >= after,
        AgentRun.started_at < before,
    )
    if runtime_id is not None:
        statement = statement.where(AgentRun.runtime_id == runtime_id)
    if deployment_id is not None:
        statement = statement.where(AgentRun.deployment_id == deployment_id)
    if status is not None:
        statement = statement.where(AgentRun.status == status)
    if cursor is not None:
        cursor_started_at, cursor_id = _decode_cursor(
            cursor,
            fingerprint=fingerprint,
        )
        statement = statement.where(
            or_(
                AgentRun.started_at < cursor_started_at,
                and_(
                    AgentRun.started_at == cursor_started_at,
                    AgentRun.id < cursor_id,
                ),
            )
        )

    rows = (
        await db.execute(
            statement.order_by(
                AgentRun.started_at.desc(),
                AgentRun.id.desc(),
            ).limit(limit + 1)
        )
    ).all()
    has_more = len(rows) > limit
    visible_rows = rows[:limit]
    items = [_run_projection(row) for row in visible_rows]
    next_cursor = None
    if has_more and items:
        next_cursor = _encode_cursor(
            run=items[-1].run,
            fingerprint=fingerprint,
        )
    return AgentRunPage(items=items, next_cursor=next_cursor)
