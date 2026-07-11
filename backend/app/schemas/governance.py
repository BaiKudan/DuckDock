from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class NamespaceGovernancePolicyUpdate(BaseModel):
    manual_review_required: bool
    require_examples: bool
    require_validation_spec: bool
    require_sandbox_success: bool
    clinic_gate_enabled: bool
    min_clinic_score: float | None = None
    clinic_max_age_hours: int
    public_sharing_requires_approval: bool
    public_share_default_expiry_days: int | None = None
    require_license_attestation: bool
    allowed_public_licenses: list[str] | None = None
    sandbox_network_mode: str
    sandbox_workspace_mode: str
    sandbox_agent_smoke_enabled: bool
    sandbox_agent_smoke_timeout_seconds: int | None = None


class NamespaceGovernancePolicyOut(BaseModel):
    id: int | None = None
    namespace_id: int
    manual_review_required: bool
    require_examples: bool
    require_validation_spec: bool
    require_sandbox_success: bool
    clinic_gate_enabled: bool
    min_clinic_score: float | None = None
    clinic_max_age_hours: int
    public_sharing_requires_approval: bool
    public_share_default_expiry_days: int | None = None
    require_license_attestation: bool
    allowed_public_licenses: list[str] | None = None
    sandbox_network_mode: str
    sandbox_workspace_mode: str
    sandbox_agent_smoke_enabled: bool
    sandbox_agent_smoke_timeout_seconds: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class SkillPackageTemplateOut(BaseModel):
    key: str
    name: str
    description: str
    recommended_files: list[str]
    package_files: dict[str, str]


class PackageValidationRequest(BaseModel):
    package_files: dict[str, str]
    expected_tag: str | None = None


class PackageValidationResponse(BaseModel):
    ok: bool
    metadata: dict | None = None
    warnings: list[str]
    errors: list[str]
    validation_spec_present: bool
    example_file_count: int
    file_count: int
