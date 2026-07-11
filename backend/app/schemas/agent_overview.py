from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.control_plane import AgentInsightStatus
from app.schemas.control_plane import AIAssetOut, MemoryCandidateOut, RuntimeInstanceOut, WorkTraceOut


class AgentOverviewUserOut(BaseModel):
    id: int
    username: str
    email: str
    full_name: str | None = None


class AgentOverviewReporterOut(BaseModel):
    credential_id: int | None = None
    token_prefix: str | None = None
    state: Literal["healthy", "waiting", "failed", "unknown"]
    message: str
    last_heartbeat_at: datetime | None = None
    last_used_at: datetime | None = None
    heartbeat_json: dict | None = None


class AgentOverviewMetricsOut(BaseModel):
    asset_count: int = 0
    work_trace_count: int = 0
    report_count: int = 0
    structured_report_count: int = 0
    pack_report_count: int = 0
    risk_signal_count: int = 0
    handover_signal_count: int = 0
    blocker_count: int = 0
    latest_report_at: datetime | None = None
    latest_activity_at: datetime | None = None


class AgentReportTimelineItemOut(BaseModel):
    report_id: str
    source: Literal["structured_report", "report_pack"]
    report_type: str
    status: str
    title: str
    summary: str | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    created_at: datetime
    collection_job_id: int | None = None
    work_trace_id: int | None = None
    upload_session_id: int | None = None
    analysis_job_id: int | None = None
    highlights: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    project_refs: list[str] = Field(default_factory=list)
    asset_count: int = 0
    memory_candidate_count: int = 0
    risk_signal_count: int = 0
    handover_signal_count: int = 0


class AgentInsightJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    runtime_id: int
    requested_by: int | None
    status: AgentInsightStatus
    prompt_version: str
    model: str | None
    input_hash: str | None
    ai_assist_json: dict | None
    result_json: dict | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AgentOverviewOut(BaseModel):
    user: AgentOverviewUserOut
    runtime: RuntimeInstanceOut
    reporter: AgentOverviewReporterOut
    metrics: AgentOverviewMetricsOut
    timeline: list[AgentReportTimelineItemOut]
    assets: list[AIAssetOut]
    work_traces: list[WorkTraceOut]
    memory_candidates: list[MemoryCandidateOut]
    latest_insight: AgentInsightJobOut | None = None


class AgentInsightCreateOut(BaseModel):
    job: AgentInsightJobOut
    queued: bool = True
