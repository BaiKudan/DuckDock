from datetime import datetime

from pydantic import BaseModel

from app.models.sandbox_validation import SandboxValidationStatus


class SandboxCheckOut(BaseModel):
    name: str
    status: SandboxValidationStatus
    summary: str
    details: dict | None = None


class SandboxValidationRunOut(BaseModel):
    id: int
    version_id: int
    status: SandboxValidationStatus
    engine: str
    summary: str | None
    checks: list[SandboxCheckOut] | None
    logs: list[str] | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
