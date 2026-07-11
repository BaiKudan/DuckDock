import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SyncEventOperation(str, enum.Enum):
    UPSERT = "upsert"
    STATE = "state"
    TOMBSTONE = "tombstone"


class SyncEventState(str, enum.Enum):
    HIDDEN = "hidden"
    PENDING = "pending"
    LIVE = "live"
    TOMBSTONE = "tombstone"


class SyncEventEntity(str, enum.Enum):
    SKILL = "skill"
    VERSION = "version"


class RegistrySyncEvent(Base):
    __tablename__ = "registry_sync_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    namespace_id: Mapped[int | None] = mapped_column(
        ForeignKey("namespaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    namespace_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    skill_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    tag: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    entity: Mapped[SyncEventEntity] = mapped_column(String(32), nullable=False)
    operation: Mapped[SyncEventOperation] = mapped_column(String(32), nullable=False, index=True)
    sync_state: Mapped[SyncEventState] = mapped_column(String(32), nullable=False, index=True)
    event: Mapped[str] = mapped_column(String(96), nullable=False)
    is_public: Mapped[bool] = mapped_column(default=False, nullable=False, index=True)
    version_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(96), nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
