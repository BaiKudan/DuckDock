from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class SkillVersionStatus(str, enum.Enum):
    QUARANTINE = "quarantine"
    SCANNING = "scanning"
    REVIEW = "review"
    PRODUCTION = "production"
    REJECTED = "rejected"


class SkillVersionReviewStatus(str, enum.Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    namespace_id: Mapped[int] = mapped_column(ForeignKey("namespaces.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    git_repo_path: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    namespace: Mapped["Namespace"] = relationship("Namespace", back_populates="skills")
    versions: Mapped[list["SkillVersion"]] = relationship(
        "SkillVersion",
        back_populates="skill",
        cascade="all, delete-orphan",
    )


class SkillVersion(Base):
    __tablename__ = "skill_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id"), nullable=False)
    tag: Mapped[str] = mapped_column(String(64), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[SkillVersionStatus] = mapped_column(
        Enum(SkillVersionStatus),
        default=SkillVersionStatus.QUARANTINE,
        nullable=False,
        index=True,
    )
    review_status: Mapped[SkillVersionReviewStatus] = mapped_column(
        Enum(SkillVersionReviewStatus),
        default=SkillVersionReviewStatus.NOT_REQUIRED,
        nullable=False,
        index=True,
    )
    review_required: Mapped[bool] = mapped_column(default=False, nullable=False)
    review_notes: Mapped[str | None] = mapped_column(Text)
    review_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    gate_result: Mapped[dict | None] = mapped_column(JSON)
    skill_metadata: Mapped[dict | None] = mapped_column(JSON)
    changelog: Mapped[str | None] = mapped_column(Text)
    publish_tags: Mapped[list[str] | None] = mapped_column(JSON)
    content_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    published_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    skill: Mapped["Skill"] = relationship("Skill", back_populates="versions")
    public_release: Mapped["PublicSkillRelease | None"] = relationship(
        "PublicSkillRelease",
        back_populates="version",
        cascade="all, delete-orphan",
        uselist=False,
    )
    sandbox_validation: Mapped["SandboxValidationRun | None"] = relationship(
        "SandboxValidationRun",
        cascade="all, delete-orphan",
        uselist=False,
        back_populates="version",
    )
