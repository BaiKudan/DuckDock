from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Integer, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    username: Mapped[str | None] = mapped_column(String(64))       # denormalized for history
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # e.g. "skill.published", "scan.triggered", "member.added", "webhook.fired"
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[int | None] = mapped_column(Integer)
    namespace_id: Mapped[int | None] = mapped_column(
        ForeignKey("namespaces.id", ondelete="SET NULL"), index=True
    )
    details: Mapped[dict | None] = mapped_column(JSON)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
