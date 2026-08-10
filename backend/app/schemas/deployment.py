from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.deployment import AgentDeploymentStatus, DeploymentComponentRole


SHA256_PATTERN = r"^[0-9a-f]{64}$"
COMPONENT_KEY_PATTERN = r"^[a-z][a-z0-9._-]{0,127}$"
ENVIRONMENT_PATTERN = r"^[a-z][a-z0-9_-]{0,31}$"


class DeploymentComponentConfiguration(BaseModel):
    """Allowlisted, non-secret configuration summary.

    Raw prompts, messages, tool I/O and credentials are deliberately not part
    of this schema. Unknown keys are rejected.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str | None = Field(default=None, min_length=1, max_length=64)
    model: str | None = Field(default=None, min_length=1, max_length=128)
    tool_name: str | None = Field(default=None, min_length=1, max_length=128)
    endpoint_kind: str | None = Field(default=None, min_length=1, max_length=64)
    policy_id: str | None = Field(default=None, min_length=1, max_length=128)
    memory_backend: str | None = Field(default=None, min_length=1, max_length=64)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int | None = Field(default=None, ge=1, le=10_000_000)
    timeout_ms: int | None = Field(default=None, ge=1, le=86_400_000)
    region: str | None = Field(default=None, min_length=1, max_length=64)


class DeploymentComponentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component_key: str = Field(
        min_length=1,
        max_length=128,
        pattern=COMPONENT_KEY_PATTERN,
    )
    component_role: DeploymentComponentRole
    ai_asset_id: int | None = Field(default=None, ge=1)
    skill_version_id: int | None = Field(default=None, ge=1)
    external_version: str | None = Field(default=None, min_length=1, max_length=255)
    content_digest: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    configuration: DeploymentComponentConfiguration | None = None

    @field_validator("external_version")
    @classmethod
    def strip_external_version(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("external_version must not be blank")
        return value

    @model_validator(mode="after")
    def require_version_identity(self) -> "DeploymentComponentCreate":
        if not any(
            (
                self.ai_asset_id,
                self.skill_version_id,
                self.external_version,
                self.content_digest,
            )
        ):
            raise ValueError(
                "component requires an Asset, SkillVersion, external version "
                "reference or content digest"
            )
        return self


class AgentDeploymentCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "namespace_id": 7,
                "runtime_id": 12,
                "agent_asset_id": 44,
                "external_deployment_id": "agent-loop-primary",
                "environment": "staging",
                "revision": "2026.07.28-1",
                "configuration_digest": "a" * 64,
                "components": [
                    {
                        "component_key": "agent",
                        "component_role": "agent",
                        "ai_asset_id": 44,
                    }
                ],
            }
        },
    )

    namespace_id: int = Field(ge=1)
    runtime_id: int = Field(ge=1)
    agent_asset_id: int | None = Field(default=None, ge=1)
    package_version_public_id: str | None = Field(default=None, min_length=1, max_length=40)
    external_deployment_id: str = Field(min_length=1, max_length=255)
    environment: str = Field(
        min_length=1,
        max_length=32,
        pattern=ENVIRONMENT_PATTERN,
    )
    revision: str = Field(min_length=1, max_length=128)
    configuration_digest: str = Field(pattern=SHA256_PATTERN)
    components: list[DeploymentComponentCreate] = Field(min_length=1, max_length=256)

    @field_validator("external_deployment_id", "revision")
    @classmethod
    def strip_identity(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("deployment identity fields must not be blank")
        return value


class DeploymentComponentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    component_key: str
    component_role: DeploymentComponentRole
    ai_asset_id: int | None
    skill_version_id: int | None
    external_version: str | None
    content_digest: str | None
    configuration_json: dict | None
    created_at: datetime


class AgentDeploymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    runtime_id: int
    agent_asset_id: int | None
    package_version_public_id: str | None = None
    external_deployment_id: str
    environment: str
    revision: str
    configuration_digest: str
    status: AgentDeploymentStatus
    activated_at: datetime | None
    retired_at: datetime | None
    created_by_user_id: int
    created_at: datetime
    components: list[DeploymentComponentOut]
