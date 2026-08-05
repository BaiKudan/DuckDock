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
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ReconciliationStatus(str, enum.Enum):
    MATCHED = "MATCHED"
    EXPECTED_LEGACY_ONLY = "EXPECTED_LEGACY_ONLY"
    MISMATCH = "MISMATCH"


class OutboxConsumerReceipt(Base):
    __tablename__ = "outbox_consumer_receipts"
    __table_args__ = (
        UniqueConstraint(
            "consumer_name",
            "event_id",
            name="uq_outbox_consumer_receipts_consumer_event",
        ),
        Index(
            "ix_outbox_consumer_receipts_consumer_processed",
            "consumer_name",
            "processed_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    consumer_name: Mapped[str] = mapped_column(String(100), nullable=False)
    event_id: Mapped[str] = mapped_column(
        ForeignKey(
            "outbox_events.event_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    result_code: Mapped[str] = mapped_column(String(100), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )


class V1V2Reconciliation(Base):
    __tablename__ = "v1_v2_reconciliations"
    __table_args__ = (
        UniqueConstraint(
            "work_trace_id",
            name="uq_v1_v2_reconciliations_work_trace",
        ),
        CheckConstraint(
            "linked_run_count >= 0 AND linked_artifact_count >= 0",
            name="ck_v1_v2_reconciliations_nonnegative_counts",
        ),
        Index(
            "ix_v1_v2_reconciliations_status_checked",
            "status",
            "checked_at",
        ),
        Index(
            "ix_v1_v2_reconciliations_namespace_status",
            "namespace_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    runtime_id: Mapped[int] = mapped_column(
        ForeignKey("runtime_instances.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    work_trace_id: Mapped[int] = mapped_column(
        ForeignKey("work_traces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_report_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
    )
    status: Mapped[ReconciliationStatus] = mapped_column(
        Enum(
            ReconciliationStatus,
            name="v1_v2_reconciliation_status",
        ),
        nullable=False,
        index=True,
    )
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    linked_run_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    linked_artifact_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    last_event_id: Mapped[str] = mapped_column(
        ForeignKey(
            "outbox_events.event_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
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
