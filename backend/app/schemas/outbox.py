from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.outbox import OutboxEventStatus


class OutboxRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=200)


class OutboxEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: str
    namespace_id: int
    aggregate_type: str
    aggregate_public_id: str
    event_type: str
    schema_version: str
    status: OutboxEventStatus
    occurred_at: datetime
    available_at: datetime
    published_at: datetime | None
    attempt_count: int
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime


class OutboxHealthOut(BaseModel):
    pending_count: int
    leased_count: int
    expired_lease_count: int
    failed_count: int
    published_count: int
    oldest_pending_age_seconds: int | None
