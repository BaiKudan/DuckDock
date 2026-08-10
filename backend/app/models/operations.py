from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _public_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class OpsSLOEvaluationStatus(str, enum.Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    BREACHED = "BREACHED"


class OpsIncidentSeverity(str, enum.Enum):
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class OpsIncidentStatus(str, enum.Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class OpsRecoveryDrillStatus(str, enum.Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"


class OpsSLOEvaluation(Base):
    __tablename__ = "ops_slo_evaluations"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_ops_slo_evaluations_public_id"),
        UniqueConstraint("idempotency_key", name="uq_ops_slo_evaluations_idempotency"),
        Index("ix_ops_slo_evaluations_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(40), default=lambda: _public_id("oslo"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    profile_version: Mapped[str] = mapped_column(String(64), nullable=False)
    window_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False)
    http_error_ratio: Mapped[float | None] = mapped_column(Float)
    evidence_ingest_p95_ms: Mapped[float | None] = mapped_column(Float)
    run_timeline_p95_ms: Mapped[float | None] = mapped_column(Float)
    policy_decision_p95_ms: Mapped[float | None] = mapped_column(Float)
    outbox_failed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    outbox_oldest_pending_age_seconds: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[OpsSLOEvaluationStatus] = mapped_column(
        Enum(OpsSLOEvaluationStatus, name="ops_slo_evaluation_status"), nullable=False
    )
    reason_codes_json: Mapped[list] = mapped_column(JSON, nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    evaluated_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class OpsIncident(Base):
    __tablename__ = "ops_incidents"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_ops_incidents_public_id"),
        UniqueConstraint("slo_evaluation_id", name="uq_ops_incidents_evaluation"),
        Index("ix_ops_incidents_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(40), default=lambda: _public_id("oinc"), nullable=False
    )
    slo_evaluation_id: Mapped[int] = mapped_column(
        ForeignKey("ops_slo_evaluations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    severity: Mapped[OpsIncidentSeverity] = mapped_column(
        Enum(OpsIncidentSeverity, name="ops_incident_severity"), nullable=False
    )
    status: Mapped[OpsIncidentStatus] = mapped_column(
        Enum(OpsIncidentStatus, name="ops_incident_status"), nullable=False, index=True
    )
    reason_codes_json: Mapped[list] = mapped_column(JSON, nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    acknowledged_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class OpsRecoveryDrill(Base):
    __tablename__ = "ops_recovery_drills"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_ops_recovery_drills_public_id"),
        UniqueConstraint("idempotency_key", name="uq_ops_recovery_drills_idempotency"),
        Index("ix_ops_recovery_drills_status_finished", "status", "finished_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(40), default=lambda: _public_id("odrl"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    environment: Mapped[str] = mapped_column(String(64), nullable=False)
    git_head: Mapped[str] = mapped_column(String(64), nullable=False)
    backup_set_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    mysql_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    object_store_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    mysql_row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    object_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rpo_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    rto_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[OpsRecoveryDrillStatus] = mapped_column(
        Enum(OpsRecoveryDrillStatus, name="ops_recovery_drill_status"), nullable=False
    )
    reason_codes_json: Mapped[list] = mapped_column(JSON, nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    executed_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
