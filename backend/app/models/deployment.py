from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.package_registry import AgentPackageVersion
    from app.models.control_plane import AIAsset, RuntimeInstance
    from app.models.skill import SkillVersion
    from app.models.user import User


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentDeploymentStatus(str, enum.Enum):
    REGISTERED = "REGISTERED"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"
    FAILED = "FAILED"


class DeploymentComponentRole(str, enum.Enum):
    AGENT = "agent"
    SKILL = "skill"
    MODEL = "model"
    TOOL = "tool"
    POLICY = "policy"
    MEMORY = "memory"
    OTHER = "other"


class AgentDeployment(Base):
    """One governed and version-pinned Agent deployment revision."""

    __tablename__ = "agent_deployments"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_agent_deployments_public_id"),
        UniqueConstraint(
            "namespace_id",
            "runtime_id",
            "external_deployment_id",
            "revision",
            name="uq_agent_deployments_tenant_runtime_external_revision",
        ),
        CheckConstraint(
            "("
            "status = 'REGISTERED' AND activated_at IS NULL AND retired_at IS NULL"
            ") OR ("
            "status = 'ACTIVE' AND activated_at IS NOT NULL AND retired_at IS NULL"
            ") OR ("
            "status = 'RETIRED' AND activated_at IS NOT NULL AND retired_at IS NOT NULL"
            ") OR ("
            "status = 'FAILED' AND activated_at IS NULL AND retired_at IS NULL"
            ")",
            name="ck_agent_deployments_status_timestamps",
        ),
        Index(
            "ix_agent_deployments_namespace_env_status_created",
            "namespace_id",
            "environment",
            "status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    runtime_id: Mapped[int] = mapped_column(
        ForeignKey("runtime_instances.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    agent_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_assets.id", ondelete="RESTRICT"),
        index=True,
    )
    package_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_package_versions.id", ondelete="RESTRICT"),
        index=True,
    )
    external_deployment_id: Mapped[str] = mapped_column(String(255), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    revision: Mapped[str] = mapped_column(String(128), nullable=False)
    configuration_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[AgentDeploymentStatus] = mapped_column(
        Enum(AgentDeploymentStatus, name="agent_deployment_status"),
        default=AgentDeploymentStatus.REGISTERED,
        nullable=False,
        index=True,
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    components: Mapped[list["DeploymentComponent"]] = relationship(
        "DeploymentComponent",
        back_populates="deployment",
        cascade="all, delete-orphan",
        order_by="DeploymentComponent.id",
        lazy="selectin",
    )
    runtime: Mapped["RuntimeInstance"] = relationship("RuntimeInstance")
    agent_asset: Mapped["AIAsset | None"] = relationship("AIAsset")
    package_version: Mapped["AgentPackageVersion | None"] = relationship(lazy="selectin")
    created_by_user: Mapped["User"] = relationship("User")

    @property
    def package_version_public_id(self) -> str | None:
        return self.package_version.public_id if self.package_version is not None else None


class DeploymentComponent(Base):
    """A version-pinned, non-secret component in a deployment snapshot."""

    __tablename__ = "deployment_components"
    __table_args__ = (
        UniqueConstraint(
            "deployment_id",
            "component_key",
            name="uq_deployment_components_deployment_key",
        ),
        CheckConstraint(
            "ai_asset_id IS NOT NULL OR "
            "skill_version_id IS NOT NULL OR "
            "external_version IS NOT NULL OR "
            "content_digest IS NOT NULL",
            name="ck_deployment_components_version_identity",
        ),
        Index(
            "ix_deployment_components_deployment_role",
            "deployment_id",
            "component_role",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deployment_id: Mapped[int] = mapped_column(
        ForeignKey("agent_deployments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    component_key: Mapped[str] = mapped_column(String(128), nullable=False)
    component_role: Mapped[DeploymentComponentRole] = mapped_column(
        Enum(DeploymentComponentRole, name="deployment_component_role"),
        nullable=False,
        index=True,
    )
    ai_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_assets.id", ondelete="RESTRICT"),
        index=True,
    )
    skill_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("skill_versions.id", ondelete="RESTRICT"),
        index=True,
    )
    external_version: Mapped[str | None] = mapped_column(String(255))
    content_digest: Mapped[str | None] = mapped_column(String(64))
    configuration_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    deployment: Mapped[AgentDeployment] = relationship(
        "AgentDeployment",
        back_populates="components",
    )
    ai_asset: Mapped["AIAsset | None"] = relationship("AIAsset")
    skill_version: Mapped["SkillVersion | None"] = relationship("SkillVersion")
