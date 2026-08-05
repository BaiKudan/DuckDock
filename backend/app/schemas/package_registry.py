from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.package_registry import (
    AgentPackageStatus,
    AgentPackageVersionStatus,
    PackageComponentType,
    PackageDependencyRelationship,
    PackageSbomFormat,
    PackageSignatureAlgorithm,
    PackageSigningKeyStatus,
)


SHA256_PATTERN = r"^[0-9a-f]{64}$"
SEMVER_PATTERN = r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$"
SOURCE_REVISION_PATTERN = r"^[0-9a-fA-F]{7,64}$"
OPAQUE_REF_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,254}$"
KEY_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$"
PACKAGE_NAME_PATTERN = r"^[a-z][a-z0-9._-]{0,127}$"
SIGNATURE_PATTERN = r"^[A-Za-z0-9+/]+={0,2}$"


def _absolute_uri(value: str, *, field: str) -> str:
    value = value.strip()
    if not value or not urlsplit(value).scheme:
        raise ValueError(f"{field} must be an absolute URI")
    return value


class AgentPackageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(ge=1)
    name: str = Field(pattern=PACKAGE_NAME_PATTERN)
    description: str | None = Field(default=None, max_length=1000)
    agent_asset_id: int = Field(ge=1)


class AgentPackageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    namespace_ref: str
    name: str
    description: str | None
    agent_asset_id: int
    agent_asset_ref: str
    status: AgentPackageStatus
    created_by_user_id: int
    created_at: datetime
    updated_at: datetime
    version_count: int = 0


class PackageSigningKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(ge=1)
    key_id: str = Field(pattern=KEY_ID_PATTERN)
    algorithm: Literal[PackageSignatureAlgorithm.ED25519] = PackageSignatureAlgorithm.ED25519
    public_key_pem: str = Field(min_length=64, max_length=4096)


class PackageSigningKeyRotate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key_id: str = Field(pattern=KEY_ID_PATTERN)
    algorithm: Literal[PackageSignatureAlgorithm.ED25519] = PackageSignatureAlgorithm.ED25519
    public_key_pem: str = Field(min_length=64, max_length=4096)


class PackageSigningKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    key_id: str
    algorithm: PackageSignatureAlgorithm
    public_key_fingerprint: str
    status: PackageSigningKeyStatus
    created_by_user_id: int
    revoked_by_user_id: int | None
    revoked_at: datetime | None
    rotated_from_id: int | None
    rotation_sequence: int
    created_at: datetime


class PackageAgentManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class PackageRuntimeManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    harness: str = Field(min_length=1, max_length=100)
    entrypoint: str = Field(min_length=1, max_length=500)
    minimum_harness_version: str = Field(min_length=1, max_length=100)
    capabilities: list[str] = Field(max_length=128)

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value or len(value) > 100 for value in cleaned):
            raise ValueError("runtime capabilities must contain bounded non-empty values")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("runtime capabilities must be unique")
        return cleaned


class PackageArtifactManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: PackageComponentType
    asset_id: str = Field(min_length=1, max_length=255)
    version_id: str = Field(min_length=1, max_length=255)
    uri: str = Field(min_length=1, max_length=1500)
    sha256: str = Field(pattern=SHA256_PATTERN)
    media_type: str = Field(min_length=1, max_length=200)

    @field_validator("uri")
    @classmethod
    def validate_uri(cls, value: str) -> str:
        return _absolute_uri(value, field="artifact uri")

    @property
    def component_ref(self) -> str:
        return f"{self.type.value}:{self.asset_id}:{self.version_id}"


class PackageProvenanceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_repository: str = Field(min_length=1, max_length=1000)
    source_revision: str = Field(pattern=SOURCE_REVISION_PATTERN)
    built_at: datetime
    builder_id: str = Field(min_length=1, max_length=255)
    build_id: str = Field(min_length=1, max_length=255)

    @field_validator("source_repository")
    @classmethod
    def validate_repository(cls, value: str) -> str:
        return _absolute_uri(value, field="source_repository")

    @field_validator("built_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("built_at must be timezone-aware")
        return value


class PackageTelemetryManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    content_policy: Literal["disabled", "metadata_only", "sampled_content", "full_content"]
    required_attributes: list[
        Literal[
            "duckdock.namespace_id",
            "duckdock.agent_asset_id",
            "duckdock.runtime_instance_id",
            "duckdock.release_id",
            "duckdock.skill_version_id",
            "duckdock.prompt_version_id",
            "duckdock.deployment_revision",
            "duckdock.component_digests",
        ]
    ] = Field(min_length=1, max_length=8)

    @field_validator("required_attributes")
    @classmethod
    def require_unique_attributes(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("telemetry required_attributes must be unique")
        return values


class PackageComponentEdgeManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_component: str = Field(min_length=1, max_length=600)
    to_component: str = Field(min_length=1, max_length=600)
    relationship: PackageDependencyRelationship


class PackageComponentGraphManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edges: list[PackageComponentEdgeManifest] = Field(max_length=4096)


class PackageSbomManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: PackageSbomFormat
    spec_version: str = Field(min_length=1, max_length=32)
    document_sha256: str = Field(pattern=SHA256_PATTERN)
    media_type: Literal["application/vnd.cyclonedx+json", "application/spdx+json"]

    @model_validator(mode="after")
    def require_matching_media_type(self) -> "PackageSbomManifest":
        expected = {
            PackageSbomFormat.CYCLONEDX_JSON: "application/vnd.cyclonedx+json",
            PackageSbomFormat.SPDX_JSON: "application/spdx+json",
        }[self.format]
        if self.media_type != expected:
            raise ValueError("SBOM media_type must match format")
        return self


class PackageAnnotationsManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: str | None = Field(default=None, max_length=1000)
    data_classification: str | None = Field(default=None, max_length=1000)
    risk_tier: str | None = Field(default=None, max_length=1000)
    cost_center: str | None = Field(default=None, max_length=1000)
    repository: str | None = Field(default=None, max_length=1000)
    documentation_url: str | None = Field(default=None, max_length=1000)


class AgentPackageManifestV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2.0"]
    package_id: str = Field(min_length=1, max_length=40)
    package_version: str = Field(pattern=SEMVER_PATTERN, max_length=128)
    namespace_id: str = Field(min_length=1, max_length=128)
    agent: PackageAgentManifest
    runtime: PackageRuntimeManifest
    artifacts: list[PackageArtifactManifest] = Field(min_length=1, max_length=512)
    provenance: PackageProvenanceManifest
    telemetry: PackageTelemetryManifest
    evaluation_policy_id: str = Field(pattern=OPAQUE_REF_PATTERN, max_length=255)
    component_graph: PackageComponentGraphManifest
    sbom: PackageSbomManifest
    annotations: PackageAnnotationsManifest | None = None

    @model_validator(mode="after")
    def require_unique_components(self) -> "AgentPackageManifestV2":
        refs = [artifact.component_ref for artifact in self.artifacts]
        if len(refs) != len(set(refs)):
            raise ValueError("manifest component refs must be unique")
        return self


class AgentPackageVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(ge=1)
    package_public_id: str = Field(min_length=1, max_length=40)
    signing_key_public_id: str = Field(min_length=1, max_length=40)
    signature: str = Field(min_length=80, max_length=512, pattern=SIGNATURE_PATTERN)
    idempotency_key: str = Field(pattern=OPAQUE_REF_PATTERN, max_length=128)
    manifest: AgentPackageManifestV2
    sbom_document: dict[str, Any]


class PackageComponentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    position: int
    component_ref: str
    component_type: PackageComponentType
    asset_ref: str
    version_ref: str
    uri: str
    sha256: str
    media_type: str


class PackageDependencyOut(BaseModel):
    from_component: str
    to_component: str
    relationship: PackageDependencyRelationship


class PackageSbomOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    format: PackageSbomFormat
    spec_version: str
    media_type: str
    document_sha256: str
    size_bytes: int
    component_count: int
    stored_at: datetime


class AgentPackageVersionOut(BaseModel):
    public_id: str
    namespace_id: int
    package_public_id: str
    package_name: str
    version: str
    status: AgentPackageVersionStatus
    manifest_schema_version: str
    manifest: AgentPackageManifestV2
    manifest_digest: str
    graph_digest: str
    evaluation_policy_id: str
    provenance_digest: str
    signing_key_public_id: str
    signing_key_id: str
    signing_key_fingerprint: str
    signing_key_status: PackageSigningKeyStatus
    signature_algorithm: PackageSignatureAlgorithm
    signature: str
    signature_digest: str
    signature_verified_at: datetime
    idempotency_key: str
    created_by_user_id: int
    created_at: datetime
    components: list[PackageComponentOut]
    dependencies: list[PackageDependencyOut]
    sbom: PackageSbomOut


class AgentPackageVersionVerificationOut(BaseModel):
    package_version_public_id: str
    manifest_digest: str
    graph_digest: str
    provenance_digest: str
    signature_valid: bool
    signature_verified_at: datetime
    signing_key_status: PackageSigningKeyStatus
    signing_key_fingerprint: str
    sbom_digest_valid: bool
    sbom_component_coverage_valid: bool
    sbom_document_sha256: str
    verified: bool


class PackageSbomDownloadOut(BaseModel):
    package_version_public_id: str
    sbom_public_id: str
    document_sha256: str
    download_url: str
    expires_in: int
    expires_at: datetime
