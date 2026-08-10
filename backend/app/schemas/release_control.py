from __future__ import annotations

import enum
import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.release_control import (
    ReleaseApprovalDecision,
    ReleaseApprovalRole,
    ReleaseCanaryOutcome,
    ReleaseEnvironmentKind,
    ReleaseEnvironmentReleaseStatus,
    ReleaseEnvironmentStatus,
    ReleaseExceptionReviewDecision,
    ReleasePolicyEnforcementOutcome,
    ReleasePolicyMode,
    ReleasePolicyRawOutcome,
    ReleasePolicyRuleType,
    ReleasePolicyRuleVerdict,
    ReleasePolicyStatus,
    ReleasePromotionStatus,
    ReleasePromotionStrategy,
    ReleaseReceiptKind,
    ReleaseReceiptStatus,
    ReleaseRollbackStatus,
)


OPAQUE_REF_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,254}$"
NAME_PATTERN = r"^[a-z][a-z0-9._-]{0,127}$"
RULE_ID_PATTERN = r"^[a-z][a-z0-9._-]{0,63}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
CAPABILITY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,99}$"
_LATEST_ALIASES = frozenset({"latest", "newest", "current"})


class VulnerabilitySeverity(str, enum.Enum):
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ReleaseEnvironmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(ge=1)
    name: str = Field(pattern=NAME_PATTERN, max_length=64)
    kind: ReleaseEnvironmentKind
    promotion_order: int = Field(ge=0, le=100)
    protected: bool = False
    minimum_approvals: int = Field(default=0, ge=0, le=10)
    requires_canary: bool = False

    @model_validator(mode="after")
    def validate_protection(self) -> "ReleaseEnvironmentCreate":
        if not self.protected and self.minimum_approvals:
            raise ValueError("unprotected Environment cannot require approvals")
        if self.kind == ReleaseEnvironmentKind.PRODUCTION and not self.protected:
            raise ValueError("PRODUCTION Environment must be protected")
        if self.kind == ReleaseEnvironmentKind.PRODUCTION and not self.requires_canary:
            raise ValueError("PRODUCTION Environment must require canary")
        return self


class ReleaseEnvironmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    name: str
    kind: ReleaseEnvironmentKind
    promotion_order: int
    protected: bool
    minimum_approvals: int
    requires_canary: bool
    status: ReleaseEnvironmentStatus
    created_by_user_id: int
    created_at: datetime


class ReleasePolicyRuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(pattern=RULE_ID_PATTERN)
    rule_type: ReleasePolicyRuleType
    allowed_risk_tiers: list[str] | None = Field(default=None, min_length=1, max_length=20)
    maximum_additions: int | None = Field(default=None, ge=0, le=512)
    allowed_capabilities: list[str] | None = Field(default=None, max_length=128)
    maximum_severity: VulnerabilitySeverity | None = None
    required: bool = True

    @field_validator("allowed_risk_tiers")
    @classmethod
    def normalize_risk_tiers(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        normalized = [value.strip().casefold() for value in values]
        if any(not value or len(value) > 100 for value in normalized):
            raise ValueError("risk tiers must be bounded non-empty values")
        if len(normalized) != len(set(normalized)):
            raise ValueError("risk tiers must be unique")
        return normalized

    @field_validator("allowed_capabilities")
    @classmethod
    def normalize_capabilities(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("capabilities must be non-empty")
        if any(re.fullmatch(CAPABILITY_PATTERN, value) is None for value in normalized):
            raise ValueError("capabilities must be bounded opaque identifiers")
        if len(normalized) != len(set(normalized)):
            raise ValueError("capabilities must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_rule_config(self) -> "ReleasePolicyRuleIn":
        expected = {
            ReleasePolicyRuleType.RISK_TIER_ALLOWED: "allowed_risk_tiers",
            ReleasePolicyRuleType.MAX_TOOL_ADDITIONS: "maximum_additions",
            ReleasePolicyRuleType.FORBID_CAPABILITY_EXPANSION: "allowed_capabilities",
            ReleasePolicyRuleType.MAX_VULNERABILITY_SEVERITY: "maximum_severity",
        }
        configured = {
            "allowed_risk_tiers": self.allowed_risk_tiers,
            "maximum_additions": self.maximum_additions,
            "allowed_capabilities": self.allowed_capabilities,
            "maximum_severity": self.maximum_severity,
        }
        required_field = expected.get(self.rule_type)
        for field_name, value in configured.items():
            if field_name == required_field:
                if value is None:
                    raise ValueError(f"{self.rule_type.value} requires {field_name}")
            elif value is not None:
                raise ValueError(f"{field_name} is not valid for {self.rule_type.value}")
        return self


class ReleasePolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(ge=1)
    name: str = Field(pattern=NAME_PATTERN)
    description: str | None = Field(default=None, max_length=1000)


class ReleasePolicyVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(ge=1)
    target_environment_public_id: str = Field(min_length=1, max_length=40, pattern=OPAQUE_REF_PATTERN)
    mode: ReleasePolicyMode
    rules: list[ReleasePolicyRuleIn] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def require_unique_rule_ids(self) -> "ReleasePolicyVersionCreate":
        rule_ids = [rule.rule_id for rule in self.rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("release policy rule_id values must be unique")
        return self


class ReleasePolicyVersionOut(BaseModel):
    public_id: str
    namespace_id: int
    policy_public_id: str
    version: int
    target_environment_public_id: str
    target_environment_name: str
    mode: ReleasePolicyMode
    rules: list[ReleasePolicyRuleIn]
    rule_count: int
    rules_digest: str = Field(pattern=SHA256_PATTERN)
    content_digest: str = Field(pattern=SHA256_PATTERN)
    schema_name: str
    schema_version: str
    created_by_user_id: int
    created_at: datetime


class ReleasePolicyOut(BaseModel):
    public_id: str
    namespace_id: int
    name: str
    description: str | None
    status: ReleasePolicyStatus
    created_by_user_id: int
    created_at: datetime
    versions: list[ReleasePolicyVersionOut]


class ReleaseCandidateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(ge=1)
    package_version_public_id: str = Field(min_length=1, max_length=40, pattern=OPAQUE_REF_PATTERN)
    deployment_public_id: str = Field(min_length=1, max_length=40, pattern=OPAQUE_REF_PATTERN)
    target_environment_public_id: str = Field(min_length=1, max_length=40, pattern=OPAQUE_REF_PATTERN)
    baseline_candidate_public_id: str | None = Field(
        default=None, min_length=1, max_length=40, pattern=OPAQUE_REF_PATTERN
    )
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=OPAQUE_REF_PATTERN)

    @field_validator(
        "package_version_public_id",
        "deployment_public_id",
        "target_environment_public_id",
        "baseline_candidate_public_id",
    )
    @classmethod
    def reject_latest_alias(cls, value: str | None) -> str | None:
        if value is not None and value.casefold() in _LATEST_ALIASES:
            raise ValueError("release candidate references must be exact")
        return value


class ReleaseCandidateOut(BaseModel):
    public_id: str
    namespace_id: int
    package_public_id: str
    package_name: str
    package_version_public_id: str
    package_version: str
    package_manifest_digest: str = Field(pattern=SHA256_PATTERN)
    deployment_public_id: str
    deployment_revision: str
    deployment_configuration_digest: str = Field(pattern=SHA256_PATTERN)
    runtime_id: int
    target_environment_public_id: str
    target_environment_name: str
    target_environment_kind: ReleaseEnvironmentKind
    baseline_candidate_public_id: str | None
    idempotency_key: str
    candidate_digest: str = Field(pattern=SHA256_PATTERN)
    schema_name: str
    schema_version: str
    created_by_user_id: int
    created_at: datetime


class ReleasePolicyEvaluationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(ge=1)
    policy_version_public_id: str = Field(min_length=1, max_length=40, pattern=OPAQUE_REF_PATTERN)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=OPAQUE_REF_PATTERN)


class ReleasePolicyRuleResultOut(BaseModel):
    position: int
    rule_id: str
    rule_type: ReleasePolicyRuleType
    verdict: ReleasePolicyRuleVerdict
    reason_code: str
    evidence_kind: str
    evidence_ref: str
    evidence_digest: str = Field(pattern=SHA256_PATTERN)
    metrics: dict
    observed_at: datetime


class ReleasePolicyDecisionOut(BaseModel):
    public_id: str
    namespace_id: int
    candidate_public_id: str
    policy_public_id: str
    policy_version_public_id: str
    policy_version: int
    policy_mode: ReleasePolicyMode
    raw_outcome: ReleasePolicyRawOutcome
    enforcement_outcome: ReleasePolicyEnforcementOutcome
    would_block: bool
    reason_codes: list[str]
    evidence_snapshot_digest: str = Field(pattern=SHA256_PATTERN)
    decision_digest: str = Field(pattern=SHA256_PATTERN)
    evaluation_duration_ms: int
    schema_name: str
    schema_version: str
    evaluated_by_user_id: int
    created_at: datetime
    rule_results: list[ReleasePolicyRuleResultOut]


class ReleaseCandidateApprovalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(ge=1)
    policy_decision_public_id: str = Field(min_length=1, max_length=40, pattern=OPAQUE_REF_PATTERN)
    decision: ReleaseApprovalDecision
    role: ReleaseApprovalRole
    comment: str | None = Field(default=None, max_length=1000)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=OPAQUE_REF_PATTERN)

    @field_validator("comment")
    @classmethod
    def normalize_comment(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def rejection_requires_comment(self) -> "ReleaseCandidateApprovalCreate":
        if self.decision == ReleaseApprovalDecision.REJECTED and (
            self.comment is None or len(self.comment) < 5
        ):
            raise ValueError("rejected approval requires a comment of at least 5 characters")
        return self


class ReleaseCandidateApprovalOut(BaseModel):
    public_id: str
    namespace_id: int
    candidate_public_id: str
    policy_decision_public_id: str
    decision: ReleaseApprovalDecision
    role: ReleaseApprovalRole
    comment: str | None
    approval_digest: str = Field(pattern=SHA256_PATTERN)
    reviewed_by_user_id: int
    created_at: datetime


class ReleasePolicyExceptionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(ge=1)
    policy_decision_public_id: str = Field(min_length=1, max_length=40, pattern=OPAQUE_REF_PATTERN)
    waived_rule_ids: list[str] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=10, max_length=2000)
    expires_at: datetime
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=OPAQUE_REF_PATTERN)

    @field_validator("waived_rule_ids")
    @classmethod
    def validate_waived_rules(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(re.fullmatch(RULE_ID_PATTERN, value) is None for value in normalized):
            raise ValueError("waived rule IDs must be bounded rule identifiers")
        if len(normalized) != len(set(normalized)):
            raise ValueError("waived rule IDs must be unique")
        return sorted(normalized)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 10:
            raise ValueError("exception reason must contain at least 10 characters")
        return normalized

    @field_validator("expires_at")
    @classmethod
    def require_aware_expiry(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must be timezone-aware")
        return value


class ReleasePolicyExceptionReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(ge=1)
    decision: ReleaseExceptionReviewDecision
    comment: str | None = Field(default=None, max_length=1000)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=OPAQUE_REF_PATTERN)

    @field_validator("comment")
    @classmethod
    def normalize_comment(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def rejection_requires_comment(self) -> "ReleasePolicyExceptionReviewCreate":
        if self.decision == ReleaseExceptionReviewDecision.REJECTED and (
            self.comment is None or len(self.comment) < 5
        ):
            raise ValueError("rejected exception review requires a comment of at least 5 characters")
        return self


class ReleasePolicyExceptionReviewOut(BaseModel):
    public_id: str
    decision: ReleaseExceptionReviewDecision
    comment: str | None
    review_digest: str = Field(pattern=SHA256_PATTERN)
    reviewed_by_user_id: int
    created_at: datetime


class ReleasePolicyExceptionOut(BaseModel):
    public_id: str
    namespace_id: int
    candidate_public_id: str
    policy_decision_public_id: str
    waived_rule_ids: list[str]
    waived_rule_count: int
    reason: str
    expires_at: datetime
    exception_digest: str = Field(pattern=SHA256_PATTERN)
    requested_by_user_id: int
    created_at: datetime
    effective: bool
    review: ReleasePolicyExceptionReviewOut | None


class ReleaseCanaryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_completed_runs: int = Field(ge=1, le=10000)
    maximum_failure_rate: float = Field(ge=0, le=1)
    maximum_untrusted_rate: float = Field(ge=0, le=1)
    observation_window_seconds: int = Field(ge=30, le=604800)


class ReleasePromotionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(ge=1)
    candidate_public_id: str = Field(min_length=1, max_length=40, pattern=OPAQUE_REF_PATTERN)
    policy_decision_public_id: str = Field(min_length=1, max_length=40, pattern=OPAQUE_REF_PATTERN)
    strategy: ReleasePromotionStrategy
    acknowledge_warnings: bool = False
    canary: ReleaseCanaryConfig | None = None
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=OPAQUE_REF_PATTERN)

    @model_validator(mode="after")
    def validate_canary_config(self) -> "ReleasePromotionCreate":
        if self.strategy == ReleasePromotionStrategy.CANARY and self.canary is None:
            raise ValueError("CANARY promotion requires canary configuration")
        if self.strategy != ReleasePromotionStrategy.CANARY and self.canary is not None:
            raise ValueError("canary configuration is only valid for CANARY promotion")
        return self


class ReleasePromotionOut(BaseModel):
    public_id: str
    dispatch_public_id: str
    namespace_id: int
    candidate_public_id: str
    policy_decision_public_id: str
    source_environment_public_id: str | None
    target_environment_public_id: str
    target_environment_name: str
    strategy: ReleasePromotionStrategy
    status: ReleasePromotionStatus
    acknowledge_warnings: bool
    canary: ReleaseCanaryConfig | None
    exception_digest: str | None
    approval_digest: str = Field(pattern=SHA256_PATTERN)
    dispatch_digest: str = Field(pattern=SHA256_PATTERN)
    requested_by_user_id: int
    created_at: datetime
    updated_at: datetime


class RuntimeReceiptReportedStatus(str, enum.Enum):
    APPLIED = "APPLIED"
    FAILED = "FAILED"


class ReleaseDeploymentReceiptCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dispatch_public_id: str = Field(min_length=1, max_length=40, pattern=OPAQUE_REF_PATTERN)
    kind: ReleaseReceiptKind
    external_receipt_id: str = Field(min_length=1, max_length=255, pattern=OPAQUE_REF_PATTERN)
    status: RuntimeReceiptReportedStatus
    observed_package_version_public_id: str = Field(
        min_length=1, max_length=40, pattern=OPAQUE_REF_PATTERN
    )
    observed_deployment_revision: str = Field(
        min_length=1, max_length=128, pattern=OPAQUE_REF_PATTERN
    )
    observed_configuration_digest: str = Field(pattern=SHA256_PATTERN)
    runtime_release_ref: str | None = Field(
        default=None, min_length=1, max_length=255, pattern=OPAQUE_REF_PATTERN
    )
    error_code: str | None = Field(default=None, min_length=1, max_length=100, pattern=OPAQUE_REF_PATTERN)
    occurred_at: datetime
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=OPAQUE_REF_PATTERN)

    @field_validator("occurred_at")
    @classmethod
    def require_aware_occurrence(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_error(self) -> "ReleaseDeploymentReceiptCreate":
        if self.status == RuntimeReceiptReportedStatus.APPLIED and self.error_code is not None:
            raise ValueError("APPLIED receipt cannot contain error_code")
        if self.status == RuntimeReceiptReportedStatus.FAILED and self.error_code is None:
            raise ValueError("FAILED receipt requires error_code")
        return self


class ReleaseDeploymentReceiptOut(BaseModel):
    public_id: str
    namespace_id: int
    runtime_id: int
    reporter_credential_id: int
    kind: ReleaseReceiptKind
    dispatch_public_id: str
    external_receipt_id: str
    status: ReleaseReceiptStatus
    observed_package_version_public_id: str
    observed_deployment_revision: str
    observed_configuration_digest: str = Field(pattern=SHA256_PATTERN)
    runtime_release_ref: str | None
    error_code: str | None
    receipt_digest: str = Field(pattern=SHA256_PATTERN)
    occurred_at: datetime
    created_at: datetime


class ReleaseEnvironmentReleaseOut(BaseModel):
    public_id: str
    namespace_id: int
    environment_public_id: str
    candidate_public_id: str
    promotion_public_id: str | None
    rollback_public_id: str | None
    receipt_public_id: str
    previous_release_public_id: str | None
    status: ReleaseEnvironmentReleaseStatus
    activation_digest: str = Field(pattern=SHA256_PATTERN)
    activated_at: datetime
    deactivated_at: datetime | None


class ReleaseCanaryEvaluationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=OPAQUE_REF_PATTERN)


class ReleaseCanaryEvaluationOut(BaseModel):
    public_id: str
    namespace_id: int
    promotion_public_id: str
    outcome: ReleaseCanaryOutcome
    reason_codes: list[str]
    window_start: datetime
    window_end: datetime
    completed_run_count: int
    failed_run_count: int
    untrusted_run_count: int
    failure_rate: float
    untrusted_rate: float
    evidence_digest: str = Field(pattern=SHA256_PATTERN)
    decision_digest: str = Field(pattern=SHA256_PATTERN)
    evaluated_by_user_id: int
    created_at: datetime


class ReleaseRollbackCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(ge=1)
    reason_code: str = Field(min_length=1, max_length=100, pattern=OPAQUE_REF_PATTERN)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=OPAQUE_REF_PATTERN)


class ReleaseRollbackOut(BaseModel):
    public_id: str
    dispatch_public_id: str
    namespace_id: int
    promotion_public_id: str
    environment_public_id: str
    source_candidate_public_id: str
    target_environment_release_public_id: str
    target_candidate_public_id: str
    status: ReleaseRollbackStatus
    reason_code: str
    dispatch_digest: str = Field(pattern=SHA256_PATTERN)
    requested_by_user_id: int
    created_at: datetime
    completed_at: datetime | None


class ReleaseReceiptCredentialCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(ge=1)
    runtime_id: int = Field(ge=1)
    device_id: str = Field(min_length=1, max_length=128, pattern=OPAQUE_REF_PATTERN)
    name: str = Field(min_length=1, max_length=128)
    expires_at: datetime | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("expires_at")
    @classmethod
    def require_aware_expiry(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("expires_at must be timezone-aware")
        return value
