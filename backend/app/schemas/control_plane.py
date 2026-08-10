from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.control_plane import (
    AnalysisJobStatus,
    AnalysisResultArtifactKind,
    AnalysisWorkerStatus,
    ApprovalStatus,
    ApprovalType,
    AssetStatus,
    AssetType,
    AdapterStepStatus,
    CollectionTriggerType,
    Criticality,
    EvidenceSourceType,
    EvidenceVisibility,
    ExecutionStatus,
    HandoverAction,
    HandoverCaseType,
    HandoverItemStatus,
    HandoverStatus,
    JobStatus,
    MemoryCandidateStatus,
    MemoryCandidateType,
    OwnerType,
    ProviderPrincipalType,
    RawRecordStream,
    ReportUploadStatus,
    RuntimeDeployType,
    RuntimeProvider,
    RuntimeStatus,
    Sensitivity,
    TraceType,
)
from app.models.control_plane import ExecutionMode
from app.models.iam import EmploymentStatus


class RuntimeInstanceCreate(BaseModel):
    namespace_id: int
    provider: RuntimeProvider
    name: str = Field(min_length=1, max_length=128)
    base_url: str | None = None
    deploy_type: RuntimeDeployType = RuntimeDeployType.PRIVATE
    credential_ref: str | None = None
    credential: str | None = Field(
        default=None,
        description="Write-only 明文凭证:服务端 Fernet 加密落库并自动接线 credential_ref,任何响应不回传。",
    )
    metadata_json: dict | None = None


class RuntimeInstanceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = None
    deploy_type: RuntimeDeployType | None = None
    status: RuntimeStatus | None = None
    credential_ref: str | None = None
    credential: str | None = Field(
        default=None,
        description="Write-only 明文凭证:提供即轮换(db: 引用就地换密文),任何响应不回传。",
    )
    metadata_json: dict | None = None


class RuntimeInstanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    public_id: str
    namespace_id: int | None
    provider: RuntimeProvider
    name: str
    base_url: str | None
    deploy_type: RuntimeDeployType
    status: RuntimeStatus
    credential_ref: str | None
    capabilities: dict | None
    metadata_json: dict | None
    last_sync_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AdapterCapabilityOut(BaseModel):
    provider: RuntimeProvider
    asset_sync: bool | str
    worktrace_sync: bool | str
    artifact_sync: bool | str
    backup_create: bool | str
    restore: bool | str
    browser_fallback: bool | str


class RuntimeConnectionTestOut(BaseModel):
    status: str
    provider: RuntimeProvider
    capabilities: AdapterCapabilityOut
    message: str


class RuntimeReportTokenCreate(BaseModel):
    name: str = Field(default="DuckDock Reporter", min_length=1, max_length=128)
    expires_at: datetime | None = None


class RuntimeReportTokenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    runtime_id: int
    name: str
    token_prefix: str
    is_active: bool
    expires_at: datetime | None
    last_used_at: datetime | None
    created_by: int | None
    created_at: datetime
    updated_at: datetime


class RuntimeReportTokenCreatedOut(RuntimeReportTokenOut):
    token: str


class ReporterCredentialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    runtime_id: int
    user_id: int | None
    device_id: str
    name: str
    token_prefix: str
    scopes: list | None
    is_active: bool
    expires_at: datetime | None
    revoked_at: datetime | None
    revoked_by: int | None
    revoked_reason: str | None
    rotated_from_id: int | None
    last_used_at: datetime | None
    last_heartbeat_at: datetime | None
    heartbeat_json: dict | None
    metadata_json: dict | None
    created_at: datetime
    updated_at: datetime


class ReporterCredentialCreatedOut(ReporterCredentialOut):
    token: str


class ReporterCredentialRotate(BaseModel):
    reason: str | None = Field(default=None, max_length=255)
    expires_at: datetime | None = None


class ReporterCredentialRevoke(BaseModel):
    reason: str = Field(default="manual revoke", min_length=1, max_length=255)


class ReporterEnrollmentCreate(BaseModel):
    namespace_id: int
    provider: RuntimeProvider = RuntimeProvider.CUSTOM
    runtime_name: str | None = Field(default=None, min_length=1, max_length=128)
    device_id: str = Field(default="default", min_length=1, max_length=128)
    agent_kind: str | None = Field(default=None, max_length=64)
    reporter_version: str | None = Field(default=None, max_length=64)
    schedule_json: dict | None = None
    metadata_json: dict | None = None
    expires_at: datetime | None = None


class ReporterEnrollmentOut(BaseModel):
    runtime: RuntimeInstanceOut
    credential: ReporterCredentialCreatedOut


class ReporterHeartbeat(BaseModel):
    device_id: str | None = Field(default=None, max_length=128)
    reporter_version: str | None = Field(default=None, max_length=64)
    agent_version: str | None = Field(default=None, max_length=64)
    status: Literal["ok", "degraded", "error"] = "ok"
    next_run_at: datetime | None = None
    schedule_json: dict | None = None
    capabilities_json: dict | None = None
    metadata_json: dict | None = None


class ReporterHeartbeatOut(BaseModel):
    runtime_id: int
    token_id: int
    credential_source: Literal["reporter_credential", "runtime_report_token"] = "reporter_credential"
    status: str
    last_seen_at: datetime
    server_time: datetime
    upload_recommended: bool = False


class ReportUploadSessionCreate(BaseModel):
    runtime_id: int
    schema_version: str = Field(default="duckdock-pack-v1", min_length=1, max_length=64)
    report_type: str = Field(default="weekly", min_length=1, max_length=64)
    period_start: datetime | None = None
    period_end: datetime | None = None
    filename: str = Field(default="duckdock-pack-v1.zip", min_length=1, max_length=255)
    content_type: str = Field(default="application/zip", min_length=1, max_length=128)
    expected_size_bytes: int | None = Field(default=None, ge=1)
    expected_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    idempotency_key: str | None = Field(default=None, max_length=128)
    metadata_json: dict | None = None


class ReportUploadSessionFinalize(BaseModel):
    sha256: str | None = Field(default=None, min_length=64, max_length=64)
    size_bytes: int | None = Field(default=None, ge=1)
    manifest: dict | None = None


class ReportUploadSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    report_id: str
    runtime_id: int
    collection_job_id: int | None
    status: ReportUploadStatus
    schema_version: str
    report_type: str
    period_start: datetime | None
    period_end: datetime | None
    bucket: str
    object_key: str
    filename: str
    content_type: str
    expected_size_bytes: int | None
    expected_sha256: str | None
    actual_size_bytes: int | None
    actual_sha256: str | None
    manifest_json: dict | None
    metadata_json: dict | None
    idempotency_key: str | None
    upload_expires_at: datetime
    finalized_at: datetime | None
    error_message: str | None
    created_by: int | None
    created_via: str
    created_at: datetime
    updated_at: datetime
    upload_url: str | None = None
    expires_in: int | None = None
    max_size_mb: int | None = None


class StructuredReportAssetRef(BaseModel):
    external_id: str | None = Field(default=None, max_length=255)
    asset_type: AssetType = AssetType.OTHER
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    status: AssetStatus = AssetStatus.ACTIVE
    criticality: Criticality = Criticality.MEDIUM
    content_hash: str | None = Field(default=None, max_length=64)
    metadata_json: dict | None = None


class StructuredReportMemoryInput(BaseModel):
    candidate_type: MemoryCandidateType = MemoryCandidateType.KNOWLEDGE_NOTE
    subject_type: str = Field(default="report", min_length=1, max_length=64)
    subject_key: str | None = Field(default=None, max_length=255)
    title: str = Field(min_length=1, max_length=255)
    summary: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(default=0.75, ge=0, le=1)
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    payload_json: dict | None = None


class StructuredReportSignalInput(BaseModel):
    signal_type: Literal["handover", "risk"] = "handover"
    subject_type: str = Field(default="report", min_length=1, max_length=64)
    subject_key: str | None = Field(default=None, max_length=255)
    title: str = Field(min_length=1, max_length=255)
    summary: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(default=0.75, ge=0, le=1)
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    payload_json: dict | None = None


class StructuredReportSubmit(BaseModel):
    runtime_id: int
    schema_version: str = Field(default="duckdock-structured-report-v1", min_length=1, max_length=64)
    report_type: str = Field(default="daily", min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    summary: str = Field(min_length=1, max_length=8000)
    period_start: datetime | None = None
    period_end: datetime | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    highlights: list[str] = Field(default_factory=list, max_length=50)
    blockers: list[str] = Field(default_factory=list, max_length=50)
    next_actions: list[str] = Field(default_factory=list, max_length=50)
    project_refs: list[str] = Field(default_factory=list, max_length=50)
    asset_refs: list[StructuredReportAssetRef] = Field(default_factory=list, max_length=100)
    memory_candidates: list[StructuredReportMemoryInput] = Field(default_factory=list, max_length=100)
    handover_signals: list[StructuredReportSignalInput] = Field(default_factory=list, max_length=100)
    metadata_json: dict | None = None

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        if value != "duckdock-structured-report-v1":
            raise ValueError("schema_version must be duckdock-structured-report-v1")
        return value


class StructuredReportOut(BaseModel):
    report_id: str
    status: Literal["succeeded", "deduped"]
    job: CollectionJobOut
    work_trace: WorkTraceOut
    assets: list[AIAssetOut] = Field(default_factory=list)
    memory_candidates: list[MemoryCandidateOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AnalysisWorkerCreate(BaseModel):
    name: str = Field(default="DuckDock OpenClaw Worker", min_length=1, max_length=128)
    capabilities_json: dict | None = None


class AnalysisWorkerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    worker_key: str
    token_prefix: str
    status: AnalysisWorkerStatus
    capabilities_json: dict | None
    last_seen_at: datetime | None
    created_by: int | None
    created_at: datetime
    updated_at: datetime


class AnalysisWorkerCreatedOut(AnalysisWorkerOut):
    token: str


class AnalysisWorkerDisableOut(BaseModel):
    worker: AnalysisWorkerOut
    requeued_job_count: int = 0
    failed_job_count: int = 0


class AnalysisQueueMetricsOut(BaseModel):
    status_counts: dict[str, int]
    worker_status_counts: dict[str, int]
    pending_count: int
    active_job_count: int
    backlog_count: int
    terminal_job_count: int
    expired_lease_count: int
    retryable_expired_lease_count: int
    registered_worker_count: int
    online_worker_count: int
    stale_worker_count: int
    disabled_worker_count: int
    oldest_pending_seconds: int | None = None
    queued_per_online_worker: float
    saturation_level: Literal["healthy", "watch", "saturated"]
    saturation_reason: str | None = None


class MemoryCandidateCreate(BaseModel):
    candidate_type: MemoryCandidateType = MemoryCandidateType.KNOWLEDGE_NOTE
    subject_type: str = Field(min_length=1, max_length=64)
    subject_key: str | None = Field(default=None, max_length=255)
    title: str = Field(min_length=1, max_length=255)
    summary: str | None = None
    confidence: float = Field(default=0.8, ge=0, le=1)
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    source_object_uri: str | None = Field(default=None, max_length=1024)
    source_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    payload_json: dict | None = None


class MemoryCandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    analysis_job_id: int | None
    report_upload_session_id: int | None
    runtime_id: int | None
    candidate_type: MemoryCandidateType
    status: MemoryCandidateStatus
    subject_type: str
    subject_key: str | None
    title: str
    summary: str | None
    confidence: float
    sensitivity: Sensitivity
    source_object_uri: str | None
    source_sha256: str | None
    payload_json: dict | None
    reviewed_by: int | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MemoryCandidateReview(BaseModel):
    status: MemoryCandidateStatus


class ReportAnalysisJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    report_upload_session_id: int
    runtime_id: int
    status: AnalysisJobStatus
    priority: int
    worker_id: int | None
    lease_owner: str | None
    lease_expires_at: datetime | None
    attempts: int
    max_attempts: int
    input_bucket: str
    input_object_key: str
    input_sha256: str | None
    input_size_bytes: int | None
    result_bucket: str | None
    result_object_key: str | None
    result_sha256: str | None
    result_size_bytes: int | None
    result_content_type: str | None
    summary_json: dict | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AnalysisResultArtifactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    analysis_job_id: int
    report_upload_session_id: int | None
    runtime_id: int | None
    kind: AnalysisResultArtifactKind
    bucket: str
    object_key: str
    filename: str
    content_type: str
    sha256: str | None
    size_bytes: int | None
    summary_json: dict | None
    created_at: datetime
    updated_at: datetime


class AnalysisResultArtifactDownloadOut(BaseModel):
    filename: str
    content_type: str
    download_url: str
    expires_in: int
    expires_at: datetime


class AnalysisResultUploadOut(BaseModel):
    kind: AnalysisResultArtifactKind
    filename: str
    content_type: str
    bucket: str
    object_key: str
    upload_url: str
    expires_in: int


class AnalysisJobLeaseRequest(BaseModel):
    lease_seconds: int = Field(default=1800, ge=60, le=7200)
    worker_name: str | None = Field(default=None, max_length=128)
    capabilities_json: dict | None = None


class AnalysisJobLeaseOut(BaseModel):
    job: ReportAnalysisJobOut
    download_url: str
    download_expires_in: int
    result_upload_url: str
    result_upload_expires_in: int
    result_bucket: str
    result_object_key: str
    result_content_type: str
    result_uploads: list[AnalysisResultUploadOut] = Field(default_factory=list)


class AnalysisJobLeaseResponse(BaseModel):
    job: AnalysisJobLeaseOut | None = None


class AnalysisResultArtifactFinalize(BaseModel):
    kind: AnalysisResultArtifactKind
    filename: str = Field(min_length=1, max_length=255)
    object_key: str = Field(min_length=1, max_length=512)
    content_type: str = Field(default="application/json", min_length=1, max_length=128)
    sha256: str | None = Field(default=None, min_length=64, max_length=64)
    size_bytes: int | None = Field(default=None, ge=1)
    summary_json: dict | None = None


class AnalysisJobFinalize(BaseModel):
    result_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    result_size_bytes: int | None = Field(default=None, ge=1)
    summary_json: dict | None = None
    result_artifacts: list[AnalysisResultArtifactFinalize] = Field(default_factory=list)
    memory_candidates: list[MemoryCandidateCreate] = Field(default_factory=list)


class AnalysisJobFail(BaseModel):
    error_message: str = Field(min_length=1, max_length=4000)
    retryable: bool = True
    summary_json: dict | None = None


class AnalysisJobCancel(BaseModel):
    reason: str = Field(default="Cancelled by administrator", min_length=1, max_length=1000)


class AnalysisJobRetry(BaseModel):
    reason: str = Field(default="Retried by administrator", min_length=1, max_length=1000)
    reset_attempts: bool = True


class CollectionScope(BaseModel):
    users: list[int] | None = None
    asset_types: list[AssetType] | None = None
    include_work_traces: bool = True
    include_artifacts: bool = True
    lookback_days: int | None = Field(default=180, ge=1, le=3650)


class CollectionJobCreate(BaseModel):
    trigger_type: CollectionTriggerType = CollectionTriggerType.MANUAL
    scope: CollectionScope = Field(default_factory=CollectionScope)


class CollectionJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    runtime_id: int | None
    trigger_type: CollectionTriggerType
    status: JobStatus
    scope_json: dict | None
    summary_json: dict | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class AdapterRunStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    collection_job_id: int
    step_name: str
    status: AdapterStepStatus
    summary_json: dict | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class AdapterCursorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    runtime_id: int
    provider: RuntimeProvider
    adapter_name: str
    stream: RawRecordStream
    cursor_json: dict | None
    high_watermark: str | None
    last_success_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RawCollectionRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    collection_job_id: int | None
    runtime_id: int | None
    provider: RuntimeProvider
    adapter_name: str
    stream: RawRecordStream
    external_id: str | None
    record_hash: str
    payload_json: dict
    normalized_type: str | None
    normalized_ref_id: int | None
    collected_at: datetime


class AdapterErrorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    collection_job_id: int | None
    runtime_id: int | None
    provider: RuntimeProvider | None
    adapter_name: str
    step_name: str | None
    error_code: str
    message: str
    retryable: bool
    context_json: dict | None
    created_at: datetime


class ProviderPrincipalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    runtime_id: int
    provider: RuntimeProvider
    external_id: str
    principal_type: ProviderPrincipalType
    display_name: str | None
    username: str | None
    email: str | None
    user_id: int | None
    metadata_json: dict | None
    first_seen_at: datetime
    last_seen_at: datetime


class RuntimeCapabilitySnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    runtime_id: int
    provider: RuntimeProvider
    adapter_name: str
    status: str
    source: str
    version: str | None
    capabilities_json: dict
    collected_at: datetime


class AIAssetCreate(BaseModel):
    namespace_id: int
    asset_type: AssetType
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    source_provider: RuntimeProvider = RuntimeProvider.CUSTOM
    source_runtime_id: int | None = None
    external_id: str | None = None
    status: AssetStatus = AssetStatus.ACTIVE
    criticality: Criticality = Criticality.MEDIUM
    metadata_json: dict | None = None
    content_hash: str | None = Field(default=None, max_length=64)


class AIAssetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    status: AssetStatus | None = None
    criticality: Criticality | None = None
    metadata_json: dict | None = None


class AIAssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    namespace_id: int | None
    asset_type: AssetType
    name: str
    description: str | None
    source_provider: RuntimeProvider
    source_runtime_id: int | None
    external_id: str | None
    status: AssetStatus
    criticality: Criticality
    metadata_json: dict | None
    content_hash: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime


class AssetOwnershipCreate(BaseModel):
    owner_type: OwnerType
    user_id: int | None = None
    org_unit_id: int | None = None
    namespace_id: int | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)
    evidence_id: int | None = None
    is_primary: bool = False


class AssetOwnershipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    asset_id: int
    owner_type: OwnerType
    user_id: int | None
    org_unit_id: int | None
    namespace_id: int | None
    confidence: float
    evidence_id: int | None
    is_primary: bool
    created_at: datetime


class WorkTraceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    namespace_id: int | None
    runtime_id: int | None
    asset_id: int | None
    external_session_id: str | None
    actor_user_id: int | None
    title: str
    summary: str | None
    trace_type: TraceType
    started_at: datetime | None
    ended_at: datetime | None
    sensitivity: Sensitivity
    metadata_json: dict | None
    created_at: datetime


class WorkArtifactOut(BaseModel):
    """产物索引(刻意不含 object_uri 存储路径;下载走后续签名 URL 端点)。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    trace_id: int | None
    asset_id: int | None
    artifact_type: str
    name: str
    sha256: str | None
    size_bytes: int
    sensitivity: str
    created_at: datetime


class WorkTraceRevealRequest(BaseModel):
    """请求查看完整工作轨迹；原因必填并写入审计日志。"""

    reason: str = Field(min_length=5, max_length=512)


class WorkTraceDetailOut(WorkTraceOut):
    """reveal 返回的全量视图:metadata 不遮蔽 + 产物清单。"""

    artifacts: list[WorkArtifactOut] = Field(default_factory=list)


class EvidenceItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    namespace_id: int | None
    work_trace_id: int | None
    source_type: EvidenceSourceType
    source_provider: RuntimeProvider
    collection_job_id: int | None
    object_uri: str | None
    sha256: str | None
    summary: str
    confidence: float
    visibility: EvidenceVisibility
    created_by: int | None
    created_at: datetime


class EvidenceDownloadLinkRequest(BaseModel):
    """请求限时证据下载链接；原因必填并接受服务端有效期约束。"""

    reason: str = Field(min_length=5, max_length=512)
    expires_in: int | None = Field(
        default=None, ge=60, description="期望有效期(秒);服务端按 REGISTRY_SIGNED_URL_MAX_SECONDS clamp"
    )


class EvidenceDownloadLinkOut(BaseModel):
    """带有效期的预签名证据下载链接。

    object_uri 指向报告包内部条目时,签名的是**所在归档对象**,`archive_path` 给出归档内路径、
    `is_archive_member=True` —— 客户端下载归档后按该路径自行提取。原始 object_uri / 桶名不外泄。
    """

    evidence_id: int
    visibility: EvidenceVisibility
    download_url: str
    expires_in: int
    expires_at: datetime
    sha256: str | None
    archive_path: str | None
    is_archive_member: bool


class HandoverCaseCreate(BaseModel):
    case_type: HandoverCaseType
    title: str = Field(min_length=1, max_length=255)
    subject_user_id: int | None = None
    namespace_id: int
    receiver_user_id: int | None = None
    fallback_owner_user_id: int | None = None
    due_at: datetime | None = None
    runtime_ids: list[int] = Field(default_factory=list)
    collection_scope: CollectionScope = Field(default_factory=CollectionScope)
    metadata_json: dict | None = None


class HandoverCaseUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    subject_user_id: int | None = None
    receiver_user_id: int | None = None
    fallback_owner_user_id: int | None = None
    due_at: datetime | None = None

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("title must not be blank")
        return stripped


class HandoverCaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_type: HandoverCaseType
    title: str
    subject_user_id: int | None
    namespace_id: int | None
    receiver_user_id: int | None
    fallback_owner_user_id: int | None
    status: HandoverStatus
    risk_level: Criticality
    due_at: datetime | None
    summary_json: dict | None
    created_by: int | None
    created_at: datetime
    updated_at: datetime


class HandoverItemCreate(BaseModel):
    asset_id: int
    recommended_action: HandoverAction = HandoverAction.MANUAL_REVIEW
    receiver_user_id: int | None = None
    risk_reason: str | None = None
    evidence_id: int | None = None


class HandoverItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    handover_case_id: int
    asset_id: int
    recommended_action: HandoverAction
    receiver_user_id: int | None
    risk_reason: str | None
    confidence: float
    evidence_id: int | None
    status: HandoverItemStatus
    requires_evidence: bool = False
    created_at: datetime


class ApprovalTaskCreate(BaseModel):
    approval_type: ApprovalType
    approver_user_id: int


class ApprovalTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    handover_case_id: int
    approver_user_id: int
    approval_type: ApprovalType
    status: ApprovalStatus
    comment: str | None
    decided_at: datetime | None
    created_at: datetime


class ApprovalDecision(BaseModel):
    decision: ApprovalStatus
    comment: str | None = None


class ExecutionActionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    handover_case_id: int
    handover_item_id: int | None
    action_type: str
    provider: RuntimeProvider
    status: ExecutionStatus
    execution_mode: ExecutionMode
    request_json: dict | None
    result_json: dict | None
    evidence_ids: list[int] = Field(default_factory=list)
    requires_evidence: bool = False
    idempotency_key: str | None
    created_at: datetime
    updated_at: datetime

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def _default_evidence_ids(cls, value):
        return value or []


class ExecuteHandoverRequest(BaseModel):
    idempotency_key: str | None = Field(default=None, max_length=128)
    selected_item_ids: list[int] | None = None
    execution_mode: Literal[ExecutionMode.MANUAL] = ExecutionMode.MANUAL


class ExecutionReceipt(BaseModel):
    """Record the result and evidence of a manually executed handover action."""

    result: ExecutionStatus
    note: str = Field(min_length=2, max_length=1000)
    evidence_ids: list[int] = Field(default_factory=list)
    idempotency_key: str | None = Field(default=None, max_length=128)


class HandoverVerifyRequest(BaseModel):
    """Archive a handover after the receiver or manager verifies completion."""

    note: str | None = Field(default=None, max_length=1000)
    acknowledge_failures: bool = Field(
        default=False,
        description="Must be true to archive a handover that contains failed actions or items.",
    )


class AIAssetFeedbackCreate(BaseModel):
    action: Literal["confirm", "supplement", "exclude"]
    note: str | None = Field(default=None, max_length=1000)
    metadata_json: dict | None = None


class MyWorkspaceProjectContextOut(BaseModel):
    key: str
    name: str
    asset_count: int = 0
    trace_count: int = 0
    last_seen_at: datetime | None = None
    sources: list[str] = Field(default_factory=list)


class MyWorkspaceReporterStatusOut(BaseModel):
    runtime: RuntimeInstanceOut
    latest_report: ReportUploadSessionOut | None = None
    state: Literal["healthy", "waiting", "failed", "unknown"]
    message: str


class MyWorkspaceReadinessOut(BaseModel):
    score: int
    reviewed_assets: int
    total_assets: int
    missing_items: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


class MyAIWorkspaceOut(BaseModel):
    user_id: int
    username: str
    employment_status: EmploymentStatus | None = None
    offboarding_visible: bool = False
    offboarding_case_count: int = 0
    assets: list[AIAssetOut]
    work_traces: list[WorkTraceOut]
    handovers: list[HandoverCaseOut]
    project_contexts: list[MyWorkspaceProjectContextOut]
    reporter_statuses: list[MyWorkspaceReporterStatusOut]
    readiness: MyWorkspaceReadinessOut
