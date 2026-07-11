from datetime import datetime
from pydantic import BaseModel
from app.models.replication import ReplicationTrigger, ReplicationJobStatus


class ReplicationRuleCreate(BaseModel):
    name: str
    destination_namespace: str
    filter_pattern: str | None = None
    trigger: ReplicationTrigger = ReplicationTrigger.MANUAL
    is_active: bool = True


class ReplicationRuleUpdate(BaseModel):
    name: str | None = None
    destination_namespace: str | None = None
    filter_pattern: str | None = None
    trigger: ReplicationTrigger | None = None
    is_active: bool | None = None


class ReplicationRuleOut(BaseModel):
    id: int
    name: str
    src_namespace_id: int
    dst_namespace_id: int
    src_namespace_name: str
    dst_namespace_name: str
    filter_pattern: str | None
    trigger: ReplicationTrigger
    is_active: bool
    created_at: datetime
    last_job_status: ReplicationJobStatus | None = None
    last_job_at: datetime | None = None

    model_config = {"from_attributes": True}


class ReplicationJobOut(BaseModel):
    id: int
    rule_id: int
    status: ReplicationJobStatus
    skills_copied: int
    skills_skipped: int
    skills_failed: int
    log: list | None
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
