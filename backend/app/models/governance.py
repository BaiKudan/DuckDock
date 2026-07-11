from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class NamespaceGovernancePolicy(Base):
    __tablename__ = "namespace_governance_policies"
    __table_args__ = (UniqueConstraint("namespace_id", name="uq_namespace_governance_policy_namespace"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    manual_review_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    require_examples: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    require_validation_spec: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    require_sandbox_success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    clinic_gate_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    min_clinic_score: Mapped[float | None] = mapped_column(Float, default=75.0)
    clinic_max_age_hours: Mapped[int] = mapped_column(Integer, default=168, nullable=False)
    public_sharing_requires_approval: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    public_share_default_expiry_days: Mapped[int | None] = mapped_column(Integer)
    require_license_attestation: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allowed_public_licenses: Mapped[list[str] | None] = mapped_column(JSON)
    sandbox_network_mode: Mapped[str] = mapped_column(String(32), default="registry_only", nullable=False)
    sandbox_workspace_mode: Mapped[str] = mapped_column(String(32), default="ephemeral", nullable=False)
    sandbox_agent_smoke_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sandbox_agent_smoke_timeout_seconds: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    namespace: Mapped["Namespace"] = relationship("Namespace")
