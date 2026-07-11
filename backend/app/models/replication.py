import enum
from datetime import datetime, timezone
from sqlalchemy import String, Enum, DateTime, Integer, ForeignKey, JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class ReplicationTrigger(str, enum.Enum):
    MANUAL = "manual"
    ON_PUBLISH = "on_publish"


class ReplicationJobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ReplicationRule(Base):
    __tablename__ = "replication_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    src_namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="CASCADE"), nullable=False
    )
    dst_namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="CASCADE"), nullable=False
    )
    filter_pattern: Mapped[str | None] = mapped_column(String(256))  # glob, e.g. "code-*"
    trigger: Mapped[ReplicationTrigger] = mapped_column(
        Enum(ReplicationTrigger), default=ReplicationTrigger.MANUAL
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    jobs: Mapped[list["ReplicationJob"]] = relationship(
        "ReplicationJob", back_populates="rule", cascade="all, delete-orphan"
    )


class ReplicationJob(Base):
    __tablename__ = "replication_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("replication_rules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[ReplicationJobStatus] = mapped_column(
        Enum(ReplicationJobStatus), default=ReplicationJobStatus.PENDING
    )
    skills_copied: Mapped[int] = mapped_column(Integer, default=0)
    skills_skipped: Mapped[int] = mapped_column(Integer, default=0)
    skills_failed: Mapped[int] = mapped_column(Integer, default=0)
    log: Mapped[list | None] = mapped_column(JSON)             # list of log strings
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(String(512))
    triggered_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    rule: Mapped["ReplicationRule"] = relationship("ReplicationRule", back_populates="jobs")
