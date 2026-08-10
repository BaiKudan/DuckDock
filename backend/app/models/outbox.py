from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OutboxEventStatus(str, enum.Enum):
    PENDING = "PENDING"
    LEASED = "LEASED"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_outbox_events_event_id"),
        UniqueConstraint(
            "idempotency_key",
            name="uq_outbox_events_idempotency_key",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_outbox_events_nonnegative_attempts",
        ),
        CheckConstraint(
            "("
            "status = 'LEASED' AND lease_owner IS NOT NULL "
            "AND lease_expires_at IS NOT NULL"
            ") OR ("
            "status <> 'LEASED' AND lease_owner IS NULL "
            "AND lease_expires_at IS NULL"
            ")",
            name="ck_outbox_events_lease_state",
        ),
        CheckConstraint(
            "("
            "status = 'PUBLISHED' AND published_at IS NOT NULL"
            ") OR ("
            "status <> 'PUBLISHED' AND published_at IS NULL"
            ")",
            name="ck_outbox_events_published_state",
        ),
        Index(
            "ix_outbox_events_dispatch",
            "status",
            "available_at",
            "lease_expires_at",
        ),
        Index(
            "ix_outbox_events_expired_lease",
            "status",
            "lease_expires_at",
            "id",
        ),
        Index(
            "ix_outbox_events_namespace_status_created",
            "namespace_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_public_id: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[OutboxEventStatus] = mapped_column(
        Enum(OutboxEventStatus, name="outbox_event_status"),
        default=OutboxEventStatus.PENDING,
        nullable=False,
        index=True,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )
