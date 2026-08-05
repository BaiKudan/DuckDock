from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.operations import (
    OpsIncidentSeverity,
    OpsIncidentStatus,
    OpsRecoveryDrillStatus,
    OpsSLOEvaluationStatus,
)


class OpsSLOEvaluationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    window_minutes: int = Field(default=15, ge=1, le=1440)


class OpsSLOEvaluationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    public_id: str
    idempotency_key: str
    profile_version: str
    window_minutes: int
    request_count: int
    error_count: int
    http_error_ratio: float | None
    evidence_ingest_p95_ms: float | None
    run_timeline_p95_ms: float | None
    policy_decision_p95_ms: float | None
    outbox_failed_count: int
    outbox_oldest_pending_age_seconds: int | None
    status: OpsSLOEvaluationStatus
    reason_codes: list[str] = Field(validation_alias="reason_codes_json")
    evidence_digest: str
    evaluated_by_user_id: int
    evaluated_at: datetime
    created_at: datetime


class OpsIncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    public_id: str
    slo_evaluation_id: int
    severity: OpsIncidentSeverity
    status: OpsIncidentStatus
    reason_codes: list[str] = Field(validation_alias="reason_codes_json")
    evidence_digest: str
    acknowledged_by_user_id: int | None
    acknowledged_at: datetime | None
    resolved_by_user_id: int | None
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class OpsIncidentAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(min_length=1, max_length=200)


class OpsRecoveryDrillCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    environment: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")
    git_head: str = Field(min_length=7, max_length=64, pattern=r"^[0-9a-f]+$")
    backup_set_digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    mysql_digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    object_store_digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    mysql_row_count: int = Field(ge=0, le=1_000_000_000)
    object_count: int = Field(ge=0, le=1_000_000_000)
    rpo_seconds: int = Field(ge=0, le=31_536_000)
    rto_seconds: int = Field(ge=0, le=31_536_000)
    started_at: datetime
    finished_at: datetime


class OpsRecoveryDrillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    public_id: str
    idempotency_key: str
    environment: str
    git_head: str
    backup_set_digest: str
    mysql_digest: str
    object_store_digest: str
    mysql_row_count: int
    object_count: int
    rpo_seconds: int
    rto_seconds: int
    status: OpsRecoveryDrillStatus
    reason_codes: list[str] = Field(validation_alias="reason_codes_json")
    evidence_digest: str
    executed_by_user_id: int
    started_at: datetime
    finished_at: datetime
    created_at: datetime


class OpsRouteMetricOut(BaseModel):
    route: str
    method: str
    request_count: int
    error_count: int
    p95_ms: float | None


class OpsSLOThresholdsOut(BaseModel):
    http_error_ratio_max: float
    evidence_ingest_p95_ms_max: float
    run_timeline_p95_ms_max: float
    policy_decision_p95_ms_max: float
    outbox_failed_count_max: int
    outbox_pending_age_seconds_max: int
    recovery_rpo_seconds_max: int
    recovery_rto_seconds_max: int


class OpsOverviewOut(BaseModel):
    profile_version: str
    metrics_path: str
    thresholds: OpsSLOThresholdsOut
    route_metrics: list[OpsRouteMetricOut]
    outbox: dict[str, int | None]
    latest_evaluation: OpsSLOEvaluationOut | None
    open_incidents: list[OpsIncidentOut]
    latest_recovery_drill: OpsRecoveryDrillOut | None
