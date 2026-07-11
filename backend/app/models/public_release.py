from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class PublicReleaseApprovalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PublicSkillRelease(Base):
    __tablename__ = "public_skill_releases"
    __table_args__ = (UniqueConstraint("version_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("skill_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    shared_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approval_status: Mapped[PublicReleaseApprovalStatus] = mapped_column(
        Enum(PublicReleaseApprovalStatus),
        default=PublicReleaseApprovalStatus.APPROVED,
        nullable=False,
        index=True,
    )
    approval_notes: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    license_name: Mapped[str | None] = mapped_column(String(128))
    license_attested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    license_attested_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    license_attested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    risk_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    risk_acknowledged_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    risk_acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    version: Mapped["SkillVersion"] = relationship("SkillVersion", back_populates="public_release")
