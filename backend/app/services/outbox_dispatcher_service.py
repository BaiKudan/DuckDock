from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.outbox import OutboxEvent, OutboxEventStatus
from app.models.user import User
from app.services.audit_service import audit


_SAFE_WORKER_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SAFE_ERROR_CODE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")


class OutboxDispatchError(ValueError):
    pass


class OutboxNotFoundError(OutboxDispatchError):
    pass


class OutboxStateError(OutboxDispatchError):
    pass


class OutboxPublisherPort(Protocol):
    async def publish(
        self,
        *,
        event_id: str,
        event_type: str,
        schema_version: str,
        payload: dict,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class LeasedOutboxEvent:
    event_id: str
    event_type: str
    schema_version: str
    payload: dict


@dataclass(frozen=True, slots=True)
class OutboxDispatchResult:
    leased: int
    published: int
    retry_scheduled: int
    failed: int


@dataclass(frozen=True, slots=True)
class OutboxHealth:
    pending_count: int
    leased_count: int
    expired_lease_count: int
    failed_count: int
    published_count: int
    oldest_pending_age_seconds: int | None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _validate_worker_id(worker_id: str) -> str:
    if _SAFE_WORKER_ID.fullmatch(worker_id) is None:
        raise OutboxDispatchError("worker_id is invalid")
    return worker_id


def _safe_error_code(value: str | None) -> str:
    candidate = (value or "").strip().lower()
    if _SAFE_ERROR_CODE.fullmatch(candidate):
        return candidate
    return "publish_failed"


def _safe_error(value: str | None) -> str:
    # Exception strings may include tokens or response bodies. Persist only a
    # stable, content-free classification.
    if not value:
        return "Publisher rejected the event"
    return "Publisher rejected the event"


async def lease_outbox_events(
    db: AsyncSession,
    *,
    worker_id: str,
    batch_size: int,
    lease_seconds: int,
    now: datetime | None = None,
) -> list[LeasedOutboxEvent]:
    worker_id = _validate_worker_id(worker_id)
    if batch_size < 1 or batch_size > 500:
        raise OutboxDispatchError("batch_size must be between 1 and 500")
    if lease_seconds < 5 or lease_seconds > 3600:
        raise OutboxDispatchError("lease_seconds must be between 5 and 3600")
    current = now or _utcnow()
    pending_rows = list(
        (
            await db.execute(
                select(OutboxEvent)
                .where(
                    OutboxEvent.status == OutboxEventStatus.PENDING,
                    OutboxEvent.available_at <= current,
                )
                .with_for_update(skip_locked=True)
                .limit(batch_size)
            )
        ).scalars()
    )
    rows = pending_rows
    remaining = batch_size - len(rows)
    if remaining:
        expired_rows = list(
            (
                await db.execute(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.status == OutboxEventStatus.LEASED,
                        OutboxEvent.lease_expires_at <= current,
                    )
                    .with_for_update(skip_locked=True)
                    .limit(remaining)
                )
            ).scalars()
        )
        rows.extend(expired_rows)
    expires_at = current + timedelta(seconds=lease_seconds)
    for row in rows:
        row.status = OutboxEventStatus.LEASED
        row.lease_owner = worker_id
        row.lease_expires_at = expires_at
        row.attempt_count += 1
    await db.flush()
    return [
        LeasedOutboxEvent(
            event_id=row.event_id,
            event_type=row.event_type,
            schema_version=row.schema_version,
            payload=dict(row.payload_json),
        )
        for row in rows
    ]


async def acknowledge_outbox_event(
    db: AsyncSession,
    *,
    event_id: str,
    worker_id: str,
    now: datetime | None = None,
) -> bool:
    row = (
        await db.execute(
            select(OutboxEvent)
            .where(OutboxEvent.event_id == event_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        row is None
        or row.status != OutboxEventStatus.LEASED
        or row.lease_owner != worker_id
    ):
        return False
    row.status = OutboxEventStatus.PUBLISHED
    row.published_at = now or _utcnow()
    row.lease_owner = None
    row.lease_expires_at = None
    row.last_error_code = None
    row.last_error = None
    await db.flush()
    return True


async def fail_outbox_event(
    db: AsyncSession,
    *,
    event_id: str,
    worker_id: str,
    error_code: str | None,
    error: str | None,
    max_attempts: int,
    base_retry_seconds: int,
    max_retry_seconds: int,
    now: datetime | None = None,
) -> OutboxEvent | None:
    row = (
        await db.execute(
            select(OutboxEvent)
            .where(OutboxEvent.event_id == event_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        row is None
        or row.status != OutboxEventStatus.LEASED
        or row.lease_owner != worker_id
    ):
        return None
    current = now or _utcnow()
    row.last_error_code = _safe_error_code(error_code)
    row.last_error = _safe_error(error)
    row.lease_owner = None
    row.lease_expires_at = None
    if row.attempt_count >= max_attempts:
        row.status = OutboxEventStatus.FAILED
        row.available_at = current
    else:
        delay = min(
            base_retry_seconds * (2 ** max(row.attempt_count - 1, 0)),
            max_retry_seconds,
        )
        row.status = OutboxEventStatus.PENDING
        row.available_at = current + timedelta(seconds=delay)
    await db.flush()
    return row


async def retry_failed_outbox_event(
    db: AsyncSession,
    *,
    event_id: str,
    actor: User,
    reason: str,
    now: datetime | None = None,
) -> OutboxEvent:
    row = (
        await db.execute(
            select(OutboxEvent)
            .where(OutboxEvent.event_id == event_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise OutboxNotFoundError("Outbox event not found")
    if row.status != OutboxEventStatus.FAILED:
        raise OutboxStateError("Only FAILED outbox events can be retried")
    previous_attempts = row.attempt_count
    row.status = OutboxEventStatus.PENDING
    row.available_at = now or _utcnow()
    row.lease_owner = None
    row.lease_expires_at = None
    row.published_at = None
    row.attempt_count = 0
    row.last_error_code = None
    row.last_error = None
    await audit(
        db,
        user=actor,
        action="outbox_event.retried",
        resource_type="outbox_event",
        resource_id=row.id,
        namespace_id=row.namespace_id,
        details={
            "event_id": row.event_id,
            "event_type": row.event_type,
            "previous_attempts": previous_attempts,
            "reason_provided": bool(reason.strip()),
        },
    )
    await db.flush()
    return row


async def get_outbox_health(
    db: AsyncSession,
    *,
    now: datetime | None = None,
) -> OutboxHealth:
    current = now or _utcnow()

    async def count(status: OutboxEventStatus) -> int:
        return int(
            await db.scalar(
                select(func.count(OutboxEvent.id)).where(
                    OutboxEvent.status == status
                )
            )
            or 0
        )

    pending = await count(OutboxEventStatus.PENDING)
    leased = await count(OutboxEventStatus.LEASED)
    failed = await count(OutboxEventStatus.FAILED)
    published = await count(OutboxEventStatus.PUBLISHED)
    expired = int(
        await db.scalar(
            select(func.count(OutboxEvent.id)).where(
                OutboxEvent.status == OutboxEventStatus.LEASED,
                OutboxEvent.lease_expires_at <= current,
            )
        )
        or 0
    )
    oldest = await db.scalar(
        select(func.min(OutboxEvent.created_at)).where(
            OutboxEvent.status.in_(
                (OutboxEventStatus.PENDING, OutboxEventStatus.LEASED)
            )
        )
    )
    oldest_age: int | None = None
    if oldest is not None:
        if oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=timezone.utc)
        oldest_age = max(0, int((current - oldest).total_seconds()))
    return OutboxHealth(
        pending_count=pending,
        leased_count=leased,
        expired_lease_count=expired,
        failed_count=failed,
        published_count=published,
        oldest_pending_age_seconds=oldest_age,
    )


async def dispatch_outbox_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    publisher: OutboxPublisherPort,
    worker_id: str,
    batch_size: int,
    lease_seconds: int,
    max_attempts: int,
    base_retry_seconds: int,
    max_retry_seconds: int,
) -> OutboxDispatchResult:
    async with session_factory() as db:
        leased = await lease_outbox_events(
            db,
            worker_id=worker_id,
            batch_size=batch_size,
            lease_seconds=lease_seconds,
        )
        await db.commit()

    published = 0
    retry_scheduled = 0
    failed = 0
    for event in leased:
        try:
            await publisher.publish(
                event_id=event.event_id,
                event_type=event.event_type,
                schema_version=event.schema_version,
                payload=event.payload,
            )
        except Exception as exc:
            async with session_factory() as db:
                row = await fail_outbox_event(
                    db,
                    event_id=event.event_id,
                    worker_id=worker_id,
                    error_code=exc.__class__.__name__.lower(),
                    error=None,
                    max_attempts=max_attempts,
                    base_retry_seconds=base_retry_seconds,
                    max_retry_seconds=max_retry_seconds,
                )
                await db.commit()
                if row is not None and row.status == OutboxEventStatus.FAILED:
                    failed += 1
                elif row is not None:
                    retry_scheduled += 1
        else:
            async with session_factory() as db:
                if await acknowledge_outbox_event(
                    db,
                    event_id=event.event_id,
                    worker_id=worker_id,
                ):
                    published += 1
                await db.commit()
    return OutboxDispatchResult(
        leased=len(leased),
        published=published,
        retry_scheduled=retry_scheduled,
        failed=failed,
    )
