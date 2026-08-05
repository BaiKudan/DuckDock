from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


GACheckStatus = Literal["PASS", "WARN", "BLOCK"]
GAReadinessStatus = Literal["READY", "READY_WITH_GAPS", "BLOCKED"]


class GAReadinessCheckOut(BaseModel):
    key: str
    title: str
    status: GACheckStatus
    observed: str
    expected: str
    detail: str


class GAReadinessOut(BaseModel):
    profile_version: str
    contract_version: str
    contract_digest: str
    expected_db_revision: str
    current_db_revision: str | None
    status: GAReadinessStatus
    pass_count: int
    warn_count: int
    block_count: int
    checked_at: datetime
    checks: list[GAReadinessCheckOut]
