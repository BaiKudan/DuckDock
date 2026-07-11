from datetime import datetime, timezone
from sqlalchemy import String, Boolean, DateTime, Integer, ForeignKey, Enum
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.models.namespace import NamespaceRole


class RobotAccount(Base):
    __tablename__ = "robot_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512))
    token_hash: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)  # first 8 chars for display
    role: Mapped[NamespaceRole] = mapped_column(
        Enum(NamespaceRole), default=NamespaceRole.READONLY, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
