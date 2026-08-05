from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.compatibility import ReconciliationStatus


class ReconciliationHealthOut(BaseModel):
    matched_count: int
    expected_legacy_only_count: int
    mismatch_count: int
    unexplained_difference_count: int


class V1V2ReconciliationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    namespace_id: int
    runtime_id: int
    work_trace_id: int
    source_report_id: str
    status: ReconciliationStatus
    reason_code: str
    linked_run_count: int
    linked_artifact_count: int
    checked_at: datetime
    created_at: datetime
    updated_at: datetime
