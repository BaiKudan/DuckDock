from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.models.skill import SkillVersionStatus
from app.models.sync_event import SyncEventEntity, SyncEventOperation, SyncEventState


class RegistryVersionEntry(BaseModel):
    namespace: str
    skill: str
    description: str | None = None
    tag: str
    commit_sha: str
    status: SkillVersionStatus
    created_at: datetime
    updated_at: datetime
    sync_cursor: datetime
    distribution_ready: bool
    manifest_path: str
    artifact_path: str
    artifact_sha256: str | None = None
    artifact_size_bytes: int | None = None
    manifest_url: str | None = None
    artifact_url: str | None = None
    urls_expire_at: datetime | None = None
    skill_metadata: dict[str, Any] | None = None


class RegistryIndexResponse(BaseModel):
    schema_version: str
    generated_at: datetime
    changed_since: datetime | None = None
    next_cursor: datetime
    include_urls: bool
    expires_in: int | None = None
    latest_only: bool
    include_non_production: bool
    items: list[RegistryVersionEntry]


class RegistryManifestResponse(BaseModel):
    manifest: dict[str, Any]
    manifest_url: str | None = None
    artifact_url: str | None = None
    expires_at: datetime | None = None
    expires_in: int | None = None


class RegistrySignedUrlResponse(BaseModel):
    namespace: str
    skill: str
    tag: str
    artifact_url: str
    manifest_url: str
    expires_at: datetime
    expires_in: int


class RegistrySyncEventEntry(BaseModel):
    cursor: int
    namespace: str
    skill: str
    tag: str | None = None
    entity: SyncEventEntity
    operation: SyncEventOperation
    sync_state: SyncEventState
    event: str
    is_public: bool
    version_status: str | None = None
    commit_sha: str | None = None
    reason: str | None = None
    payload: dict[str, Any] | None = None
    created_at: datetime


class RegistrySyncEventsResponse(BaseModel):
    schema_version: str
    generated_at: datetime
    since_cursor: int | None = None
    next_cursor: int
    limit: int
    include_state_events: bool
    items: list[RegistrySyncEventEntry]
