from datetime import datetime, timezone
from sqlalchemy import DateTime, Integer, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class RetentionPolicy(Base):
    """Per-namespace retention rules for skill versions."""
    __tablename__ = "retention_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    keep_last_n: Mapped[int | None] = mapped_column(Integer)    # keep latest N versions per skill
    keep_days: Mapped[int | None] = mapped_column(Integer)      # keep versions newer than N days
    delete_rejected: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )


class NamespaceQuota(Base):
    """Storage + count limits per namespace."""
    __tablename__ = "namespace_quotas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    max_skills: Mapped[int] = mapped_column(Integer, default=100)
    max_versions_per_skill: Mapped[int] = mapped_column(Integer, default=50)
    max_total_versions: Mapped[int] = mapped_column(Integer, default=2000)
    max_storage_bytes: Mapped[int] = mapped_column(Integer, default=536870912)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
