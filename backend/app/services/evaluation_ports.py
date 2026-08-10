from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, Sequence


@dataclass(frozen=True, slots=True)
class EphemeralEvaluationCase:
    """Content-bearing case that must never be persisted by the core service."""

    input_text: str
    actual_output: str
    expected_output: str | None = None
    context: tuple[str, ...] = ()
    retrieval_context: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MetricSpec:
    name: str
    threshold: float


@dataclass(frozen=True, slots=True)
class MetricSummary:
    name: str
    score: float
    threshold: float
    passed: bool


class EvaluationAdapterPort(Protocol):
    def evaluate(
        self,
        *,
        case: EphemeralEvaluationCase,
        metrics: Sequence[MetricSpec],
    ) -> list[MetricSummary]: ...


@dataclass(frozen=True, slots=True)
class EvaluationRunRequest:
    evaluation_public_id: str
    experiment_public_id: str
    experiment_name: str
    namespace_id: int
    dataset_name: str
    provider_dataset_ref: str
    dataset_version: datetime | None
    expected_item_count: int
    target_type: str
    target_ref: str
    target_digest: str
    evaluator_kind: str
    evaluator_provider: str
    evaluator_implementation_ref: str
    evaluator_config_digest: str


@dataclass(frozen=True, slots=True)
class EvaluationResultManifestSummary:
    provider: str
    provider_dataset_ref: str
    provider_experiment_ref: str
    schema_name: str
    schema_version: str
    content_digest: str
    expected_count: int
    processed_count: int
    scored_count: int
    passed_count: int
    failed_count: int
    error_count: int
    completeness: str
    loss_reason: str | None


@dataclass(frozen=True, slots=True)
class EvaluationRunSummary:
    provider_evaluation_ref: str
    score: float | None
    total_count: int
    processed_count: int
    scored_count: int
    passed_count: int
    failed_count: int
    error_count: int
    completeness: str
    loss_reason: str | None
    result_manifest: EvaluationResultManifestSummary


class EvaluationTargetPort(Protocol):
    """Executes content ephemerally; implementations must never persist it."""

    def execute(
        self,
        *,
        target_type: str,
        target_ref: str,
        target_digest: str,
        item_input: Any,
        item_metadata: dict[str, Any] | None,
    ) -> Any: ...


class EvaluationRunnerPort(Protocol):
    async def run(
        self,
        *,
        request: EvaluationRunRequest,
    ) -> EvaluationRunSummary: ...


@dataclass(frozen=True, slots=True)
class TraceDatasetMaterializationReceipt:
    """Metadata-only receipt for a provider-hosted Trace2Dataset write."""

    provider_dataset_ref: str
    provider_dataset_item_ref: str
    source_trace_ref: str
    source_observation_ref: str
    provider_version_ref: str
    manifest_digest: str
    item_count: int


@dataclass(frozen=True, slots=True)
class TraceDatasetCandidate:
    """Content-free Langfuse Observation metadata shown to curators."""

    source_trace_ref: str
    source_observation_ref: str
    name: str
    observation_type: str
    start_time: datetime
    end_time: datetime | None
    environment: str | None


@dataclass(frozen=True, slots=True)
class TraceDatasetBatchSource:
    """Explicit source reference selected by a curator."""

    source_trace_ref: str
    source_observation_ref: str


@dataclass(frozen=True, slots=True)
class TraceDatasetBatchItemReceipt:
    """Content-free provider receipt for one item in a batch."""

    source_trace_ref: str
    source_observation_ref: str
    provider_dataset_item_ref: str


@dataclass(frozen=True, slots=True)
class TraceDatasetBatchMaterializationReceipt:
    """Metadata-only receipt for one atomic DuckDock Dataset version."""

    provider_dataset_ref: str
    provider_version_ref: str
    manifest_digest: str
    item_count: int
    items: tuple[TraceDatasetBatchItemReceipt, ...]


class TraceDatasetCandidateReaderPort(Protocol):
    """Lists provider metadata without returning observation IO."""

    async def list_candidates(
        self,
        *,
        from_start_time: datetime,
        to_start_time: datetime,
        name: str | None,
        observation_type: str | None,
        environment: str | None,
        root_only: bool,
        limit: int,
    ) -> list[TraceDatasetCandidate]: ...


class TraceDatasetMaterializerPort(Protocol):
    """Copies provider content without returning it to DuckDock core."""

    async def materialize(
        self,
        *,
        dataset_public_id: str,
        provider_dataset_name: str,
        provider_dataset_ref: str,
        trace_ref: str,
        observation_ref: str | None,
    ) -> TraceDatasetMaterializationReceipt: ...

    async def materialize_batch(
        self,
        *,
        dataset_public_id: str,
        provider_dataset_name: str,
        provider_dataset_ref: str,
        sources: Sequence[TraceDatasetBatchSource],
    ) -> TraceDatasetBatchMaterializationReceipt: ...


@dataclass(frozen=True, slots=True)
class AnnotationQueueDescriptor:
    """Content-free provider queue metadata safe for DuckDock governance."""

    provider_queue_ref: str
    name: str
    description: str | None
    score_config_ids: tuple[str, ...]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AnnotationQueueItemSnapshot:
    """Provider receipt/status for a queue item; never contains annotation IO."""

    provider_queue_item_ref: str
    source_observation_ref: str
    status: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class AnnotationQueuePort(Protocol):
    async def list_queues(self, *, limit: int) -> list[AnnotationQueueDescriptor]: ...

    async def get_queue(self, *, queue_ref: str) -> AnnotationQueueDescriptor: ...

    async def ensure_observations(
        self,
        *,
        queue_ref: str,
        observation_refs: Sequence[str],
    ) -> list[AnnotationQueueItemSnapshot]: ...

    async def get_observation_items(
        self,
        *,
        queue_ref: str,
        observation_refs: Sequence[str],
    ) -> list[AnnotationQueueItemSnapshot]: ...


@dataclass(frozen=True, slots=True)
class PromotionEvidenceSource:
    source_trace_ref: str
    source_observation_ref: str


@dataclass(frozen=True, slots=True)
class PromotionQualityRule:
    score_config_id: str
    score_data_type: str
    minimum_numeric_score: float | None
    accepted_values: tuple[str | bool, ...]


@dataclass(frozen=True, slots=True)
class PromotionEvidenceItem:
    """Derived evidence only; raw score values/comments never leave adapter."""

    source_trace_ref: str
    source_observation_ref: str
    score_present: bool
    quality_passed: bool
    diversity_bucket_present: bool
    score_evidence_digest: str | None
    diversity_bucket_digest: str | None


class PromotionEvidencePort(Protocol):
    async def evaluate_promotion_evidence(
        self,
        *,
        queue_ref: str,
        quality_rule: PromotionQualityRule,
        diversity_dimension: str,
        sources: Sequence[PromotionEvidenceSource],
    ) -> list[PromotionEvidenceItem]: ...


@dataclass(frozen=True, slots=True)
class SemanticClusteringSource:
    """Exact provider reference whose content is read only inside an adapter."""

    source_trace_ref: str
    source_observation_ref: str


@dataclass(frozen=True, slots=True)
class SemanticEmbeddingEvidence:
    """Ephemeral vector plus safe digests; the vector must never be persisted."""

    source_trace_ref: str
    source_observation_ref: str
    content_digest: str
    embedding_digest: str
    vector: tuple[float, ...]


class SemanticEmbeddingEvidencePort(Protocol):
    """Reads provider IO and embeds it ephemerally behind one replaceable seam."""

    async def embed_observations(
        self,
        *,
        sources: Sequence[SemanticClusteringSource],
        model_ref: str,
        dimensions: int,
        max_content_chars: int,
    ) -> list[SemanticEmbeddingEvidence]: ...
