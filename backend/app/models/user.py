import enum
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import String, Boolean, Enum, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.namespace import NamespaceMember


class SystemRole(str, enum.Enum):
    ADMIN = "admin"
    USER = "user"


class AuthSource(str, enum.Enum):
    LOCAL = "local"
    OIDC = "oidc"
    LDAP = "ldap"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    full_name: Mapped[str | None] = mapped_column(String(255))
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    system_role: Mapped[SystemRole] = mapped_column(
        Enum(SystemRole), default=SystemRole.USER, nullable=False
    )
    auth_source: Mapped[AuthSource] = mapped_column(
        Enum(AuthSource), default=AuthSource.LOCAL, nullable=False
    )
    enterprise_uid: Mapped[str | None] = mapped_column(String(96), unique=True, index=True)
    external_subject: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    namespace_memberships: Mapped[list["NamespaceMember"]] = relationship(
        "NamespaceMember", back_populates="user", cascade="all, delete-orphan"
    )
