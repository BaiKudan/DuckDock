from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.tenant_resolution_service import TenantEntityType


class TenantRemediationAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: TenantEntityType
    target_id: int = Field(gt=0)
    namespace_id: int = Field(gt=0)
    reason: str | None = Field(default=None, min_length=3, max_length=1_000)


class TenantRemediationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1, le=1)
    manifest_id: str = Field(
        min_length=3,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    change_ticket: str = Field(
        min_length=3,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    )
    reason: str = Field(min_length=10, max_length=1_000)
    approved_by_user_id: int = Field(gt=0)
    assignments: list[TenantRemediationAssignment] = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def assignments_must_be_unique(self):
        keys = [(item.target_type, item.target_id) for item in self.assignments]
        if len(keys) != len(set(keys)):
            raise ValueError("manifest contains duplicate target assignments")
        return self
