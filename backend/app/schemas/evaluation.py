from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.evaluation import (
    EvaluationAnnotationDispatchStatus,
    EvaluationAnnotationItemSyncStatus,
    EvaluationAnnotationProviderStatus,
    EvaluationAnnotationQueueBindingStatus,
    EvaluationCaseRoutingLane,
    EvaluationCaseRoutingOutcome,
    EvaluationCaseRoutingPolicyStatus,
    EvaluationCaseRoutingStrategy,
    EvaluationComparisonOutcome,
    EvaluationDatasetCurationDecision,
    EvaluationDatasetSourceType,
    EvaluationDatasetStatus,
    EvaluationExperienceActivationDecision,
    EvaluationExperienceAssetVersionStatus,
    EvaluationExperienceCandidateStatus,
    EvaluationExperienceExtractionOutcome,
    EvaluationExperienceReviewDecision,
    EvaluationFailureCategory,
    EvaluationFailureTaxonomyPolicyStatus,
    EvaluationProvider,
    EvaluationPromotionDiversityDimension,
    EvaluationPromotionOutcome,
    EvaluationPromotionPolicyStatus,
    EvaluationPromotionScoreDataType,
    EvaluationResultCompleteness,
    EvaluationSamplingPolicyStatus,
    EvaluationSamplingStrategy,
    EvaluationSemanticClusteringOutcome,
    EvaluationSemanticClusteringPolicyStatus,
    EvaluationSemanticMonitorAlertSeverity,
    EvaluationSemanticMonitorAlertStatus,
    EvaluationSemanticMonitorRunStatus,
    EvaluationSemanticMonitorStatus,
    EvaluationSemanticRegressionOutcome,
    EvaluationSemanticRegressionPolicyStatus,
    EvaluationStatus,
    EvaluatorKind,
    EvaluatorStatus,
    ExperimentStatus,
    RegressionPolicyStatus,
)


_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PUBLIC_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]*$")
_IMPLEMENTATION_REF = re.compile(
    r"^(?:prompt|artifact|deepeval|rule|provider)://"
    r"[A-Za-z0-9][A-Za-z0-9._:/@-]*$"
)


def _safe_name(value: str) -> str:
    candidate = value.strip()
    if _SAFE_NAME.fullmatch(candidate) is None:
        raise ValueError("value contains unsupported characters")
    return candidate


def _sha256(value: str) -> str:
    if _SHA256.fullmatch(value) is None:
        raise ValueError("digest must be 64 lowercase hexadecimal characters")
    return value


class EvaluationDatasetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=500)
    provider: EvaluationProvider = EvaluationProvider.LANGFUSE
    provider_dataset_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )
    sync_provider: bool = True

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _safe_name(value)

    @field_validator("provider_dataset_ref")
    @classmethod
    def _provider_ref(cls, value: str | None) -> str | None:
        if value is not None and _PUBLIC_REF.fullmatch(value) is None:
            raise ValueError("provider_dataset_ref is malformed")
        return value

    @model_validator(mode="after")
    def _provider_sync_contract(self):
        if self.sync_provider and self.provider != EvaluationProvider.LANGFUSE:
            raise ValueError("automatic provider sync currently supports LANGFUSE only")
        return self


class EvaluationDatasetVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_digest: str = Field(min_length=64, max_length=64)
    item_count: int = Field(ge=0)
    schema_name: str = Field(min_length=1, max_length=100)
    schema_version: str = Field(min_length=1, max_length=50)
    provider_version_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )

    @field_validator("content_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        return _sha256(value)

    @field_validator("schema_name", "schema_version")
    @classmethod
    def _schema(cls, value: str) -> str:
        return _safe_name(value)

    @field_validator("provider_version_ref")
    @classmethod
    def _provider_version(cls, value: str | None) -> str | None:
        if value is not None and _PUBLIC_REF.fullmatch(value) is None:
            raise ValueError("provider_version_ref is malformed")
        return value


class EvaluationDatasetVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    version: int
    content_digest: str
    item_count: int
    schema_name: str
    schema_version: str
    provider_version_ref: str | None
    created_at: datetime


class EvaluationDatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    name: str
    description: str | None
    provider: EvaluationProvider
    provider_dataset_ref: str | None
    status: EvaluationDatasetStatus
    versions: list[EvaluationDatasetVersionOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class TraceDatasetMaterializationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str = Field(min_length=32, max_length=32)
    observation_id: str | None = Field(
        default=None,
        min_length=16,
        max_length=16,
    )

    @field_validator("trace_id")
    @classmethod
    def _trace_id(cls, value: str) -> str:
        candidate = value.strip().lower()
        if re.fullmatch(r"[0-9a-f]{32}", candidate) is None:
            raise ValueError("trace_id must be 32 hexadecimal characters")
        return candidate

    @field_validator("observation_id")
    @classmethod
    def _observation_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        candidate = value.strip().lower()
        if re.fullmatch(r"[0-9a-f]{16}", candidate) is None:
            raise ValueError("observation_id must be 16 hexadecimal characters")
        return candidate


class EvaluationDatasetMaterializationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    dataset_public_id: str
    dataset_version_public_id: str
    dataset_version: int
    source_type: EvaluationDatasetSourceType
    source_trace_ref: str
    source_observation_ref: str
    provider_dataset_item_ref: str
    provider_version_ref: str
    manifest_digest: str
    item_count: int
    schema_name: str
    schema_version: str
    created_by_user_id: int
    created_at: datetime


class TraceDatasetCandidateOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_trace_ref: str
    source_observation_ref: str
    name: str
    observation_type: str
    start_time: datetime
    end_time: datetime | None
    environment: str | None
    already_materialized: bool
    already_governed: bool


class EvaluationDatasetCurationItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str = Field(min_length=32, max_length=32)
    observation_id: str = Field(min_length=16, max_length=16)

    @field_validator("trace_id")
    @classmethod
    def _trace_id(cls, value: str) -> str:
        candidate = value.strip().lower()
        if re.fullmatch(r"[0-9a-f]{32}", candidate) is None:
            raise ValueError("trace_id must be 32 hexadecimal characters")
        return candidate

    @field_validator("observation_id")
    @classmethod
    def _observation_id(cls, value: str) -> str:
        candidate = value.strip().lower()
        if re.fullmatch(r"[0-9a-f]{16}", candidate) is None:
            raise ValueError("observation_id must be 16 hexadecimal characters")
        return candidate


class EvaluationDatasetCurationBatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[EvaluationDatasetCurationItemCreate] = Field(
        min_length=1,
        max_length=20,
    )

    @model_validator(mode="after")
    def _unique_sources(self):
        sources = {(item.trace_id, item.observation_id) for item in self.items}
        if len(sources) != len(self.items):
            raise ValueError("curation batch contains duplicate sources")
        return self


class EvaluationDatasetCurationReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: EvaluationDatasetCurationDecision
    comment: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _review_contract(self):
        comment = self.comment.strip() if self.comment is not None else None
        if self.decision == EvaluationDatasetCurationDecision.REJECTED and (comment is None or len(comment) < 5):
            raise ValueError("rejection comment must contain at least 5 chars")
        self.comment = comment or None
        return self


class EvaluationDatasetCurationItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    position: int
    source_trace_ref: str
    source_observation_ref: str
    provider_dataset_item_ref: str | None = None


class EvaluationDatasetCurationReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    decision: EvaluationDatasetCurationDecision
    comment: str | None
    review_digest: str
    reviewed_by_user_id: int
    created_at: datetime


class EvaluationDatasetCurationMaterializationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    dataset_version_public_id: str
    dataset_version: int
    provider_version_ref: str
    manifest_digest: str
    selected_item_count: int
    dataset_item_count: int
    created_by_user_id: int
    created_at: datetime


class EvaluationDatasetCurationBatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    dataset_public_id: str
    status: str
    selection_digest: str
    item_count: int
    schema_name: str
    schema_version: str
    submitted_by_user_id: int
    created_at: datetime
    items: list[EvaluationDatasetCurationItemOut]
    review: EvaluationDatasetCurationReviewOut | None
    materialization: EvaluationDatasetCurationMaterializationOut | None


class EvaluationSamplingPolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _safe_name(value)


class EvaluationSamplingPolicyVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: EvaluationSamplingStrategy = EvaluationSamplingStrategy.STABLE_HASH
    sample_size: int = Field(default=10, ge=1, le=20)
    minimum_sample_size: int = Field(default=1, ge=1, le=20)
    candidate_limit: int = Field(default=100, ge=1, le=100)
    observation_name: str | None = Field(default=None, min_length=1, max_length=200)
    observation_type: str | None = Field(
        default=None,
        min_length=1,
        max_length=32,
        pattern=r"^[A-Za-z_]+$",
    )
    environment: str | None = Field(default=None, min_length=1, max_length=100)
    root_only: bool = True

    @field_validator("observation_name", "environment")
    @classmethod
    def _metadata_filter(cls, value: str | None) -> str | None:
        if value is None:
            return None
        candidate = value.strip()
        if not candidate or any(not char.isprintable() for char in candidate):
            raise ValueError("sampling filter contains invalid characters")
        return candidate

    @field_validator("observation_type")
    @classmethod
    def _observation_type(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None

    @model_validator(mode="after")
    def _sampling_bounds(self):
        if self.minimum_sample_size > self.sample_size:
            raise ValueError("minimum_sample_size may not exceed sample_size")
        if self.candidate_limit < self.sample_size:
            raise ValueError("candidate_limit may not be below sample_size")
        return self


class EvaluationSamplingRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_public_id: str = Field(min_length=1, max_length=36)
    from_start_time: datetime
    to_start_time: datetime


class EvaluationSamplingPolicyVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    version: int
    config_digest: str
    strategy: EvaluationSamplingStrategy
    sample_size: int
    minimum_sample_size: int
    candidate_limit: int
    observation_name: str | None
    observation_type: str | None
    environment: str | None
    root_only: bool
    exclude_governed: bool
    schema_name: str
    schema_version: str
    created_by_user_id: int
    created_at: datetime


class EvaluationSamplingPolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    name: str
    description: str | None
    status: EvaluationSamplingPolicyStatus
    versions: list[EvaluationSamplingPolicyVersionOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class EvaluationSamplingRunItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    position: int
    source_trace_ref: str
    source_observation_ref: str
    rank_digest: str


class EvaluationSamplingRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    policy_public_id: str
    policy_version_public_id: str
    policy_version: int
    dataset_public_id: str
    curation_batch_public_id: str
    from_start_time: datetime
    to_start_time: datetime
    candidate_count: int
    eligible_count: int
    selected_count: int
    selection_digest: str
    run_digest: str
    schema_name: str
    schema_version: str
    created_by_user_id: int
    created_at: datetime
    items: list[EvaluationSamplingRunItemOut]


class EvaluationProviderAnnotationQueueOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_queue_ref: str
    name: str
    description: str | None
    score_config_ids: list[str]
    created_at: datetime
    updated_at: datetime
    already_bound: bool = False


class EvaluationAnnotationQueueBindingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(gt=0)
    provider_queue_ref: str = Field(min_length=1, max_length=255)

    @field_validator("provider_queue_ref")
    @classmethod
    def _provider_queue_ref(cls, value: str) -> str:
        candidate = value.strip()
        if _PUBLIC_REF.fullmatch(candidate) is None:
            raise ValueError("provider_queue_ref is malformed")
        return candidate


class EvaluationAnnotationQueueBindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    provider: EvaluationProvider
    provider_queue_ref: str
    provider_queue_name: str
    score_config_ids: list[str]
    provider_updated_at: datetime
    status: EvaluationAnnotationQueueBindingStatus
    schema_name: str
    schema_version: str
    created_by_user_id: int
    created_at: datetime
    updated_at: datetime


class EvaluationAnnotationDispatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    curation_batch_public_id: str = Field(min_length=1, max_length=40)

    @field_validator("curation_batch_public_id")
    @classmethod
    def _curation_batch_ref(cls, value: str) -> str:
        candidate = value.strip()
        if _PUBLIC_REF.fullmatch(candidate) is None:
            raise ValueError("curation_batch_public_id is malformed")
        return candidate


class EvaluationAnnotationDispatchItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    position: int
    source_trace_ref: str
    source_observation_ref: str
    sync_status: EvaluationAnnotationItemSyncStatus
    provider_queue_item_ref: str | None
    provider_annotation_status: EvaluationAnnotationProviderStatus | None
    attempt_count: int
    error_code: str | None
    provider_created_at: datetime | None
    provider_updated_at: datetime | None
    provider_completed_at: datetime | None
    last_reconciled_at: datetime | None


class EvaluationAnnotationDispatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    binding_public_id: str
    provider_queue_ref: str
    provider_queue_name: str
    curation_batch_public_id: str
    sampling_run_public_id: str | None
    request_digest: str
    status: EvaluationAnnotationDispatchStatus
    item_count: int
    synced_count: int
    completed_count: int
    failed_count: int
    attempt_count: int
    error_code: str | None
    schema_name: str
    schema_version: str
    created_by_user_id: int
    started_at: datetime | None
    synced_at: datetime | None
    last_reconciled_at: datetime | None
    created_at: datetime
    updated_at: datetime
    items: list[EvaluationAnnotationDispatchItemOut]


class EvaluationPromotionPolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _safe_name(value)


class EvaluationPromotionPolicyVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binding_public_id: str = Field(min_length=1, max_length=40)
    score_config_id: str = Field(min_length=1, max_length=255)
    score_data_type: EvaluationPromotionScoreDataType
    minimum_numeric_score: float | None = None
    accepted_values: list[str | bool] = Field(default_factory=list, max_length=20)
    diversity_dimension: EvaluationPromotionDiversityDimension = EvaluationPromotionDiversityDimension.NONE
    min_distinct_buckets: int = Field(default=1, ge=1, le=20)

    @field_validator("binding_public_id", "score_config_id")
    @classmethod
    def _provider_ref(cls, value: str) -> str:
        candidate = value.strip()
        if _PUBLIC_REF.fullmatch(candidate) is None:
            raise ValueError("promotion provider reference is malformed")
        return candidate

    @field_validator("accepted_values")
    @classmethod
    def _accepted_values(cls, values: list[str | bool]) -> list[str | bool]:
        normalized: list[str | bool] = []
        for value in values:
            if isinstance(value, bool):
                normalized.append(value)
                continue
            candidate = value.strip()
            if not candidate or len(candidate) > 200 or any(not char.isprintable() for char in candidate):
                raise ValueError("promotion accepted value is invalid")
            normalized.append(candidate)
        if len({(type(value).__name__, str(value)) for value in normalized}) != len(normalized):
            raise ValueError("promotion accepted values must be unique")
        return normalized

    @model_validator(mode="after")
    def _quality_rule(self):
        if self.score_data_type == EvaluationPromotionScoreDataType.NUMERIC:
            if self.minimum_numeric_score is None or self.accepted_values:
                raise ValueError("numeric promotion rules require only minimum_numeric_score")
        elif self.score_data_type == EvaluationPromotionScoreDataType.BOOLEAN:
            if self.minimum_numeric_score is not None or not self.accepted_values:
                raise ValueError("boolean promotion rules require accepted_values")
            if any(not isinstance(value, bool) for value in self.accepted_values):
                raise ValueError("boolean accepted_values must contain booleans")
        else:
            if self.minimum_numeric_score is not None or not self.accepted_values:
                raise ValueError("categorical promotion rules require accepted_values")
            if any(not isinstance(value, str) for value in self.accepted_values):
                raise ValueError("categorical accepted_values must contain strings")
        if self.diversity_dimension == EvaluationPromotionDiversityDimension.NONE and self.min_distinct_buckets != 1:
            raise ValueError("NONE diversity requires min_distinct_buckets=1")
        return self


class EvaluationPromotionRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dispatch_public_id: str = Field(min_length=1, max_length=40)

    @field_validator("dispatch_public_id")
    @classmethod
    def _dispatch_ref(cls, value: str) -> str:
        candidate = value.strip()
        if _PUBLIC_REF.fullmatch(candidate) is None:
            raise ValueError("dispatch_public_id is malformed")
        return candidate


class EvaluationPromotionPolicyVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    version: int
    binding_public_id: str
    provider_queue_ref: str
    config_digest: str
    score_config_id: str
    score_data_type: EvaluationPromotionScoreDataType
    minimum_numeric_score: float | None
    accepted_values: list[str | bool]
    diversity_dimension: EvaluationPromotionDiversityDimension
    min_distinct_buckets: int
    schema_name: str
    schema_version: str
    created_by_user_id: int
    created_at: datetime


class EvaluationPromotionPolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    name: str
    description: str | None
    status: EvaluationPromotionPolicyStatus
    versions: list[EvaluationPromotionPolicyVersionOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class EvaluationPromotionRunItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    position: int
    source_trace_ref: str
    source_observation_ref: str
    score_present: bool
    quality_passed: bool
    diversity_bucket_present: bool
    score_evidence_digest: str | None
    diversity_bucket_digest: str | None
    reason_code: str


class EvaluationPromotionRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    policy_public_id: str
    policy_version_public_id: str
    policy_version: int
    binding_public_id: str
    dispatch_public_id: str
    curation_batch_public_id: str
    request_digest: str
    evidence_digest: str
    outcome: EvaluationPromotionOutcome
    reason_codes: list[str]
    item_count: int
    completed_count: int
    scored_count: int
    passed_count: int
    distinct_bucket_count: int
    schema_name: str
    schema_version: str
    created_by_user_id: int
    created_at: datetime
    items: list[EvaluationPromotionRunItemOut]


class EvaluationCaseRoutingPolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _safe_name(value)


class EvaluationCaseRoutingPolicyVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_promotion_policy_version_public_id: str = Field(min_length=1, max_length=40)
    strategy: EvaluationCaseRoutingStrategy = EvaluationCaseRoutingStrategy.CLUSTER_ROUND_ROBIN
    golden_target_size: int = Field(default=20, ge=1, le=20)
    golden_min_items: int = Field(default=1, ge=1, le=20)
    bad_case_target_size: int = Field(default=20, ge=1, le=20)
    bad_case_min_items: int = Field(default=1, ge=1, le=20)

    @field_validator("source_promotion_policy_version_public_id")
    @classmethod
    def _promotion_version_ref(cls, value: str) -> str:
        candidate = value.strip()
        if _PUBLIC_REF.fullmatch(candidate) is None:
            raise ValueError("promotion policy version reference is malformed")
        return candidate

    @model_validator(mode="after")
    def _sizes(self):
        if self.golden_min_items > self.golden_target_size:
            raise ValueError("golden_min_items exceeds golden_target_size")
        if self.bad_case_min_items > self.bad_case_target_size:
            raise ValueError("bad_case_min_items exceeds bad_case_target_size")
        return self


class EvaluationCaseRoutingRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    promotion_run_public_ids: list[str] = Field(min_length=1, max_length=20)
    golden_dataset_public_id: str = Field(min_length=1, max_length=36)
    bad_case_dataset_public_id: str = Field(min_length=1, max_length=36)

    @field_validator(
        "golden_dataset_public_id",
        "bad_case_dataset_public_id",
    )
    @classmethod
    def _dataset_ref(cls, value: str) -> str:
        candidate = value.strip()
        if _PUBLIC_REF.fullmatch(candidate) is None:
            raise ValueError("routing Dataset reference is malformed")
        return candidate

    @field_validator("promotion_run_public_ids")
    @classmethod
    def _promotion_run_refs(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(_PUBLIC_REF.fullmatch(value) is None for value in normalized):
            raise ValueError("promotion run reference is malformed")
        if len(set(normalized)) != len(normalized):
            raise ValueError("promotion run references must be unique")
        return normalized

    @model_validator(mode="after")
    def _distinct_datasets(self):
        if self.golden_dataset_public_id == self.bad_case_dataset_public_id:
            raise ValueError("Golden and Bad Case Datasets must be distinct")
        return self


class EvaluationCaseRoutingPolicyVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    version: int
    source_promotion_policy_public_id: str
    source_promotion_policy_version_public_id: str
    source_promotion_policy_version: int
    config_digest: str
    strategy: EvaluationCaseRoutingStrategy
    golden_target_size: int
    golden_min_items: int
    bad_case_target_size: int
    bad_case_min_items: int
    schema_name: str
    schema_version: str
    created_by_user_id: int
    created_at: datetime


class EvaluationCaseRoutingPolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    name: str
    description: str | None
    status: EvaluationCaseRoutingPolicyStatus
    versions: list[EvaluationCaseRoutingPolicyVersionOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class EvaluationCaseRoutingRunItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    position: int
    source_promotion_run_public_id: str
    source_trace_ref: str
    source_observation_ref: str
    lane: EvaluationCaseRoutingLane
    selected: bool
    cluster_digest: str | None
    rank_digest: str | None
    reason_code: str


class EvaluationCaseRoutingRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    policy_public_id: str
    policy_version_public_id: str
    policy_version: int
    source_promotion_policy_version_public_id: str
    golden_dataset_public_id: str
    bad_case_dataset_public_id: str
    golden_curation_batch_public_id: str | None
    bad_case_curation_batch_public_id: str | None
    request_digest: str
    evidence_digest: str
    routing_digest: str
    outcome: EvaluationCaseRoutingOutcome
    reason_codes: list[str]
    source_run_count: int
    candidate_count: int
    golden_candidate_count: int
    bad_case_candidate_count: int
    excluded_count: int
    golden_selected_count: int
    bad_case_selected_count: int
    golden_cluster_count: int
    bad_case_cluster_count: int
    schema_name: str
    schema_version: str
    created_by_user_id: int
    created_at: datetime
    items: list[EvaluationCaseRoutingRunItemOut]


class EvaluationSemanticClusteringPolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _safe_name(value)


class EvaluationSemanticClusteringPolicyVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_case_routing_policy_version_public_id: str = Field(min_length=1, max_length=40)
    embedding_profile: str = Field(default="openai-compatible-local", min_length=1, max_length=100)
    model_ref: str = Field(default="bge-small-en-v1.5", min_length=1, max_length=255)
    dimensions: int = Field(default=384, ge=1, le=4096)
    similarity_threshold: float = Field(default=0.82, ge=0, le=1)
    min_cluster_size: int = Field(default=2, ge=2, le=20)
    max_items: int = Field(default=20, ge=2, le=20)
    max_content_chars: int = Field(default=8000, ge=100, le=16000)

    @field_validator("source_case_routing_policy_version_public_id", "model_ref")
    @classmethod
    def _public_ref(cls, value: str) -> str:
        candidate = value.strip()
        if _PUBLIC_REF.fullmatch(candidate) is None:
            raise ValueError("provider or policy reference is malformed")
        return candidate

    @field_validator("embedding_profile")
    @classmethod
    def _profile(cls, value: str) -> str:
        return _safe_name(value)


class EvaluationSemanticClusteringRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_case_routing_run_public_id: str = Field(min_length=1, max_length=40)

    @field_validator("source_case_routing_run_public_id")
    @classmethod
    def _routing_run_ref(cls, value: str) -> str:
        candidate = value.strip()
        if _PUBLIC_REF.fullmatch(candidate) is None:
            raise ValueError("case routing run reference is malformed")
        return candidate


class EvaluationSemanticRegressionPolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _safe_name(value)


class EvaluationSemanticRegressionPolicyVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_pairwise_assignment_agreement: float = Field(default=0.9, ge=0, le=1)
    maximum_cluster_count_change_ratio: float = Field(default=0.5, ge=0, le=1)
    maximum_mean_centroid_similarity_drop: float = Field(default=0.05, ge=0, le=1)
    maximum_eligible_cluster_ratio_drop: float = Field(default=0.25, ge=0, le=1)


class EvaluationSemanticRegressionComparisonCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(gt=0)
    baseline_run_public_id: str = Field(min_length=1, max_length=40)
    candidate_run_public_id: str = Field(min_length=1, max_length=40)
    policy_version_public_id: str = Field(min_length=1, max_length=40)

    @field_validator(
        "baseline_run_public_id",
        "candidate_run_public_id",
        "policy_version_public_id",
    )
    @classmethod
    def _public_ref(cls, value: str) -> str:
        candidate = value.strip()
        if _PUBLIC_REF.fullmatch(candidate) is None:
            raise ValueError("semantic regression reference is malformed")
        return candidate


class EvaluationSemanticMonitorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=500)
    baseline_run_public_id: str = Field(min_length=1, max_length=40)
    candidate_policy_version_public_id: str = Field(min_length=1, max_length=40)
    regression_policy_version_public_id: str = Field(min_length=1, max_length=40)
    interval_seconds: int = Field(default=3600, ge=60, le=2592000)
    first_run_at: datetime | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _safe_name(value)

    @field_validator(
        "baseline_run_public_id",
        "candidate_policy_version_public_id",
        "regression_policy_version_public_id",
    )
    @classmethod
    def _public_ref(cls, value: str) -> str:
        candidate = value.strip()
        if _PUBLIC_REF.fullmatch(candidate) is None:
            raise ValueError("semantic monitor reference is malformed")
        return candidate


class EvaluationSemanticMonitorAcknowledge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=500)

    @field_validator("note")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class EvaluationFailureTaxonomyPolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _safe_name(value)


class EvaluationFailureTaxonomyPolicyVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_case_routing_policy_version_public_id: str = Field(min_length=1, max_length=40)
    source_semantic_clustering_policy_version_public_id: str | None = Field(default=None, min_length=1, max_length=40)
    min_cluster_occurrences: int = Field(default=2, ge=2, le=20)
    min_source_runs: int = Field(default=2, ge=2, le=20)
    include_isolated: bool = False
    max_candidates: int = Field(default=20, ge=1, le=20)

    @field_validator(
        "source_case_routing_policy_version_public_id",
        "source_semantic_clustering_policy_version_public_id",
    )
    @classmethod
    def _routing_version_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        candidate = value.strip()
        if _PUBLIC_REF.fullmatch(candidate) is None:
            raise ValueError("case routing policy version reference is malformed")
        return candidate


class EvaluationExperienceExtractionRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_case_routing_run_public_id: str = Field(min_length=1, max_length=40)
    source_semantic_clustering_run_public_id: str | None = Field(default=None, min_length=1, max_length=40)

    @field_validator(
        "source_case_routing_run_public_id",
        "source_semantic_clustering_run_public_id",
    )
    @classmethod
    def _routing_run_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        candidate = value.strip()
        if _PUBLIC_REF.fullmatch(candidate) is None:
            raise ValueError("case routing run reference is malformed")
        return candidate


class EvaluationExperienceCandidateReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: EvaluationExperienceReviewDecision
    comment: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _review_contract(self):
        comment = self.comment.strip() if self.comment is not None else None
        if self.decision == EvaluationExperienceReviewDecision.REJECTED and (comment is None or len(comment) < 5):
            raise ValueError("rejection comment must contain at least 5 chars")
        self.comment = comment or None
        return self


class EvaluationExperienceAssetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(gt=0)
    source_candidate_public_id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=500)

    @field_validator("source_candidate_public_id")
    @classmethod
    def _source_candidate_ref(cls, value: str) -> str:
        candidate = value.strip()
        if _PUBLIC_REF.fullmatch(candidate) is None:
            raise ValueError("Experience candidate reference is malformed")
        return candidate

    @field_validator("name")
    @classmethod
    def _asset_name(cls, value: str) -> str:
        candidate = value.strip()
        if not candidate:
            raise ValueError("Experience asset name must not be blank")
        return candidate

    @field_validator("description")
    @classmethod
    def _asset_description(cls, value: str | None) -> str | None:
        candidate = value.strip() if value is not None else None
        return candidate or None


class EvaluationExperienceAssetVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=10, max_length=4000)
    applicability: str = Field(min_length=5, max_length=1000)
    change_summary: str | None = Field(default=None, max_length=1000)

    @field_validator("body", "applicability")
    @classmethod
    def _required_content(cls, value: str) -> str:
        candidate = value.strip()
        if not candidate:
            raise ValueError("Experience content must not be blank")
        return candidate

    @field_validator("change_summary")
    @classmethod
    def _change_summary(cls, value: str | None) -> str | None:
        candidate = value.strip() if value is not None else None
        return candidate or None


class EvaluationExperienceActivationRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_note: str | None = Field(default=None, max_length=1000)

    @field_validator("request_note")
    @classmethod
    def _request_note(cls, value: str | None) -> str | None:
        candidate = value.strip() if value is not None else None
        return candidate or None


class EvaluationExperienceActivationReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: EvaluationExperienceActivationDecision
    comment: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _activation_review_contract(self):
        comment = self.comment.strip() if self.comment is not None else None
        if self.decision == EvaluationExperienceActivationDecision.REJECTED and (comment is None or len(comment) < 5):
            raise ValueError("rejection comment must contain at least 5 chars")
        self.comment = comment or None
        return self


class EvaluationSemanticClusteringPolicyVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    version: int
    source_case_routing_policy_public_id: str
    source_case_routing_policy_version_public_id: str
    source_case_routing_policy_version: int
    config_digest: str
    embedding_profile: str
    model_ref: str
    dimensions: int
    similarity_threshold: float
    min_cluster_size: int
    max_items: int
    max_content_chars: int
    schema_name: str
    schema_version: str
    created_by_user_id: int
    created_at: datetime


class EvaluationSemanticClusteringPolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    name: str
    description: str | None
    status: EvaluationSemanticClusteringPolicyStatus
    versions: list[EvaluationSemanticClusteringPolicyVersionOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class EvaluationSemanticClusteringRunItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    position: int
    source_case_routing_item_position: int
    source_trace_ref: str
    source_observation_ref: str
    semantic_cluster_digest: str
    cluster_size: int
    similarity_to_centroid: float
    content_digest: str
    embedding_digest: str


class EvaluationSemanticClusteringRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    policy_public_id: str
    policy_version_public_id: str
    policy_version: int
    source_case_routing_run_public_id: str
    request_digest: str
    evidence_digest: str
    clustering_digest: str
    outcome: EvaluationSemanticClusteringOutcome
    reason_codes: list[str]
    source_item_count: int
    cluster_count: int
    eligible_cluster_count: int
    schema_name: str
    schema_version: str
    created_by_user_id: int
    created_at: datetime
    items: list[EvaluationSemanticClusteringRunItemOut]


class EvaluationSemanticRegressionPolicyVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    version: int
    config_digest: str
    minimum_pairwise_assignment_agreement: float
    maximum_cluster_count_change_ratio: float
    maximum_mean_centroid_similarity_drop: float
    maximum_eligible_cluster_ratio_drop: float
    require_exact_source_content: bool
    schema_name: str
    schema_version: str
    created_by_user_id: int
    created_at: datetime


class EvaluationSemanticRegressionPolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    name: str
    description: str | None
    status: EvaluationSemanticRegressionPolicyStatus
    versions: list[EvaluationSemanticRegressionPolicyVersionOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class EvaluationSemanticRegressionComparisonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    baseline_run_public_id: str
    baseline_policy_version_public_id: str
    candidate_run_public_id: str
    candidate_policy_version_public_id: str
    policy_public_id: str
    policy_name: str
    policy_version_public_id: str
    policy_version: int
    outcome: EvaluationSemanticRegressionOutcome
    reason_codes: list[str]
    source_item_count: int
    pairwise_assignment_agreement: float | None
    baseline_cluster_count: int
    candidate_cluster_count: int
    cluster_count_change_ratio: float
    baseline_eligible_cluster_ratio: float
    candidate_eligible_cluster_ratio: float
    eligible_cluster_ratio_drop: float
    baseline_mean_centroid_similarity: float
    candidate_mean_centroid_similarity: float
    mean_centroid_similarity_drop: float
    assignment_agreement_breached: bool
    cluster_count_change_breached: bool
    eligible_cluster_ratio_drop_breached: bool
    centroid_similarity_drop_breached: bool
    reproducibility_digest: str
    schema_name: str
    schema_version: str
    created_by_user_id: int
    created_at: datetime


class EvaluationSemanticMonitorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    name: str
    description: str | None
    baseline_run_public_id: str
    candidate_policy_version_public_id: str
    regression_policy_version_public_id: str
    interval_seconds: int
    config_digest: str
    status: EvaluationSemanticMonitorStatus
    next_run_at: datetime
    created_by_user_id: int
    created_at: datetime
    updated_at: datetime


class EvaluationSemanticMonitorRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    monitor_public_id: str
    scheduled_for: datetime
    status: EvaluationSemanticMonitorRunStatus
    attempt_count: int
    candidate_run_public_id: str | None
    comparison_public_id: str | None
    alert_public_id: str | None
    outcome: EvaluationSemanticRegressionOutcome | None
    error_code: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class EvaluationSemanticMonitorAlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    monitor_public_id: str
    monitor_run_public_id: str
    comparison_public_id: str | None
    severity: EvaluationSemanticMonitorAlertSeverity
    status: EvaluationSemanticMonitorAlertStatus
    reason_codes: list[str]
    error_code: str | None
    acknowledged_by_user_id: int | None
    acknowledged_at: datetime | None
    acknowledgement_note: str | None
    created_at: datetime
    updated_at: datetime


class EvaluationFailureTaxonomyPolicyVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    version: int
    source_case_routing_policy_public_id: str
    source_case_routing_policy_version_public_id: str
    source_case_routing_policy_version: int
    source_semantic_clustering_policy_public_id: str | None
    source_semantic_clustering_policy_version_public_id: str | None
    source_semantic_clustering_policy_version: int | None
    config_digest: str
    min_cluster_occurrences: int
    min_source_runs: int
    include_isolated: bool
    max_candidates: int
    schema_name: str
    schema_version: str
    created_by_user_id: int
    created_at: datetime


class EvaluationFailureTaxonomyPolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    name: str
    description: str | None
    status: EvaluationFailureTaxonomyPolicyStatus
    versions: list[EvaluationFailureTaxonomyPolicyVersionOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class EvaluationExperienceCandidateEvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    position: int
    source_case_routing_item_position: int
    source_promotion_run_public_id: str
    source_trace_ref: str
    source_observation_ref: str


class EvaluationExperienceCandidateReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    decision: EvaluationExperienceReviewDecision
    comment: str | None
    review_digest: str
    reviewed_by_user_id: int
    created_at: datetime


class EvaluationExperienceCandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    position: int
    category: EvaluationFailureCategory
    status: EvaluationExperienceCandidateStatus
    cluster_digest: str
    rank_digest: str
    evidence_digest: str
    source_item_count: int
    source_run_count: int
    reason_code: str
    created_at: datetime
    evidence_items: list[EvaluationExperienceCandidateEvidenceOut]
    review: EvaluationExperienceCandidateReviewOut | None


class EvaluationExperienceActivationReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    decision: EvaluationExperienceActivationDecision
    comment: str | None
    review_digest: str
    reviewed_by_user_id: int
    created_at: datetime


class EvaluationExperienceActivationRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    request_note: str | None
    request_digest: str
    requested_by_user_id: int
    created_at: datetime
    review: EvaluationExperienceActivationReviewOut | None


class EvaluationExperienceAssetVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    version: int
    status: EvaluationExperienceAssetVersionStatus
    body: str
    applicability: str
    change_summary: str | None
    content_digest: str
    source_evidence_digest: str
    schema_name: str
    schema_version: str
    created_by_user_id: int
    created_at: datetime
    activation_request: EvaluationExperienceActivationRequestOut | None


class EvaluationExperienceAssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    source_candidate_public_id: str
    source_candidate_category: EvaluationFailureCategory
    source_candidate_evidence_digest: str
    name: str
    description: str | None
    created_by_user_id: int
    created_at: datetime
    versions: list[EvaluationExperienceAssetVersionOut] = Field(default_factory=list)


class EvaluationExperienceExtractionRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    policy_public_id: str
    policy_version_public_id: str
    policy_version: int
    source_case_routing_run_public_id: str
    source_semantic_clustering_run_public_id: str | None
    request_digest: str
    evidence_digest: str
    extraction_digest: str
    outcome: EvaluationExperienceExtractionOutcome
    reason_codes: list[str]
    source_bad_case_count: int
    cluster_count: int
    eligible_cluster_count: int
    candidate_count: int
    schema_name: str
    schema_version: str
    created_by_user_id: int
    created_at: datetime
    candidates: list[EvaluationExperienceCandidateOut]


class EvaluatorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=128)
    kind: EvaluatorKind
    provider: EvaluationProvider
    provider_evaluator_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _safe_name(value)

    @field_validator("provider_evaluator_ref")
    @classmethod
    def _provider_ref(cls, value: str | None) -> str | None:
        if value is not None and _PUBLIC_REF.fullmatch(value) is None:
            raise ValueError("provider_evaluator_ref is malformed")
        return value


class EvaluatorVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_digest: str = Field(min_length=64, max_length=64)
    implementation_ref: str = Field(min_length=1, max_length=512)
    rubric_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
    )
    provider_version_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )
    activate: bool = True

    @field_validator("config_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        return _sha256(value)

    @field_validator("implementation_ref")
    @classmethod
    def _implementation(cls, value: str) -> str:
        if _IMPLEMENTATION_REF.fullmatch(value) is None:
            raise ValueError("implementation_ref must be a supported opaque reference")
        return value

    @field_validator("rubric_version")
    @classmethod
    def _rubric(cls, value: str | None) -> str | None:
        if value is not None:
            return _safe_name(value)
        return value

    @field_validator("provider_version_ref")
    @classmethod
    def _provider_ref(cls, value: str | None) -> str | None:
        if value is not None and _PUBLIC_REF.fullmatch(value) is None:
            raise ValueError("provider_version_ref is malformed")
        return value


class EvaluatorVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    version: int
    config_digest: str
    implementation_ref: str
    rubric_version: str | None
    provider_version_ref: str | None
    created_at: datetime


class EvaluatorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    name: str
    kind: EvaluatorKind
    provider: EvaluationProvider
    provider_evaluator_ref: str | None
    status: EvaluatorStatus
    versions: list[EvaluatorVersionOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ExperimentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=128)
    dataset_version_public_id: str = Field(min_length=1, max_length=36)
    target_type: str = Field(min_length=1, max_length=64)
    target_ref: str = Field(min_length=1, max_length=255)
    target_digest: str = Field(min_length=64, max_length=64)
    provider: EvaluationProvider = EvaluationProvider.LANGFUSE
    provider_experiment_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )

    @field_validator("name", "target_type")
    @classmethod
    def _name(cls, value: str) -> str:
        return _safe_name(value)

    @field_validator(
        "dataset_version_public_id",
        "target_ref",
        "provider_experiment_ref",
    )
    @classmethod
    def _ref(cls, value: str | None) -> str | None:
        if value is not None and _PUBLIC_REF.fullmatch(value) is None:
            raise ValueError("reference is malformed")
        return value

    @field_validator("target_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        return _sha256(value)


class ExperimentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    namespace_id: int
    name: str
    dataset_version_public_id: str
    target_type: str
    target_ref: str
    target_digest: str
    provider: EvaluationProvider
    provider_experiment_ref: str | None
    status: ExperimentStatus
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime


class EvaluationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluator_version_public_id: str = Field(min_length=1, max_length=36)
    provider_evaluation_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )

    @field_validator("evaluator_version_public_id", "provider_evaluation_ref")
    @classmethod
    def _ref(cls, value: str | None) -> str | None:
        if value is not None and _PUBLIC_REF.fullmatch(value) is None:
            raise ValueError("reference is malformed")
        return value


class EvaluationComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=0, le=1)
    total_count: int = Field(ge=0)
    passed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    provider_evaluation_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )

    @model_validator(mode="after")
    def _counts(self):
        if self.passed_count + self.failed_count > self.total_count:
            raise ValueError("passed_count + failed_count cannot exceed total_count")
        return self


class EvaluationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str = Field(max_length=40)
    namespace_id: int
    experiment_public_id: str
    evaluator_version_public_id: str
    status: EvaluationStatus
    provider_evaluation_ref: str | None
    score: float | None
    total_count: int
    processed_count: int
    scored_count: int
    passed_count: int
    failed_count: int
    error_count: int
    result_completeness: EvaluationResultCompleteness | None
    result_manifest_public_id: str | None
    error_code: str | None
    attempt_count: int
    available_at: datetime | None
    lease_expires_at: datetime | None
    cancel_requested_at: datetime | None
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime


class EvaluationResultManifestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str = Field(max_length=36)
    namespace_id: int
    evaluation_public_id: str
    version: int
    provider: EvaluationProvider
    provider_dataset_ref: str
    provider_experiment_ref: str
    schema_name: str
    schema_version: str
    content_digest: str
    score: float | None
    expected_count: int
    processed_count: int
    scored_count: int
    passed_count: int
    failed_count: int
    error_count: int
    completeness: EvaluationResultCompleteness
    loss_reason: str | None
    created_at: datetime


class RegressionPolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _safe_name(value)


class RegressionPolicyVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_candidate_score: float | None = Field(default=None, ge=0, le=1)
    maximum_score_drop: float = Field(default=0, ge=0, le=1)
    maximum_pass_rate_drop: float = Field(default=0, ge=0, le=1)


class RegressionPolicyVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str = Field(max_length=36)
    version: int
    content_digest: str
    minimum_candidate_score: float | None
    maximum_score_drop: float
    maximum_pass_rate_drop: float
    require_complete_results: bool
    created_at: datetime


class RegressionPolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str = Field(max_length=36)
    namespace_id: int
    name: str
    description: str | None
    status: RegressionPolicyStatus
    versions: list[RegressionPolicyVersionOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class EvaluationComparisonCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_id: int = Field(gt=0)
    baseline_evaluation_public_id: str = Field(min_length=1, max_length=40)
    baseline_manifest_public_id: str = Field(min_length=1, max_length=36)
    candidate_evaluation_public_id: str = Field(min_length=1, max_length=40)
    candidate_manifest_public_id: str = Field(min_length=1, max_length=36)
    policy_version_public_id: str = Field(min_length=1, max_length=36)

    @field_validator(
        "baseline_evaluation_public_id",
        "baseline_manifest_public_id",
        "candidate_evaluation_public_id",
        "candidate_manifest_public_id",
        "policy_version_public_id",
    )
    @classmethod
    def _ref(cls, value: str) -> str:
        if _PUBLIC_REF.fullmatch(value) is None:
            raise ValueError("reference is malformed")
        return value

    @model_validator(mode="after")
    def _distinct_evaluations(self):
        if self.baseline_evaluation_public_id == self.candidate_evaluation_public_id:
            raise ValueError("baseline and candidate evaluations must differ")
        if self.baseline_manifest_public_id == self.candidate_manifest_public_id:
            raise ValueError("baseline and candidate manifests must differ")
        return self


class EvaluationComparisonPinOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_public_id: str
    experiment_public_id: str
    manifest_public_id: str
    dataset_version_public_id: str
    dataset_content_digest: str
    evaluator_version_public_id: str
    evaluator_config_digest: str
    target_type: str
    target_ref: str
    target_digest: str
    provider: EvaluationProvider
    provider_dataset_ref: str
    provider_experiment_ref: str
    result_content_digest: str
    result_completeness: EvaluationResultCompleteness
    score: float | None
    scored_count: int
    passed_count: int
    pass_rate: float | None


class RegressionPolicySnapshotOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_public_id: str
    policy_name: str
    version_public_id: str
    version: int
    content_digest: str
    minimum_candidate_score: float | None
    maximum_score_drop: float
    maximum_pass_rate_drop: float
    require_complete_results: bool


class EvaluationComparisonOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_id: str = Field(max_length=36)
    namespace_id: int
    outcome: EvaluationComparisonOutcome
    reason_code: str
    baseline: EvaluationComparisonPinOut
    candidate: EvaluationComparisonPinOut
    policy: RegressionPolicySnapshotOut
    score_delta: float | None
    pass_rate_delta: float | None
    score_floor_breached: bool
    score_drop_breached: bool
    pass_rate_drop_breached: bool
    schema_name: str
    schema_version: str
    reproducibility_digest: str
    created_at: datetime
