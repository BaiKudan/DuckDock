from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ScannerRuleSuppression(Base):
    """Namespace-scoped suppression of scanner rules.

    Issues matching rule_id (and optionally file_pattern, a glob) are dropped
    before scan aggregation — they no longer count toward severity totals or
    gate a release. file_pattern="*" means "every file".
    """

    __tablename__ = "scanner_rule_suppressions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    file_pattern: Mapped[str] = mapped_column(String(255), nullable=False, default="*")
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "namespace_id", "rule_id", "file_pattern", name="uq_scanner_suppression"
        ),
    )
