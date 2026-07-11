import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class SandboxValidationStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class SandboxValidationRun(Base):
    __tablename__ = "sandbox_validation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("skill_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        unique=True,
    )
    status: Mapped[SandboxValidationStatus] = mapped_column(
        Enum(SandboxValidationStatus),
        default=SandboxValidationStatus.PENDING,
        nullable=False,
        index=True,
    )
    engine: Mapped[str] = mapped_column(String(64), default="openclaw-docker", nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    checks: Mapped[list | None] = mapped_column(JSON)
    logs: Mapped[list | None] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    version: Mapped["SkillVersion"] = relationship("SkillVersion", back_populates="sandbox_validation")
