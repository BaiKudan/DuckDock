from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EvaluationProvider(str, enum.Enum):
    LANGFUSE = "LANGFUSE"
    DEEPEVAL = "DEEPEVAL"
    CUSTOM = "CUSTOM"


class EvaluationDatasetStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class EvaluationDatasetSourceType(str, enum.Enum):
    LANGFUSE_TRACE = "LANGFUSE_TRACE"


class EvaluationDatasetCurationDecision(str, enum.Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class EvaluationSamplingPolicyStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class EvaluationSamplingStrategy(str, enum.Enum):
    STABLE_HASH = "STABLE_HASH"


class EvaluationAnnotationQueueBindingStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class EvaluationAnnotationDispatchStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SYNCED = "SYNCED"
    FAILED = "FAILED"


class EvaluationAnnotationItemSyncStatus(str, enum.Enum):
    PENDING = "PENDING"
    SYNCED = "SYNCED"
    FAILED = "FAILED"


class EvaluationAnnotationProviderStatus(str, enum.Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"


class EvaluationPromotionPolicyStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class EvaluationPromotionScoreDataType(str, enum.Enum):
    NUMERIC = "NUMERIC"
    BOOLEAN = "BOOLEAN"
    CATEGORICAL = "CATEGORICAL"


class EvaluationPromotionDiversityDimension(str, enum.Enum):
    NONE = "NONE"
    OBSERVATION_NAME = "OBSERVATION_NAME"
    OBSERVATION_TYPE = "OBSERVATION_TYPE"
    ENVIRONMENT = "ENVIRONMENT"


class EvaluationPromotionOutcome(str, enum.Enum):
    RECOMMENDED = "RECOMMENDED"
    BLOCKED = "BLOCKED"


class EvaluationCaseRoutingPolicyStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class EvaluationCaseRoutingStrategy(str, enum.Enum):
    CLUSTER_ROUND_ROBIN = "CLUSTER_ROUND_ROBIN"


class EvaluationCaseRoutingLane(str, enum.Enum):
    GOLDEN = "GOLDEN"
    BAD_CASE = "BAD_CASE"
    EXCLUDED = "EXCLUDED"


class EvaluationCaseRoutingOutcome(str, enum.Enum):
    ROUTED = "ROUTED"
    BLOCKED = "BLOCKED"


class EvaluationFailureTaxonomyPolicyStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class EvaluationSemanticClusteringPolicyStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class EvaluationSemanticClusteringOutcome(str, enum.Enum):
    CLUSTERED = "CLUSTERED"
    BLOCKED = "BLOCKED"


class EvaluationSemanticRegressionPolicyStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class EvaluationSemanticRegressionOutcome(str, enum.Enum):
    PASS = "PASS"
    DRIFTED = "DRIFTED"
    INCONCLUSIVE = "INCONCLUSIVE"


class EvaluationSemanticMonitorStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    RETIRED = "RETIRED"


class EvaluationSemanticMonitorRunStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class EvaluationSemanticMonitorAlertSeverity(str, enum.Enum):
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class EvaluationSemanticMonitorAlertStatus(str, enum.Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"


class EvaluationFailureCategory(str, enum.Enum):
    CROSS_RUN_RECURRING = "CROSS_RUN_RECURRING"
    SINGLE_RUN_RECURRING = "SINGLE_RUN_RECURRING"
    ISOLATED = "ISOLATED"


class EvaluationExperienceExtractionOutcome(str, enum.Enum):
    EXTRACTED = "EXTRACTED"
    BLOCKED = "BLOCKED"


class EvaluationExperienceCandidateStatus(str, enum.Enum):
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class EvaluationExperienceReviewDecision(str, enum.Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class EvaluationExperienceAssetVersionStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PENDING_ACTIVATION = "PENDING_ACTIVATION"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    RETIRED = "RETIRED"


class EvaluationExperienceActivationDecision(str, enum.Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class EvaluatorKind(str, enum.Enum):
    LLM_JUDGE = "LLM_JUDGE"
    CODE = "CODE"
    RULE = "RULE"
    DEEPEVAL = "DEEPEVAL"


class EvaluatorStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class ExperimentStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class EvaluationStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class EvaluationResultCompleteness(str, enum.Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"


class RegressionPolicyStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class EvaluationComparisonOutcome(str, enum.Enum):
    PASS = "PASS"
    REGRESSION = "REGRESSION"
    INCONCLUSIVE = "INCONCLUSIVE"


class EvaluationDataset(Base):
    """Governance record for provider-hosted evaluation data.

    Dataset items and their input/expected-output content intentionally remain
    in the configured evaluation provider. DuckDock stores only identity,
    version digests and counts.
    """

    __tablename__ = "evaluation_datasets"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_evaluation_datasets_public_id"),
        UniqueConstraint(
            "namespace_id",
            "name",
            name="uq_evaluation_datasets_namespace_name",
        ),
        Index(
            "ix_evaluation_datasets_namespace_status",
            "namespace_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    provider: Mapped[EvaluationProvider] = mapped_column(
        Enum(EvaluationProvider, name="evaluation_provider"),
        nullable=False,
    )
    provider_dataset_ref: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[EvaluationDatasetStatus] = mapped_column(
        Enum(EvaluationDatasetStatus, name="evaluation_dataset_status"),
        default=EvaluationDatasetStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    versions: Mapped[list["EvaluationDatasetVersion"]] = relationship(
        back_populates="dataset",
        lazy="selectin",
    )


class EvaluationDatasetVersion(Base):
    __tablename__ = "evaluation_dataset_versions"
    __table_args__ = (
        UniqueConstraint(
            "public_id",
            name="uq_evaluation_dataset_versions_public_id",
        ),
        UniqueConstraint(
            "dataset_id",
            "version",
            name="uq_evaluation_dataset_versions_dataset_version",
        ),
        UniqueConstraint(
            "dataset_id",
            "content_digest",
            name="uq_evaluation_dataset_versions_dataset_digest",
        ),
        CheckConstraint(
            "version >= 1 AND item_count >= 0",
            name="ck_evaluation_dataset_versions_positive",
        ),
        Index(
            "ix_evaluation_dataset_versions_dataset_created",
            "dataset_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False)
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_datasets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_version_ref: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    dataset: Mapped[EvaluationDataset] = relationship(back_populates="versions")


class EvaluationDatasetMaterialization(Base):
    """Immutable metadata receipt for one provider-hosted Trace2Dataset write."""

    __tablename__ = "evaluation_dataset_materializations"
    __table_args__ = (
        UniqueConstraint(
            "public_id",
            name="uq_evaluation_dataset_materializations_public_id",
        ),
        UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_evaluation_dataset_materializations_idempotency",
        ),
        UniqueConstraint(
            "dataset_id",
            "source_trace_ref",
            "source_observation_ref",
            name="uq_evaluation_dataset_materializations_source",
        ),
        UniqueConstraint(
            "dataset_version_id",
            name="uq_evaluation_dataset_materializations_version",
        ),
        Index(
            "ix_evaluation_dataset_materializations_namespace_created",
            "namespace_id",
            "created_at",
        ),
        Index(
            "ix_evaluation_dataset_materializations_dataset_created",
            "dataset_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_datasets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    dataset_version_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_dataset_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_type: Mapped[EvaluationDatasetSourceType] = mapped_column(
        Enum(
            EvaluationDatasetSourceType,
            name="evaluation_dataset_source_type",
        ),
        nullable=False,
    )
    source_trace_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    source_observation_ref: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    provider_dataset_item_ref: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    dataset: Mapped[EvaluationDataset] = relationship()
    dataset_version: Mapped[EvaluationDatasetVersion] = relationship()


class EvaluationDatasetCurationBatch(Base):
    """Immutable set of provider Observation references awaiting review."""

    __tablename__ = "evaluation_dataset_curation_batches"
    __table_args__ = (
        UniqueConstraint(
            "public_id",
            name="uq_evaluation_dataset_curation_batches_public_id",
        ),
        UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_eval_dataset_curation_batches_idempotency",
        ),
        UniqueConstraint(
            "dataset_id",
            "selection_digest",
            name="uq_eval_dataset_curation_batches_selection",
        ),
        CheckConstraint(
            "item_count >= 1 AND item_count <= 20",
            name="ck_eval_dataset_curation_batches_item_count",
        ),
        Index(
            "ix_eval_dataset_curation_batches_namespace_created",
            "namespace_id",
            "created_at",
        ),
        Index(
            "ix_eval_dataset_curation_batches_dataset_created",
            "dataset_id",
            "created_at",
        ),
        Index(
            "ix_eval_dataset_curation_batches_namespace_id",
            "namespace_id",
        ),
        Index(
            "ix_eval_dataset_curation_batches_dataset_id",
            "dataset_id",
        ),
        Index(
            "ix_eval_dataset_curation_batches_submitted_by",
            "submitted_by_user_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
    )
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_datasets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    selection_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    submitted_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    dataset: Mapped[EvaluationDataset] = relationship()
    items: Mapped[list["EvaluationDatasetCurationItem"]] = relationship(
        back_populates="batch",
        lazy="selectin",
        order_by="EvaluationDatasetCurationItem.position",
    )
    review: Mapped["EvaluationDatasetCurationReview | None"] = relationship(
        back_populates="batch",
        lazy="selectin",
        uselist=False,
    )
    materialization: Mapped["EvaluationDatasetCurationMaterialization | None"] = relationship(
        back_populates="batch",
        lazy="selectin",
        uselist=False,
    )


class EvaluationDatasetCurationItem(Base):
    """One immutable source reference inside a curation batch."""

    __tablename__ = "evaluation_dataset_curation_items"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "position",
            name="uq_eval_dataset_curation_items_position",
        ),
        UniqueConstraint(
            "batch_id",
            "source_trace_ref",
            "source_observation_ref",
            name="uq_eval_dataset_curation_items_source",
        ),
        Index(
            "ix_eval_dataset_curation_items_batch_id",
            "batch_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey(
            "evaluation_dataset_curation_batches.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    source_trace_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    source_observation_ref: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    batch: Mapped[EvaluationDatasetCurationBatch] = relationship(back_populates="items")


class EvaluationDatasetCurationReview(Base):
    """Immutable human decision for one curation batch."""

    __tablename__ = "evaluation_dataset_curation_reviews"
    __table_args__ = (
        UniqueConstraint(
            "public_id",
            name="uq_evaluation_dataset_curation_reviews_public_id",
        ),
        UniqueConstraint(
            "batch_id",
            name="uq_evaluation_dataset_curation_reviews_batch",
        ),
        UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_eval_dataset_curation_reviews_idempotency",
        ),
        Index(
            "ix_eval_dataset_curation_reviews_namespace_id",
            "namespace_id",
        ),
        Index(
            "ix_eval_dataset_curation_reviews_batch_id",
            "batch_id",
        ),
        Index(
            "ix_eval_dataset_curation_reviews_reviewed_by",
            "reviewed_by_user_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
    )
    batch_id: Mapped[int] = mapped_column(
        ForeignKey(
            "evaluation_dataset_curation_batches.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    decision: Mapped[EvaluationDatasetCurationDecision] = mapped_column(
        Enum(
            EvaluationDatasetCurationDecision,
            name="evaluation_dataset_curation_decision",
        ),
        nullable=False,
    )
    comment: Mapped[str | None] = mapped_column(String(1000))
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    review_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewed_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    batch: Mapped[EvaluationDatasetCurationBatch] = relationship(back_populates="review")


class EvaluationDatasetCurationMaterialization(Base):
    """Immutable receipt for one approved batch and one Dataset version."""

    __tablename__ = "evaluation_dataset_curation_materializations"
    __table_args__ = (
        UniqueConstraint(
            "public_id",
            name="uq_eval_dataset_curation_materializations_public_id",
        ),
        UniqueConstraint(
            "batch_id",
            name="uq_eval_dataset_curation_materializations_batch",
        ),
        UniqueConstraint(
            "dataset_version_id",
            name="uq_eval_dataset_curation_materializations_version",
        ),
        UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_eval_dataset_curation_materializations_idempotency",
        ),
        Index(
            "ix_eval_dataset_curation_materializations_namespace_id",
            "namespace_id",
        ),
        Index(
            "ix_eval_dataset_curation_materializations_batch_id",
            "batch_id",
        ),
        Index(
            "ix_eval_dataset_curation_materializations_version_id",
            "dataset_version_id",
        ),
        Index(
            "ix_eval_dataset_curation_materializations_created_by",
            "created_by_user_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
    )
    batch_id: Mapped[int] = mapped_column(
        ForeignKey(
            "evaluation_dataset_curation_batches.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    dataset_version_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_dataset_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    batch: Mapped[EvaluationDatasetCurationBatch] = relationship(back_populates="materialization")
    dataset_version: Mapped[EvaluationDatasetVersion] = relationship()
    items: Mapped[list["EvaluationDatasetCurationMaterializedItem"]] = relationship(
        back_populates="materialization",
        lazy="selectin",
    )


class EvaluationDatasetCurationMaterializedItem(Base):
    """Immutable provider item mapping produced by batch materialization."""

    __tablename__ = "evaluation_dataset_curation_materialized_items"
    __table_args__ = (
        UniqueConstraint(
            "materialization_id",
            "curation_item_id",
            name="uq_eval_dataset_curation_materialized_items_item",
        ),
        UniqueConstraint(
            "materialization_id",
            "provider_dataset_item_ref",
            name="uq_eval_dataset_curation_materialized_items_provider",
        ),
        Index(
            "ix_eval_dataset_curation_materialized_items_materialization",
            "materialization_id",
        ),
        Index(
            "ix_eval_dataset_curation_materialized_items_curation_item",
            "curation_item_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    materialization_id: Mapped[int] = mapped_column(
        ForeignKey(
            "evaluation_dataset_curation_materializations.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    curation_item_id: Mapped[int] = mapped_column(
        ForeignKey(
            "evaluation_dataset_curation_items.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    provider_dataset_item_ref: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    materialization: Mapped[EvaluationDatasetCurationMaterialization] = relationship(back_populates="items")
    curation_item: Mapped[EvaluationDatasetCurationItem] = relationship()


class EvaluationSamplingPolicy(Base):
    """Namespace-scoped identity for reproducible trace sampling rules."""

    __tablename__ = "evaluation_sampling_policies"
    __table_args__ = (
        UniqueConstraint(
            "public_id",
            name="uq_evaluation_sampling_policies_public_id",
        ),
        UniqueConstraint(
            "namespace_id",
            "name",
            name="uq_evaluation_sampling_policies_namespace_name",
        ),
        Index(
            "ix_evaluation_sampling_policies_namespace_status_created",
            "namespace_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[EvaluationSamplingPolicyStatus] = mapped_column(
        Enum(
            EvaluationSamplingPolicyStatus,
            name="evaluation_sampling_policy_status",
        ),
        default=EvaluationSamplingPolicyStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    versions: Mapped[list["EvaluationSamplingPolicyVersion"]] = relationship(
        back_populates="policy",
        lazy="selectin",
        order_by="EvaluationSamplingPolicyVersion.version",
    )


class EvaluationSamplingPolicyVersion(Base):
    """Immutable metadata-only sampling policy version."""

    __tablename__ = "evaluation_sampling_policy_versions"
    __table_args__ = (
        UniqueConstraint(
            "public_id",
            name="uq_evaluation_sampling_policy_versions_public_id",
        ),
        UniqueConstraint(
            "policy_id",
            "version",
            name="uq_evaluation_sampling_policy_versions_policy_version",
        ),
        UniqueConstraint(
            "policy_id",
            "config_digest",
            name="uq_evaluation_sampling_policy_versions_policy_digest",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_evaluation_sampling_policy_versions_positive",
        ),
        CheckConstraint(
            "sample_size >= 1 AND sample_size <= 20 "
            "AND minimum_sample_size >= 1 "
            "AND minimum_sample_size <= sample_size",
            name="ck_evaluation_sampling_policy_versions_sample_size",
        ),
        CheckConstraint(
            "candidate_limit >= sample_size AND candidate_limit <= 100",
            name="ck_evaluation_sampling_policy_versions_candidate_limit",
        ),
        CheckConstraint(
            "exclude_governed = 1",
            name="ck_evaluation_sampling_policy_versions_exclude_governed",
        ),
        Index(
            "ix_evaluation_sampling_policy_versions_policy_created",
            "policy_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False)
    policy_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_sampling_policies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    config_digest: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    strategy: Mapped[EvaluationSamplingStrategy] = mapped_column(
        Enum(
            EvaluationSamplingStrategy,
            name="evaluation_sampling_strategy",
        ),
        nullable=False,
    )
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    observation_name: Mapped[str | None] = mapped_column(String(200))
    observation_type: Mapped[str | None] = mapped_column(String(32))
    environment: Mapped[str | None] = mapped_column(String(100))
    root_only: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    exclude_governed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    policy: Mapped[EvaluationSamplingPolicy] = relationship(back_populates="versions")
    runs: Mapped[list["EvaluationSamplingRun"]] = relationship(
        back_populates="policy_version",
        lazy="selectin",
    )


class EvaluationSamplingRun(Base):
    """Immutable selection snapshot produced by an exact policy version."""

    __tablename__ = "evaluation_sampling_runs"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_evaluation_sampling_runs_public_id"),
        UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_evaluation_sampling_runs_idempotency",
        ),
        UniqueConstraint(
            "dataset_id",
            "run_digest",
            name="uq_evaluation_sampling_runs_dataset_digest",
        ),
        UniqueConstraint(
            "curation_batch_id",
            name="uq_evaluation_sampling_runs_curation_batch",
        ),
        CheckConstraint(
            "candidate_count >= 0 AND eligible_count >= 0 "
            "AND selected_count >= 1 "
            "AND eligible_count <= candidate_count "
            "AND selected_count <= eligible_count "
            "AND selected_count <= 20",
            name="ck_evaluation_sampling_runs_counts",
        ),
        Index(
            "ix_evaluation_sampling_runs_namespace_created",
            "namespace_id",
            "created_at",
        ),
        Index(
            "ix_evaluation_sampling_runs_policy_version_created",
            "policy_version_id",
            "created_at",
        ),
        Index(
            "ix_evaluation_sampling_runs_dataset_created",
            "dataset_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    policy_version_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_sampling_policy_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_datasets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    curation_batch_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_dataset_curation_batches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    from_start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    to_start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    eligible_count: Mapped[int] = mapped_column(Integer, nullable=False)
    selected_count: Mapped[int] = mapped_column(Integer, nullable=False)
    selection_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    run_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    policy_version: Mapped[EvaluationSamplingPolicyVersion] = relationship(back_populates="runs")
    dataset: Mapped[EvaluationDataset] = relationship()
    curation_batch: Mapped[EvaluationDatasetCurationBatch] = relationship()
    items: Mapped[list["EvaluationSamplingRunItem"]] = relationship(
        back_populates="run",
        lazy="selectin",
        order_by="EvaluationSamplingRunItem.position",
    )


class EvaluationSamplingRunItem(Base):
    """Content-free ranked source reference selected by a sampling run."""

    __tablename__ = "evaluation_sampling_run_items"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "position",
            name="uq_evaluation_sampling_run_items_position",
        ),
        UniqueConstraint(
            "run_id",
            "source_trace_ref",
            "source_observation_ref",
            name="uq_evaluation_sampling_run_items_source",
        ),
        Index("ix_evaluation_sampling_run_items_run_id", "run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_sampling_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    source_trace_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    source_observation_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    rank_digest: Mapped[str] = mapped_column(String(64), nullable=False)

    run: Mapped[EvaluationSamplingRun] = relationship(back_populates="items")


class EvaluationAnnotationQueueBinding(Base):
    """Replaceable provider queue identity bound to one Namespace."""

    __tablename__ = "evaluation_annotation_queue_bindings"
    __table_args__ = (
        UniqueConstraint(
            "public_id",
            name="uq_evaluation_annotation_queue_bindings_public_id",
        ),
        UniqueConstraint(
            "namespace_id",
            "provider",
            "provider_queue_ref",
            name="uq_eval_annotation_queue_bindings_provider_ref",
        ),
        Index(
            "ix_eval_annotation_queue_bindings_namespace_status_created",
            "namespace_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    provider: Mapped[EvaluationProvider] = mapped_column(
        Enum(EvaluationProvider, name="evaluation_provider"),
        nullable=False,
    )
    provider_queue_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_queue_name: Mapped[str] = mapped_column(String(200), nullable=False)
    score_config_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    provider_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[EvaluationAnnotationQueueBindingStatus] = mapped_column(
        Enum(
            EvaluationAnnotationQueueBindingStatus,
            name="evaluation_annotation_queue_binding_status",
        ),
        default=EvaluationAnnotationQueueBindingStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    dispatches: Mapped[list["EvaluationAnnotationDispatch"]] = relationship(
        back_populates="binding",
        lazy="selectin",
    )


class EvaluationAnnotationDispatch(Base):
    """Durable, retryable intent to place one curation batch in a queue."""

    __tablename__ = "evaluation_annotation_dispatches"
    __table_args__ = (
        UniqueConstraint(
            "public_id",
            name="uq_evaluation_annotation_dispatches_public_id",
        ),
        UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_evaluation_annotation_dispatches_idempotency",
        ),
        UniqueConstraint(
            "binding_id",
            "curation_batch_id",
            name="uq_evaluation_annotation_dispatches_binding_batch",
        ),
        UniqueConstraint(
            "binding_id",
            "request_digest",
            name="uq_evaluation_annotation_dispatches_request_digest",
        ),
        CheckConstraint(
            "item_count >= 1 AND item_count <= 20 "
            "AND synced_count >= 0 AND completed_count >= 0 "
            "AND failed_count >= 0 AND synced_count <= item_count "
            "AND completed_count <= synced_count "
            "AND failed_count <= item_count "
            "AND synced_count + failed_count <= item_count",
            name="ck_evaluation_annotation_dispatches_counts",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_evaluation_annotation_dispatches_attempts",
        ),
        CheckConstraint(
            "(status = 'RUNNING' AND lease_owner IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'RUNNING' AND lease_owner IS NULL "
            "AND lease_expires_at IS NULL)",
            name="ck_evaluation_annotation_dispatches_lease",
        ),
        CheckConstraint(
            "status <> 'SYNCED' OR (synced_count = item_count AND failed_count = 0)",
            name="ck_evaluation_annotation_dispatches_synced",
        ),
        Index(
            "ix_evaluation_annotation_dispatches_dispatch",
            "status",
            "available_at",
            "lease_expires_at",
        ),
        Index(
            "ix_eval_annotation_dispatches_namespace_created",
            "namespace_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    binding_id: Mapped[int] = mapped_column(
        ForeignKey(
            "evaluation_annotation_queue_bindings.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )
    curation_batch_id: Mapped[int] = mapped_column(
        ForeignKey(
            "evaluation_dataset_curation_batches.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )
    sampling_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("evaluation_sampling_runs.id", ondelete="RESTRICT"),
        index=True,
    )
    provider_queue_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[EvaluationAnnotationDispatchStatus] = mapped_column(
        Enum(
            EvaluationAnnotationDispatchStatus,
            name="evaluation_annotation_dispatch_status",
        ),
        default=EvaluationAnnotationDispatchStatus.PENDING,
        nullable=False,
        index=True,
    )
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    synced_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    error_code: Mapped[str | None] = mapped_column(String(100))
    schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    binding: Mapped[EvaluationAnnotationQueueBinding] = relationship(back_populates="dispatches")
    curation_batch: Mapped[EvaluationDatasetCurationBatch] = relationship()
    sampling_run: Mapped[EvaluationSamplingRun | None] = relationship()
    items: Mapped[list["EvaluationAnnotationDispatchItem"]] = relationship(
        back_populates="dispatch",
        lazy="selectin",
        order_by="EvaluationAnnotationDispatchItem.position",
    )


class EvaluationAnnotationDispatchItem(Base):
    """Content-free provider queue receipt for one Observation."""

    __tablename__ = "evaluation_annotation_dispatch_items"
    __table_args__ = (
        UniqueConstraint(
            "dispatch_id",
            "position",
            name="uq_evaluation_annotation_dispatch_items_position",
        ),
        UniqueConstraint(
            "dispatch_id",
            "source_observation_ref",
            name="uq_evaluation_annotation_dispatch_items_source",
        ),
        UniqueConstraint(
            "dispatch_id",
            "provider_queue_item_ref",
            name="uq_evaluation_annotation_dispatch_items_provider_ref",
        ),
        CheckConstraint(
            "position >= 1 AND position <= 20 AND attempt_count >= 0",
            name="ck_evaluation_annotation_dispatch_items_position_attempts",
        ),
        CheckConstraint(
            "sync_status <> 'SYNCED' OR "
            "(provider_queue_item_ref IS NOT NULL "
            "AND provider_annotation_status IS NOT NULL)",
            name="ck_evaluation_annotation_dispatch_items_synced",
        ),
        Index(
            "ix_evaluation_annotation_dispatch_items_dispatch_id",
            "dispatch_id",
        ),
        Index(
            "ix_eval_annotation_items_provider_status",
            "provider_annotation_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dispatch_id: Mapped[int] = mapped_column(
        ForeignKey(
            "evaluation_annotation_dispatches.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    source_trace_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    source_observation_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    sync_status: Mapped[EvaluationAnnotationItemSyncStatus] = mapped_column(
        Enum(
            EvaluationAnnotationItemSyncStatus,
            name="evaluation_annotation_item_sync_status",
        ),
        default=EvaluationAnnotationItemSyncStatus.PENDING,
        nullable=False,
        index=True,
    )
    provider_queue_item_ref: Mapped[str | None] = mapped_column(String(255))
    provider_annotation_status: Mapped[EvaluationAnnotationProviderStatus | None] = mapped_column(
        Enum(
            EvaluationAnnotationProviderStatus,
            name="evaluation_annotation_provider_status",
        ),
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    provider_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    dispatch: Mapped[EvaluationAnnotationDispatch] = relationship(back_populates="items")


class EvaluationPromotionPolicy(Base):
    """Namespace-scoped identity for annotation-driven promotion rules."""

    __tablename__ = "evaluation_promotion_policies"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_evaluation_promotion_policies_public_id"),
        UniqueConstraint(
            "namespace_id",
            "name",
            name="uq_evaluation_promotion_policies_namespace_name",
        ),
        Index(
            "ix_eval_promotion_policies_namespace_status_created",
            "namespace_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[EvaluationPromotionPolicyStatus] = mapped_column(
        Enum(
            EvaluationPromotionPolicyStatus,
            name="evaluation_promotion_policy_status",
        ),
        default=EvaluationPromotionPolicyStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    versions: Mapped[list["EvaluationPromotionPolicyVersion"]] = relationship(
        back_populates="policy",
        lazy="selectin",
        order_by="EvaluationPromotionPolicyVersion.version",
    )


class EvaluationPromotionPolicyVersion(Base):
    """Immutable quality/diversity contract pinned to a queue binding."""

    __tablename__ = "evaluation_promotion_policy_versions"
    __table_args__ = (
        UniqueConstraint(
            "public_id",
            name="uq_eval_promotion_policy_versions_public_id",
        ),
        UniqueConstraint(
            "policy_id",
            "version",
            name="uq_eval_promotion_policy_versions_policy_version",
        ),
        UniqueConstraint(
            "policy_id",
            "config_digest",
            name="uq_eval_promotion_policy_versions_policy_digest",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_eval_promotion_policy_versions_positive",
        ),
        CheckConstraint(
            "min_distinct_buckets >= 1 AND min_distinct_buckets <= 20",
            name="ck_eval_promotion_policy_versions_buckets",
        ),
        Index(
            "ix_eval_promotion_policy_versions_policy_created",
            "policy_id",
            "created_at",
        ),
        Index("ix_eval_promotion_policy_versions_policy_id", "policy_id"),
        Index("ix_eval_promotion_policy_versions_binding_id", "binding_id"),
        Index(
            "ix_eval_promotion_policy_versions_config_digest",
            "config_digest",
        ),
        Index(
            "ix_eval_promotion_policy_versions_created_by",
            "created_by_user_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    policy_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_promotion_policies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    binding_id: Mapped[int] = mapped_column(
        ForeignKey(
            "evaluation_annotation_queue_bindings.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    config_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    score_config_id: Mapped[str] = mapped_column(String(255), nullable=False)
    score_data_type: Mapped[EvaluationPromotionScoreDataType] = mapped_column(
        Enum(
            EvaluationPromotionScoreDataType,
            name="evaluation_promotion_score_data_type",
        ),
        nullable=False,
    )
    minimum_numeric_score: Mapped[float | None] = mapped_column(Float)
    accepted_values_json: Mapped[list[str | bool] | None] = mapped_column(JSON)
    diversity_dimension: Mapped[EvaluationPromotionDiversityDimension] = mapped_column(
        Enum(
            EvaluationPromotionDiversityDimension,
            name="evaluation_promotion_diversity_dimension",
        ),
        nullable=False,
    )
    min_distinct_buckets: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    policy: Mapped[EvaluationPromotionPolicy] = relationship(back_populates="versions")
    binding: Mapped[EvaluationAnnotationQueueBinding] = relationship()
    runs: Mapped[list["EvaluationPromotionRun"]] = relationship(
        back_populates="policy_version",
        lazy="selectin",
    )


class EvaluationPromotionRun(Base):
    """Immutable, content-free recommendation for one completed dispatch."""

    __tablename__ = "evaluation_promotion_runs"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_evaluation_promotion_runs_public_id"),
        UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_evaluation_promotion_runs_idempotency",
        ),
        UniqueConstraint(
            "policy_version_id",
            "dispatch_id",
            "evidence_digest",
            name="uq_eval_promotion_runs_version_dispatch_evidence",
        ),
        CheckConstraint(
            "item_count >= 1 AND item_count <= 20 "
            "AND completed_count = item_count "
            "AND scored_count >= 0 AND scored_count <= item_count "
            "AND passed_count >= 0 AND passed_count <= scored_count "
            "AND distinct_bucket_count >= 0 "
            "AND distinct_bucket_count <= item_count",
            name="ck_evaluation_promotion_runs_counts",
        ),
        Index(
            "ix_eval_promotion_runs_namespace_created",
            "namespace_id",
            "created_at",
        ),
        Index(
            "ix_eval_promotion_runs_dispatch_created",
            "dispatch_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    policy_version_id: Mapped[int] = mapped_column(
        ForeignKey(
            "evaluation_promotion_policy_versions.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )
    dispatch_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_annotation_dispatches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[EvaluationPromotionOutcome] = mapped_column(
        Enum(
            EvaluationPromotionOutcome,
            name="evaluation_promotion_outcome",
        ),
        nullable=False,
        index=True,
    )
    reason_codes_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    scored_count: Mapped[int] = mapped_column(Integer, nullable=False)
    passed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    distinct_bucket_count: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    policy_version: Mapped[EvaluationPromotionPolicyVersion] = relationship(back_populates="runs")
    dispatch: Mapped[EvaluationAnnotationDispatch] = relationship()
    items: Mapped[list["EvaluationPromotionRunItem"]] = relationship(
        back_populates="run",
        lazy="selectin",
        order_by="EvaluationPromotionRunItem.position",
    )


class EvaluationPromotionRunItem(Base):
    """Boolean/digest evidence only; score values stay in Langfuse."""

    __tablename__ = "evaluation_promotion_run_items"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "position",
            name="uq_evaluation_promotion_run_items_position",
        ),
        UniqueConstraint(
            "run_id",
            "source_observation_ref",
            name="uq_evaluation_promotion_run_items_source",
        ),
        CheckConstraint(
            "position >= 1 AND position <= 20",
            name="ck_evaluation_promotion_run_items_position",
        ),
        Index("ix_evaluation_promotion_run_items_run_id", "run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_promotion_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    source_trace_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    source_observation_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    score_present: Mapped[bool] = mapped_column(Boolean, nullable=False)
    quality_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    diversity_bucket_present: Mapped[bool] = mapped_column(Boolean, nullable=False)
    score_evidence_digest: Mapped[str | None] = mapped_column(String(64))
    diversity_bucket_digest: Mapped[str | None] = mapped_column(String(64))
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)

    run: Mapped[EvaluationPromotionRun] = relationship(back_populates="items")


class EvaluationCaseRoutingPolicy(Base):
    """Namespace-scoped identity for Golden/Bad Case routing rules."""

    __tablename__ = "evaluation_case_routing_policies"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_eval_case_routing_policies_public_id"),
        UniqueConstraint(
            "namespace_id",
            "name",
            name="uq_eval_case_routing_policies_ns_name",
        ),
        Index(
            "ix_eval_case_routing_policies_ns_status_created",
            "namespace_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[EvaluationCaseRoutingPolicyStatus] = mapped_column(
        Enum(
            EvaluationCaseRoutingPolicyStatus,
            name="evaluation_case_routing_policy_status",
        ),
        default=EvaluationCaseRoutingPolicyStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    versions: Mapped[list["EvaluationCaseRoutingPolicyVersion"]] = relationship(
        back_populates="policy",
        lazy="selectin",
        order_by="EvaluationCaseRoutingPolicyVersion.version",
    )


class EvaluationCaseRoutingPolicyVersion(Base):
    """Immutable cluster-balanced routing contract."""

    __tablename__ = "evaluation_case_routing_policy_versions"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_eval_case_routing_versions_public_id"),
        UniqueConstraint(
            "policy_id",
            "version",
            name="uq_eval_case_routing_versions_policy_version",
        ),
        UniqueConstraint(
            "policy_id",
            "config_digest",
            name="uq_eval_case_routing_versions_policy_digest",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_eval_case_routing_versions_positive",
        ),
        CheckConstraint(
            "golden_target_size >= 1 AND golden_target_size <= 20 "
            "AND golden_min_items >= 1 "
            "AND golden_min_items <= golden_target_size "
            "AND bad_case_target_size >= 1 "
            "AND bad_case_target_size <= 20 "
            "AND bad_case_min_items >= 1 "
            "AND bad_case_min_items <= bad_case_target_size",
            name="ck_eval_case_routing_versions_sizes",
        ),
        Index(
            "ix_eval_case_routing_versions_policy_created",
            "policy_id",
            "created_at",
        ),
        Index(
            "ix_eval_case_routing_versions_source_promotion",
            "source_promotion_policy_version_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    policy_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_case_routing_policies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_promotion_policy_version_id: Mapped[int] = mapped_column(
        ForeignKey(
            "evaluation_promotion_policy_versions.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    config_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy: Mapped[EvaluationCaseRoutingStrategy] = mapped_column(
        Enum(
            EvaluationCaseRoutingStrategy,
            name="evaluation_case_routing_strategy",
        ),
        nullable=False,
    )
    golden_target_size: Mapped[int] = mapped_column(Integer, nullable=False)
    golden_min_items: Mapped[int] = mapped_column(Integer, nullable=False)
    bad_case_target_size: Mapped[int] = mapped_column(Integer, nullable=False)
    bad_case_min_items: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    policy: Mapped[EvaluationCaseRoutingPolicy] = relationship(back_populates="versions")
    source_promotion_policy_version: Mapped[EvaluationPromotionPolicyVersion] = relationship()
    runs: Mapped[list["EvaluationCaseRoutingRun"]] = relationship(
        back_populates="policy_version",
        lazy="selectin",
    )


class EvaluationCaseRoutingRun(Base):
    """Immutable cross-batch routing result and generated review queues."""

    __tablename__ = "evaluation_case_routing_runs"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_eval_case_routing_runs_public_id"),
        UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_eval_case_routing_runs_idempotency",
        ),
        UniqueConstraint(
            "policy_version_id",
            "golden_dataset_id",
            "bad_case_dataset_id",
            "evidence_digest",
            name="uq_eval_case_routing_runs_evidence",
        ),
        CheckConstraint(
            "source_run_count >= 1 AND source_run_count <= 20 "
            "AND candidate_count >= 1 AND candidate_count <= 400 "
            "AND golden_candidate_count >= 0 "
            "AND bad_case_candidate_count >= 0 "
            "AND excluded_count >= 0 "
            "AND golden_candidate_count + bad_case_candidate_count "
            "+ excluded_count = candidate_count "
            "AND golden_selected_count >= 0 "
            "AND golden_selected_count <= 20 "
            "AND golden_selected_count <= golden_candidate_count "
            "AND bad_case_selected_count >= 0 "
            "AND bad_case_selected_count <= 20 "
            "AND bad_case_selected_count <= bad_case_candidate_count "
            "AND golden_cluster_count >= 0 "
            "AND golden_cluster_count <= golden_candidate_count "
            "AND bad_case_cluster_count >= 0 "
            "AND bad_case_cluster_count <= bad_case_candidate_count",
            name="ck_eval_case_routing_runs_counts",
        ),
        Index(
            "ix_eval_case_routing_runs_ns_created",
            "namespace_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    policy_version_id: Mapped[int] = mapped_column(
        ForeignKey(
            "evaluation_case_routing_policy_versions.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )
    golden_dataset_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_datasets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    bad_case_dataset_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_datasets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    golden_curation_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "evaluation_dataset_curation_batches.id",
            ondelete="RESTRICT",
        ),
        unique=True,
    )
    bad_case_curation_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "evaluation_dataset_curation_batches.id",
            ondelete="RESTRICT",
        ),
        unique=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    routing_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[EvaluationCaseRoutingOutcome] = mapped_column(
        Enum(
            EvaluationCaseRoutingOutcome,
            name="evaluation_case_routing_outcome",
        ),
        nullable=False,
        index=True,
    )
    reason_codes_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    source_run_count: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    golden_candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    bad_case_candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    excluded_count: Mapped[int] = mapped_column(Integer, nullable=False)
    golden_selected_count: Mapped[int] = mapped_column(Integer, nullable=False)
    bad_case_selected_count: Mapped[int] = mapped_column(Integer, nullable=False)
    golden_cluster_count: Mapped[int] = mapped_column(Integer, nullable=False)
    bad_case_cluster_count: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    policy_version: Mapped[EvaluationCaseRoutingPolicyVersion] = relationship(back_populates="runs")
    golden_dataset: Mapped[EvaluationDataset] = relationship(foreign_keys=[golden_dataset_id])
    bad_case_dataset: Mapped[EvaluationDataset] = relationship(foreign_keys=[bad_case_dataset_id])
    golden_curation_batch: Mapped[EvaluationDatasetCurationBatch | None] = relationship(
        foreign_keys=[golden_curation_batch_id]
    )
    bad_case_curation_batch: Mapped[EvaluationDatasetCurationBatch | None] = relationship(
        foreign_keys=[bad_case_curation_batch_id]
    )
    items: Mapped[list["EvaluationCaseRoutingRunItem"]] = relationship(
        back_populates="run",
        lazy="selectin",
        order_by="EvaluationCaseRoutingRunItem.position",
    )


class EvaluationCaseRoutingRunItem(Base):
    """Content-free routing decision for one Promotion evidence item."""

    __tablename__ = "evaluation_case_routing_run_items"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "position",
            name="uq_eval_case_routing_items_position",
        ),
        UniqueConstraint(
            "run_id",
            "source_observation_ref",
            name="uq_eval_case_routing_items_source",
        ),
        CheckConstraint(
            "position >= 1 AND position <= 400",
            name="ck_eval_case_routing_items_position",
        ),
        Index("ix_eval_case_routing_items_run_id", "run_id"),
        Index(
            "ix_eval_case_routing_items_source_promotion_run",
            "source_promotion_run_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_case_routing_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_promotion_run_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_promotion_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    source_trace_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    source_observation_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    lane: Mapped[EvaluationCaseRoutingLane] = mapped_column(
        Enum(
            EvaluationCaseRoutingLane,
            name="evaluation_case_routing_lane",
        ),
        nullable=False,
        index=True,
    )
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False)
    cluster_digest: Mapped[str | None] = mapped_column(String(64))
    rank_digest: Mapped[str | None] = mapped_column(String(64))
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)

    run: Mapped[EvaluationCaseRoutingRun] = relationship(back_populates="items")
    source_promotion_run: Mapped[EvaluationPromotionRun] = relationship()


class EvaluationSemanticClusteringPolicy(Base):
    """Namespace identity for provider-neutral semantic clustering rules."""

    __tablename__ = "evaluation_semantic_clustering_policies"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_eval_semantic_clustering_policies_public_id"),
        UniqueConstraint(
            "namespace_id",
            "name",
            name="uq_eval_semantic_clustering_policies_ns_name",
        ),
        Index(
            "ix_eval_semantic_clustering_policies_ns_status_created",
            "namespace_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[EvaluationSemanticClusteringPolicyStatus] = mapped_column(
        Enum(
            EvaluationSemanticClusteringPolicyStatus,
            name="evaluation_semantic_clustering_policy_status",
        ),
        default=EvaluationSemanticClusteringPolicyStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    versions: Mapped[list["EvaluationSemanticClusteringPolicyVersion"]] = relationship(
        back_populates="policy",
        lazy="selectin",
        order_by="EvaluationSemanticClusteringPolicyVersion.version",
    )


class EvaluationSemanticClusteringPolicyVersion(Base):
    """Immutable embedding and similarity contract over routed Bad Cases."""

    __tablename__ = "evaluation_semantic_clustering_policy_versions"
    __table_args__ = (
        UniqueConstraint(
            "public_id",
            name="uq_eval_semantic_clustering_versions_public_id",
        ),
        UniqueConstraint(
            "policy_id",
            "version",
            name="uq_eval_semantic_clustering_versions_policy_version",
        ),
        UniqueConstraint(
            "policy_id",
            "config_digest",
            name="uq_eval_semantic_clustering_versions_policy_digest",
        ),
        CheckConstraint(
            "version >= 1 AND dimensions >= 1 AND dimensions <= 4096 "
            "AND similarity_threshold >= 0 AND similarity_threshold <= 1 "
            "AND min_cluster_size >= 2 AND min_cluster_size <= 20 "
            "AND max_items >= 2 AND max_items <= 20 "
            "AND max_content_chars >= 100 AND max_content_chars <= 16000",
            name="ck_eval_semantic_clustering_versions_limits",
        ),
        Index(
            "ix_eval_semantic_clustering_versions_policy_created",
            "policy_id",
            "created_at",
        ),
        Index(
            "ix_eval_semantic_clustering_versions_source_routing",
            "source_case_routing_policy_version_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    policy_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_semantic_clustering_policies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_case_routing_policy_version_id: Mapped[int] = mapped_column(
        ForeignKey(
            "evaluation_case_routing_policy_versions.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    config_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_profile: Mapped[str] = mapped_column(String(100), nullable=False)
    model_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    similarity_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    min_cluster_size: Mapped[int] = mapped_column(Integer, nullable=False)
    max_items: Mapped[int] = mapped_column(Integer, nullable=False)
    max_content_chars: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    policy: Mapped[EvaluationSemanticClusteringPolicy] = relationship(back_populates="versions")
    source_case_routing_policy_version: Mapped[EvaluationCaseRoutingPolicyVersion] = relationship()
    runs: Mapped[list["EvaluationSemanticClusteringRun"]] = relationship(
        back_populates="policy_version",
        lazy="selectin",
    )


class EvaluationSemanticClusteringRun(Base):
    """Immutable metadata receipt for one ephemeral semantic clustering pass."""

    __tablename__ = "evaluation_semantic_clustering_runs"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_eval_semantic_clustering_runs_public_id"),
        UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_eval_semantic_clustering_runs_idempotency",
        ),
        Index(
            "ix_eval_semantic_clustering_runs_evidence",
            "policy_version_id",
            "source_case_routing_run_id",
            "evidence_digest",
        ),
        CheckConstraint(
            "source_item_count >= 1 AND source_item_count <= 20 "
            "AND cluster_count >= 1 AND cluster_count <= source_item_count "
            "AND eligible_cluster_count >= 0 "
            "AND eligible_cluster_count <= cluster_count",
            name="ck_eval_semantic_clustering_runs_counts",
        ),
        Index(
            "ix_eval_semantic_clustering_runs_ns_created",
            "namespace_id",
            "created_at",
        ),
        Index(
            "ix_eval_semantic_clustering_runs_source_routing",
            "source_case_routing_run_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    policy_version_id: Mapped[int] = mapped_column(
        ForeignKey(
            "evaluation_semantic_clustering_policy_versions.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )
    source_case_routing_run_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_case_routing_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    clustering_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[EvaluationSemanticClusteringOutcome] = mapped_column(
        Enum(
            EvaluationSemanticClusteringOutcome,
            name="evaluation_semantic_clustering_outcome",
        ),
        nullable=False,
        index=True,
    )
    reason_codes_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    source_item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    cluster_count: Mapped[int] = mapped_column(Integer, nullable=False)
    eligible_cluster_count: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    policy_version: Mapped[EvaluationSemanticClusteringPolicyVersion] = relationship(back_populates="runs")
    source_case_routing_run: Mapped[EvaluationCaseRoutingRun] = relationship()
    items: Mapped[list["EvaluationSemanticClusteringRunItem"]] = relationship(
        back_populates="run",
        lazy="selectin",
        order_by="EvaluationSemanticClusteringRunItem.position",
    )


class EvaluationSemanticClusteringRunItem(Base):
    """Content-free membership receipt; raw vectors are deliberately absent."""

    __tablename__ = "evaluation_semantic_clustering_run_items"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "position",
            name="uq_eval_semantic_clustering_items_position",
        ),
        UniqueConstraint(
            "run_id",
            "source_case_routing_item_id",
            name="uq_eval_semantic_clustering_items_source",
        ),
        CheckConstraint(
            "position >= 1 AND position <= 20 "
            "AND cluster_size >= 1 AND cluster_size <= 20 "
            "AND similarity_to_centroid >= -1 "
            "AND similarity_to_centroid <= 1",
            name="ck_eval_semantic_clustering_items_limits",
        ),
        Index("ix_eval_semantic_clustering_items_run", "run_id"),
        Index(
            "ix_eval_semantic_clustering_items_source",
            "source_case_routing_item_id",
        ),
        Index(
            "ix_eval_semantic_clustering_items_cluster",
            "semantic_cluster_digest",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_semantic_clustering_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_case_routing_item_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_case_routing_run_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    semantic_cluster_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    cluster_size: Mapped[int] = mapped_column(Integer, nullable=False)
    similarity_to_centroid: Mapped[float] = mapped_column(Float, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_digest: Mapped[str] = mapped_column(String(64), nullable=False)

    run: Mapped[EvaluationSemanticClusteringRun] = relationship(back_populates="items")
    source_case_routing_item: Mapped[EvaluationCaseRoutingRunItem] = relationship()


class EvaluationSemanticRegressionPolicy(Base):
    """Namespace identity for semantic clustering regression thresholds."""

    __tablename__ = "evaluation_semantic_regression_policies"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_eval_semantic_regression_policies_public_id"),
        UniqueConstraint(
            "namespace_id",
            "name",
            name="uq_eval_semantic_regression_policies_ns_name",
        ),
        Index(
            "ix_eval_semantic_regression_policies_ns_status_created",
            "namespace_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[EvaluationSemanticRegressionPolicyStatus] = mapped_column(
        Enum(
            EvaluationSemanticRegressionPolicyStatus,
            name="evaluation_semantic_regression_policy_status",
        ),
        default=EvaluationSemanticRegressionPolicyStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    versions: Mapped[list["EvaluationSemanticRegressionPolicyVersion"]] = relationship(
        back_populates="policy",
        lazy="selectin",
        order_by="EvaluationSemanticRegressionPolicyVersion.version",
    )


class EvaluationSemanticRegressionPolicyVersion(Base):
    """Immutable offline quality and drift thresholds."""

    __tablename__ = "evaluation_semantic_regression_policy_versions"
    __table_args__ = (
        UniqueConstraint(
            "public_id",
            name="uq_eval_semantic_regression_versions_public_id",
        ),
        UniqueConstraint(
            "policy_id",
            "version",
            name="uq_eval_semantic_regression_versions_policy_version",
        ),
        UniqueConstraint(
            "policy_id",
            "config_digest",
            name="uq_eval_semantic_regression_versions_policy_digest",
        ),
        CheckConstraint(
            "version >= 1 "
            "AND minimum_pairwise_assignment_agreement >= 0 "
            "AND minimum_pairwise_assignment_agreement <= 1 "
            "AND maximum_cluster_count_change_ratio >= 0 "
            "AND maximum_cluster_count_change_ratio <= 1 "
            "AND maximum_mean_centroid_similarity_drop >= 0 "
            "AND maximum_mean_centroid_similarity_drop <= 1 "
            "AND maximum_eligible_cluster_ratio_drop >= 0 "
            "AND maximum_eligible_cluster_ratio_drop <= 1 "
            "AND require_exact_source_content = 1",
            name="ck_eval_semantic_regression_versions_limits",
        ),
        Index(
            "ix_eval_semantic_regression_versions_policy_created",
            "policy_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    policy_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_semantic_regression_policies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    config_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    minimum_pairwise_assignment_agreement: Mapped[float] = mapped_column(Float, nullable=False)
    maximum_cluster_count_change_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    maximum_mean_centroid_similarity_drop: Mapped[float] = mapped_column(Float, nullable=False)
    maximum_eligible_cluster_ratio_drop: Mapped[float] = mapped_column(Float, nullable=False)
    require_exact_source_content: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    policy: Mapped[EvaluationSemanticRegressionPolicy] = relationship(back_populates="versions")
    comparisons: Mapped[list["EvaluationSemanticRegressionComparison"]] = relationship(
        back_populates="policy_version", lazy="selectin"
    )


class EvaluationSemanticRegressionComparison(Base):
    """Immutable offline comparison of two exact semantic clustering runs."""

    __tablename__ = "evaluation_semantic_regression_comparisons"
    __table_args__ = (
        UniqueConstraint(
            "public_id",
            name="uq_eval_semantic_regression_comparisons_public_id",
        ),
        UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_eval_semantic_regression_comparisons_idempotency",
        ),
        UniqueConstraint(
            "baseline_run_id",
            "candidate_run_id",
            "policy_version_id",
            name="uq_eval_semantic_regression_comparisons_exact_pins",
        ),
        CheckConstraint(
            "baseline_run_id <> candidate_run_id",
            name="ck_eval_semantic_regression_comparisons_distinct_runs",
        ),
        CheckConstraint(
            "source_item_count >= 2 AND source_item_count <= 20 "
            "AND baseline_cluster_count >= 1 "
            "AND candidate_cluster_count >= 1",
            name="ck_eval_semantic_regression_comparisons_counts",
        ),
        CheckConstraint(
            "(pairwise_assignment_agreement IS NULL OR "
            "(pairwise_assignment_agreement >= 0 "
            "AND pairwise_assignment_agreement <= 1)) "
            "AND baseline_eligible_cluster_ratio >= 0 "
            "AND baseline_eligible_cluster_ratio <= 1 "
            "AND candidate_eligible_cluster_ratio >= 0 "
            "AND candidate_eligible_cluster_ratio <= 1 "
            "AND cluster_count_change_ratio >= 0 "
            "AND cluster_count_change_ratio <= 1 "
            "AND eligible_cluster_ratio_drop >= 0 "
            "AND eligible_cluster_ratio_drop <= 1",
            name="ck_eval_semantic_regression_comparisons_metric_ranges",
        ),
        CheckConstraint(
            "baseline_mean_centroid_similarity >= -1 "
            "AND baseline_mean_centroid_similarity <= 1 "
            "AND candidate_mean_centroid_similarity >= -1 "
            "AND candidate_mean_centroid_similarity <= 1 "
            "AND mean_centroid_similarity_drop >= 0 "
            "AND mean_centroid_similarity_drop <= 2",
            name="ck_eval_semantic_regression_comparisons_similarity_ranges",
        ),
        Index(
            "ix_eval_semantic_regression_comparisons_ns_outcome_created",
            "namespace_id",
            "outcome",
            "created_at",
        ),
        Index(
            "ix_eval_semantic_regression_comparisons_candidate",
            "candidate_run_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    baseline_run_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_semantic_clustering_runs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    candidate_run_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_semantic_clustering_runs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    policy_version_id: Mapped[int] = mapped_column(
        ForeignKey(
            "evaluation_semantic_regression_policy_versions.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    outcome: Mapped[EvaluationSemanticRegressionOutcome] = mapped_column(
        Enum(
            EvaluationSemanticRegressionOutcome,
            name="evaluation_semantic_regression_outcome",
        ),
        nullable=False,
        index=True,
    )
    reason_codes_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    source_item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    pairwise_assignment_agreement: Mapped[float | None] = mapped_column(Float)
    baseline_cluster_count: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_cluster_count: Mapped[int] = mapped_column(Integer, nullable=False)
    cluster_count_change_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    baseline_eligible_cluster_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    candidate_eligible_cluster_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    eligible_cluster_ratio_drop: Mapped[float] = mapped_column(Float, nullable=False)
    baseline_mean_centroid_similarity: Mapped[float] = mapped_column(Float, nullable=False)
    candidate_mean_centroid_similarity: Mapped[float] = mapped_column(Float, nullable=False)
    mean_centroid_similarity_drop: Mapped[float] = mapped_column(Float, nullable=False)
    assignment_agreement_breached: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cluster_count_change_breached: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    eligible_cluster_ratio_drop_breached: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    centroid_similarity_drop_breached: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reproducibility_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    baseline_run: Mapped[EvaluationSemanticClusteringRun] = relationship(foreign_keys=[baseline_run_id])
    candidate_run: Mapped[EvaluationSemanticClusteringRun] = relationship(foreign_keys=[candidate_run_id])
    policy_version: Mapped[EvaluationSemanticRegressionPolicyVersion] = relationship(back_populates="comparisons")


class EvaluationSemanticMonitor(Base):
    """Exact, immutable semantic drift pins plus operational schedule state."""

    __tablename__ = "evaluation_semantic_monitors"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_eval_semantic_monitors_public_id"),
        UniqueConstraint(
            "namespace_id",
            "name",
            name="uq_eval_semantic_monitors_ns_name",
        ),
        UniqueConstraint(
            "namespace_id",
            "config_digest",
            name="uq_eval_semantic_monitors_ns_config",
        ),
        CheckConstraint(
            "interval_seconds >= 60 AND interval_seconds <= 2592000",
            name="ck_eval_semantic_monitors_interval",
        ),
        Index(
            "ix_eval_semantic_monitors_ns_status_due",
            "namespace_id",
            "status",
            "next_run_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    baseline_run_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_semantic_clustering_runs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    candidate_policy_version_id: Mapped[int] = mapped_column(
        ForeignKey(
            "evaluation_semantic_clustering_policy_versions.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )
    regression_policy_version_id: Mapped[int] = mapped_column(
        ForeignKey(
            "evaluation_semantic_regression_policy_versions.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    config_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[EvaluationSemanticMonitorStatus] = mapped_column(
        Enum(
            EvaluationSemanticMonitorStatus,
            name="evaluation_semantic_monitor_status",
        ),
        default=EvaluationSemanticMonitorStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    baseline_run: Mapped[EvaluationSemanticClusteringRun] = relationship(foreign_keys=[baseline_run_id])
    candidate_policy_version: Mapped[EvaluationSemanticClusteringPolicyVersion] = relationship()
    regression_policy_version: Mapped[EvaluationSemanticRegressionPolicyVersion] = relationship()
    runs: Mapped[list["EvaluationSemanticMonitorRun"]] = relationship(
        back_populates="monitor",
        lazy="selectin",
        order_by="EvaluationSemanticMonitorRun.created_at",
    )
    alerts: Mapped[list["EvaluationSemanticMonitorAlert"]] = relationship(
        back_populates="monitor",
        lazy="selectin",
        order_by="EvaluationSemanticMonitorAlert.created_at",
    )


class EvaluationSemanticMonitorRun(Base):
    """Durable leaseable receipt for one scheduled semantic observation."""

    __tablename__ = "evaluation_semantic_monitor_runs"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_eval_semantic_monitor_runs_public_id"),
        UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_eval_semantic_monitor_runs_idempotency",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 100",
            name="ck_eval_semantic_monitor_runs_attempts",
        ),
        Index(
            "ix_eval_semantic_monitor_runs_dispatch",
            "status",
            "available_at",
            "lease_expires_at",
        ),
        Index(
            "ix_eval_semantic_monitor_runs_ns_created",
            "namespace_id",
            "created_at",
        ),
        Index(
            "ix_eval_semantic_monitor_runs_monitor_scheduled",
            "monitor_id",
            "scheduled_for",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    monitor_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_semantic_monitors.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[EvaluationSemanticMonitorRunStatus] = mapped_column(
        Enum(
            EvaluationSemanticMonitorRunStatus,
            name="evaluation_semantic_monitor_run_status",
        ),
        default=EvaluationSemanticMonitorRunStatus.QUEUED,
        nullable=False,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    candidate_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("evaluation_semantic_clustering_runs.id", ondelete="RESTRICT"),
        index=True,
    )
    comparison_id: Mapped[int | None] = mapped_column(
        ForeignKey("evaluation_semantic_regression_comparisons.id", ondelete="RESTRICT"),
        index=True,
    )
    outcome: Mapped[EvaluationSemanticRegressionOutcome | None] = mapped_column(
        Enum(
            EvaluationSemanticRegressionOutcome,
            name="evaluation_semantic_monitor_run_outcome",
        )
    )
    error_code: Mapped[str | None] = mapped_column(String(100))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    monitor: Mapped[EvaluationSemanticMonitor] = relationship(back_populates="runs")
    candidate_run: Mapped[EvaluationSemanticClusteringRun | None] = relationship()
    comparison: Mapped[EvaluationSemanticRegressionComparison | None] = relationship()
    alert: Mapped["EvaluationSemanticMonitorAlert | None"] = relationship(
        back_populates="monitor_run",
        uselist=False,
    )


class EvaluationSemanticMonitorAlert(Base):
    """Content-free in-app alert opened by a non-passing monitor run."""

    __tablename__ = "evaluation_semantic_monitor_alerts"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_eval_semantic_monitor_alerts_public_id"),
        UniqueConstraint(
            "monitor_run_id",
            name="uq_eval_semantic_monitor_alerts_run",
        ),
        Index(
            "ix_eval_semantic_monitor_alerts_ns_status_created",
            "namespace_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    monitor_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_semantic_monitors.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    monitor_run_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_semantic_monitor_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    comparison_id: Mapped[int | None] = mapped_column(
        ForeignKey("evaluation_semantic_regression_comparisons.id", ondelete="RESTRICT"),
        index=True,
    )
    severity: Mapped[EvaluationSemanticMonitorAlertSeverity] = mapped_column(
        Enum(
            EvaluationSemanticMonitorAlertSeverity,
            name="evaluation_semantic_monitor_alert_severity",
        ),
        nullable=False,
        index=True,
    )
    status: Mapped[EvaluationSemanticMonitorAlertStatus] = mapped_column(
        Enum(
            EvaluationSemanticMonitorAlertStatus,
            name="evaluation_semantic_monitor_alert_status",
        ),
        default=EvaluationSemanticMonitorAlertStatus.OPEN,
        nullable=False,
        index=True,
    )
    reason_codes_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    acknowledged_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledgement_note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    monitor: Mapped[EvaluationSemanticMonitor] = relationship(back_populates="alerts")
    monitor_run: Mapped[EvaluationSemanticMonitorRun] = relationship(back_populates="alert")
    comparison: Mapped[EvaluationSemanticRegressionComparison | None] = relationship()


class EvaluationFailureTaxonomyPolicy(Base):
    """Namespace identity for metadata-only failure classification rules."""

    __tablename__ = "evaluation_failure_taxonomy_policies"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_eval_failure_taxonomy_policies_public_id"),
        UniqueConstraint(
            "namespace_id",
            "name",
            name="uq_eval_failure_taxonomy_policies_ns_name",
        ),
        Index(
            "ix_eval_failure_taxonomy_policies_ns_status_created",
            "namespace_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[EvaluationFailureTaxonomyPolicyStatus] = mapped_column(
        Enum(
            EvaluationFailureTaxonomyPolicyStatus,
            name="evaluation_failure_taxonomy_policy_status",
        ),
        default=EvaluationFailureTaxonomyPolicyStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    versions: Mapped[list["EvaluationFailureTaxonomyPolicyVersion"]] = relationship(
        back_populates="policy",
        lazy="selectin",
        order_by="EvaluationFailureTaxonomyPolicyVersion.version",
    )


class EvaluationFailureTaxonomyPolicyVersion(Base):
    """Immutable rules for classifying routed Bad Case clusters."""

    __tablename__ = "evaluation_failure_taxonomy_policy_versions"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_eval_failure_taxonomy_versions_public_id"),
        UniqueConstraint(
            "policy_id",
            "version",
            name="uq_eval_failure_taxonomy_versions_policy_version",
        ),
        UniqueConstraint(
            "policy_id",
            "config_digest",
            name="uq_eval_failure_taxonomy_versions_policy_digest",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_eval_failure_taxonomy_versions_positive",
        ),
        CheckConstraint(
            "min_cluster_occurrences >= 2 "
            "AND min_cluster_occurrences <= 20 "
            "AND min_source_runs >= 2 AND min_source_runs <= 20 "
            "AND max_candidates >= 1 AND max_candidates <= 20",
            name="ck_eval_failure_taxonomy_versions_limits",
        ),
        Index(
            "ix_eval_failure_taxonomy_versions_policy_created",
            "policy_id",
            "created_at",
        ),
        Index(
            "ix_eval_failure_taxonomy_versions_source_routing",
            "source_case_routing_policy_version_id",
        ),
        Index(
            "ix_eval_failure_taxonomy_versions_source_semantic",
            "source_semantic_clustering_policy_version_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    policy_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_failure_taxonomy_policies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_case_routing_policy_version_id: Mapped[int] = mapped_column(
        ForeignKey(
            "evaluation_case_routing_policy_versions.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    source_semantic_clustering_policy_version_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "evaluation_semantic_clustering_policy_versions.id",
            ondelete="RESTRICT",
        )
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    config_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    min_cluster_occurrences: Mapped[int] = mapped_column(Integer, nullable=False)
    min_source_runs: Mapped[int] = mapped_column(Integer, nullable=False)
    include_isolated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    max_candidates: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    policy: Mapped[EvaluationFailureTaxonomyPolicy] = relationship(back_populates="versions")
    source_case_routing_policy_version: Mapped[EvaluationCaseRoutingPolicyVersion] = relationship()
    source_semantic_clustering_policy_version: Mapped[EvaluationSemanticClusteringPolicyVersion | None] = relationship()
    runs: Mapped[list["EvaluationExperienceExtractionRun"]] = relationship(
        back_populates="policy_version",
        lazy="selectin",
    )


class EvaluationExperienceExtractionRun(Base):
    """Immutable classification of selected Bad Cases into candidates."""

    __tablename__ = "evaluation_experience_extraction_runs"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_eval_experience_runs_public_id"),
        UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_eval_experience_runs_idempotency",
        ),
        UniqueConstraint(
            "policy_version_id",
            "source_case_routing_run_id",
            "evidence_digest",
            name="uq_eval_experience_runs_evidence",
        ),
        CheckConstraint(
            "source_bad_case_count >= 1 AND source_bad_case_count <= 20 "
            "AND cluster_count >= 1 AND cluster_count <= source_bad_case_count "
            "AND eligible_cluster_count >= 0 "
            "AND eligible_cluster_count <= cluster_count "
            "AND candidate_count >= 0 AND candidate_count <= 20 "
            "AND candidate_count <= eligible_cluster_count",
            name="ck_eval_experience_runs_counts",
        ),
        Index(
            "ix_eval_experience_runs_ns_created",
            "namespace_id",
            "created_at",
        ),
        Index(
            "ix_eval_experience_runs_source_routing",
            "source_case_routing_run_id",
        ),
        Index(
            "ix_eval_experience_runs_source_semantic",
            "source_semantic_clustering_run_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    policy_version_id: Mapped[int] = mapped_column(
        ForeignKey(
            "evaluation_failure_taxonomy_policy_versions.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )
    source_case_routing_run_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_case_routing_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_semantic_clustering_run_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "evaluation_semantic_clustering_runs.id",
            ondelete="RESTRICT",
        )
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    extraction_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[EvaluationExperienceExtractionOutcome] = mapped_column(
        Enum(
            EvaluationExperienceExtractionOutcome,
            name="evaluation_experience_extraction_outcome",
        ),
        nullable=False,
        index=True,
    )
    reason_codes_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    source_bad_case_count: Mapped[int] = mapped_column(Integer, nullable=False)
    cluster_count: Mapped[int] = mapped_column(Integer, nullable=False)
    eligible_cluster_count: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    policy_version: Mapped[EvaluationFailureTaxonomyPolicyVersion] = relationship(back_populates="runs")
    source_case_routing_run: Mapped[EvaluationCaseRoutingRun] = relationship()
    source_semantic_clustering_run: Mapped[EvaluationSemanticClusteringRun | None] = relationship()
    candidates: Mapped[list["EvaluationExperienceCandidate"]] = relationship(
        back_populates="run",
        lazy="selectin",
        order_by="EvaluationExperienceCandidate.position",
    )


class EvaluationExperienceCandidate(Base):
    """Content-free failure-cluster candidate awaiting human review."""

    __tablename__ = "evaluation_experience_candidates"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_eval_experience_candidates_public_id"),
        UniqueConstraint(
            "run_id",
            "position",
            name="uq_eval_experience_candidates_position",
        ),
        UniqueConstraint(
            "run_id",
            "cluster_digest",
            name="uq_eval_experience_candidates_cluster",
        ),
        CheckConstraint(
            "position >= 1 AND position <= 20 "
            "AND source_item_count >= 1 AND source_item_count <= 20 "
            "AND source_run_count >= 1 "
            "AND source_run_count <= source_item_count",
            name="ck_eval_experience_candidates_counts",
        ),
        Index("ix_eval_experience_candidates_run_id", "run_id"),
        Index(
            "ix_eval_experience_candidates_status_created",
            "status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_experience_extraction_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[EvaluationFailureCategory] = mapped_column(
        Enum(
            EvaluationFailureCategory,
            name="evaluation_failure_category",
        ),
        nullable=False,
        index=True,
    )
    status: Mapped[EvaluationExperienceCandidateStatus] = mapped_column(
        Enum(
            EvaluationExperienceCandidateStatus,
            name="evaluation_experience_candidate_status",
        ),
        default=EvaluationExperienceCandidateStatus.PENDING_REVIEW,
        nullable=False,
        index=True,
    )
    cluster_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    rank_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    source_item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_run_count: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    run: Mapped[EvaluationExperienceExtractionRun] = relationship(back_populates="candidates")
    evidence_items: Mapped[list["EvaluationExperienceCandidateEvidence"]] = relationship(
        back_populates="candidate",
        lazy="selectin",
        order_by="EvaluationExperienceCandidateEvidence.position",
    )
    review: Mapped["EvaluationExperienceCandidateReview | None"] = relationship(
        back_populates="candidate",
        uselist=False,
        lazy="selectin",
    )


class EvaluationExperienceCandidateEvidence(Base):
    """Exact metadata-only lineage from a candidate to routed Bad Cases."""

    __tablename__ = "evaluation_experience_candidate_evidence"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id",
            "position",
            name="uq_eval_experience_evidence_position",
        ),
        UniqueConstraint(
            "candidate_id",
            "source_case_routing_item_id",
            name="uq_eval_experience_evidence_source",
        ),
        CheckConstraint(
            "position >= 1 AND position <= 20",
            name="ck_eval_experience_evidence_position",
        ),
        Index("ix_eval_experience_evidence_candidate", "candidate_id"),
        Index(
            "ix_eval_experience_evidence_source_routing",
            "source_case_routing_item_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_experience_candidates.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_case_routing_item_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_case_routing_run_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    candidate: Mapped[EvaluationExperienceCandidate] = relationship(back_populates="evidence_items")
    source_case_routing_item: Mapped[EvaluationCaseRoutingRunItem] = relationship()


class EvaluationExperienceCandidateReview(Base):
    """Single immutable human decision for an Experience candidate."""

    __tablename__ = "evaluation_experience_candidate_reviews"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_eval_experience_reviews_public_id"),
        UniqueConstraint("candidate_id", name="uq_eval_experience_reviews_candidate"),
        Index(
            "ix_eval_experience_reviews_reviewer_created",
            "reviewed_by_user_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_experience_candidates.id", ondelete="RESTRICT"),
        nullable=False,
    )
    decision: Mapped[EvaluationExperienceReviewDecision] = mapped_column(
        Enum(
            EvaluationExperienceReviewDecision,
            name="evaluation_experience_review_decision",
        ),
        nullable=False,
        index=True,
    )
    comment: Mapped[str | None] = mapped_column(String(1000))
    review_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewed_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    candidate: Mapped[EvaluationExperienceCandidate] = relationship(back_populates="review")


class EvaluationExperienceAsset(Base):
    """Provider-neutral Experience identity created from one approved candidate."""

    __tablename__ = "evaluation_experience_assets"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_eval_experience_assets_public_id"),
        UniqueConstraint(
            "source_candidate_id",
            name="uq_eval_experience_assets_source_candidate",
        ),
        UniqueConstraint(
            "namespace_id",
            "name",
            name="uq_eval_experience_assets_namespace_name",
        ),
        Index(
            "ix_eval_experience_assets_namespace_created",
            "namespace_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_candidate_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_experience_candidates.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    source_candidate: Mapped[EvaluationExperienceCandidate] = relationship()
    versions: Mapped[list["EvaluationExperienceAssetVersion"]] = relationship(
        back_populates="asset",
        lazy="selectin",
        order_by="EvaluationExperienceAssetVersion.version",
    )


class EvaluationExperienceAssetVersion(Base):
    """Immutable human-authored Experience content with governed status changes."""

    __tablename__ = "evaluation_experience_asset_versions"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_eval_experience_asset_versions_public_id"),
        UniqueConstraint(
            "asset_id",
            "version",
            name="uq_eval_experience_asset_versions_asset_version",
        ),
        UniqueConstraint(
            "asset_id",
            "content_digest",
            name="uq_eval_experience_asset_versions_asset_digest",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_eval_experience_asset_versions_positive",
        ),
        Index(
            "ix_eval_experience_asset_versions_asset_created",
            "asset_id",
            "created_at",
        ),
        Index(
            "ix_eval_experience_asset_versions_status_created",
            "status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_experience_assets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[EvaluationExperienceAssetVersionStatus] = mapped_column(
        Enum(
            EvaluationExperienceAssetVersionStatus,
            name="evaluation_experience_asset_version_status",
        ),
        default=EvaluationExperienceAssetVersionStatus.DRAFT,
        nullable=False,
        index=True,
    )
    body: Mapped[str] = mapped_column(String(4000), nullable=False)
    applicability: Mapped[str] = mapped_column(String(1000), nullable=False)
    change_summary: Mapped[str | None] = mapped_column(String(1000))
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    source_evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    asset: Mapped[EvaluationExperienceAsset] = relationship(back_populates="versions")
    activation_request: Mapped["EvaluationExperienceActivationRequest | None"] = relationship(
        back_populates="version_record", uselist=False, lazy="selectin"
    )


class EvaluationExperienceActivationRequest(Base):
    """Single immutable request to activate an exact Experience version."""

    __tablename__ = "evaluation_experience_activation_requests"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_eval_experience_activation_requests_public_id"),
        UniqueConstraint("version_id", name="uq_eval_experience_activation_requests_version"),
        Index(
            "ix_eval_experience_activation_requests_namespace_created",
            "namespace_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    version_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_experience_asset_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    request_note: Mapped[str | None] = mapped_column(String(1000))
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    version_record: Mapped[EvaluationExperienceAssetVersion] = relationship(back_populates="activation_request")
    review: Mapped["EvaluationExperienceActivationReview | None"] = relationship(
        back_populates="activation_request", uselist=False, lazy="selectin"
    )


class EvaluationExperienceActivationReview(Base):
    """Immutable four-eyes activation decision over an exact version."""

    __tablename__ = "evaluation_experience_activation_reviews"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_eval_experience_activation_reviews_public_id"),
        UniqueConstraint(
            "activation_request_id",
            name="uq_eval_experience_activation_reviews_request",
        ),
        Index(
            "ix_eval_experience_activation_reviews_reviewer_created",
            "reviewed_by_user_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    activation_request_id: Mapped[int] = mapped_column(
        ForeignKey(
            "evaluation_experience_activation_requests.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    decision: Mapped[EvaluationExperienceActivationDecision] = mapped_column(
        Enum(
            EvaluationExperienceActivationDecision,
            name="evaluation_experience_activation_decision",
        ),
        nullable=False,
        index=True,
    )
    comment: Mapped[str | None] = mapped_column(String(1000))
    review_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewed_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    activation_request: Mapped[EvaluationExperienceActivationRequest] = relationship(back_populates="review")


class Evaluator(Base):
    __tablename__ = "evaluators"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_evaluators_public_id"),
        UniqueConstraint(
            "namespace_id",
            "name",
            name="uq_evaluators_namespace_name",
        ),
        Index(
            "ix_evaluators_namespace_status_kind",
            "namespace_id",
            "status",
            "kind",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[EvaluatorKind] = mapped_column(
        Enum(EvaluatorKind, name="evaluator_kind"),
        nullable=False,
    )
    provider: Mapped[EvaluationProvider] = mapped_column(
        Enum(EvaluationProvider, name="evaluation_provider"),
        nullable=False,
    )
    provider_evaluator_ref: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[EvaluatorStatus] = mapped_column(
        Enum(EvaluatorStatus, name="evaluator_status"),
        default=EvaluatorStatus.DRAFT,
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    versions: Mapped[list["EvaluatorVersion"]] = relationship(
        back_populates="evaluator",
        lazy="selectin",
    )


class EvaluatorVersion(Base):
    __tablename__ = "evaluator_versions"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_evaluator_versions_public_id"),
        UniqueConstraint(
            "evaluator_id",
            "version",
            name="uq_evaluator_versions_evaluator_version",
        ),
        UniqueConstraint(
            "evaluator_id",
            "config_digest",
            name="uq_evaluator_versions_evaluator_digest",
        ),
        CheckConstraint("version >= 1", name="ck_evaluator_versions_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False)
    evaluator_id: Mapped[int] = mapped_column(
        ForeignKey("evaluators.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    config_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    implementation_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    rubric_version: Mapped[str | None] = mapped_column(String(64))
    provider_version_ref: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    evaluator: Mapped[Evaluator] = relationship(back_populates="versions")


class Experiment(Base):
    __tablename__ = "evaluation_experiments"
    __table_args__ = (
        UniqueConstraint(
            "public_id",
            name="uq_evaluation_experiments_public_id",
        ),
        UniqueConstraint(
            "namespace_id",
            "name",
            name="uq_evaluation_experiments_namespace_name",
        ),
        Index(
            "ix_evaluation_experiments_namespace_status_created",
            "namespace_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_version_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_dataset_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    target_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[EvaluationProvider] = mapped_column(
        Enum(EvaluationProvider, name="evaluation_provider"),
        nullable=False,
    )
    provider_experiment_ref: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[ExperimentStatus] = mapped_column(
        Enum(ExperimentStatus, name="experiment_status"),
        default=ExperimentStatus.DRAFT,
        nullable=False,
        index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    dataset_version: Mapped[EvaluationDatasetVersion] = relationship()
    evaluations: Mapped[list["Evaluation"]] = relationship(
        back_populates="experiment",
        lazy="selectin",
    )


class Evaluation(Base):
    __tablename__ = "evaluations"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_evaluations_public_id"),
        UniqueConstraint(
            "experiment_id",
            "evaluator_version_id",
            name="uq_evaluations_experiment_evaluator",
        ),
        UniqueConstraint(
            "namespace_id",
            "execution_key",
            name="uq_evaluations_namespace_execution_key",
        ),
        CheckConstraint(
            "total_count >= 0 AND processed_count >= 0 "
            "AND scored_count >= 0 AND passed_count >= 0 "
            "AND failed_count >= 0 AND error_count >= 0 "
            "AND processed_count <= total_count "
            "AND scored_count <= processed_count "
            "AND passed_count + failed_count = scored_count "
            "AND scored_count + error_count = total_count",
            name="ck_evaluations_counts",
        ),
        CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 1)",
            name="ck_evaluations_score_range",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_evaluations_nonnegative_attempts",
        ),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_evaluations_lease_pair",
        ),
        CheckConstraint(
            "("
            "result_completeness IS NULL "
            "AND status NOT IN ('COMPLETED', 'PARTIAL')"
            ") OR ("
            "result_completeness = 'COMPLETE' "
            "AND status = 'COMPLETED' AND error_count = 0"
            ") OR ("
            "result_completeness = 'PARTIAL' "
            "AND status = 'PARTIAL' AND error_count > 0"
            ")",
            name="ck_evaluations_result_state",
        ),
        Index(
            "ix_evaluations_namespace_status_created",
            "namespace_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_evaluations_dispatch",
            "status",
            "available_at",
            "lease_expires_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_experiments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    evaluator_version_id: Mapped[int] = mapped_column(
        ForeignKey("evaluator_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[EvaluationStatus] = mapped_column(
        Enum(EvaluationStatus, name="evaluation_status"),
        default=EvaluationStatus.PENDING,
        nullable=False,
        index=True,
    )
    execution_key: Mapped[str | None] = mapped_column(String(128))
    provider_evaluation_ref: Mapped[str | None] = mapped_column(String(255))
    score: Mapped[float | None] = mapped_column(Float)
    total_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    scored_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    passed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    result_completeness: Mapped[EvaluationResultCompleteness | None] = mapped_column(
        Enum(
            EvaluationResultCompleteness,
            name="evaluation_result_completeness",
        ),
        index=True,
    )
    error_code: Mapped[str | None] = mapped_column(String(100))
    available_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    experiment: Mapped[Experiment] = relationship(back_populates="evaluations")
    evaluator_version: Mapped[EvaluatorVersion] = relationship()
    result_manifests: Mapped[list["EvaluationResultManifest"]] = relationship(
        back_populates="evaluation",
        lazy="selectin",
    )


class EvaluationResultManifest(Base):
    """Immutable metadata index for provider-hosted case results."""

    __tablename__ = "evaluation_result_manifests"
    __table_args__ = (
        UniqueConstraint(
            "public_id",
            name="uq_evaluation_result_manifests_public_id",
        ),
        UniqueConstraint(
            "evaluation_id",
            "version",
            name="uq_evaluation_result_manifests_evaluation_version",
        ),
        UniqueConstraint(
            "provider",
            "provider_experiment_ref",
            name="uq_evaluation_result_manifests_provider_experiment",
        ),
        CheckConstraint(
            "version >= 1 AND expected_count >= 0 "
            "AND processed_count >= 0 AND scored_count >= 0 "
            "AND passed_count >= 0 AND failed_count >= 0 "
            "AND error_count >= 0 "
            "AND processed_count <= expected_count "
            "AND scored_count <= processed_count "
            "AND passed_count + failed_count = scored_count "
            "AND scored_count + error_count = expected_count",
            name="ck_evaluation_result_manifests_counts",
        ),
        CheckConstraint(
            "("
            "completeness = 'COMPLETE' AND error_count = 0 "
            "AND processed_count = expected_count "
            "AND loss_reason IS NULL"
            ") OR ("
            "completeness = 'PARTIAL' AND error_count > 0 "
            "AND loss_reason IS NOT NULL"
            ")",
            name="ck_evaluation_result_manifests_completeness",
        ),
        CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 1)",
            name="ck_evaluation_result_manifests_score_range",
        ),
        Index(
            "ix_evaluation_result_manifests_namespace_created",
            "namespace_id",
            "created_at",
        ),
        Index(
            "ix_evaluation_result_manifests_evaluation_created",
            "evaluation_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    evaluation_id: Mapped[int] = mapped_column(
        ForeignKey("evaluations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[EvaluationProvider] = mapped_column(
        Enum(EvaluationProvider, name="evaluation_provider"),
        nullable=False,
    )
    provider_dataset_ref: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    provider_experiment_ref: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    content_digest: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    score: Mapped[float | None] = mapped_column(Float)
    expected_count: Mapped[int] = mapped_column(Integer, nullable=False)
    processed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    scored_count: Mapped[int] = mapped_column(Integer, nullable=False)
    passed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False)
    completeness: Mapped[EvaluationResultCompleteness] = mapped_column(
        Enum(
            EvaluationResultCompleteness,
            name="evaluation_result_completeness",
        ),
        nullable=False,
        index=True,
    )
    loss_reason: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    evaluation: Mapped[Evaluation] = relationship(
        back_populates="result_manifests",
    )


class RegressionPolicy(Base):
    __tablename__ = "regression_policies"
    __table_args__ = (
        UniqueConstraint(
            "public_id",
            name="uq_regression_policies_public_id",
        ),
        UniqueConstraint(
            "namespace_id",
            "name",
            name="uq_regression_policies_namespace_name",
        ),
        Index(
            "ix_regression_policies_namespace_status_created",
            "namespace_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[RegressionPolicyStatus] = mapped_column(
        Enum(RegressionPolicyStatus, name="regression_policy_status"),
        default=RegressionPolicyStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    versions: Mapped[list["RegressionPolicyVersion"]] = relationship(
        back_populates="policy",
        lazy="selectin",
    )


class RegressionPolicyVersion(Base):
    __tablename__ = "regression_policy_versions"
    __table_args__ = (
        UniqueConstraint(
            "public_id",
            name="uq_regression_policy_versions_public_id",
        ),
        UniqueConstraint(
            "policy_id",
            "version",
            name="uq_regression_policy_versions_policy_version",
        ),
        UniqueConstraint(
            "policy_id",
            "content_digest",
            name="uq_regression_policy_versions_policy_digest",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_regression_policy_versions_positive",
        ),
        CheckConstraint(
            "minimum_candidate_score IS NULL OR (minimum_candidate_score >= 0 AND minimum_candidate_score <= 1)",
            name="ck_regression_policy_versions_min_score",
        ),
        CheckConstraint(
            "maximum_score_drop >= 0 AND maximum_score_drop <= 1 "
            "AND maximum_pass_rate_drop >= 0 "
            "AND maximum_pass_rate_drop <= 1",
            name="ck_regression_policy_versions_drop_ranges",
        ),
        CheckConstraint(
            "require_complete_results = 1",
            name="ck_regression_policy_versions_complete_only",
        ),
        Index(
            "ix_regression_policy_versions_policy_created",
            "policy_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False)
    policy_id: Mapped[int] = mapped_column(
        ForeignKey("regression_policies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_digest: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    minimum_candidate_score: Mapped[float | None] = mapped_column(Float)
    maximum_score_drop: Mapped[float] = mapped_column(Float, nullable=False)
    maximum_pass_rate_drop: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )
    require_complete_results: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    policy: Mapped[RegressionPolicy] = relationship(back_populates="versions")


class EvaluationComparison(Base):
    __tablename__ = "evaluation_comparisons"
    __table_args__ = (
        UniqueConstraint(
            "public_id",
            name="uq_evaluation_comparisons_public_id",
        ),
        UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_evaluation_comparisons_namespace_idempotency",
        ),
        UniqueConstraint(
            "baseline_manifest_id",
            "candidate_manifest_id",
            "policy_version_id",
            name="uq_evaluation_comparisons_exact_pins",
        ),
        CheckConstraint(
            "baseline_evaluation_id <> candidate_evaluation_id",
            name="ck_evaluation_comparisons_distinct_evaluations",
        ),
        CheckConstraint(
            "baseline_manifest_id <> candidate_manifest_id",
            name="ck_evaluation_comparisons_distinct_manifests",
        ),
        CheckConstraint(
            "(baseline_score IS NULL OR "
            "(baseline_score >= 0 AND baseline_score <= 1)) "
            "AND (candidate_score IS NULL OR "
            "(candidate_score >= 0 AND candidate_score <= 1)) "
            "AND (baseline_pass_rate IS NULL OR "
            "(baseline_pass_rate >= 0 AND baseline_pass_rate <= 1)) "
            "AND (candidate_pass_rate IS NULL OR "
            "(candidate_pass_rate >= 0 AND candidate_pass_rate <= 1))",
            name="ck_evaluation_comparisons_metric_ranges",
        ),
        CheckConstraint(
            "(score_delta IS NULL OR "
            "(score_delta >= -1 AND score_delta <= 1)) "
            "AND (pass_rate_delta IS NULL OR "
            "(pass_rate_delta >= -1 AND pass_rate_delta <= 1))",
            name="ck_evaluation_comparisons_delta_ranges",
        ),
        Index(
            "ix_evaluation_comparisons_namespace_outcome_created",
            "namespace_id",
            "outcome",
            "created_at",
        ),
        Index(
            "ix_evaluation_comparisons_candidate_created",
            "candidate_evaluation_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    baseline_evaluation_id: Mapped[int] = mapped_column(
        ForeignKey("evaluations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    candidate_evaluation_id: Mapped[int] = mapped_column(
        ForeignKey("evaluations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    baseline_manifest_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_result_manifests.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    candidate_manifest_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_result_manifests.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    policy_version_id: Mapped[int] = mapped_column(
        ForeignKey("regression_policy_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    outcome: Mapped[EvaluationComparisonOutcome] = mapped_column(
        Enum(
            EvaluationComparisonOutcome,
            name="evaluation_comparison_outcome",
        ),
        nullable=False,
        index=True,
    )
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    baseline_score: Mapped[float | None] = mapped_column(Float)
    candidate_score: Mapped[float | None] = mapped_column(Float)
    score_delta: Mapped[float | None] = mapped_column(Float)
    baseline_pass_rate: Mapped[float | None] = mapped_column(Float)
    candidate_pass_rate: Mapped[float | None] = mapped_column(Float)
    pass_rate_delta: Mapped[float | None] = mapped_column(Float)
    score_floor_breached: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    score_drop_breached: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    pass_rate_drop_breached: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    reproducibility_digest: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
