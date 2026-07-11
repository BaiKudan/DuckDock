from datetime import datetime
from pydantic import BaseModel

from app.models.scan import ScanStatus, IssueSeverity


class ScanIssue(BaseModel):
    rule: str               # e.g. "BASH_INJECTION", "PROMPT_INJECTION"
    severity: IssueSeverity
    message: str
    file: str | None = None
    snippet: str | None = None
    line: int | None = None


class ScanResultOut(BaseModel):
    id: int
    version_id: int
    status: ScanStatus
    issues: list[ScanIssue] | None
    critical_count: int
    high_count: int
    medium_count: int
    low_count: int
    scanner_version: str
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ScanSummary(BaseModel):
    """Lightweight summary embedded in version responses."""
    status: ScanStatus
    critical_count: int
    high_count: int
    medium_count: int
    low_count: int
    completed_at: datetime | None
