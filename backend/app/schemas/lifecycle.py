from datetime import datetime
from pydantic import BaseModel


class RetentionPolicyUpdate(BaseModel):
    keep_last_n: int | None = None
    keep_days: int | None = None
    delete_rejected: bool = True


class RetentionPolicyOut(BaseModel):
    id: int
    namespace_id: int
    keep_last_n: int | None
    keep_days: int | None
    delete_rejected: bool
    updated_at: datetime

    model_config = {"from_attributes": True}


class QuotaUpdate(BaseModel):
    max_skills: int = 100
    max_versions_per_skill: int = 50
    max_total_versions: int = 2000
    max_storage_bytes: int = 536870912


class QuotaOut(BaseModel):
    id: int | None = None
    namespace_id: int
    max_skills: int
    max_versions_per_skill: int
    max_total_versions: int
    max_storage_bytes: int
    # live usage
    current_skills: int = 0
    current_total_versions: int = 0
    current_storage_bytes: int = 0

    model_config = {"from_attributes": True}
