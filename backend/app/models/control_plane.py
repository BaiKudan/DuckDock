from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RuntimeProvider(str, enum.Enum):
    OPENCLAW = "openclaw"
    ARKCLAW = "arkclaw"
    WORKBUDDY = "workbuddy"
    JVS = "jvs"
    CUSTOM = "custom"


class RuntimeDeployType(str, enum.Enum):
    SAAS = "saas"
    PRIVATE = "private"
    ON_PREM = "on_prem"
    OFFLINE = "offline"


class RuntimeStatus(str, enum.Enum):
    ACTIVE = "active"
    DEGRADED = "degraded"
    DISABLED = "disabled"


class CollectionTriggerType(str, enum.Enum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    OFFBOARDING = "offboarding"
    PROJECT_HANDOVER = "project_handover"
    WEBHOOK = "webhook"


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL_FAILED = "partial_failed"
    CANCELLED = "cancelled"


class ReportUploadStatus(str, enum.Enum):
    PENDING = "pending"
    UPLOADED = "uploaded"
    VERIFYING = "verifying"
    INGESTING = "ingesting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"


class AnalysisWorkerStatus(str, enum.Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    STALE = "stale"


class AnalysisJobStatus(str, enum.Enum):
    PENDING = "pending"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentInsightStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AnalysisResultArtifactKind(str, enum.Enum):
    ANALYSIS_RESULT = "analysis_result"
    ASSET_CARDS = "asset_cards"
    WORKTRACE_SUMMARY = "worktrace_summary"
    MEMORY_CANDIDATES = "memory_candidates"
    HANDOVER_SIGNALS = "handover_signals"
    REDACTION_REPORT = "redaction_report"
    OTHER = "other"


class MemoryCandidateType(str, enum.Enum):
    ASSET_SUMMARY = "asset_summary"
    WORKTRACE_SUMMARY = "worktrace_summary"
    PROJECT_CONTEXT = "project_context"
    OWNERSHIP_SIGNAL = "ownership_signal"
    HANDOVER_SIGNAL = "handover_signal"
    RISK_SIGNAL = "risk_signal"
    KNOWLEDGE_NOTE = "knowledge_note"


class MemoryCandidateStatus(str, enum.Enum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class AdapterStepStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class RawRecordStream(str, enum.Enum):
    CAPABILITY = "capability"
    PRINCIPAL = "principal"
    ASSET = "asset"
    WORKTRACE = "worktrace"
    ARTIFACT = "artifact"
    EVIDENCE = "evidence"
    BACKUP_MANIFEST = "backup_manifest"
    UNKNOWN = "unknown"


class ProviderPrincipalType(str, enum.Enum):
    USER = "user"
    BOT = "bot"
    SERVICE_ACCOUNT = "service_account"
    GROUP = "group"
    UNKNOWN = "unknown"


class AssetType(str, enum.Enum):
    SKILL = "skill"
    AGENT = "agent"
    PROMPT = "prompt"
    WORKFLOW = "workflow"
    MCP = "mcp"
    TOOL = "tool"
    KNOWLEDGE_BASE = "knowledge_base"
    SCHEDULED_TASK = "scheduled_task"
    CREDENTIAL_REF = "credential_ref"
    WORKSPACE = "workspace"
    OTHER = "other"


class AssetStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"
    ORPHANED = "orphaned"
    RISKY = "risky"
    TRANSFERRED = "transferred"


class Criticality(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class OwnerType(str, enum.Enum):
    CREATOR = "creator"
    MAINTAINER = "maintainer"
    BUSINESS_OWNER = "business_owner"
    STEWARD = "steward"
    RECEIVER = "receiver"


class TraceType(str, enum.Enum):
    SESSION = "session"
    TASK_RUN = "task_run"
    AUTOMATION_RUN = "automation_run"
    FILE_CHANGE = "file_change"
    APPROVAL = "approval"
    DEPLOYMENT = "deployment"


class Sensitivity(str, enum.Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class ArtifactType(str, enum.Enum):
    FILE = "file"
    TRANSCRIPT = "transcript"
    DIFF = "diff"
    REPORT = "report"
    PACKAGE = "package"
    SCREENSHOT = "screenshot"
    LOG = "log"


class EvidenceSourceType(str, enum.Enum):
    API = "api"
    BACKUP_PACKAGE = "backup_package"
    BROWSER_SNAPSHOT = "browser_snapshot"
    AUDIT_LOG = "audit_log"
    USER_CONFIRM = "user_confirm"
    LLM_ANALYSIS = "llm_analysis"


class EvidenceVisibility(str, enum.Enum):
    NORMAL = "normal"
    SENSITIVE = "sensitive"
    RESTRICTED = "restricted"


class HandoverCaseType(str, enum.Enum):
    EMPLOYEE_OFFBOARDING = "employee_offboarding"
    PROJECT_HANDOVER = "project_handover"
    VENDOR_EXIT = "vendor_exit"
    INCIDENT_TAKEOVER = "incident_takeover"


class HandoverStatus(str, enum.Enum):
    DRAFT = "draft"
    COLLECTING = "collecting"
    ANALYZING = "analyzing"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class HandoverAction(str, enum.Enum):
    TRANSFER_OWNER = "transfer_owner"
    ARCHIVE = "archive"
    DISABLE = "disable"
    ROTATE_SECRET = "rotate_secret"
    EXPORT_PACKAGE = "export_package"
    MANUAL_REVIEW = "manual_review"
    IGNORE = "ignore"


class HandoverItemStatus(str, enum.Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    EXECUTING = "executing"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class ApprovalType(str, enum.Enum):
    MANAGER = "manager"
    RECEIVER = "receiver"
    SECURITY = "security"
    PLATFORM_ADMIN = "platform_admin"


class ApprovalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    DELEGATED = "delegated"


class ExecutionMode(str, enum.Enum):
    """FR-019(2026-06-12):执行双模式——manual 默认(人工回执=自我二次审查),auto=探针 lease。"""

    MANUAL = "manual"
    AUTO = "auto"


class ExecutionStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REQUIRES_MANUAL = "requires_manual"


class CredentialRecord(Base):
    """运行时凭证密文记录(specs/001 FR-016 · T011)。

    只存 Fernet 密文;明文绝不落库、绝不出接口。`RuntimeInstance.credential_ref`
    以 `db:<id>` 指向本表,轮换时就地更新密文并记 `rotated_at`。
    """

    __tablename__ = "credential_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(128))
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class RuntimeInstance(Base):
    __tablename__ = "runtime_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[RuntimeProvider] = mapped_column(Enum(RuntimeProvider), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    base_url: Mapped[str | None] = mapped_column(String(512))
    deploy_type: Mapped[RuntimeDeployType] = mapped_column(Enum(RuntimeDeployType), nullable=False, index=True)
    status: Mapped[RuntimeStatus] = mapped_column(
        Enum(RuntimeStatus),
        default=RuntimeStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    credential_ref: Mapped[str | None] = mapped_column(String(255))
    capabilities: Mapped[dict | None] = mapped_column(JSON)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )


class RuntimeReportToken(Base):
    __tablename__ = "runtime_report_tokens"
    __table_args__ = (
        UniqueConstraint("token_prefix", name="uq_runtime_report_tokens_prefix"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    runtime_id: Mapped[int] = mapped_column(ForeignKey("runtime_instances.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class ReporterCredential(Base):
    __tablename__ = "reporter_credentials"
    __table_args__ = (
        UniqueConstraint("token_prefix", name="uq_reporter_credentials_prefix"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    runtime_id: Mapped[int] = mapped_column(ForeignKey("runtime_instances.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    device_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    scopes: Mapped[list | None] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    revoked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    revoked_reason: Mapped[str | None] = mapped_column(Text)
    rotated_from_id: Mapped[int | None] = mapped_column(ForeignKey("reporter_credentials.id", ondelete="SET NULL"), index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    heartbeat_json: Mapped[dict | None] = mapped_column(JSON)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class CollectionJob(Base):
    __tablename__ = "collection_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    runtime_id: Mapped[int | None] = mapped_column(ForeignKey("runtime_instances.id", ondelete="SET NULL"), index=True)
    trigger_type: Mapped[CollectionTriggerType] = mapped_column(Enum(CollectionTriggerType), nullable=False, index=True)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.PENDING, nullable=False, index=True)
    scope_json: Mapped[dict | None] = mapped_column(JSON)
    summary_json: Mapped[dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ReportUploadSession(Base):
    __tablename__ = "report_upload_sessions"
    __table_args__ = (
        UniqueConstraint("report_id", name="uq_report_upload_sessions_report_id"),
        UniqueConstraint("object_key", name="uq_report_upload_sessions_object_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    runtime_id: Mapped[int] = mapped_column(ForeignKey("runtime_instances.id", ondelete="CASCADE"), nullable=False, index=True)
    collection_job_id: Mapped[int | None] = mapped_column(ForeignKey("collection_jobs.id", ondelete="SET NULL"), index=True)
    status: Mapped[ReportUploadStatus] = mapped_column(
        Enum(ReportUploadStatus),
        default=ReportUploadStatus.PENDING,
        nullable=False,
        index=True,
    )
    schema_version: Mapped[str] = mapped_column(String(64), default="duckdock-pack-v1", nullable=False, index=True)
    report_type: Mapped[str] = mapped_column(String(64), default="weekly", nullable=False, index=True)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), default="duckdock-pack-v1.zip", nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), default="application/zip", nullable=False)
    expected_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    expected_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    actual_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    actual_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    manifest_json: Mapped[dict | None] = mapped_column(JSON)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    upload_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    created_via: Mapped[str] = mapped_column(String(64), default="runtime_report_token", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class AnalysisWorker(Base):
    __tablename__ = "analysis_workers"
    __table_args__ = (
        UniqueConstraint("worker_key", name="uq_analysis_workers_worker_key"),
        UniqueConstraint("token_prefix", name="uq_analysis_workers_token_prefix"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    worker_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[AnalysisWorkerStatus] = mapped_column(
        Enum(AnalysisWorkerStatus),
        default=AnalysisWorkerStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    capabilities_json: Mapped[dict | None] = mapped_column(JSON)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class ReportAnalysisJob(Base):
    __tablename__ = "report_analysis_jobs"
    __table_args__ = (
        UniqueConstraint("report_upload_session_id", name="uq_report_analysis_jobs_session"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_upload_session_id: Mapped[int] = mapped_column(
        ForeignKey("report_upload_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    runtime_id: Mapped[int] = mapped_column(ForeignKey("runtime_instances.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[AnalysisJobStatus] = mapped_column(
        Enum(AnalysisJobStatus),
        default=AnalysisJobStatus.PENDING,
        nullable=False,
        index=True,
    )
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False, index=True)
    worker_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_workers.id", ondelete="SET NULL"), index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    input_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    input_object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    input_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    input_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    result_bucket: Mapped[str | None] = mapped_column(String(255))
    result_object_key: Mapped[str | None] = mapped_column(String(512), index=True)
    result_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    result_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    result_content_type: Mapped[str | None] = mapped_column(String(128))
    summary_json: Mapped[dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class AnalysisResultArtifact(Base):
    __tablename__ = "analysis_result_artifacts"
    __table_args__ = (
        UniqueConstraint("analysis_job_id", "object_key", name="uq_analysis_result_artifacts_job_object"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_job_id: Mapped[int] = mapped_column(ForeignKey("report_analysis_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    report_upload_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("report_upload_sessions.id", ondelete="SET NULL"),
        index=True,
    )
    runtime_id: Mapped[int | None] = mapped_column(ForeignKey("runtime_instances.id", ondelete="SET NULL"), index=True)
    kind: Mapped[AnalysisResultArtifactKind] = mapped_column(Enum(AnalysisResultArtifactKind), nullable=False, index=True)
    bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    summary_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class AgentInsightJob(Base):
    __tablename__ = "agent_insight_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    runtime_id: Mapped[int] = mapped_column(ForeignKey("runtime_instances.id", ondelete="CASCADE"), nullable=False, index=True)
    requested_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    status: Mapped[AgentInsightStatus] = mapped_column(
        Enum(AgentInsightStatus),
        default=AgentInsightStatus.PENDING,
        nullable=False,
        index=True,
    )
    prompt_version: Mapped[str] = mapped_column(String(64), default="agent-overview-v1", nullable=False)
    model: Mapped[str | None] = mapped_column(String(128))
    input_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    ai_assist_json: Mapped[dict | None] = mapped_column(JSON)
    result_json: Mapped[dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class MemoryCandidate(Base):
    __tablename__ = "memory_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_job_id: Mapped[int | None] = mapped_column(ForeignKey("report_analysis_jobs.id", ondelete="SET NULL"), index=True)
    report_upload_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("report_upload_sessions.id", ondelete="SET NULL"),
        index=True,
    )
    runtime_id: Mapped[int | None] = mapped_column(ForeignKey("runtime_instances.id", ondelete="SET NULL"), index=True)
    candidate_type: Mapped[MemoryCandidateType] = mapped_column(Enum(MemoryCandidateType), nullable=False, index=True)
    status: Mapped[MemoryCandidateStatus] = mapped_column(
        Enum(MemoryCandidateStatus),
        default=MemoryCandidateStatus.CANDIDATE,
        nullable=False,
        index=True,
    )
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subject_key: Mapped[str | None] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    summary: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.8, nullable=False)
    sensitivity: Mapped[Sensitivity] = mapped_column(Enum(Sensitivity), default=Sensitivity.INTERNAL, nullable=False, index=True)
    source_object_uri: Mapped[str | None] = mapped_column(String(1024))
    source_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    payload_json: Mapped[dict | None] = mapped_column(JSON)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class AIAsset(Base):
    __tablename__ = "ai_assets"
    __table_args__ = (
        UniqueConstraint(
            "source_provider",
            "source_runtime_id",
            "external_id",
            name="uq_ai_assets_source_external",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_type: Mapped[AssetType] = mapped_column(Enum(AssetType), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    source_provider: Mapped[RuntimeProvider] = mapped_column(Enum(RuntimeProvider), nullable=False, index=True)
    source_runtime_id: Mapped[int | None] = mapped_column(ForeignKey("runtime_instances.id", ondelete="SET NULL"), index=True)
    external_id: Mapped[str | None] = mapped_column(String(255), index=True)
    status: Mapped[AssetStatus] = mapped_column(Enum(AssetStatus), default=AssetStatus.ACTIVE, nullable=False, index=True)
    criticality: Mapped[Criticality] = mapped_column(Enum(Criticality), default=Criticality.MEDIUM, nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class AdapterCursor(Base):
    __tablename__ = "adapter_cursors"
    __table_args__ = (
        UniqueConstraint("runtime_id", "stream", name="uq_adapter_cursors_runtime_stream"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    runtime_id: Mapped[int] = mapped_column(ForeignKey("runtime_instances.id", ondelete="CASCADE"), nullable=False, index=True)
    provider: Mapped[RuntimeProvider] = mapped_column(Enum(RuntimeProvider), nullable=False, index=True)
    adapter_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    stream: Mapped[RawRecordStream] = mapped_column(Enum(RawRecordStream), nullable=False, index=True)
    cursor_json: Mapped[dict | None] = mapped_column(JSON)
    high_watermark: Mapped[str | None] = mapped_column(String(255), index=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class AdapterRunStep(Base):
    __tablename__ = "adapter_run_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    collection_job_id: Mapped[int] = mapped_column(ForeignKey("collection_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    step_name: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    status: Mapped[AdapterStepStatus] = mapped_column(Enum(AdapterStepStatus), default=AdapterStepStatus.PENDING, nullable=False, index=True)
    summary_json: Mapped[dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class RawCollectionRecord(Base):
    __tablename__ = "raw_collection_records"
    __table_args__ = (
        UniqueConstraint(
            "runtime_id",
            "stream",
            "external_id",
            "record_hash",
            name="uq_raw_collection_records_dedupe",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    collection_job_id: Mapped[int | None] = mapped_column(ForeignKey("collection_jobs.id", ondelete="SET NULL"), index=True)
    runtime_id: Mapped[int | None] = mapped_column(ForeignKey("runtime_instances.id", ondelete="SET NULL"), index=True)
    provider: Mapped[RuntimeProvider] = mapped_column(Enum(RuntimeProvider), nullable=False, index=True)
    adapter_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    stream: Mapped[RawRecordStream] = mapped_column(Enum(RawRecordStream), nullable=False, index=True)
    external_id: Mapped[str | None] = mapped_column(String(255), index=True)
    record_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    normalized_type: Mapped[str | None] = mapped_column(String(64), index=True)
    normalized_ref_id: Mapped[int | None] = mapped_column(Integer, index=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


class AdapterError(Base):
    __tablename__ = "adapter_errors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    collection_job_id: Mapped[int | None] = mapped_column(ForeignKey("collection_jobs.id", ondelete="SET NULL"), index=True)
    runtime_id: Mapped[int | None] = mapped_column(ForeignKey("runtime_instances.id", ondelete="SET NULL"), index=True)
    provider: Mapped[RuntimeProvider | None] = mapped_column(Enum(RuntimeProvider), index=True)
    adapter_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    step_name: Mapped[str | None] = mapped_column(String(96), index=True)
    error_code: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    context_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


class ProviderPrincipal(Base):
    __tablename__ = "provider_principals"
    __table_args__ = (
        UniqueConstraint("runtime_id", "external_id", name="uq_provider_principals_runtime_external"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    runtime_id: Mapped[int] = mapped_column(ForeignKey("runtime_instances.id", ondelete="CASCADE"), nullable=False, index=True)
    provider: Mapped[RuntimeProvider] = mapped_column(Enum(RuntimeProvider), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    principal_type: Mapped[ProviderPrincipalType] = mapped_column(
        Enum(ProviderPrincipalType),
        default=ProviderPrincipalType.USER,
        nullable=False,
        index=True,
    )
    display_name: Mapped[str | None] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(128), index=True)
    email: Mapped[str | None] = mapped_column(String(255), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


class RuntimeCapabilitySnapshot(Base):
    __tablename__ = "runtime_capability_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    runtime_id: Mapped[int] = mapped_column(ForeignKey("runtime_instances.id", ondelete="CASCADE"), nullable=False, index=True)
    provider: Mapped[RuntimeProvider] = mapped_column(Enum(RuntimeProvider), nullable=False, index=True)
    adapter_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="ok", nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), default="adapter", nullable=False, index=True)
    version: Mapped[str | None] = mapped_column(String(128))
    capabilities_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


class AssetOwnership(Base):
    __tablename__ = "asset_ownerships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("ai_assets.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_type: Mapped[OwnerType] = mapped_column(Enum(OwnerType), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    org_unit_id: Mapped[int | None] = mapped_column(ForeignKey("org_units.id", ondelete="SET NULL"), index=True)
    namespace_id: Mapped[int | None] = mapped_column(ForeignKey("namespaces.id", ondelete="SET NULL"), index=True)
    confidence: Mapped[float] = mapped_column(default=1.0, nullable=False)
    evidence_id: Mapped[int | None] = mapped_column(ForeignKey("evidence_items.id", ondelete="SET NULL"), index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class RuntimeBinding(Base):
    __tablename__ = "runtime_bindings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("ai_assets.id", ondelete="CASCADE"), nullable=False, index=True)
    runtime_id: Mapped[int] = mapped_column(ForeignKey("runtime_instances.id", ondelete="CASCADE"), nullable=False, index=True)
    external_ref: Mapped[str | None] = mapped_column(String(255))
    environment: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False, index=True)
    usage_status: Mapped[str] = mapped_column(String(32), default="active", nullable=False, index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class WorkTrace(Base):
    __tablename__ = "work_traces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    runtime_id: Mapped[int | None] = mapped_column(ForeignKey("runtime_instances.id", ondelete="SET NULL"), index=True)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("ai_assets.id", ondelete="SET NULL"), index=True)
    external_session_id: Mapped[str | None] = mapped_column(String(255), index=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    summary: Mapped[str | None] = mapped_column(Text)
    trace_type: Mapped[TraceType] = mapped_column(Enum(TraceType), nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sensitivity: Mapped[Sensitivity] = mapped_column(Enum(Sensitivity), default=Sensitivity.INTERNAL, nullable=False, index=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class WorkArtifact(Base):
    __tablename__ = "work_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trace_id: Mapped[int | None] = mapped_column(ForeignKey("work_traces.id", ondelete="CASCADE"), index=True)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("ai_assets.id", ondelete="SET NULL"), index=True)
    artifact_type: Mapped[ArtifactType] = mapped_column(Enum(ArtifactType), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    object_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    sensitivity: Mapped[Sensitivity] = mapped_column(Enum(Sensitivity), default=Sensitivity.INTERNAL, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class EvidenceItem(Base):
    __tablename__ = "evidence_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_type: Mapped[EvidenceSourceType] = mapped_column(Enum(EvidenceSourceType), nullable=False, index=True)
    source_provider: Mapped[RuntimeProvider] = mapped_column(Enum(RuntimeProvider), nullable=False, index=True)
    collection_job_id: Mapped[int | None] = mapped_column(ForeignKey("collection_jobs.id", ondelete="SET NULL"), index=True)
    object_uri: Mapped[str | None] = mapped_column(String(1024))
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(default=1.0, nullable=False)
    visibility: Mapped[EvidenceVisibility] = mapped_column(Enum(EvidenceVisibility), default=EvidenceVisibility.NORMAL, nullable=False, index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class HandoverCase(Base):
    __tablename__ = "handover_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_type: Mapped[HandoverCaseType] = mapped_column(Enum(HandoverCaseType), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    subject_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    namespace_id: Mapped[int | None] = mapped_column(ForeignKey("namespaces.id", ondelete="SET NULL"), index=True)
    receiver_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    status: Mapped[HandoverStatus] = mapped_column(Enum(HandoverStatus), default=HandoverStatus.DRAFT, nullable=False, index=True)
    risk_level: Mapped[Criticality] = mapped_column(Enum(Criticality), default=Criticality.MEDIUM, nullable=False, index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    summary_json: Mapped[dict | None] = mapped_column(JSON)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class HandoverItem(Base):
    __tablename__ = "handover_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    handover_case_id: Mapped[int] = mapped_column(ForeignKey("handover_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("ai_assets.id", ondelete="CASCADE"), nullable=False, index=True)
    recommended_action: Mapped[HandoverAction] = mapped_column(Enum(HandoverAction), nullable=False, index=True)
    receiver_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    risk_reason: Mapped[str | None] = mapped_column(Text)
    # 顾问对 recommended_action 的置信度 0~1(规则版固定 0;LLM 版填模型把握)。T042 · 原则 V:仍是建议。
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False, server_default=text("'0'"))
    evidence_id: Mapped[int | None] = mapped_column(ForeignKey("evidence_items.id", ondelete="SET NULL"), index=True)
    status: Mapped[HandoverItemStatus] = mapped_column(Enum(HandoverItemStatus), default=HandoverItemStatus.PROPOSED, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ApprovalTask(Base):
    __tablename__ = "approval_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    handover_case_id: Mapped[int] = mapped_column(ForeignKey("handover_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    approver_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    approval_type: Mapped[ApprovalType] = mapped_column(Enum(ApprovalType), nullable=False, index=True)
    status: Mapped[ApprovalStatus] = mapped_column(Enum(ApprovalStatus), default=ApprovalStatus.PENDING, nullable=False, index=True)
    comment: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ExecutionAction(Base):
    __tablename__ = "execution_actions"
    __table_args__ = (
        UniqueConstraint("handover_case_id", "idempotency_key", name="uq_execution_actions_case_idempotency_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    handover_case_id: Mapped[int] = mapped_column(ForeignKey("handover_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    handover_item_id: Mapped[int | None] = mapped_column(ForeignKey("handover_items.id", ondelete="SET NULL"), index=True)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider: Mapped[RuntimeProvider] = mapped_column(Enum(RuntimeProvider), default=RuntimeProvider.CUSTOM, nullable=False, index=True)
    status: Mapped[ExecutionStatus] = mapped_column(Enum(ExecutionStatus), default=ExecutionStatus.PENDING, nullable=False, index=True)
    execution_mode: Mapped[ExecutionMode] = mapped_column(
        Enum(ExecutionMode), default=ExecutionMode.MANUAL, nullable=False, index=True, server_default="MANUAL"
    )
    request_json: Mapped[dict | None] = mapped_column(JSON)
    result_json: Mapped[dict | None] = mapped_column(JSON)
    evidence_ids: Mapped[list | None] = mapped_column(JSON)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
