from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.control_plane import WorkloadIdentityKind
from app.models.iam import DirectoryLifecycleAction, DirectoryLifecycleSource


class DirectoryCredentialCreate(BaseModel):
    provider_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=128)
    expires_at: datetime | None = None


class DirectoryCredentialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    provider_id: int
    name: str
    token_prefix: str
    scopes_json: list[str]
    is_active: bool
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_by_user_id: int
    created_at: datetime


class DirectoryCredentialCreatedOut(DirectoryCredentialOut):
    token: str


class DirectoryCredentialRevoke(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class ScimPatchOperation(BaseModel):
    op: Literal["replace", "Replace", "REPLACE"]
    path: str | None = Field(default=None, max_length=128)
    value: bool | dict[str, Any]


class ScimPatchRequest(BaseModel):
    schemas: list[str] = Field(min_length=1, max_length=10)
    operations: list[ScimPatchOperation] = Field(
        alias="Operations", min_length=1, max_length=20
    )

    @field_validator("schemas")
    @classmethod
    def require_patch_schema(cls, value: list[str]) -> list[str]:
        if "urn:ietf:params:scim:api:messages:2.0:PatchOp" not in value:
            raise ValueError("SCIM PatchOp schema is required")
        return value


class DirectoryLifecycleEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    provider_id: int
    external_event_id: str
    subject_digest: str
    payload_digest: str
    action: DirectoryLifecycleAction
    source: DirectoryLifecycleSource
    user_id: int
    user_was_active: bool
    credentials_revoked: int
    runtime_tokens_revoked: int
    memberships_removed: int
    role_bindings_removed: int
    handover_case_ids_json: list[int]
    outcome_digest: str
    occurred_at: datetime


class ScimUserLifecycleOut(BaseModel):
    schemas: list[str] = ["urn:ietf:params:scim:schemas:core:2.0:User"]
    id: str
    active: bool
    meta: dict[str, str]
    duckdock_event: DirectoryLifecycleEventOut


class WorkloadIdentityCreate(BaseModel):
    namespace_id: int = Field(ge=1)
    runtime_id: int = Field(ge=1)
    principal_kind: WorkloadIdentityKind
    name: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    scopes: list[str] = Field(min_length=1, max_length=16)
    expires_at: datetime | None = None


class WorkloadIdentityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    public_id: str
    runtime_id: int
    user_id: int | None
    device_id: str
    name: str
    token_prefix: str
    scopes: list[str] | None
    principal_kind: WorkloadIdentityKind
    generation: int
    is_active: bool
    expires_at: datetime | None
    revoked_at: datetime | None
    rotated_from_id: int | None
    last_used_at: datetime | None
    last_heartbeat_at: datetime | None
    created_at: datetime


class WorkloadIdentityCreatedOut(WorkloadIdentityOut):
    token: str


class WorkloadIdentityRotate(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    expires_at: datetime | None = None


class WorkloadIdentityRevoke(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
