from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    BigInteger,
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
from app.models.control_plane import Sensitivity

if TYPE_CHECKING:
    from app.models.control_plane import RuntimeInstance, WorkTrace
    from app.models.deployment import AgentDeployment
    from app.models.user import User


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ContentCaptureMode(str, enum.Enum):
    METADATA_ONLY = "metadata_only"


class AgentSessionStatus(str, enum.Enum):
    OPEN = "OPEN"
    ENDED = "ENDED"
    ABANDONED = "ABANDONED"


class AgentRunStatus(str, enum.Enum):
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


class TrustLevel(str, enum.Enum):
    CHANNEL_AUTHENTICATED = "CHANNEL_AUTHENTICATED"
    PRODUCER_ATTESTED = "PRODUCER_ATTESTED"
    UNVERIFIED = "UNVERIFIED"


class TrustSource(str, enum.Enum):
    REPORTER = "REPORTER"
    COLLECTOR = "COLLECTOR"
    IMPORT = "IMPORT"
    ADMIN = "ADMIN"


class AgentSession(Base):
    __tablename__ = "agent_sessions"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_agent_sessions_public_id"),
        UniqueConstraint(
            "namespace_id",
            "runtime_id",
            "external_session_id",
            name="uq_agent_sessions_tenant_runtime_external",
        ),
        UniqueConstraint(
            "namespace_id",
            "runtime_id",
            "start_idempotency_key",
            name="uq_agent_sessions_tenant_runtime_start_key",
        ),
        UniqueConstraint(
            "namespace_id",
            "runtime_id",
            "completion_idempotency_key",
            name="uq_agent_sessions_tenant_runtime_completion_key",
        ),
        CheckConstraint(
            "("
            "status = 'OPEN' AND ended_at IS NULL "
            "AND completion_idempotency_key IS NULL "
            "AND completion_envelope_sha256 IS NULL"
            ") OR ("
            "status IN ('ENDED', 'ABANDONED') AND ended_at IS NOT NULL "
            "AND completion_idempotency_key IS NOT NULL "
            "AND completion_envelope_sha256 IS NOT NULL"
            ")",
            name="ck_agent_sessions_status_completion",
        ),
        CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="ck_agent_sessions_time_order",
        ),
        CheckConstraint(
            "run_count >= 0 AND error_count >= 0",
            name="ck_agent_sessions_nonnegative_counts",
        ),
        Index(
            "ix_agent_sessions_namespace_status_started",
            "namespace_id",
            "status",
            "started_at",
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
    deployment_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_deployments.id", ondelete="RESTRICT"),
        index=True,
    )
    work_trace_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_traces.id", ondelete="SET NULL"),
        index=True,
    )
    external_session_id: Mapped[str] = mapped_column(String(256), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    status: Mapped[AgentSessionStatus] = mapped_column(
        Enum(AgentSessionStatus, name="agent_session_status"),
        default=AgentSessionStatus.OPEN,
        nullable=False,
        index=True,
    )
    content_capture_mode: Mapped[ContentCaptureMode] = mapped_column(
        Enum(ContentCaptureMode, name="content_capture_mode"),
        default=ContentCaptureMode.METADATA_ONLY,
        nullable=False,
    )
    sensitivity: Mapped[Sensitivity] = mapped_column(
        Enum(Sensitivity),
        default=Sensitivity.RESTRICTED,
        nullable=False,
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    start_idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    start_envelope_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    completion_idempotency_key: Mapped[str | None] = mapped_column(String(128))
    completion_envelope_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    runs: Mapped[list["AgentRun"]] = relationship(
        "AgentRun",
        back_populates="session",
        lazy="selectin",
    )
    runtime: Mapped["RuntimeInstance"] = relationship("RuntimeInstance")
    deployment: Mapped["AgentDeployment | None"] = relationship("AgentDeployment")
    work_trace: Mapped["WorkTrace | None"] = relationship("WorkTrace")
    actor_user: Mapped["User | None"] = relationship("User")


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_agent_runs_public_id"),
        UniqueConstraint(
            "namespace_id",
            "runtime_id",
            "external_run_id",
            name="uq_agent_runs_tenant_runtime_external",
        ),
        UniqueConstraint(
            "namespace_id",
            "runtime_id",
            "start_idempotency_key",
            name="uq_agent_runs_tenant_runtime_start_key",
        ),
        UniqueConstraint(
            "namespace_id",
            "runtime_id",
            "completion_idempotency_key",
            name="uq_agent_runs_tenant_runtime_completion_key",
        ),
        UniqueConstraint(
            "namespace_id",
            "otel_trace_id",
            name="uq_agent_runs_tenant_otel_trace",
        ),
        CheckConstraint("attempt >= 1", name="ck_agent_runs_positive_attempt"),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_agent_runs_nonnegative_duration",
        ),
        CheckConstraint(
            "(step_count IS NULL OR step_count >= 0) AND "
            "(model_call_count IS NULL OR model_call_count >= 0) AND "
            "(tool_call_count IS NULL OR tool_call_count >= 0) AND "
            "(input_token_count IS NULL OR input_token_count >= 0) AND "
            "(output_token_count IS NULL OR output_token_count >= 0)",
            name="ck_agent_runs_nonnegative_aggregates",
        ),
        CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="ck_agent_runs_time_order",
        ),
        CheckConstraint(
            "("
            "status = 'STARTED' AND ended_at IS NULL "
            "AND completion_idempotency_key IS NULL "
            "AND completion_envelope_sha256 IS NULL"
            ") OR ("
            "status IN ('SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT') "
            "AND ended_at IS NOT NULL "
            "AND completion_idempotency_key IS NOT NULL "
            "AND completion_envelope_sha256 IS NOT NULL"
            ")",
            name="ck_agent_runs_status_completion",
        ),
        Index(
            "ix_agent_runs_namespace_started",
            "namespace_id",
            "started_at",
        ),
        Index(
            "ix_agent_runs_namespace_runtime_started",
            "namespace_id",
            "runtime_id",
            "started_at",
        ),
        Index(
            "ix_agent_runs_namespace_deployment_started",
            "namespace_id",
            "deployment_id",
            "started_at",
        ),
        Index(
            "ix_agent_runs_namespace_status_started",
            "namespace_id",
            "status",
            "started_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="RESTRICT"),
        index=True,
    )
    runtime_id: Mapped[int] = mapped_column(
        ForeignKey("runtime_instances.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    deployment_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_deployments.id", ondelete="RESTRICT"),
        index=True,
    )
    work_trace_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_traces.id", ondelete="SET NULL"),
        index=True,
    )
    external_run_id: Mapped[str] = mapped_column(String(256), nullable=False)
    otel_trace_id: Mapped[str | None] = mapped_column(String(32))
    root_span_id: Mapped[str | None] = mapped_column(String(16))
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[AgentRunStatus] = mapped_column(
        Enum(AgentRunStatus, name="agent_run_status"),
        default=AgentRunStatus.STARTED,
        nullable=False,
        index=True,
    )
    trust_level: Mapped[TrustLevel] = mapped_column(
        Enum(TrustLevel, name="agent_run_trust_level"),
        nullable=False,
        index=True,
    )
    trust_source: Mapped[TrustSource] = mapped_column(
        Enum(TrustSource, name="agent_run_trust_source"),
        nullable=False,
        index=True,
    )
    source_schema: Mapped[str] = mapped_column(String(100), nullable=False)
    source_schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    normalizer_version: Mapped[str] = mapped_column(String(64), nullable=False)
    content_capture_mode: Mapped[ContentCaptureMode] = mapped_column(
        Enum(ContentCaptureMode, name="content_capture_mode"),
        default=ContentCaptureMode.METADATA_ONLY,
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    step_count: Mapped[int | None] = mapped_column(Integer)
    model_call_count: Mapped[int | None] = mapped_column(Integer)
    tool_call_count: Mapped[int | None] = mapped_column(Integer)
    input_token_count: Mapped[int | None] = mapped_column(BigInteger)
    output_token_count: Mapped[int | None] = mapped_column(BigInteger)
    error_type: Mapped[str | None] = mapped_column(String(100))
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    start_idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    start_envelope_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    completion_idempotency_key: Mapped[str | None] = mapped_column(String(128))
    completion_envelope_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    session: Mapped[AgentSession | None] = relationship(
        "AgentSession",
        back_populates="runs",
    )
    runtime: Mapped["RuntimeInstance"] = relationship("RuntimeInstance")
    deployment: Mapped["AgentDeployment | None"] = relationship("AgentDeployment")
    work_trace: Mapped["WorkTrace | None"] = relationship("WorkTrace")
