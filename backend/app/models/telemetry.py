from __future__ import annotations

import enum
from datetime import datetime, timezone

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


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentRunArtifactKind(str, enum.Enum):
    TRAJECTORY = "TRAJECTORY"
    EVALUATION = "EVALUATION"
    LOG_BUNDLE = "LOG_BUNDLE"
    OTHER = "OTHER"


class AgentRunArtifactCompleteness(str, enum.Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


class TelemetrySinkStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    ERROR = "ERROR"


class TraceBackendRefStatus(str, enum.Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    ERROR = "ERROR"
    STALE = "STALE"


class GenericTraceProjectionStatus(str, enum.Enum):
    MAPPED = "MAPPED"
    UNMATCHED = "UNMATCHED"
    QUARANTINED = "QUARANTINED"


class PackImportStatus(str, enum.Enum):
    PENDING_VALIDATION = "PENDING_VALIDATION"
    VALIDATING = "VALIDATING"
    IMPORTED = "IMPORTED"
    IMPORTED_PARTIAL = "IMPORTED_PARTIAL"
    QUARANTINED = "QUARANTINED"
    REJECTED = "REJECTED"


class PackUploadMode(str, enum.Enum):
    SINGLE_PUT = "SINGLE_PUT"
    MULTIPART = "MULTIPART"


class PackBatchDisposition(str, enum.Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class AgentRunArtifact(Base):
    __tablename__ = "agent_run_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "public_id",
            name="uq_agent_run_artifacts_public_id",
        ),
        UniqueConstraint(
            "namespace_id",
            "agent_run_id",
            "kind",
            "sha256",
            name="uq_agent_run_artifacts_run_kind_sha256",
        ),
        CheckConstraint(
            "size_bytes >= 0",
            name="ck_agent_run_artifacts_nonnegative_size",
        ),
        Index(
            "ix_agent_run_artifacts_namespace_run_created",
            "namespace_id",
            "agent_run_id",
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
    agent_run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    kind: Mapped[AgentRunArtifactKind] = mapped_column(
        Enum(AgentRunArtifactKind, name="agent_run_artifact_kind"),
        nullable=False,
        index=True,
    )
    schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    object_uri: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sensitivity: Mapped[Sensitivity] = mapped_column(
        Enum(Sensitivity),
        default=Sensitivity.RESTRICTED,
        nullable=False,
        index=True,
    )
    redaction_policy_version: Mapped[str | None] = mapped_column(String(64))
    completeness: Mapped[AgentRunArtifactCompleteness] = mapped_column(
        Enum(
            AgentRunArtifactCompleteness,
            name="agent_run_artifact_completeness",
        ),
        default=AgentRunArtifactCompleteness.UNKNOWN,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    agent_run: Mapped["AgentRun"] = relationship("AgentRun")


class TelemetrySink(Base):
    __tablename__ = "telemetry_sinks"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_telemetry_sinks_public_id"),
        UniqueConstraint(
            "namespace_id",
            "name",
            name="uq_telemetry_sinks_namespace_name",
        ),
        Index(
            "ix_telemetry_sinks_namespace_status_provider",
            "namespace_id",
            "status",
            "provider",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(512), nullable=False)
    project_ref: Mapped[str | None] = mapped_column(String(255))
    credential_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[TelemetrySinkStatus] = mapped_column(
        Enum(TelemetrySinkStatus, name="telemetry_sink_status"),
        default=TelemetrySinkStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    config_json: Mapped[dict | None] = mapped_column(JSON)
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


class TraceBackendRef(Base):
    __tablename__ = "trace_backend_refs"
    __table_args__ = (
        UniqueConstraint(
            "telemetry_sink_id",
            "external_trace_id",
            name="uq_trace_backend_refs_sink_external_trace",
        ),
        UniqueConstraint(
            "agent_run_id",
            "telemetry_sink_id",
            name="uq_trace_backend_refs_run_sink",
        ),
        Index(
            "ix_trace_backend_refs_namespace_run_status",
            "namespace_id",
            "agent_run_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    agent_run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    telemetry_sink_id: Mapped[int] = mapped_column(
        ForeignKey("telemetry_sinks.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    external_trace_id: Mapped[str] = mapped_column(String(256), nullable=False)
    external_session_id: Mapped[str | None] = mapped_column(String(256))
    trace_url: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[TraceBackendRefStatus] = mapped_column(
        Enum(TraceBackendRefStatus, name="trace_backend_ref_status"),
        default=TraceBackendRefStatus.PENDING,
        nullable=False,
        index=True,
    )
    last_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_error_code: Mapped[str | None] = mapped_column(String(100))
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

    agent_run: Mapped["AgentRun"] = relationship("AgentRun")
    telemetry_sink: Mapped[TelemetrySink] = relationship("TelemetrySink")


class GenericTraceProjection(Base):
    """Metadata-only result of reconciling one trusted Generic OTLP trace."""

    __tablename__ = "generic_trace_projections"
    __table_args__ = (
        UniqueConstraint(
            "public_id",
            name="uq_generic_trace_projections_public_id",
        ),
        UniqueConstraint(
            "namespace_id",
            "telemetry_sink_id",
            "external_trace_id",
            name="uq_generic_trace_projections_sink_trace",
        ),
        CheckConstraint(
            "candidate_root_count >= 0 AND observed_span_count >= 1",
            name="ck_generic_trace_projections_nonnegative_counts",
        ),
        CheckConstraint(
            "("
            "status = 'MAPPED' AND agent_run_id IS NOT NULL "
            "AND reason_code IS NULL"
            ") OR ("
            "status = 'UNMATCHED' AND agent_run_id IS NULL "
            "AND reason_code IS NOT NULL"
            ") OR ("
            "status = 'QUARANTINED' AND reason_code IS NOT NULL"
            ")",
            name="ck_generic_trace_projections_status_mapping",
        ),
        Index(
            "ix_generic_trace_projections_namespace_runtime_status",
            "namespace_id",
            "runtime_id",
            "status",
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
    telemetry_sink_id: Mapped[int] = mapped_column(
        ForeignKey("telemetry_sinks.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    agent_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"),
        index=True,
    )
    external_trace_id: Mapped[str] = mapped_column(String(32), nullable=False)
    root_span_id: Mapped[str | None] = mapped_column(String(16))
    external_run_id: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[GenericTraceProjectionStatus] = mapped_column(
        Enum(
            GenericTraceProjectionStatus,
            name="generic_trace_projection_status",
        ),
        nullable=False,
        index=True,
    )
    reason_code: Mapped[str | None] = mapped_column(String(100))
    source_schema: Mapped[str] = mapped_column(String(100), nullable=False)
    source_schema_version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    normalizer_version: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_root_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    observed_span_count: Mapped[int] = mapped_column(Integer, nullable=False)
    first_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    content_capture_mode: Mapped[str] = mapped_column(
        String(32),
        default="metadata_only",
        nullable=False,
    )
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

    agent_run: Mapped["AgentRun | None"] = relationship("AgentRun")
    telemetry_sink: Mapped[TelemetrySink] = relationship("TelemetrySink")


class PackImport(Base):
    """Metadata-only state for one authenticated immutable Pack import."""

    __tablename__ = "pack_imports"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_pack_imports_public_id"),
        UniqueConstraint(
            "namespace_id",
            "runtime_id",
            "pack_id",
            name="uq_pack_imports_tenant_runtime_pack",
        ),
        UniqueConstraint(
            "staging_object_key",
            name="uq_pack_imports_staging_object_key",
        ),
        CheckConstraint(
            "expected_size_bytes > 0 AND "
            "(actual_size_bytes IS NULL OR actual_size_bytes > 0)",
            name="ck_pack_imports_positive_sizes",
        ),
        CheckConstraint(
            "payload_count >= 1 AND verified_payload_count >= 0 "
            "AND verified_payload_count <= payload_count",
            name="ck_pack_imports_payload_counts",
        ),
        CheckConstraint(
            "("
            "status = 'IMPORTED' AND agent_run_id IS NOT NULL "
            "AND verified_payload_count = payload_count "
            "AND loss_reason IS NULL AND last_error_code IS NULL"
            ") OR ("
            "status = 'IMPORTED_PARTIAL' AND agent_run_id IS NOT NULL "
            "AND verified_payload_count = payload_count "
            "AND loss_reason IS NOT NULL AND last_error_code IS NULL"
            ") OR ("
            "status IN ('PENDING_VALIDATION', 'VALIDATING') "
            "AND verified_payload_count = 0"
            ") OR ("
            "status IN ('QUARANTINED', 'REJECTED') "
            "AND last_error_code IS NOT NULL"
            ")",
            name="ck_pack_imports_status_state",
        ),
        Index(
            "ix_pack_imports_namespace_runtime_status",
            "namespace_id",
            "runtime_id",
            "status",
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
    reporter_credential_id: Mapped[int] = mapped_column(
        ForeignKey("reporter_credentials.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    adapter_handshake_id: Mapped[int | None] = mapped_column(
        ForeignKey("adapter_handshakes.id", ondelete="RESTRICT"),
        index=True,
    )
    agent_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"),
        index=True,
    )
    pack_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[PackImportStatus] = mapped_column(
        Enum(PackImportStatus, name="pack_import_status"),
        default=PackImportStatus.PENDING_VALIDATION,
        nullable=False,
        index=True,
    )
    manifest_schema: Mapped[str] = mapped_column(String(100), nullable=False)
    manifest_version: Mapped[str] = mapped_column(String(50), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    producer_adapter_id: Mapped[str] = mapped_column(String(100), nullable=False)
    producer_adapter_version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    producer_instance_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    external_session_id: Mapped[str | None] = mapped_column(String(256))
    external_run_id: Mapped[str | None] = mapped_column(String(256))
    run_public_id: Mapped[str | None] = mapped_column(String(36))
    external_deployment_id: Mapped[str | None] = mapped_column(String(255))
    deployment_revision: Mapped[str | None] = mapped_column(String(128))
    otel_trace_id: Mapped[str | None] = mapped_column(String(32))
    staging_object_key: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )
    upload_mode: Mapped[PackUploadMode] = mapped_column(
        Enum(PackUploadMode, name="pack_upload_mode"),
        default=PackUploadMode.SINGLE_PUT,
        nullable=False,
    )
    multipart_upload_id: Mapped[str | None] = mapped_column(String(255))
    multipart_part_size_bytes: Mapped[int | None] = mapped_column(Integer)
    multipart_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    expected_pack_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    actual_pack_sha256: Mapped[str | None] = mapped_column(String(64))
    expected_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actual_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    payload_count: Mapped[int] = mapped_column(Integer, nullable=False)
    verified_payload_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    loss_reason: Mapped[str | None] = mapped_column(String(100))
    trust_level: Mapped[str] = mapped_column(
        String(32),
        default="CHANNEL_AUTHENTICATED",
        nullable=False,
    )
    trust_source: Mapped[str] = mapped_column(
        String(16),
        default="IMPORT",
        nullable=False,
    )
    content_capture_mode: Mapped[str] = mapped_column(
        String(32),
        default="metadata_only",
        nullable=False,
    )
    redaction_policy_version: Mapped[str | None] = mapped_column(String(64))
    redaction_receipt_sha256: Mapped[str | None] = mapped_column(String(64))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    imported_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
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

    agent_run: Mapped["AgentRun | None"] = relationship("AgentRun")
    adapter_handshake: Mapped["AdapterHandshake | None"] = relationship(
        "AdapterHandshake"
    )
    artifacts: Mapped[list["PackImportArtifact"]] = relationship(
        "PackImportArtifact",
        back_populates="pack_import",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class PackImportArtifact(Base):
    """Idempotent association between an import and a final artifact."""

    __tablename__ = "pack_import_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "pack_import_id",
            "payload_path",
            name="uq_pack_import_artifacts_import_path",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pack_import_id: Mapped[int] = mapped_column(
        ForeignKey("pack_imports.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_run_artifact_id: Mapped[int] = mapped_column(
        ForeignKey("agent_run_artifacts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    payload_path: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    pack_import: Mapped[PackImport] = relationship(
        "PackImport",
        back_populates="artifacts",
    )
    artifact: Mapped[AgentRunArtifact] = relationship("AgentRunArtifact")


class PackBatchStream(Base):
    """Server-side durable acknowledgement state for one Pack producer queue."""

    __tablename__ = "pack_batch_streams"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_pack_batch_streams_public_id"),
        UniqueConstraint(
            "reporter_credential_id",
            "stream_key",
            name="uq_pack_batch_streams_credential_key",
        ),
        CheckConstraint(
            "ack_cursor >= 0",
            name="ck_pack_batch_streams_nonnegative_cursor",
        ),
        Index(
            "ix_pack_batch_streams_namespace_runtime_updated",
            "namespace_id",
            "runtime_id",
            "updated_at",
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
    reporter_credential_id: Mapped[int] = mapped_column(
        ForeignKey("reporter_credentials.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    stream_key: Mapped[str] = mapped_column(String(128), nullable=False)
    ack_cursor: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )
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

    receipts: Mapped[list["PackBatchReceipt"]] = relationship(
        "PackBatchReceipt",
        back_populates="stream",
        cascade="all, delete-orphan",
    )


class PackBatchReceipt(Base):
    """Immutable per-item acknowledgement retained for safe replay."""

    __tablename__ = "pack_batch_receipts"
    __table_args__ = (
        UniqueConstraint(
            "pack_batch_stream_id",
            "sequence",
            name="uq_pack_batch_receipts_stream_sequence",
        ),
        UniqueConstraint(
            "pack_batch_stream_id",
            "idempotency_key",
            name="uq_pack_batch_receipts_stream_key",
        ),
        CheckConstraint(
            "sequence >= 1",
            name="ck_pack_batch_receipts_positive_sequence",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pack_batch_stream_id: Mapped[int] = mapped_column(
        ForeignKey("pack_batch_streams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    pack_import_id: Mapped[int | None] = mapped_column(
        ForeignKey("pack_imports.id", ondelete="RESTRICT"),
        index=True,
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    disposition: Mapped[PackBatchDisposition] = mapped_column(
        Enum(PackBatchDisposition, name="pack_batch_disposition"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    stream: Mapped[PackBatchStream] = relationship(
        "PackBatchStream",
        back_populates="receipts",
    )
    pack_import: Mapped[PackImport | None] = relationship("PackImport")


class PackExport(Base):
    """Durable index for one immutable server-generated ATIF Pack."""

    __tablename__ = "pack_exports"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_pack_exports_public_id"),
        UniqueConstraint(
            "reporter_credential_id",
            "idempotency_key",
            name="uq_pack_exports_credential_key",
        ),
        UniqueConstraint("object_key", name="uq_pack_exports_object_key"),
        CheckConstraint(
            "size_bytes > 0 AND payload_count >= 1",
            name="ck_pack_exports_positive_sizes",
        ),
        Index(
            "ix_pack_exports_namespace_runtime_created",
            "namespace_id",
            "runtime_id",
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
    reporter_credential_id: Mapped[int] = mapped_column(
        ForeignKey("reporter_credentials.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    agent_run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    pack_id: Mapped[str] = mapped_column(String(128), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    agent_run: Mapped["AgentRun"] = relationship("AgentRun")


from app.models.execution import AgentRun  # noqa: E402
from app.models.fleet import AdapterHandshake  # noqa: E402
