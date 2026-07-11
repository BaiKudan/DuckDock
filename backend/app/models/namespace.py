import enum
from datetime import datetime, timezone

from sqlalchemy import String, Text, Enum, DateTime, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class NamespaceRole(str, enum.Enum):
    ADMIN = "admin"
    DEVELOPER = "developer"
    READONLY = "readonly"


class Namespace(Base):
    __tablename__ = "namespaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    members: Mapped[list["NamespaceMember"]] = relationship(
        "NamespaceMember", back_populates="namespace", cascade="all, delete-orphan"
    )
    skills: Mapped[list["Skill"]] = relationship(
        "Skill", back_populates="namespace", cascade="all, delete-orphan"
    )


class NamespaceMember(Base):
    __tablename__ = "namespace_members"
    __table_args__ = (UniqueConstraint("namespace_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    namespace_id: Mapped[int] = mapped_column(ForeignKey("namespaces.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[NamespaceRole] = mapped_column(
        Enum(NamespaceRole), default=NamespaceRole.DEVELOPER, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    namespace: Mapped["Namespace"] = relationship("Namespace", back_populates="members")
    user: Mapped["User"] = relationship("User", back_populates="namespace_memberships")
