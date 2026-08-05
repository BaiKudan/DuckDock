from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, status

from app.core.deps import (
    CurrentUser,
    DB,
    require_namespace_member,
    require_namespace_writer,
)
from app.core.time import ensure_utc
from app.models.evaluation import (
    Evaluation,
    EvaluationAnnotationDispatch,
    EvaluationAnnotationQueueBinding,
    EvaluationCaseRoutingPolicy,
    EvaluationCaseRoutingPolicyVersion,
    EvaluationCaseRoutingRun,
    EvaluationComparison,
    EvaluationDataset,
    EvaluationDatasetCurationBatch,
    EvaluationDatasetMaterialization,
    EvaluationDatasetVersion,
    EvaluationExperienceAsset,
    EvaluationExperienceCandidate,
    EvaluationExperienceExtractionRun,
    EvaluationFailureTaxonomyPolicy,
    EvaluationFailureTaxonomyPolicyVersion,
    EvaluationPromotionPolicy,
    EvaluationPromotionPolicyVersion,
    EvaluationPromotionRun,
    EvaluationSemanticClusteringPolicy,
    EvaluationSemanticClusteringPolicyVersion,
    EvaluationSemanticClusteringRun,
    EvaluationSemanticMonitor,
    EvaluationSemanticMonitorAlert,
    EvaluationSemanticMonitorAlertStatus,
    EvaluationSemanticMonitorRun,
    EvaluationSemanticRegressionComparison,
    EvaluationSemanticRegressionPolicy,
    EvaluationSemanticRegressionPolicyVersion,
    EvaluationResultManifest,
    EvaluationSamplingPolicy,
    EvaluationSamplingRun,
    Evaluator,
    EvaluatorVersion,
    Experiment,
    RegressionPolicy,
    RegressionPolicyVersion,
)
from app.schemas.evaluation import (
    EvaluationAnnotationDispatchCreate,
    EvaluationAnnotationDispatchItemOut,
    EvaluationAnnotationDispatchOut,
    EvaluationAnnotationQueueBindingCreate,
    EvaluationAnnotationQueueBindingOut,
    EvaluationCaseRoutingPolicyCreate,
    EvaluationCaseRoutingPolicyOut,
    EvaluationCaseRoutingPolicyVersionCreate,
    EvaluationCaseRoutingPolicyVersionOut,
    EvaluationCaseRoutingRunCreate,
    EvaluationCaseRoutingRunItemOut,
    EvaluationCaseRoutingRunOut,
    EvaluationComparisonCreate,
    EvaluationComparisonOut,
    EvaluationComparisonPinOut,
    EvaluationComplete,
    EvaluationCreate,
    EvaluationDatasetCreate,
    EvaluationDatasetCurationBatchCreate,
    EvaluationDatasetCurationBatchOut,
    EvaluationDatasetCurationItemOut,
    EvaluationDatasetCurationMaterializationOut,
    EvaluationDatasetCurationReviewCreate,
    EvaluationDatasetCurationReviewOut,
    EvaluationDatasetMaterializationOut,
    EvaluationDatasetOut,
    EvaluationDatasetVersionCreate,
    EvaluationDatasetVersionOut,
    EvaluationExperienceActivationRequestCreate,
    EvaluationExperienceActivationRequestOut,
    EvaluationExperienceActivationReviewCreate,
    EvaluationExperienceActivationReviewOut,
    EvaluationExperienceAssetCreate,
    EvaluationExperienceAssetOut,
    EvaluationExperienceAssetVersionCreate,
    EvaluationExperienceAssetVersionOut,
    EvaluationExperienceCandidateEvidenceOut,
    EvaluationExperienceCandidateOut,
    EvaluationExperienceCandidateReviewCreate,
    EvaluationExperienceCandidateReviewOut,
    EvaluationExperienceExtractionRunCreate,
    EvaluationExperienceExtractionRunOut,
    EvaluationFailureTaxonomyPolicyCreate,
    EvaluationFailureTaxonomyPolicyOut,
    EvaluationFailureTaxonomyPolicyVersionCreate,
    EvaluationFailureTaxonomyPolicyVersionOut,
    EvaluationOut,
    EvaluationResultManifestOut,
    EvaluationSamplingPolicyCreate,
    EvaluationSamplingPolicyOut,
    EvaluationSamplingPolicyVersionCreate,
    EvaluationSamplingPolicyVersionOut,
    EvaluationSamplingRunCreate,
    EvaluationSamplingRunItemOut,
    EvaluationSamplingRunOut,
    EvaluationProviderAnnotationQueueOut,
    EvaluationPromotionPolicyCreate,
    EvaluationPromotionPolicyOut,
    EvaluationPromotionPolicyVersionCreate,
    EvaluationPromotionPolicyVersionOut,
    EvaluationPromotionRunCreate,
    EvaluationPromotionRunItemOut,
    EvaluationPromotionRunOut,
    EvaluationSemanticClusteringPolicyCreate,
    EvaluationSemanticClusteringPolicyOut,
    EvaluationSemanticClusteringPolicyVersionCreate,
    EvaluationSemanticClusteringPolicyVersionOut,
    EvaluationSemanticClusteringRunCreate,
    EvaluationSemanticClusteringRunItemOut,
    EvaluationSemanticClusteringRunOut,
    EvaluationSemanticMonitorAcknowledge,
    EvaluationSemanticMonitorAlertOut,
    EvaluationSemanticMonitorCreate,
    EvaluationSemanticMonitorOut,
    EvaluationSemanticMonitorRunOut,
    EvaluationSemanticRegressionComparisonCreate,
    EvaluationSemanticRegressionComparisonOut,
    EvaluationSemanticRegressionPolicyCreate,
    EvaluationSemanticRegressionPolicyOut,
    EvaluationSemanticRegressionPolicyVersionCreate,
    EvaluationSemanticRegressionPolicyVersionOut,
    EvaluatorCreate,
    EvaluatorOut,
    EvaluatorVersionCreate,
    EvaluatorVersionOut,
    ExperimentCreate,
    ExperimentOut,
    RegressionPolicyCreate,
    RegressionPolicyOut,
    RegressionPolicySnapshotOut,
    RegressionPolicyVersionCreate,
    RegressionPolicyVersionOut,
    TraceDatasetMaterializationCreate,
    TraceDatasetCandidateOut,
)
from app.services.evaluation_curation_service import (
    create_evaluation_dataset_curation_batch,
    evaluation_dataset_curation_status,
    get_evaluation_dataset_curation_batch,
    list_evaluation_dataset_curation_batches,
    list_trace_dataset_candidates,
    materialize_evaluation_dataset_curation_batch,
    review_evaluation_dataset_curation_batch,
)
from app.services.evaluation_annotation_service import (
    create_annotation_dispatch,
    create_annotation_queue_binding,
    get_annotation_dispatch,
    get_annotation_queue_binding,
    list_annotation_dispatches,
    list_annotation_queue_bindings,
    list_provider_annotation_queues,
    reconcile_annotation_dispatch,
    retry_annotation_dispatch,
)
from app.services.evaluation_comparison_service import (
    EvaluationComparisonPin,
    create_evaluation_comparison,
    create_regression_policy,
    create_regression_policy_version,
    get_evaluation_comparison,
    get_regression_policy,
    list_evaluation_comparisons,
    list_regression_policies,
    load_evaluation_comparison_view,
)
from app.services.evaluation_case_routing_service import (
    create_evaluation_case_routing_policy,
    create_evaluation_case_routing_policy_version,
    get_evaluation_case_routing_policy,
    get_evaluation_case_routing_policy_version,
    get_evaluation_case_routing_run,
    list_evaluation_case_routing_policies,
    list_evaluation_case_routing_runs,
    run_evaluation_case_routing_policy,
)
from app.services.evaluation_service import (
    EvaluationHubConflictError,
    EvaluationHubError,
    EvaluationHubNotFoundError,
    EvaluationHubProviderError,
    EvaluationHubStateError,
    complete_evaluation,
    create_evaluation,
    create_evaluation_dataset,
    create_evaluation_dataset_version,
    create_evaluator,
    create_evaluator_version,
    create_experiment,
    get_evaluation,
    get_evaluation_dataset,
    get_evaluator,
    get_experiment,
    list_evaluation_datasets,
    list_evaluation_dataset_materializations,
    list_evaluations,
    list_evaluators,
    list_experiments,
    materialize_evaluation_dataset_trace,
)
from app.services.evaluation_execution_service import (
    get_latest_evaluation_result_manifest,
    list_evaluation_result_manifests,
    request_evaluation_cancel,
    retry_failed_evaluation,
)
from app.services.evaluation_experience_service import (
    create_evaluation_failure_taxonomy_policy,
    create_evaluation_failure_taxonomy_policy_version,
    get_evaluation_experience_candidate,
    get_evaluation_experience_extraction_run,
    get_evaluation_failure_taxonomy_policy,
    get_evaluation_failure_taxonomy_policy_version,
    list_evaluation_experience_extraction_runs,
    list_evaluation_failure_taxonomy_policies,
    review_evaluation_experience_candidate,
    run_evaluation_experience_extraction,
)
from app.services.evaluation_experience_asset_service import (
    create_evaluation_experience_asset,
    create_evaluation_experience_asset_version,
    get_evaluation_experience_activation_request,
    get_evaluation_experience_asset,
    get_evaluation_experience_asset_version,
    list_evaluation_experience_assets,
    request_evaluation_experience_activation,
    review_evaluation_experience_activation,
)
from app.services.evaluation_semantic_clustering_service import (
    create_evaluation_semantic_clustering_policy,
    create_evaluation_semantic_clustering_policy_version,
    get_evaluation_semantic_clustering_policy,
    get_evaluation_semantic_clustering_policy_version,
    get_evaluation_semantic_clustering_run,
    list_evaluation_semantic_clustering_policies,
    list_evaluation_semantic_clustering_runs,
    run_evaluation_semantic_clustering,
)
from app.services.evaluation_semantic_regression_service import (
    create_evaluation_semantic_regression_comparison,
    create_evaluation_semantic_regression_policy,
    create_evaluation_semantic_regression_policy_version,
    get_evaluation_semantic_regression_comparison,
    get_evaluation_semantic_regression_policy,
    list_evaluation_semantic_regression_comparisons,
    list_evaluation_semantic_regression_policies,
)
from app.services.evaluation_semantic_monitor_service import (
    acknowledge_evaluation_semantic_monitor_alert,
    create_evaluation_semantic_monitor,
    get_evaluation_semantic_monitor,
    get_evaluation_semantic_monitor_alert,
    list_evaluation_semantic_monitor_alerts,
    list_evaluation_semantic_monitor_runs,
    list_evaluation_semantic_monitors,
    run_evaluation_semantic_monitor_now,
    set_evaluation_semantic_monitor_paused,
)
from app.services.evaluation_sampling_service import (
    create_evaluation_sampling_policy,
    create_evaluation_sampling_policy_version,
    get_evaluation_sampling_policy,
    get_evaluation_sampling_policy_version,
    get_evaluation_sampling_run,
    list_evaluation_sampling_policies,
    list_evaluation_sampling_runs,
    run_evaluation_sampling_policy,
)
from app.services.evaluation_promotion_service import (
    create_evaluation_promotion_policy,
    create_evaluation_promotion_policy_version,
    get_evaluation_promotion_policy,
    get_evaluation_promotion_policy_version,
    get_evaluation_promotion_run,
    list_evaluation_promotion_policies,
    list_evaluation_promotion_runs,
    run_evaluation_promotion_policy,
)


dataset_router = APIRouter(
    prefix="/evaluation-datasets",
    tags=["evaluation-hub"],
)
dataset_materialization_router = APIRouter(
    prefix="/evaluation-dataset-materializations",
    tags=["evaluation-hub"],
)
dataset_curation_router = APIRouter(
    prefix="/evaluation-dataset-curation-batches",
    tags=["evaluation-hub"],
)
trace_candidate_router = APIRouter(
    prefix="/evaluation-trace-candidates",
    tags=["evaluation-hub"],
)
sampling_policy_router = APIRouter(
    prefix="/evaluation-sampling-policies",
    tags=["evaluation-hub"],
)
sampling_policy_version_router = APIRouter(
    prefix="/evaluation-sampling-policy-versions",
    tags=["evaluation-hub"],
)
sampling_run_router = APIRouter(
    prefix="/evaluation-sampling-runs",
    tags=["evaluation-hub"],
)
annotation_queue_binding_router = APIRouter(
    prefix="/evaluation-annotation-queue-bindings",
    tags=["evaluation-hub"],
)
annotation_dispatch_router = APIRouter(
    prefix="/evaluation-annotation-dispatches",
    tags=["evaluation-hub"],
)
promotion_policy_router = APIRouter(
    prefix="/evaluation-promotion-policies",
    tags=["evaluation-hub"],
)
promotion_policy_version_router = APIRouter(
    prefix="/evaluation-promotion-policy-versions",
    tags=["evaluation-hub"],
)
promotion_run_router = APIRouter(
    prefix="/evaluation-promotion-runs",
    tags=["evaluation-hub"],
)
case_routing_policy_router = APIRouter(
    prefix="/evaluation-case-routing-policies",
    tags=["evaluation-hub"],
)
case_routing_policy_version_router = APIRouter(
    prefix="/evaluation-case-routing-policy-versions",
    tags=["evaluation-hub"],
)
case_routing_run_router = APIRouter(
    prefix="/evaluation-case-routing-runs",
    tags=["evaluation-hub"],
)
semantic_clustering_policy_router = APIRouter(
    prefix="/evaluation-semantic-clustering-policies",
    tags=["evaluation-hub"],
)
semantic_clustering_policy_version_router = APIRouter(
    prefix="/evaluation-semantic-clustering-policy-versions",
    tags=["evaluation-hub"],
)
semantic_clustering_run_router = APIRouter(
    prefix="/evaluation-semantic-clustering-runs",
    tags=["evaluation-hub"],
)
semantic_regression_policy_router = APIRouter(
    prefix="/evaluation-semantic-regression-policies",
    tags=["evaluation-hub"],
)
semantic_regression_comparison_router = APIRouter(
    prefix="/evaluation-semantic-regression-comparisons",
    tags=["evaluation-hub"],
)
semantic_monitor_router = APIRouter(
    prefix="/evaluation-semantic-monitors",
    tags=["evaluation-hub"],
)
semantic_monitor_run_router = APIRouter(
    prefix="/evaluation-semantic-monitor-runs",
    tags=["evaluation-hub"],
)
semantic_monitor_alert_router = APIRouter(
    prefix="/evaluation-semantic-monitor-alerts",
    tags=["evaluation-hub"],
)
failure_taxonomy_policy_router = APIRouter(
    prefix="/evaluation-failure-taxonomy-policies",
    tags=["evaluation-hub"],
)
failure_taxonomy_policy_version_router = APIRouter(
    prefix="/evaluation-failure-taxonomy-policy-versions",
    tags=["evaluation-hub"],
)
experience_extraction_run_router = APIRouter(
    prefix="/evaluation-experience-extraction-runs",
    tags=["evaluation-hub"],
)
experience_candidate_router = APIRouter(
    prefix="/evaluation-experience-candidates",
    tags=["evaluation-hub"],
)
experience_asset_router = APIRouter(
    prefix="/evaluation-experience-assets",
    tags=["evaluation-hub"],
)
experience_asset_version_router = APIRouter(
    prefix="/evaluation-experience-asset-versions",
    tags=["evaluation-hub"],
)
experience_activation_router = APIRouter(
    prefix="/evaluation-experience-activation-requests",
    tags=["evaluation-hub"],
)
evaluator_router = APIRouter(prefix="/evaluators", tags=["evaluation-hub"])
experiment_router = APIRouter(
    prefix="/evaluation-experiments",
    tags=["evaluation-hub"],
)
evaluation_router = APIRouter(prefix="/evaluations", tags=["evaluation-hub"])
regression_policy_router = APIRouter(
    prefix="/regression-policies",
    tags=["evaluation-hub"],
)
evaluation_comparison_router = APIRouter(
    prefix="/evaluation-comparisons",
    tags=["evaluation-hub"],
)
EvaluationIdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
]
ComparisonIdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
]
MaterializationIdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
]


def _http_error(exc: EvaluationHubError) -> HTTPException:
    if isinstance(exc, EvaluationHubNotFoundError):
        return HTTPException(status_code=404, detail="Evaluation resource not found")
    if isinstance(exc, (EvaluationHubConflictError, EvaluationHubStateError)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, EvaluationHubProviderError):
        return HTTPException(
            status_code=503,
            detail="Evaluation provider is unavailable",
        )
    return HTTPException(status_code=422, detail="Evaluation write rejected")


def _dataset_out(
    dataset: EvaluationDataset,
    *,
    versions: list[EvaluationDatasetVersion] | None = None,
) -> EvaluationDatasetOut:
    ordered_versions = sorted(
        dataset.versions if versions is None else versions,
        key=lambda value: value.version,
    )
    return EvaluationDatasetOut(
        public_id=dataset.public_id,
        namespace_id=dataset.namespace_id,
        name=dataset.name,
        description=dataset.description,
        provider=dataset.provider,
        provider_dataset_ref=dataset.provider_dataset_ref,
        status=dataset.status,
        versions=[EvaluationDatasetVersionOut.model_validate(version) for version in ordered_versions],
        created_at=dataset.created_at,
        updated_at=dataset.updated_at,
    )


def _dataset_materialization_out(
    value: EvaluationDatasetMaterialization,
) -> EvaluationDatasetMaterializationOut:
    return EvaluationDatasetMaterializationOut(
        public_id=value.public_id,
        namespace_id=value.namespace_id,
        dataset_public_id=value.dataset.public_id,
        dataset_version_public_id=value.dataset_version.public_id,
        dataset_version=value.dataset_version.version,
        source_type=value.source_type,
        source_trace_ref=value.source_trace_ref,
        source_observation_ref=value.source_observation_ref,
        provider_dataset_item_ref=value.provider_dataset_item_ref,
        provider_version_ref=value.dataset_version.provider_version_ref or "",
        manifest_digest=value.dataset_version.content_digest,
        item_count=value.dataset_version.item_count,
        schema_name=value.schema_name,
        schema_version=value.schema_version,
        created_by_user_id=value.created_by_user_id,
        created_at=value.created_at,
    )


def _dataset_curation_batch_out(
    batch: EvaluationDatasetCurationBatch,
) -> EvaluationDatasetCurationBatchOut:
    materialization = batch.materialization
    provider_refs = (
        {item.curation_item_id: item.provider_dataset_item_ref for item in materialization.items}
        if materialization is not None
        else {}
    )
    review = batch.review
    return EvaluationDatasetCurationBatchOut(
        public_id=batch.public_id,
        namespace_id=batch.namespace_id,
        dataset_public_id=batch.dataset.public_id,
        status=evaluation_dataset_curation_status(batch),
        selection_digest=batch.selection_digest,
        item_count=batch.item_count,
        schema_name=batch.schema_name,
        schema_version=batch.schema_version,
        submitted_by_user_id=batch.submitted_by_user_id,
        created_at=batch.created_at,
        items=[
            EvaluationDatasetCurationItemOut(
                position=item.position,
                source_trace_ref=item.source_trace_ref,
                source_observation_ref=item.source_observation_ref,
                provider_dataset_item_ref=provider_refs.get(item.id),
            )
            for item in sorted(batch.items, key=lambda value: value.position)
        ],
        review=(EvaluationDatasetCurationReviewOut.model_validate(review) if review is not None else None),
        materialization=(
            EvaluationDatasetCurationMaterializationOut(
                public_id=materialization.public_id,
                dataset_version_public_id=(materialization.dataset_version.public_id),
                dataset_version=materialization.dataset_version.version,
                provider_version_ref=(materialization.dataset_version.provider_version_ref or ""),
                manifest_digest=(materialization.dataset_version.content_digest),
                selected_item_count=materialization.item_count,
                dataset_item_count=materialization.dataset_version.item_count,
                created_by_user_id=materialization.created_by_user_id,
                created_at=materialization.created_at,
            )
            if materialization is not None
            else None
        ),
    )


def _sampling_policy_out(
    policy: EvaluationSamplingPolicy,
) -> EvaluationSamplingPolicyOut:
    return EvaluationSamplingPolicyOut(
        public_id=policy.public_id,
        namespace_id=policy.namespace_id,
        name=policy.name,
        description=policy.description,
        status=policy.status,
        versions=[
            EvaluationSamplingPolicyVersionOut.model_validate(version)
            for version in sorted(policy.versions, key=lambda value: value.version)
        ],
        created_at=policy.created_at,
        updated_at=policy.updated_at,
    )


def _sampling_run_out(run: EvaluationSamplingRun) -> EvaluationSamplingRunOut:
    version = run.policy_version
    return EvaluationSamplingRunOut(
        public_id=run.public_id,
        namespace_id=run.namespace_id,
        policy_public_id=version.policy.public_id,
        policy_version_public_id=version.public_id,
        policy_version=version.version,
        dataset_public_id=run.dataset.public_id,
        curation_batch_public_id=run.curation_batch.public_id,
        from_start_time=ensure_utc(run.from_start_time),
        to_start_time=ensure_utc(run.to_start_time),
        candidate_count=run.candidate_count,
        eligible_count=run.eligible_count,
        selected_count=run.selected_count,
        selection_digest=run.selection_digest,
        run_digest=run.run_digest,
        schema_name=run.schema_name,
        schema_version=run.schema_version,
        created_by_user_id=run.created_by_user_id,
        created_at=ensure_utc(run.created_at),
        items=[
            EvaluationSamplingRunItemOut.model_validate(item)
            for item in sorted(run.items, key=lambda value: value.position)
        ],
    )


def _annotation_queue_binding_out(
    binding: EvaluationAnnotationQueueBinding,
) -> EvaluationAnnotationQueueBindingOut:
    return EvaluationAnnotationQueueBindingOut(
        public_id=binding.public_id,
        namespace_id=binding.namespace_id,
        provider=binding.provider,
        provider_queue_ref=binding.provider_queue_ref,
        provider_queue_name=binding.provider_queue_name,
        score_config_ids=list(binding.score_config_ids_json),
        provider_updated_at=ensure_utc(binding.provider_updated_at),
        status=binding.status,
        schema_name=binding.schema_name,
        schema_version=binding.schema_version,
        created_by_user_id=binding.created_by_user_id,
        created_at=ensure_utc(binding.created_at),
        updated_at=ensure_utc(binding.updated_at),
    )


def _annotation_dispatch_out(
    dispatch: EvaluationAnnotationDispatch,
) -> EvaluationAnnotationDispatchOut:
    return EvaluationAnnotationDispatchOut(
        public_id=dispatch.public_id,
        namespace_id=dispatch.namespace_id,
        binding_public_id=dispatch.binding.public_id,
        provider_queue_ref=dispatch.provider_queue_ref,
        provider_queue_name=dispatch.binding.provider_queue_name,
        curation_batch_public_id=dispatch.curation_batch.public_id,
        sampling_run_public_id=(dispatch.sampling_run.public_id if dispatch.sampling_run is not None else None),
        request_digest=dispatch.request_digest,
        status=dispatch.status,
        item_count=dispatch.item_count,
        synced_count=dispatch.synced_count,
        completed_count=dispatch.completed_count,
        failed_count=dispatch.failed_count,
        attempt_count=dispatch.attempt_count,
        error_code=dispatch.error_code,
        schema_name=dispatch.schema_name,
        schema_version=dispatch.schema_version,
        created_by_user_id=dispatch.created_by_user_id,
        started_at=(ensure_utc(dispatch.started_at) if dispatch.started_at is not None else None),
        synced_at=(ensure_utc(dispatch.synced_at) if dispatch.synced_at is not None else None),
        last_reconciled_at=(
            ensure_utc(dispatch.last_reconciled_at) if dispatch.last_reconciled_at is not None else None
        ),
        created_at=ensure_utc(dispatch.created_at),
        updated_at=ensure_utc(dispatch.updated_at),
        items=[
            EvaluationAnnotationDispatchItemOut.model_validate(item)
            for item in sorted(dispatch.items, key=lambda value: value.position)
        ],
    )


def _promotion_policy_version_out(
    version: EvaluationPromotionPolicyVersion,
) -> EvaluationPromotionPolicyVersionOut:
    return EvaluationPromotionPolicyVersionOut(
        public_id=version.public_id,
        version=version.version,
        binding_public_id=version.binding.public_id,
        provider_queue_ref=version.binding.provider_queue_ref,
        config_digest=version.config_digest,
        score_config_id=version.score_config_id,
        score_data_type=version.score_data_type,
        minimum_numeric_score=version.minimum_numeric_score,
        accepted_values=list(version.accepted_values_json or []),
        diversity_dimension=version.diversity_dimension,
        min_distinct_buckets=version.min_distinct_buckets,
        schema_name=version.schema_name,
        schema_version=version.schema_version,
        created_by_user_id=version.created_by_user_id,
        created_at=ensure_utc(version.created_at),
    )


def _promotion_policy_out(
    policy: EvaluationPromotionPolicy,
) -> EvaluationPromotionPolicyOut:
    return EvaluationPromotionPolicyOut(
        public_id=policy.public_id,
        namespace_id=policy.namespace_id,
        name=policy.name,
        description=policy.description,
        status=policy.status,
        versions=[
            _promotion_policy_version_out(version)
            for version in sorted(policy.versions, key=lambda value: value.version)
        ],
        created_at=ensure_utc(policy.created_at),
        updated_at=ensure_utc(policy.updated_at),
    )


def _promotion_run_out(run: EvaluationPromotionRun) -> EvaluationPromotionRunOut:
    version = run.policy_version
    return EvaluationPromotionRunOut(
        public_id=run.public_id,
        namespace_id=run.namespace_id,
        policy_public_id=version.policy.public_id,
        policy_version_public_id=version.public_id,
        policy_version=version.version,
        binding_public_id=version.binding.public_id,
        dispatch_public_id=run.dispatch.public_id,
        curation_batch_public_id=run.dispatch.curation_batch.public_id,
        request_digest=run.request_digest,
        evidence_digest=run.evidence_digest,
        outcome=run.outcome,
        reason_codes=list(run.reason_codes_json),
        item_count=run.item_count,
        completed_count=run.completed_count,
        scored_count=run.scored_count,
        passed_count=run.passed_count,
        distinct_bucket_count=run.distinct_bucket_count,
        schema_name=run.schema_name,
        schema_version=run.schema_version,
        created_by_user_id=run.created_by_user_id,
        created_at=ensure_utc(run.created_at),
        items=[
            EvaluationPromotionRunItemOut.model_validate(item)
            for item in sorted(run.items, key=lambda value: value.position)
        ],
    )


def _case_routing_policy_version_out(
    version: EvaluationCaseRoutingPolicyVersion,
) -> EvaluationCaseRoutingPolicyVersionOut:
    source_version = version.source_promotion_policy_version
    return EvaluationCaseRoutingPolicyVersionOut(
        public_id=version.public_id,
        version=version.version,
        source_promotion_policy_public_id=source_version.policy.public_id,
        source_promotion_policy_version_public_id=source_version.public_id,
        source_promotion_policy_version=source_version.version,
        config_digest=version.config_digest,
        strategy=version.strategy,
        golden_target_size=version.golden_target_size,
        golden_min_items=version.golden_min_items,
        bad_case_target_size=version.bad_case_target_size,
        bad_case_min_items=version.bad_case_min_items,
        schema_name=version.schema_name,
        schema_version=version.schema_version,
        created_by_user_id=version.created_by_user_id,
        created_at=ensure_utc(version.created_at),
    )


def _case_routing_policy_out(
    policy: EvaluationCaseRoutingPolicy,
) -> EvaluationCaseRoutingPolicyOut:
    return EvaluationCaseRoutingPolicyOut(
        public_id=policy.public_id,
        namespace_id=policy.namespace_id,
        name=policy.name,
        description=policy.description,
        status=policy.status,
        versions=[
            _case_routing_policy_version_out(version)
            for version in sorted(policy.versions, key=lambda value: value.version)
        ],
        created_at=ensure_utc(policy.created_at),
        updated_at=ensure_utc(policy.updated_at),
    )


def _case_routing_run_out(
    run: EvaluationCaseRoutingRun,
) -> EvaluationCaseRoutingRunOut:
    version = run.policy_version
    return EvaluationCaseRoutingRunOut(
        public_id=run.public_id,
        namespace_id=run.namespace_id,
        policy_public_id=version.policy.public_id,
        policy_version_public_id=version.public_id,
        policy_version=version.version,
        source_promotion_policy_version_public_id=(version.source_promotion_policy_version.public_id),
        golden_dataset_public_id=run.golden_dataset.public_id,
        bad_case_dataset_public_id=run.bad_case_dataset.public_id,
        golden_curation_batch_public_id=(
            run.golden_curation_batch.public_id if run.golden_curation_batch is not None else None
        ),
        bad_case_curation_batch_public_id=(
            run.bad_case_curation_batch.public_id if run.bad_case_curation_batch is not None else None
        ),
        request_digest=run.request_digest,
        evidence_digest=run.evidence_digest,
        routing_digest=run.routing_digest,
        outcome=run.outcome,
        reason_codes=list(run.reason_codes_json),
        source_run_count=run.source_run_count,
        candidate_count=run.candidate_count,
        golden_candidate_count=run.golden_candidate_count,
        bad_case_candidate_count=run.bad_case_candidate_count,
        excluded_count=run.excluded_count,
        golden_selected_count=run.golden_selected_count,
        bad_case_selected_count=run.bad_case_selected_count,
        golden_cluster_count=run.golden_cluster_count,
        bad_case_cluster_count=run.bad_case_cluster_count,
        schema_name=run.schema_name,
        schema_version=run.schema_version,
        created_by_user_id=run.created_by_user_id,
        created_at=ensure_utc(run.created_at),
        items=[
            EvaluationCaseRoutingRunItemOut(
                position=item.position,
                source_promotion_run_public_id=(item.source_promotion_run.public_id),
                source_trace_ref=item.source_trace_ref,
                source_observation_ref=item.source_observation_ref,
                lane=item.lane,
                selected=item.selected,
                cluster_digest=item.cluster_digest,
                rank_digest=item.rank_digest,
                reason_code=item.reason_code,
            )
            for item in sorted(run.items, key=lambda value: value.position)
        ],
    )


def _semantic_clustering_policy_version_out(
    version: EvaluationSemanticClusteringPolicyVersion,
) -> EvaluationSemanticClusteringPolicyVersionOut:
    source_version = version.source_case_routing_policy_version
    return EvaluationSemanticClusteringPolicyVersionOut(
        public_id=version.public_id,
        version=version.version,
        source_case_routing_policy_public_id=source_version.policy.public_id,
        source_case_routing_policy_version_public_id=source_version.public_id,
        source_case_routing_policy_version=source_version.version,
        config_digest=version.config_digest,
        embedding_profile=version.embedding_profile,
        model_ref=version.model_ref,
        dimensions=version.dimensions,
        similarity_threshold=version.similarity_threshold,
        min_cluster_size=version.min_cluster_size,
        max_items=version.max_items,
        max_content_chars=version.max_content_chars,
        schema_name=version.schema_name,
        schema_version=version.schema_version,
        created_by_user_id=version.created_by_user_id,
        created_at=ensure_utc(version.created_at),
    )


def _semantic_clustering_policy_out(
    policy: EvaluationSemanticClusteringPolicy,
) -> EvaluationSemanticClusteringPolicyOut:
    return EvaluationSemanticClusteringPolicyOut(
        public_id=policy.public_id,
        namespace_id=policy.namespace_id,
        name=policy.name,
        description=policy.description,
        status=policy.status,
        versions=[
            _semantic_clustering_policy_version_out(version)
            for version in sorted(policy.versions, key=lambda value: value.version)
        ],
        created_at=ensure_utc(policy.created_at),
        updated_at=ensure_utc(policy.updated_at),
    )


def _semantic_clustering_run_out(
    run: EvaluationSemanticClusteringRun,
) -> EvaluationSemanticClusteringRunOut:
    version = run.policy_version
    return EvaluationSemanticClusteringRunOut(
        public_id=run.public_id,
        namespace_id=run.namespace_id,
        policy_public_id=version.policy.public_id,
        policy_version_public_id=version.public_id,
        policy_version=version.version,
        source_case_routing_run_public_id=run.source_case_routing_run.public_id,
        request_digest=run.request_digest,
        evidence_digest=run.evidence_digest,
        clustering_digest=run.clustering_digest,
        outcome=run.outcome,
        reason_codes=list(run.reason_codes_json),
        source_item_count=run.source_item_count,
        cluster_count=run.cluster_count,
        eligible_cluster_count=run.eligible_cluster_count,
        schema_name=run.schema_name,
        schema_version=run.schema_version,
        created_by_user_id=run.created_by_user_id,
        created_at=ensure_utc(run.created_at),
        items=[
            EvaluationSemanticClusteringRunItemOut(
                position=item.position,
                source_case_routing_item_position=(item.source_case_routing_item.position),
                source_trace_ref=item.source_case_routing_item.source_trace_ref,
                source_observation_ref=(item.source_case_routing_item.source_observation_ref),
                semantic_cluster_digest=item.semantic_cluster_digest,
                cluster_size=item.cluster_size,
                similarity_to_centroid=item.similarity_to_centroid,
                content_digest=item.content_digest,
                embedding_digest=item.embedding_digest,
            )
            for item in sorted(run.items, key=lambda value: value.position)
        ],
    )


def _semantic_regression_policy_version_out(
    version: EvaluationSemanticRegressionPolicyVersion,
) -> EvaluationSemanticRegressionPolicyVersionOut:
    return EvaluationSemanticRegressionPolicyVersionOut(
        public_id=version.public_id,
        version=version.version,
        config_digest=version.config_digest,
        minimum_pairwise_assignment_agreement=(version.minimum_pairwise_assignment_agreement),
        maximum_cluster_count_change_ratio=(version.maximum_cluster_count_change_ratio),
        maximum_mean_centroid_similarity_drop=(version.maximum_mean_centroid_similarity_drop),
        maximum_eligible_cluster_ratio_drop=(version.maximum_eligible_cluster_ratio_drop),
        require_exact_source_content=version.require_exact_source_content,
        schema_name=version.schema_name,
        schema_version=version.schema_version,
        created_by_user_id=version.created_by_user_id,
        created_at=ensure_utc(version.created_at),
    )


def _semantic_regression_policy_out(
    policy: EvaluationSemanticRegressionPolicy,
) -> EvaluationSemanticRegressionPolicyOut:
    return EvaluationSemanticRegressionPolicyOut(
        public_id=policy.public_id,
        namespace_id=policy.namespace_id,
        name=policy.name,
        description=policy.description,
        status=policy.status,
        versions=[
            _semantic_regression_policy_version_out(version)
            for version in sorted(policy.versions, key=lambda value: value.version)
        ],
        created_at=ensure_utc(policy.created_at),
        updated_at=ensure_utc(policy.updated_at),
    )


def _semantic_regression_comparison_out(
    comparison: EvaluationSemanticRegressionComparison,
) -> EvaluationSemanticRegressionComparisonOut:
    version = comparison.policy_version
    return EvaluationSemanticRegressionComparisonOut(
        public_id=comparison.public_id,
        namespace_id=comparison.namespace_id,
        baseline_run_public_id=comparison.baseline_run.public_id,
        baseline_policy_version_public_id=(comparison.baseline_run.policy_version.public_id),
        candidate_run_public_id=comparison.candidate_run.public_id,
        candidate_policy_version_public_id=(comparison.candidate_run.policy_version.public_id),
        policy_public_id=version.policy.public_id,
        policy_name=version.policy.name,
        policy_version_public_id=version.public_id,
        policy_version=version.version,
        outcome=comparison.outcome,
        reason_codes=list(comparison.reason_codes_json),
        source_item_count=comparison.source_item_count,
        pairwise_assignment_agreement=(comparison.pairwise_assignment_agreement),
        baseline_cluster_count=comparison.baseline_cluster_count,
        candidate_cluster_count=comparison.candidate_cluster_count,
        cluster_count_change_ratio=comparison.cluster_count_change_ratio,
        baseline_eligible_cluster_ratio=(comparison.baseline_eligible_cluster_ratio),
        candidate_eligible_cluster_ratio=(comparison.candidate_eligible_cluster_ratio),
        eligible_cluster_ratio_drop=comparison.eligible_cluster_ratio_drop,
        baseline_mean_centroid_similarity=(comparison.baseline_mean_centroid_similarity),
        candidate_mean_centroid_similarity=(comparison.candidate_mean_centroid_similarity),
        mean_centroid_similarity_drop=(comparison.mean_centroid_similarity_drop),
        assignment_agreement_breached=(comparison.assignment_agreement_breached),
        cluster_count_change_breached=(comparison.cluster_count_change_breached),
        eligible_cluster_ratio_drop_breached=(comparison.eligible_cluster_ratio_drop_breached),
        centroid_similarity_drop_breached=(comparison.centroid_similarity_drop_breached),
        reproducibility_digest=comparison.reproducibility_digest,
        schema_name=comparison.schema_name,
        schema_version=comparison.schema_version,
        created_by_user_id=comparison.created_by_user_id,
        created_at=ensure_utc(comparison.created_at),
    )


def _semantic_monitor_out(
    monitor: EvaluationSemanticMonitor,
) -> EvaluationSemanticMonitorOut:
    return EvaluationSemanticMonitorOut(
        public_id=monitor.public_id,
        namespace_id=monitor.namespace_id,
        name=monitor.name,
        description=monitor.description,
        baseline_run_public_id=monitor.baseline_run.public_id,
        candidate_policy_version_public_id=monitor.candidate_policy_version.public_id,
        regression_policy_version_public_id=monitor.regression_policy_version.public_id,
        interval_seconds=monitor.interval_seconds,
        config_digest=monitor.config_digest,
        status=monitor.status,
        next_run_at=ensure_utc(monitor.next_run_at),
        created_by_user_id=monitor.created_by_user_id,
        created_at=ensure_utc(monitor.created_at),
        updated_at=ensure_utc(monitor.updated_at),
    )


def _semantic_monitor_run_out(
    run: EvaluationSemanticMonitorRun,
) -> EvaluationSemanticMonitorRunOut:
    return EvaluationSemanticMonitorRunOut(
        public_id=run.public_id,
        namespace_id=run.namespace_id,
        monitor_public_id=run.monitor.public_id,
        scheduled_for=ensure_utc(run.scheduled_for),
        status=run.status,
        attempt_count=run.attempt_count,
        candidate_run_public_id=(
            run.candidate_run.public_id if run.candidate_run is not None else None
        ),
        comparison_public_id=(
            run.comparison.public_id if run.comparison is not None else None
        ),
        alert_public_id=run.alert.public_id if run.alert is not None else None,
        outcome=run.outcome,
        error_code=run.error_code,
        started_at=ensure_utc(run.started_at) if run.started_at is not None else None,
        finished_at=ensure_utc(run.finished_at) if run.finished_at is not None else None,
        created_at=ensure_utc(run.created_at),
        updated_at=ensure_utc(run.updated_at),
    )


def _semantic_monitor_alert_out(
    alert: EvaluationSemanticMonitorAlert,
) -> EvaluationSemanticMonitorAlertOut:
    return EvaluationSemanticMonitorAlertOut(
        public_id=alert.public_id,
        namespace_id=alert.namespace_id,
        monitor_public_id=alert.monitor.public_id,
        monitor_run_public_id=alert.monitor_run.public_id,
        comparison_public_id=(
            alert.comparison.public_id if alert.comparison is not None else None
        ),
        severity=alert.severity,
        status=alert.status,
        reason_codes=list(alert.reason_codes_json),
        error_code=alert.error_code,
        acknowledged_by_user_id=alert.acknowledged_by_user_id,
        acknowledged_at=(
            ensure_utc(alert.acknowledged_at)
            if alert.acknowledged_at is not None
            else None
        ),
        acknowledgement_note=alert.acknowledgement_note,
        created_at=ensure_utc(alert.created_at),
        updated_at=ensure_utc(alert.updated_at),
    )


def _failure_taxonomy_policy_version_out(
    version: EvaluationFailureTaxonomyPolicyVersion,
) -> EvaluationFailureTaxonomyPolicyVersionOut:
    source_version = version.source_case_routing_policy_version
    semantic_version = version.source_semantic_clustering_policy_version
    return EvaluationFailureTaxonomyPolicyVersionOut(
        public_id=version.public_id,
        version=version.version,
        source_case_routing_policy_public_id=source_version.policy.public_id,
        source_case_routing_policy_version_public_id=source_version.public_id,
        source_case_routing_policy_version=source_version.version,
        source_semantic_clustering_policy_public_id=(
            semantic_version.policy.public_id if semantic_version is not None else None
        ),
        source_semantic_clustering_policy_version_public_id=(
            semantic_version.public_id if semantic_version is not None else None
        ),
        source_semantic_clustering_policy_version=(semantic_version.version if semantic_version is not None else None),
        config_digest=version.config_digest,
        min_cluster_occurrences=version.min_cluster_occurrences,
        min_source_runs=version.min_source_runs,
        include_isolated=version.include_isolated,
        max_candidates=version.max_candidates,
        schema_name=version.schema_name,
        schema_version=version.schema_version,
        created_by_user_id=version.created_by_user_id,
        created_at=ensure_utc(version.created_at),
    )


def _failure_taxonomy_policy_out(
    policy: EvaluationFailureTaxonomyPolicy,
) -> EvaluationFailureTaxonomyPolicyOut:
    return EvaluationFailureTaxonomyPolicyOut(
        public_id=policy.public_id,
        namespace_id=policy.namespace_id,
        name=policy.name,
        description=policy.description,
        status=policy.status,
        versions=[
            _failure_taxonomy_policy_version_out(version)
            for version in sorted(policy.versions, key=lambda value: value.version)
        ],
        created_at=ensure_utc(policy.created_at),
        updated_at=ensure_utc(policy.updated_at),
    )


def _experience_candidate_out(
    candidate: EvaluationExperienceCandidate,
) -> EvaluationExperienceCandidateOut:
    review = candidate.review
    return EvaluationExperienceCandidateOut(
        public_id=candidate.public_id,
        position=candidate.position,
        category=candidate.category,
        status=candidate.status,
        cluster_digest=candidate.cluster_digest,
        rank_digest=candidate.rank_digest,
        evidence_digest=candidate.evidence_digest,
        source_item_count=candidate.source_item_count,
        source_run_count=candidate.source_run_count,
        reason_code=candidate.reason_code,
        created_at=ensure_utc(candidate.created_at),
        evidence_items=[
            EvaluationExperienceCandidateEvidenceOut(
                position=evidence.position,
                source_case_routing_item_position=(evidence.source_case_routing_item.position),
                source_promotion_run_public_id=(evidence.source_case_routing_item.source_promotion_run.public_id),
                source_trace_ref=(evidence.source_case_routing_item.source_trace_ref),
                source_observation_ref=(evidence.source_case_routing_item.source_observation_ref),
            )
            for evidence in sorted(candidate.evidence_items, key=lambda value: value.position)
        ],
        review=(
            EvaluationExperienceCandidateReviewOut(
                public_id=review.public_id,
                decision=review.decision,
                comment=review.comment,
                review_digest=review.review_digest,
                reviewed_by_user_id=review.reviewed_by_user_id,
                created_at=ensure_utc(review.created_at),
            )
            if review is not None
            else None
        ),
    )


def _experience_asset_out(
    asset: EvaluationExperienceAsset,
) -> EvaluationExperienceAssetOut:
    versions = []
    for version in sorted(asset.versions, key=lambda value: value.version):
        activation_request = version.activation_request
        review = activation_request.review if activation_request is not None else None
        versions.append(
            EvaluationExperienceAssetVersionOut(
                public_id=version.public_id,
                version=version.version,
                status=version.status,
                body=version.body,
                applicability=version.applicability,
                change_summary=version.change_summary,
                content_digest=version.content_digest,
                source_evidence_digest=version.source_evidence_digest,
                schema_name=version.schema_name,
                schema_version=version.schema_version,
                created_by_user_id=version.created_by_user_id,
                created_at=ensure_utc(version.created_at),
                activation_request=(
                    EvaluationExperienceActivationRequestOut(
                        public_id=activation_request.public_id,
                        request_note=activation_request.request_note,
                        request_digest=activation_request.request_digest,
                        requested_by_user_id=(activation_request.requested_by_user_id),
                        created_at=ensure_utc(activation_request.created_at),
                        review=(
                            EvaluationExperienceActivationReviewOut(
                                public_id=review.public_id,
                                decision=review.decision,
                                comment=review.comment,
                                review_digest=review.review_digest,
                                reviewed_by_user_id=review.reviewed_by_user_id,
                                created_at=ensure_utc(review.created_at),
                            )
                            if review is not None
                            else None
                        ),
                    )
                    if activation_request is not None
                    else None
                ),
            )
        )
    return EvaluationExperienceAssetOut(
        public_id=asset.public_id,
        namespace_id=asset.namespace_id,
        source_candidate_public_id=asset.source_candidate.public_id,
        source_candidate_category=asset.source_candidate.category,
        source_candidate_evidence_digest=asset.source_candidate.evidence_digest,
        name=asset.name,
        description=asset.description,
        created_by_user_id=asset.created_by_user_id,
        created_at=ensure_utc(asset.created_at),
        versions=versions,
    )


def _experience_extraction_run_out(
    run: EvaluationExperienceExtractionRun,
) -> EvaluationExperienceExtractionRunOut:
    version = run.policy_version
    return EvaluationExperienceExtractionRunOut(
        public_id=run.public_id,
        namespace_id=run.namespace_id,
        policy_public_id=version.policy.public_id,
        policy_version_public_id=version.public_id,
        policy_version=version.version,
        source_case_routing_run_public_id=run.source_case_routing_run.public_id,
        source_semantic_clustering_run_public_id=(
            run.source_semantic_clustering_run.public_id if run.source_semantic_clustering_run is not None else None
        ),
        request_digest=run.request_digest,
        evidence_digest=run.evidence_digest,
        extraction_digest=run.extraction_digest,
        outcome=run.outcome,
        reason_codes=list(run.reason_codes_json),
        source_bad_case_count=run.source_bad_case_count,
        cluster_count=run.cluster_count,
        eligible_cluster_count=run.eligible_cluster_count,
        candidate_count=run.candidate_count,
        schema_name=run.schema_name,
        schema_version=run.schema_version,
        created_by_user_id=run.created_by_user_id,
        created_at=ensure_utc(run.created_at),
        candidates=[
            _experience_candidate_out(candidate)
            for candidate in sorted(run.candidates, key=lambda value: value.position)
        ],
    )


def _evaluator_out(
    evaluator: Evaluator,
    *,
    versions: list[EvaluatorVersion] | None = None,
) -> EvaluatorOut:
    ordered_versions = sorted(
        evaluator.versions if versions is None else versions,
        key=lambda value: value.version,
    )
    return EvaluatorOut(
        public_id=evaluator.public_id,
        namespace_id=evaluator.namespace_id,
        name=evaluator.name,
        kind=evaluator.kind,
        provider=evaluator.provider,
        provider_evaluator_ref=evaluator.provider_evaluator_ref,
        status=evaluator.status,
        versions=[EvaluatorVersionOut.model_validate(version) for version in ordered_versions],
        created_at=evaluator.created_at,
        updated_at=evaluator.updated_at,
    )


async def _experiment_out(db: DB, experiment: Experiment) -> ExperimentOut:
    version = await db.get(EvaluationDatasetVersion, experiment.dataset_version_id)
    if version is None:
        raise HTTPException(status_code=500, detail="Dataset version is missing")
    return ExperimentOut(
        public_id=experiment.public_id,
        namespace_id=experiment.namespace_id,
        name=experiment.name,
        dataset_version_public_id=version.public_id,
        target_type=experiment.target_type,
        target_ref=experiment.target_ref,
        target_digest=experiment.target_digest,
        provider=experiment.provider,
        provider_experiment_ref=experiment.provider_experiment_ref,
        status=experiment.status,
        started_at=experiment.started_at,
        ended_at=experiment.ended_at,
        created_at=experiment.created_at,
        updated_at=experiment.updated_at,
    )


async def _evaluation_out(db: DB, evaluation: Evaluation) -> EvaluationOut:
    experiment = await db.get(Experiment, evaluation.experiment_id)
    evaluator_version = await db.get(
        EvaluatorVersion,
        evaluation.evaluator_version_id,
    )
    if experiment is None or evaluator_version is None:
        raise HTTPException(status_code=500, detail="Evaluation reference is missing")
    latest_manifest = await get_latest_evaluation_result_manifest(
        db,
        evaluation_id=evaluation.id,
    )
    return EvaluationOut(
        public_id=evaluation.public_id,
        namespace_id=evaluation.namespace_id,
        experiment_public_id=experiment.public_id,
        evaluator_version_public_id=evaluator_version.public_id,
        status=evaluation.status,
        provider_evaluation_ref=evaluation.provider_evaluation_ref,
        score=evaluation.score,
        total_count=evaluation.total_count,
        processed_count=evaluation.processed_count,
        scored_count=evaluation.scored_count,
        passed_count=evaluation.passed_count,
        failed_count=evaluation.failed_count,
        error_count=evaluation.error_count,
        result_completeness=evaluation.result_completeness,
        result_manifest_public_id=(latest_manifest.public_id if latest_manifest is not None else None),
        error_code=evaluation.error_code,
        attempt_count=evaluation.attempt_count,
        available_at=evaluation.available_at,
        lease_expires_at=evaluation.lease_expires_at,
        cancel_requested_at=evaluation.cancel_requested_at,
        started_at=evaluation.started_at,
        ended_at=evaluation.ended_at,
        created_at=evaluation.created_at,
        updated_at=evaluation.updated_at,
    )


def _result_manifest_out(
    manifest: EvaluationResultManifest,
    *,
    evaluation_public_id: str,
) -> EvaluationResultManifestOut:
    return EvaluationResultManifestOut(
        public_id=manifest.public_id,
        namespace_id=manifest.namespace_id,
        evaluation_public_id=evaluation_public_id,
        version=manifest.version,
        provider=manifest.provider,
        provider_dataset_ref=manifest.provider_dataset_ref,
        provider_experiment_ref=manifest.provider_experiment_ref,
        schema_name=manifest.schema_name,
        schema_version=manifest.schema_version,
        content_digest=manifest.content_digest,
        score=manifest.score,
        expected_count=manifest.expected_count,
        processed_count=manifest.processed_count,
        scored_count=manifest.scored_count,
        passed_count=manifest.passed_count,
        failed_count=manifest.failed_count,
        error_count=manifest.error_count,
        completeness=manifest.completeness,
        loss_reason=manifest.loss_reason,
        created_at=manifest.created_at,
    )


def _regression_policy_out(
    policy: RegressionPolicy,
    *,
    versions: list[RegressionPolicyVersion] | None = None,
) -> RegressionPolicyOut:
    ordered_versions = sorted(
        policy.versions if versions is None else versions,
        key=lambda value: value.version,
    )
    return RegressionPolicyOut(
        public_id=policy.public_id,
        namespace_id=policy.namespace_id,
        name=policy.name,
        description=policy.description,
        status=policy.status,
        versions=[RegressionPolicyVersionOut.model_validate(version) for version in ordered_versions],
        created_at=policy.created_at,
        updated_at=policy.updated_at,
    )


def _comparison_pin_out(
    pin: EvaluationComparisonPin,
) -> EvaluationComparisonPinOut:
    pass_rate = pin.manifest.passed_count / pin.manifest.scored_count if pin.manifest.scored_count else None
    return EvaluationComparisonPinOut(
        evaluation_public_id=pin.evaluation.public_id,
        experiment_public_id=pin.experiment.public_id,
        manifest_public_id=pin.manifest.public_id,
        dataset_version_public_id=pin.dataset_version.public_id,
        dataset_content_digest=pin.dataset_version.content_digest,
        evaluator_version_public_id=pin.evaluator_version.public_id,
        evaluator_config_digest=pin.evaluator_version.config_digest,
        target_type=pin.experiment.target_type,
        target_ref=pin.experiment.target_ref,
        target_digest=pin.experiment.target_digest,
        provider=pin.manifest.provider,
        provider_dataset_ref=pin.manifest.provider_dataset_ref,
        provider_experiment_ref=pin.manifest.provider_experiment_ref,
        result_content_digest=pin.manifest.content_digest,
        result_completeness=pin.manifest.completeness,
        score=pin.manifest.score,
        scored_count=pin.manifest.scored_count,
        passed_count=pin.manifest.passed_count,
        pass_rate=pass_rate,
    )


async def _comparison_out(
    db: DB,
    comparison: EvaluationComparison,
) -> EvaluationComparisonOut:
    view = await load_evaluation_comparison_view(
        db,
        comparison=comparison,
    )
    return EvaluationComparisonOut(
        public_id=comparison.public_id,
        namespace_id=comparison.namespace_id,
        outcome=comparison.outcome,
        reason_code=comparison.reason_code,
        baseline=_comparison_pin_out(view.baseline),
        candidate=_comparison_pin_out(view.candidate),
        policy=RegressionPolicySnapshotOut(
            policy_public_id=view.policy.public_id,
            policy_name=view.policy.name,
            version_public_id=view.policy_version.public_id,
            version=view.policy_version.version,
            content_digest=view.policy_version.content_digest,
            minimum_candidate_score=(view.policy_version.minimum_candidate_score),
            maximum_score_drop=(view.policy_version.maximum_score_drop),
            maximum_pass_rate_drop=(view.policy_version.maximum_pass_rate_drop),
            require_complete_results=(view.policy_version.require_complete_results),
        ),
        score_delta=comparison.score_delta,
        pass_rate_delta=comparison.pass_rate_delta,
        score_floor_breached=comparison.score_floor_breached,
        score_drop_breached=comparison.score_drop_breached,
        pass_rate_drop_breached=(comparison.pass_rate_drop_breached),
        schema_name=comparison.schema_name,
        schema_version=comparison.schema_version,
        reproducibility_digest=comparison.reproducibility_digest,
        created_at=ensure_utc(comparison.created_at),
    )


async def _hide_forbidden(resource, db: DB, current_user):
    try:
        await require_namespace_member(current_user, resource.namespace_id, db)
    except HTTPException as exc:
        if exc.status_code == 403:
            raise HTTPException(
                status_code=404,
                detail="Evaluation resource not found",
            ) from exc
        raise
    return resource


@dataset_router.post(
    "",
    response_model=EvaluationDatasetOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_dataset(
    body: EvaluationDatasetCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_writer(current_user, body.namespace_id, db)
    try:
        dataset = await create_evaluation_dataset(
            db,
            request=body,
            actor=current_user,
        )
        return _dataset_out(dataset, versions=[])
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@dataset_router.post(
    "/{public_id}/trace-materializations",
    response_model=EvaluationDatasetMaterializationOut,
    status_code=status.HTTP_201_CREATED,
)
async def materialize_dataset_trace(
    public_id: str,
    body: TraceDatasetMaterializationCreate,
    idempotency_key: MaterializationIdempotencyKey,
    db: DB,
    current_user: CurrentUser,
):
    try:
        dataset = await get_evaluation_dataset(db, public_id=public_id)
        await _hide_forbidden(dataset, db, current_user)
        await require_namespace_writer(
            current_user,
            dataset.namespace_id,
            db,
        )
        value = await materialize_evaluation_dataset_trace(
            db,
            dataset_public_id=public_id,
            request=body,
            idempotency_key=idempotency_key,
            actor=current_user,
        )
        return _dataset_materialization_out(value)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@dataset_materialization_router.get(
    "",
    response_model=list[EvaluationDatasetMaterializationOut],
)
async def list_dataset_materializations(
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    values = await list_evaluation_dataset_materializations(
        db,
        namespace_id=namespace_id,
        limit=limit,
    )
    return [_dataset_materialization_out(value) for value in values]


@trace_candidate_router.get(
    "",
    response_model=list[TraceDatasetCandidateOut],
)
async def list_trace_candidates(
    dataset_public_id: Annotated[str, Query(min_length=1, max_length=36)],
    from_start_time: Annotated[datetime, Query()],
    to_start_time: Annotated[datetime, Query()],
    db: DB,
    current_user: CurrentUser,
    name: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    observation_type: Annotated[
        str | None,
        Query(min_length=1, max_length=32, pattern=r"^[A-Za-z_]+$"),
    ] = None,
    environment: Annotated[
        str | None,
        Query(min_length=1, max_length=100),
    ] = None,
    root_only: bool = True,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    try:
        dataset = await get_evaluation_dataset(
            db,
            public_id=dataset_public_id,
        )
        await _hide_forbidden(dataset, db, current_user)
        _, views = await list_trace_dataset_candidates(
            db,
            dataset_public_id=dataset_public_id,
            from_start_time=ensure_utc(from_start_time),
            to_start_time=ensure_utc(to_start_time),
            name=name.strip() if name else None,
            observation_type=(observation_type.upper() if observation_type else None),
            environment=environment.strip() if environment else None,
            root_only=root_only,
            limit=limit,
        )
        return [
            TraceDatasetCandidateOut(
                source_trace_ref=view.candidate.source_trace_ref,
                source_observation_ref=(view.candidate.source_observation_ref),
                name=view.candidate.name,
                observation_type=view.candidate.observation_type,
                start_time=ensure_utc(view.candidate.start_time),
                end_time=(ensure_utc(view.candidate.end_time) if view.candidate.end_time is not None else None),
                environment=view.candidate.environment,
                already_materialized=view.already_materialized,
                already_governed=view.already_governed,
            )
            for view in views
        ]
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@dataset_router.post(
    "/{public_id}/curation-batches",
    response_model=EvaluationDatasetCurationBatchOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_dataset_curation_batch(
    public_id: str,
    body: EvaluationDatasetCurationBatchCreate,
    idempotency_key: MaterializationIdempotencyKey,
    db: DB,
    current_user: CurrentUser,
):
    try:
        dataset = await get_evaluation_dataset(db, public_id=public_id)
        await _hide_forbidden(dataset, db, current_user)
        await require_namespace_writer(
            current_user,
            dataset.namespace_id,
            db,
        )
        batch = await create_evaluation_dataset_curation_batch(
            db,
            dataset_public_id=public_id,
            request=body,
            idempotency_key=idempotency_key,
            actor=current_user,
        )
        return _dataset_curation_batch_out(batch)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@dataset_curation_router.get(
    "",
    response_model=list[EvaluationDatasetCurationBatchOut],
)
async def list_dataset_curation_batches(
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    batches = await list_evaluation_dataset_curation_batches(
        db,
        namespace_id=namespace_id,
        limit=limit,
    )
    return [_dataset_curation_batch_out(batch) for batch in batches]


@dataset_curation_router.post(
    "/{public_id}/reviews",
    response_model=EvaluationDatasetCurationBatchOut,
    status_code=status.HTTP_201_CREATED,
)
async def review_dataset_curation_batch(
    public_id: str,
    body: EvaluationDatasetCurationReviewCreate,
    idempotency_key: MaterializationIdempotencyKey,
    db: DB,
    current_user: CurrentUser,
):
    try:
        existing = await get_evaluation_dataset_curation_batch(
            db,
            public_id=public_id,
        )
        await _hide_forbidden(existing, db, current_user)
        await require_namespace_writer(
            current_user,
            existing.namespace_id,
            db,
        )
        batch = await review_evaluation_dataset_curation_batch(
            db,
            batch_public_id=public_id,
            request=body,
            idempotency_key=idempotency_key,
            actor=current_user,
        )
        return _dataset_curation_batch_out(batch)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@dataset_curation_router.post(
    "/{public_id}/materializations",
    response_model=EvaluationDatasetCurationBatchOut,
    status_code=status.HTTP_201_CREATED,
)
async def materialize_dataset_curation_batch(
    public_id: str,
    idempotency_key: MaterializationIdempotencyKey,
    db: DB,
    current_user: CurrentUser,
):
    try:
        existing = await get_evaluation_dataset_curation_batch(
            db,
            public_id=public_id,
        )
        await _hide_forbidden(existing, db, current_user)
        await require_namespace_writer(
            current_user,
            existing.namespace_id,
            db,
        )
        batch = await materialize_evaluation_dataset_curation_batch(
            db,
            batch_public_id=public_id,
            idempotency_key=idempotency_key,
            actor=current_user,
        )
        return _dataset_curation_batch_out(batch)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@sampling_policy_router.post(
    "",
    response_model=EvaluationSamplingPolicyOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_sampling_policy_definition(
    body: EvaluationSamplingPolicyCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_writer(current_user, body.namespace_id, db)
    try:
        policy = await create_evaluation_sampling_policy(
            db,
            request=body,
            actor=current_user,
        )
        return _sampling_policy_out(policy)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@sampling_policy_router.get(
    "",
    response_model=list[EvaluationSamplingPolicyOut],
)
async def list_sampling_policy_definitions(
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    policies = await list_evaluation_sampling_policies(
        db,
        namespace_id=namespace_id,
        limit=limit,
    )
    return [_sampling_policy_out(policy) for policy in policies]


@sampling_policy_router.get(
    "/{public_id}",
    response_model=EvaluationSamplingPolicyOut,
)
async def get_sampling_policy_definition(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    try:
        policy = await get_evaluation_sampling_policy(db, public_id=public_id)
        await _hide_forbidden(policy, db, current_user)
        return _sampling_policy_out(policy)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@sampling_policy_router.post(
    "/{public_id}/versions",
    response_model=EvaluationSamplingPolicyVersionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_sampling_policy_definition_version(
    public_id: str,
    body: EvaluationSamplingPolicyVersionCreate,
    db: DB,
    current_user: CurrentUser,
):
    try:
        policy = await get_evaluation_sampling_policy(db, public_id=public_id)
        await _hide_forbidden(policy, db, current_user)
        await require_namespace_writer(current_user, policy.namespace_id, db)
        version = await create_evaluation_sampling_policy_version(
            db,
            policy=policy,
            request=body,
            actor=current_user,
        )
        return EvaluationSamplingPolicyVersionOut.model_validate(version)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@sampling_policy_version_router.post(
    "/{public_id}/runs",
    response_model=EvaluationSamplingRunOut,
    status_code=status.HTTP_201_CREATED,
)
async def execute_sampling_policy_version(
    public_id: str,
    body: EvaluationSamplingRunCreate,
    idempotency_key: MaterializationIdempotencyKey,
    db: DB,
    current_user: CurrentUser,
):
    try:
        version = await get_evaluation_sampling_policy_version(db, public_id=public_id)
        await _hide_forbidden(version.policy, db, current_user)
        await require_namespace_writer(current_user, version.policy.namespace_id, db)
        dataset = await get_evaluation_dataset(db, public_id=body.dataset_public_id)
        await _hide_forbidden(dataset, db, current_user)
        if dataset.namespace_id != version.policy.namespace_id:
            raise EvaluationHubNotFoundError("EvaluationDataset not found")
        run = await run_evaluation_sampling_policy(
            db,
            version=version,
            request=body,
            idempotency_key=idempotency_key,
            actor=current_user,
        )
        return _sampling_run_out(run)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@sampling_run_router.get(
    "",
    response_model=list[EvaluationSamplingRunOut],
)
async def list_sampling_runs(
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    runs = await list_evaluation_sampling_runs(
        db,
        namespace_id=namespace_id,
        limit=limit,
    )
    return [_sampling_run_out(run) for run in runs]


@sampling_run_router.get(
    "/{public_id}",
    response_model=EvaluationSamplingRunOut,
)
async def get_sampling_run(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    try:
        run = await get_evaluation_sampling_run(db, public_id=public_id)
        await _hide_forbidden(run, db, current_user)
        return _sampling_run_out(run)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@annotation_queue_binding_router.get(
    "/provider-queues",
    response_model=list[EvaluationProviderAnnotationQueueOut],
)
async def list_provider_queues_for_annotation(
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    try:
        rows = await list_provider_annotation_queues(
            db,
            namespace_id=namespace_id,
            limit=limit,
        )
        return [
            EvaluationProviderAnnotationQueueOut(
                provider_queue_ref=descriptor.provider_queue_ref,
                name=descriptor.name,
                description=descriptor.description,
                score_config_ids=list(descriptor.score_config_ids),
                created_at=descriptor.created_at,
                updated_at=descriptor.updated_at,
                already_bound=already_bound,
            )
            for descriptor, already_bound in rows
        ]
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@annotation_queue_binding_router.post(
    "",
    response_model=EvaluationAnnotationQueueBindingOut,
    status_code=status.HTTP_201_CREATED,
)
async def bind_provider_annotation_queue(
    body: EvaluationAnnotationQueueBindingCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_writer(current_user, body.namespace_id, db)
    try:
        binding = await create_annotation_queue_binding(
            db,
            namespace_id=body.namespace_id,
            provider_queue_ref=body.provider_queue_ref,
            actor=current_user,
        )
        return _annotation_queue_binding_out(binding)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@annotation_queue_binding_router.get(
    "",
    response_model=list[EvaluationAnnotationQueueBindingOut],
)
async def list_annotation_queue_binding_records(
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    rows = await list_annotation_queue_bindings(
        db,
        namespace_id=namespace_id,
        limit=limit,
    )
    return [_annotation_queue_binding_out(value) for value in rows]


@annotation_queue_binding_router.get(
    "/{public_id}",
    response_model=EvaluationAnnotationQueueBindingOut,
)
async def get_annotation_queue_binding_record(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    try:
        binding = await get_annotation_queue_binding(db, public_id=public_id)
        await _hide_forbidden(binding, db, current_user)
        return _annotation_queue_binding_out(binding)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@annotation_queue_binding_router.post(
    "/{public_id}/dispatches",
    response_model=EvaluationAnnotationDispatchOut,
    status_code=status.HTTP_201_CREATED,
)
async def dispatch_curation_batch_to_annotation_queue(
    public_id: str,
    body: EvaluationAnnotationDispatchCreate,
    idempotency_key: MaterializationIdempotencyKey,
    db: DB,
    current_user: CurrentUser,
):
    try:
        binding = await get_annotation_queue_binding(db, public_id=public_id)
        await _hide_forbidden(binding, db, current_user)
        await require_namespace_writer(current_user, binding.namespace_id, db)
        dispatch = await create_annotation_dispatch(
            db,
            binding_public_id=public_id,
            curation_batch_public_id=body.curation_batch_public_id,
            idempotency_key=idempotency_key,
            actor=current_user,
        )
        return _annotation_dispatch_out(dispatch)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@annotation_dispatch_router.get(
    "",
    response_model=list[EvaluationAnnotationDispatchOut],
)
async def list_annotation_dispatch_records(
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    rows = await list_annotation_dispatches(
        db,
        namespace_id=namespace_id,
        limit=limit,
    )
    return [_annotation_dispatch_out(value) for value in rows]


@annotation_dispatch_router.get(
    "/{public_id}",
    response_model=EvaluationAnnotationDispatchOut,
)
async def get_annotation_dispatch_record(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    try:
        dispatch = await get_annotation_dispatch(db, public_id=public_id)
        await _hide_forbidden(dispatch, db, current_user)
        return _annotation_dispatch_out(dispatch)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@annotation_dispatch_router.post(
    "/{public_id}/retry",
    response_model=EvaluationAnnotationDispatchOut,
)
async def retry_annotation_dispatch_record(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    try:
        dispatch = await get_annotation_dispatch(db, public_id=public_id)
        await _hide_forbidden(dispatch, db, current_user)
        await require_namespace_writer(current_user, dispatch.namespace_id, db)
        dispatch = await retry_annotation_dispatch(
            db,
            public_id=public_id,
            actor=current_user,
        )
        return _annotation_dispatch_out(dispatch)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@annotation_dispatch_router.post(
    "/{public_id}/reconcile",
    response_model=EvaluationAnnotationDispatchOut,
)
async def reconcile_annotation_dispatch_record(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    try:
        dispatch = await get_annotation_dispatch(db, public_id=public_id)
        await _hide_forbidden(dispatch, db, current_user)
        await require_namespace_writer(current_user, dispatch.namespace_id, db)
        dispatch = await reconcile_annotation_dispatch(
            db,
            public_id=public_id,
            actor=current_user,
        )
        return _annotation_dispatch_out(dispatch)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@promotion_policy_router.post(
    "",
    response_model=EvaluationPromotionPolicyOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_promotion_policy_definition(
    body: EvaluationPromotionPolicyCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_writer(current_user, body.namespace_id, db)
    try:
        policy = await create_evaluation_promotion_policy(db, request=body, actor=current_user)
        return _promotion_policy_out(policy)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@promotion_policy_router.get(
    "",
    response_model=list[EvaluationPromotionPolicyOut],
)
async def list_promotion_policy_definitions(
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    policies = await list_evaluation_promotion_policies(db, namespace_id=namespace_id, limit=limit)
    return [_promotion_policy_out(policy) for policy in policies]


@promotion_policy_router.get(
    "/{public_id}",
    response_model=EvaluationPromotionPolicyOut,
)
async def get_promotion_policy_definition(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    try:
        policy = await get_evaluation_promotion_policy(db, public_id=public_id)
        await _hide_forbidden(policy, db, current_user)
        return _promotion_policy_out(policy)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@promotion_policy_router.post(
    "/{public_id}/versions",
    response_model=EvaluationPromotionPolicyVersionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_promotion_policy_definition_version(
    public_id: str,
    body: EvaluationPromotionPolicyVersionCreate,
    db: DB,
    current_user: CurrentUser,
):
    try:
        policy = await get_evaluation_promotion_policy(db, public_id=public_id)
        await _hide_forbidden(policy, db, current_user)
        await require_namespace_writer(current_user, policy.namespace_id, db)
        version = await create_evaluation_promotion_policy_version(
            db,
            policy=policy,
            request=body,
            actor=current_user,
        )
        return _promotion_policy_version_out(version)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@promotion_policy_version_router.post(
    "/{public_id}/runs",
    response_model=EvaluationPromotionRunOut,
    status_code=status.HTTP_201_CREATED,
)
async def execute_promotion_policy_version(
    public_id: str,
    body: EvaluationPromotionRunCreate,
    idempotency_key: MaterializationIdempotencyKey,
    db: DB,
    current_user: CurrentUser,
):
    try:
        version = await get_evaluation_promotion_policy_version(db, public_id=public_id)
        await _hide_forbidden(version.policy, db, current_user)
        await require_namespace_writer(current_user, version.policy.namespace_id, db)
        run = await run_evaluation_promotion_policy(
            db,
            version_public_id=public_id,
            dispatch_public_id=body.dispatch_public_id,
            idempotency_key=idempotency_key,
            actor=current_user,
        )
        return _promotion_run_out(run)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@promotion_run_router.get(
    "",
    response_model=list[EvaluationPromotionRunOut],
)
async def list_promotion_run_records(
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    runs = await list_evaluation_promotion_runs(db, namespace_id=namespace_id, limit=limit)
    return [_promotion_run_out(run) for run in runs]


@promotion_run_router.get(
    "/{public_id}",
    response_model=EvaluationPromotionRunOut,
)
async def get_promotion_run_record(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    try:
        run = await get_evaluation_promotion_run(db, public_id=public_id)
        await _hide_forbidden(run, db, current_user)
        return _promotion_run_out(run)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@case_routing_policy_router.post(
    "",
    response_model=EvaluationCaseRoutingPolicyOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_case_routing_policy_definition(
    body: EvaluationCaseRoutingPolicyCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_writer(current_user, body.namespace_id, db)
    try:
        policy = await create_evaluation_case_routing_policy(db, request=body, actor=current_user)
        return _case_routing_policy_out(policy)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@case_routing_policy_router.get(
    "",
    response_model=list[EvaluationCaseRoutingPolicyOut],
)
async def list_case_routing_policy_definitions(
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    policies = await list_evaluation_case_routing_policies(db, namespace_id=namespace_id, limit=limit)
    return [_case_routing_policy_out(policy) for policy in policies]


@case_routing_policy_router.get(
    "/{public_id}",
    response_model=EvaluationCaseRoutingPolicyOut,
)
async def get_case_routing_policy_definition(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    try:
        policy = await get_evaluation_case_routing_policy(db, public_id=public_id)
        await _hide_forbidden(policy, db, current_user)
        return _case_routing_policy_out(policy)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@case_routing_policy_router.post(
    "/{public_id}/versions",
    response_model=EvaluationCaseRoutingPolicyVersionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_case_routing_policy_definition_version(
    public_id: str,
    body: EvaluationCaseRoutingPolicyVersionCreate,
    db: DB,
    current_user: CurrentUser,
):
    try:
        policy = await get_evaluation_case_routing_policy(db, public_id=public_id)
        await _hide_forbidden(policy, db, current_user)
        await require_namespace_writer(current_user, policy.namespace_id, db)
        version = await create_evaluation_case_routing_policy_version(
            db,
            policy=policy,
            request=body,
            actor=current_user,
        )
        return _case_routing_policy_version_out(version)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@case_routing_policy_version_router.post(
    "/{public_id}/runs",
    response_model=EvaluationCaseRoutingRunOut,
    status_code=status.HTTP_201_CREATED,
)
async def execute_case_routing_policy_version(
    public_id: str,
    body: EvaluationCaseRoutingRunCreate,
    idempotency_key: MaterializationIdempotencyKey,
    db: DB,
    current_user: CurrentUser,
):
    try:
        version = await get_evaluation_case_routing_policy_version(db, public_id=public_id)
        await _hide_forbidden(version.policy, db, current_user)
        await require_namespace_writer(current_user, version.policy.namespace_id, db)
        run = await run_evaluation_case_routing_policy(
            db,
            version_public_id=public_id,
            request=body,
            idempotency_key=idempotency_key,
            actor=current_user,
        )
        return _case_routing_run_out(run)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@case_routing_run_router.get(
    "",
    response_model=list[EvaluationCaseRoutingRunOut],
)
async def list_case_routing_run_records(
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    runs = await list_evaluation_case_routing_runs(db, namespace_id=namespace_id, limit=limit)
    return [_case_routing_run_out(run) for run in runs]


@case_routing_run_router.get(
    "/{public_id}",
    response_model=EvaluationCaseRoutingRunOut,
)
async def get_case_routing_run_record(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    try:
        run = await get_evaluation_case_routing_run(db, public_id=public_id)
        await _hide_forbidden(run, db, current_user)
        return _case_routing_run_out(run)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@semantic_clustering_policy_router.post(
    "",
    response_model=EvaluationSemanticClusteringPolicyOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_semantic_clustering_policy_definition(
    body: EvaluationSemanticClusteringPolicyCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_writer(current_user, body.namespace_id, db)
    try:
        policy = await create_evaluation_semantic_clustering_policy(db, request=body, actor=current_user)
        return _semantic_clustering_policy_out(policy)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@semantic_clustering_policy_router.get("", response_model=list[EvaluationSemanticClusteringPolicyOut])
async def list_semantic_clustering_policy_definitions(
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    policies = await list_evaluation_semantic_clustering_policies(db, namespace_id=namespace_id, limit=limit)
    return [_semantic_clustering_policy_out(value) for value in policies]


@semantic_clustering_policy_router.get("/{public_id}", response_model=EvaluationSemanticClusteringPolicyOut)
async def get_semantic_clustering_policy_definition(public_id: str, db: DB, current_user: CurrentUser):
    try:
        policy = await get_evaluation_semantic_clustering_policy(db, public_id=public_id)
        await _hide_forbidden(policy, db, current_user)
        return _semantic_clustering_policy_out(policy)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@semantic_clustering_policy_router.post(
    "/{public_id}/versions",
    response_model=EvaluationSemanticClusteringPolicyVersionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_semantic_clustering_policy_definition_version(
    public_id: str,
    body: EvaluationSemanticClusteringPolicyVersionCreate,
    db: DB,
    current_user: CurrentUser,
):
    try:
        policy = await get_evaluation_semantic_clustering_policy(db, public_id=public_id)
        await _hide_forbidden(policy, db, current_user)
        await require_namespace_writer(current_user, policy.namespace_id, db)
        version = await create_evaluation_semantic_clustering_policy_version(
            db, policy=policy, request=body, actor=current_user
        )
        return _semantic_clustering_policy_version_out(version)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@semantic_clustering_policy_version_router.post(
    "/{public_id}/runs",
    response_model=EvaluationSemanticClusteringRunOut,
    status_code=status.HTTP_201_CREATED,
)
async def execute_semantic_clustering_policy_version(
    public_id: str,
    body: EvaluationSemanticClusteringRunCreate,
    idempotency_key: MaterializationIdempotencyKey,
    db: DB,
    current_user: CurrentUser,
):
    try:
        version = await get_evaluation_semantic_clustering_policy_version(db, public_id=public_id)
        await _hide_forbidden(version.policy, db, current_user)
        await require_namespace_writer(current_user, version.policy.namespace_id, db)
        run = await run_evaluation_semantic_clustering(
            db,
            version_public_id=public_id,
            request=body,
            idempotency_key=idempotency_key,
            actor=current_user,
        )
        return _semantic_clustering_run_out(run)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@semantic_clustering_run_router.get("", response_model=list[EvaluationSemanticClusteringRunOut])
async def list_semantic_clustering_run_records(
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    runs = await list_evaluation_semantic_clustering_runs(db, namespace_id=namespace_id, limit=limit)
    return [_semantic_clustering_run_out(value) for value in runs]


@semantic_clustering_run_router.get("/{public_id}", response_model=EvaluationSemanticClusteringRunOut)
async def get_semantic_clustering_run_record(public_id: str, db: DB, current_user: CurrentUser):
    try:
        run = await get_evaluation_semantic_clustering_run(db, public_id=public_id)
        await _hide_forbidden(run, db, current_user)
        return _semantic_clustering_run_out(run)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@semantic_regression_policy_router.post(
    "",
    response_model=EvaluationSemanticRegressionPolicyOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_semantic_regression_policy_definition(
    body: EvaluationSemanticRegressionPolicyCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_writer(current_user, body.namespace_id, db)
    try:
        policy = await create_evaluation_semantic_regression_policy(db, request=body, actor=current_user)
        return _semantic_regression_policy_out(policy)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@semantic_regression_policy_router.get("", response_model=list[EvaluationSemanticRegressionPolicyOut])
async def list_semantic_regression_policy_definitions(
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    policies = await list_evaluation_semantic_regression_policies(db, namespace_id=namespace_id, limit=limit)
    return [_semantic_regression_policy_out(value) for value in policies]


@semantic_regression_policy_router.get("/{public_id}", response_model=EvaluationSemanticRegressionPolicyOut)
async def get_semantic_regression_policy_definition(public_id: str, db: DB, current_user: CurrentUser):
    try:
        policy = await get_evaluation_semantic_regression_policy(db, public_id=public_id)
        await _hide_forbidden(policy, db, current_user)
        return _semantic_regression_policy_out(policy)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@semantic_regression_policy_router.post(
    "/{public_id}/versions",
    response_model=EvaluationSemanticRegressionPolicyVersionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_semantic_regression_policy_definition_version(
    public_id: str,
    body: EvaluationSemanticRegressionPolicyVersionCreate,
    db: DB,
    current_user: CurrentUser,
):
    try:
        policy = await get_evaluation_semantic_regression_policy(db, public_id=public_id)
        await _hide_forbidden(policy, db, current_user)
        await require_namespace_writer(current_user, policy.namespace_id, db)
        version = await create_evaluation_semantic_regression_policy_version(
            db, policy=policy, request=body, actor=current_user
        )
        return _semantic_regression_policy_version_out(version)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@semantic_regression_comparison_router.post(
    "",
    response_model=EvaluationSemanticRegressionComparisonOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_semantic_regression_comparison_record(
    body: EvaluationSemanticRegressionComparisonCreate,
    idempotency_key: MaterializationIdempotencyKey,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_writer(current_user, body.namespace_id, db)
    try:
        comparison = await create_evaluation_semantic_regression_comparison(
            db,
            request=body,
            idempotency_key=idempotency_key,
            actor=current_user,
        )
        return _semantic_regression_comparison_out(comparison)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@semantic_regression_comparison_router.get("", response_model=list[EvaluationSemanticRegressionComparisonOut])
async def list_semantic_regression_comparison_records(
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    comparisons = await list_evaluation_semantic_regression_comparisons(db, namespace_id=namespace_id, limit=limit)
    return [_semantic_regression_comparison_out(value) for value in comparisons]


@semantic_regression_comparison_router.get("/{public_id}", response_model=EvaluationSemanticRegressionComparisonOut)
async def get_semantic_regression_comparison_record(public_id: str, db: DB, current_user: CurrentUser):
    try:
        comparison = await get_evaluation_semantic_regression_comparison(db, public_id=public_id)
        await _hide_forbidden(comparison, db, current_user)
        return _semantic_regression_comparison_out(comparison)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@semantic_monitor_router.post(
    "",
    response_model=EvaluationSemanticMonitorOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_semantic_monitor_definition(
    body: EvaluationSemanticMonitorCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_writer(current_user, body.namespace_id, db)
    try:
        monitor = await create_evaluation_semantic_monitor(
            db, request=body, actor=current_user
        )
        return _semantic_monitor_out(monitor)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@semantic_monitor_router.get("", response_model=list[EvaluationSemanticMonitorOut])
async def list_semantic_monitor_definitions(
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    monitors = await list_evaluation_semantic_monitors(
        db, namespace_id=namespace_id, limit=limit
    )
    return [_semantic_monitor_out(value) for value in monitors]


@semantic_monitor_router.get(
    "/{public_id}", response_model=EvaluationSemanticMonitorOut
)
async def get_semantic_monitor_definition(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    try:
        monitor = await get_evaluation_semantic_monitor(db, public_id=public_id)
        await _hide_forbidden(monitor, db, current_user)
        return _semantic_monitor_out(monitor)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@semantic_monitor_router.post(
    "/{public_id}/pause", response_model=EvaluationSemanticMonitorOut
)
async def pause_semantic_monitor_definition(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    try:
        monitor = await get_evaluation_semantic_monitor(db, public_id=public_id)
        await _hide_forbidden(monitor, db, current_user)
        await require_namespace_writer(current_user, monitor.namespace_id, db)
        monitor = await set_evaluation_semantic_monitor_paused(
            db, monitor=monitor, paused=True, actor=current_user
        )
        return _semantic_monitor_out(monitor)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@semantic_monitor_router.post(
    "/{public_id}/resume", response_model=EvaluationSemanticMonitorOut
)
async def resume_semantic_monitor_definition(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    try:
        monitor = await get_evaluation_semantic_monitor(db, public_id=public_id)
        await _hide_forbidden(monitor, db, current_user)
        await require_namespace_writer(current_user, monitor.namespace_id, db)
        monitor = await set_evaluation_semantic_monitor_paused(
            db, monitor=monitor, paused=False, actor=current_user
        )
        return _semantic_monitor_out(monitor)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@semantic_monitor_router.post(
    "/{public_id}/run-now",
    response_model=EvaluationSemanticMonitorRunOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_semantic_monitor_definition_now(
    public_id: str,
    idempotency_key: MaterializationIdempotencyKey,
    db: DB,
    current_user: CurrentUser,
):
    try:
        monitor = await get_evaluation_semantic_monitor(db, public_id=public_id)
        await _hide_forbidden(monitor, db, current_user)
        await require_namespace_writer(current_user, monitor.namespace_id, db)
        run = await run_evaluation_semantic_monitor_now(
            db,
            monitor=monitor,
            idempotency_key=idempotency_key,
            actor=current_user,
        )
        return _semantic_monitor_run_out(run)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@semantic_monitor_run_router.get(
    "", response_model=list[EvaluationSemanticMonitorRunOut]
)
async def list_semantic_monitor_run_records(
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    runs = await list_evaluation_semantic_monitor_runs(
        db, namespace_id=namespace_id, limit=limit
    )
    return [_semantic_monitor_run_out(value) for value in runs]


@semantic_monitor_alert_router.get(
    "", response_model=list[EvaluationSemanticMonitorAlertOut]
)
async def list_semantic_monitor_alert_records(
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    alert_status: Annotated[
        EvaluationSemanticMonitorAlertStatus | None,
        Query(alias="status"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    alerts = await list_evaluation_semantic_monitor_alerts(
        db,
        namespace_id=namespace_id,
        status=alert_status,
        limit=limit,
    )
    return [_semantic_monitor_alert_out(value) for value in alerts]


@semantic_monitor_alert_router.post(
    "/{public_id}/acknowledge",
    response_model=EvaluationSemanticMonitorAlertOut,
)
async def acknowledge_semantic_monitor_alert_record(
    public_id: str,
    body: EvaluationSemanticMonitorAcknowledge,
    db: DB,
    current_user: CurrentUser,
):
    try:
        alert = await get_evaluation_semantic_monitor_alert(
            db, public_id=public_id
        )
        await _hide_forbidden(alert, db, current_user)
        await require_namespace_writer(current_user, alert.namespace_id, db)
        alert = await acknowledge_evaluation_semantic_monitor_alert(
            db,
            alert=alert,
            request=body,
            actor=current_user,
        )
        return _semantic_monitor_alert_out(alert)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@failure_taxonomy_policy_router.post(
    "",
    response_model=EvaluationFailureTaxonomyPolicyOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_failure_taxonomy_policy_definition(
    body: EvaluationFailureTaxonomyPolicyCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_writer(current_user, body.namespace_id, db)
    try:
        policy = await create_evaluation_failure_taxonomy_policy(db, request=body, actor=current_user)
        return _failure_taxonomy_policy_out(policy)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@failure_taxonomy_policy_router.get(
    "",
    response_model=list[EvaluationFailureTaxonomyPolicyOut],
)
async def list_failure_taxonomy_policy_definitions(
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    policies = await list_evaluation_failure_taxonomy_policies(db, namespace_id=namespace_id, limit=limit)
    return [_failure_taxonomy_policy_out(policy) for policy in policies]


@failure_taxonomy_policy_router.get(
    "/{public_id}",
    response_model=EvaluationFailureTaxonomyPolicyOut,
)
async def get_failure_taxonomy_policy_definition(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    try:
        policy = await get_evaluation_failure_taxonomy_policy(db, public_id=public_id)
        await _hide_forbidden(policy, db, current_user)
        return _failure_taxonomy_policy_out(policy)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@failure_taxonomy_policy_router.post(
    "/{public_id}/versions",
    response_model=EvaluationFailureTaxonomyPolicyVersionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_failure_taxonomy_policy_definition_version(
    public_id: str,
    body: EvaluationFailureTaxonomyPolicyVersionCreate,
    db: DB,
    current_user: CurrentUser,
):
    try:
        policy = await get_evaluation_failure_taxonomy_policy(db, public_id=public_id)
        await _hide_forbidden(policy, db, current_user)
        await require_namespace_writer(current_user, policy.namespace_id, db)
        version = await create_evaluation_failure_taxonomy_policy_version(
            db,
            policy=policy,
            request=body,
            actor=current_user,
        )
        return _failure_taxonomy_policy_version_out(version)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@failure_taxonomy_policy_version_router.post(
    "/{public_id}/runs",
    response_model=EvaluationExperienceExtractionRunOut,
    status_code=status.HTTP_201_CREATED,
)
async def execute_failure_taxonomy_policy_version(
    public_id: str,
    body: EvaluationExperienceExtractionRunCreate,
    idempotency_key: MaterializationIdempotencyKey,
    db: DB,
    current_user: CurrentUser,
):
    try:
        version = await get_evaluation_failure_taxonomy_policy_version(db, public_id=public_id)
        await _hide_forbidden(version.policy, db, current_user)
        await require_namespace_writer(current_user, version.policy.namespace_id, db)
        run = await run_evaluation_experience_extraction(
            db,
            version_public_id=public_id,
            request=body,
            idempotency_key=idempotency_key,
            actor=current_user,
        )
        return _experience_extraction_run_out(run)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@experience_extraction_run_router.get(
    "",
    response_model=list[EvaluationExperienceExtractionRunOut],
)
async def list_experience_extraction_run_records(
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    runs = await list_evaluation_experience_extraction_runs(db, namespace_id=namespace_id, limit=limit)
    return [_experience_extraction_run_out(run) for run in runs]


@experience_extraction_run_router.get(
    "/{public_id}",
    response_model=EvaluationExperienceExtractionRunOut,
)
async def get_experience_extraction_run_record(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    try:
        run = await get_evaluation_experience_extraction_run(db, public_id=public_id)
        await _hide_forbidden(run, db, current_user)
        return _experience_extraction_run_out(run)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@experience_candidate_router.get(
    "/{public_id}",
    response_model=EvaluationExperienceCandidateOut,
)
async def get_experience_candidate_record(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    try:
        candidate = await get_evaluation_experience_candidate(db, public_id=public_id)
        await require_namespace_member(current_user, candidate.run.namespace_id, db)
        return _experience_candidate_out(candidate)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@experience_candidate_router.post(
    "/{public_id}/reviews",
    response_model=EvaluationExperienceCandidateOut,
    status_code=status.HTTP_201_CREATED,
)
async def review_experience_candidate_record(
    public_id: str,
    body: EvaluationExperienceCandidateReviewCreate,
    db: DB,
    current_user: CurrentUser,
):
    try:
        candidate = await get_evaluation_experience_candidate(db, public_id=public_id)
        await require_namespace_writer(current_user, candidate.run.namespace_id, db)
        candidate = await review_evaluation_experience_candidate(
            db,
            public_id=public_id,
            request=body,
            actor=current_user,
        )
        return _experience_candidate_out(candidate)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@experience_asset_router.get("", response_model=list[EvaluationExperienceAssetOut])
async def list_experience_asset_records(
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    assets = await list_evaluation_experience_assets(db, namespace_id=namespace_id, limit=limit)
    return [_experience_asset_out(asset) for asset in assets]


@experience_asset_router.post(
    "",
    response_model=EvaluationExperienceAssetOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_experience_asset_record(
    body: EvaluationExperienceAssetCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_writer(current_user, body.namespace_id, db)
    try:
        asset = await create_evaluation_experience_asset(db, request=body, actor=current_user)
        return _experience_asset_out(asset)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@experience_asset_router.get("/{public_id}", response_model=EvaluationExperienceAssetOut)
async def get_experience_asset_record(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    try:
        asset = await get_evaluation_experience_asset(db, public_id=public_id)
        await require_namespace_member(current_user, asset.namespace_id, db)
        return _experience_asset_out(asset)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@experience_asset_router.post(
    "/{public_id}/versions",
    response_model=EvaluationExperienceAssetOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_experience_asset_version_record(
    public_id: str,
    body: EvaluationExperienceAssetVersionCreate,
    db: DB,
    current_user: CurrentUser,
):
    try:
        asset = await get_evaluation_experience_asset(db, public_id=public_id)
        await require_namespace_writer(current_user, asset.namespace_id, db)
        asset = await create_evaluation_experience_asset_version(
            db,
            asset_public_id=public_id,
            request=body,
            actor=current_user,
        )
        return _experience_asset_out(asset)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@experience_asset_version_router.post(
    "/{public_id}/activation-requests",
    response_model=EvaluationExperienceAssetOut,
    status_code=status.HTTP_201_CREATED,
)
async def request_experience_asset_activation_record(
    public_id: str,
    body: EvaluationExperienceActivationRequestCreate,
    db: DB,
    current_user: CurrentUser,
):
    try:
        version = await get_evaluation_experience_asset_version(db, public_id=public_id)
        await require_namespace_writer(current_user, version.asset.namespace_id, db)
        asset = await request_evaluation_experience_activation(
            db,
            version_public_id=public_id,
            request=body,
            actor=current_user,
        )
        return _experience_asset_out(asset)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@experience_activation_router.post(
    "/{public_id}/reviews",
    response_model=EvaluationExperienceAssetOut,
    status_code=status.HTTP_201_CREATED,
)
async def review_experience_asset_activation_record(
    public_id: str,
    body: EvaluationExperienceActivationReviewCreate,
    db: DB,
    current_user: CurrentUser,
):
    try:
        activation_request = await get_evaluation_experience_activation_request(db, public_id=public_id)
        await require_namespace_writer(current_user, activation_request.namespace_id, db)
        asset = await review_evaluation_experience_activation(
            db,
            request_public_id=public_id,
            request=body,
            actor=current_user,
        )
        return _experience_asset_out(asset)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@dataset_router.get("", response_model=list[EvaluationDatasetOut])
async def list_datasets(
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    rows = await list_evaluation_datasets(
        db,
        namespace_id=namespace_id,
        limit=limit,
    )
    return [_dataset_out(row) for row in rows]


@dataset_router.get("/{public_id}", response_model=EvaluationDatasetOut)
async def get_dataset(public_id: str, db: DB, current_user: CurrentUser):
    try:
        dataset = await get_evaluation_dataset(db, public_id=public_id)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc
    await _hide_forbidden(dataset, db, current_user)
    return _dataset_out(dataset)


@dataset_router.post(
    "/{public_id}/versions",
    response_model=EvaluationDatasetVersionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_dataset_version(
    public_id: str,
    body: EvaluationDatasetVersionCreate,
    db: DB,
    current_user: CurrentUser,
):
    try:
        dataset = await get_evaluation_dataset(db, public_id=public_id)
        await _hide_forbidden(dataset, db, current_user)
        await require_namespace_writer(current_user, dataset.namespace_id, db)
        return await create_evaluation_dataset_version(
            db,
            dataset=dataset,
            request=body,
            actor=current_user,
        )
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@evaluator_router.post(
    "",
    response_model=EvaluatorOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_evaluator_definition(
    body: EvaluatorCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_writer(current_user, body.namespace_id, db)
    try:
        evaluator = await create_evaluator(
            db,
            request=body,
            actor=current_user,
        )
        return _evaluator_out(evaluator, versions=[])
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@evaluator_router.get("", response_model=list[EvaluatorOut])
async def list_evaluator_definitions(
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    rows = await list_evaluators(db, namespace_id=namespace_id, limit=limit)
    return [_evaluator_out(row) for row in rows]


@evaluator_router.get("/{public_id}", response_model=EvaluatorOut)
async def get_evaluator_definition(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    try:
        evaluator = await get_evaluator(db, public_id=public_id)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc
    await _hide_forbidden(evaluator, db, current_user)
    return _evaluator_out(evaluator)


@evaluator_router.post(
    "/{public_id}/versions",
    response_model=EvaluatorVersionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_evaluator_definition_version(
    public_id: str,
    body: EvaluatorVersionCreate,
    db: DB,
    current_user: CurrentUser,
):
    try:
        evaluator = await get_evaluator(db, public_id=public_id)
        await _hide_forbidden(evaluator, db, current_user)
        await require_namespace_writer(current_user, evaluator.namespace_id, db)
        return await create_evaluator_version(
            db,
            evaluator=evaluator,
            request=body,
            actor=current_user,
        )
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@experiment_router.post(
    "",
    response_model=ExperimentOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_experiment_definition(
    body: ExperimentCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_writer(current_user, body.namespace_id, db)
    try:
        experiment = await create_experiment(
            db,
            request=body,
            actor=current_user,
        )
        return await _experiment_out(db, experiment)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@experiment_router.get("", response_model=list[ExperimentOut])
async def list_experiment_definitions(
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    rows = await list_experiments(db, namespace_id=namespace_id, limit=limit)
    return [await _experiment_out(db, row) for row in rows]


@experiment_router.get("/{public_id}", response_model=ExperimentOut)
async def get_experiment_definition(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    try:
        experiment = await get_experiment(db, public_id=public_id)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc
    await _hide_forbidden(experiment, db, current_user)
    return await _experiment_out(db, experiment)


@experiment_router.post(
    "/{public_id}/evaluations",
    response_model=EvaluationOut,
    status_code=status.HTTP_201_CREATED,
)
async def start_evaluation(
    public_id: str,
    body: EvaluationCreate,
    db: DB,
    current_user: CurrentUser,
    idempotency_key: EvaluationIdempotencyKey = None,
):
    try:
        experiment = await get_experiment(db, public_id=public_id)
        await _hide_forbidden(experiment, db, current_user)
        await require_namespace_writer(
            current_user,
            experiment.namespace_id,
            db,
        )
        evaluation = await create_evaluation(
            db,
            experiment=experiment,
            request=body,
            actor=current_user,
            execution_key=idempotency_key,
        )
        return await _evaluation_out(db, evaluation)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@evaluation_router.post(
    "/{public_id}/cancel",
    response_model=EvaluationOut,
)
async def cancel_evaluation_run(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    try:
        evaluation = await get_evaluation(db, public_id=public_id)
        await _hide_forbidden(evaluation, db, current_user)
        await require_namespace_writer(
            current_user,
            evaluation.namespace_id,
            db,
        )
        cancelled = await request_evaluation_cancel(
            db,
            evaluation=evaluation,
            actor=current_user,
        )
        return await _evaluation_out(db, cancelled)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@evaluation_router.post(
    "/{public_id}/retry",
    response_model=EvaluationOut,
)
async def retry_evaluation_run(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    try:
        evaluation = await get_evaluation(db, public_id=public_id)
        await _hide_forbidden(evaluation, db, current_user)
        await require_namespace_writer(
            current_user,
            evaluation.namespace_id,
            db,
        )
        retried = await retry_failed_evaluation(
            db,
            evaluation=evaluation,
            actor=current_user,
        )
        return await _evaluation_out(db, retried)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@evaluation_router.get("", response_model=list[EvaluationOut])
async def list_evaluation_runs(
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    experiment_public_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    experiment_id = None
    if experiment_public_id is not None:
        try:
            experiment = await get_experiment(
                db,
                public_id=experiment_public_id,
                namespace_id=namespace_id,
            )
        except EvaluationHubError as exc:
            raise _http_error(exc) from exc
        experiment_id = experiment.id
    rows = await list_evaluations(
        db,
        namespace_id=namespace_id,
        experiment_id=experiment_id,
        limit=limit,
    )
    return [await _evaluation_out(db, row) for row in rows]


@evaluation_router.get("/{public_id}", response_model=EvaluationOut)
async def get_evaluation_run(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    try:
        evaluation = await get_evaluation(db, public_id=public_id)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc
    await _hide_forbidden(evaluation, db, current_user)
    return await _evaluation_out(db, evaluation)


@evaluation_router.get(
    "/{public_id}/result-manifests",
    response_model=list[EvaluationResultManifestOut],
)
async def list_evaluation_run_result_manifests(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    try:
        evaluation = await get_evaluation(db, public_id=public_id)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc
    await _hide_forbidden(evaluation, db, current_user)
    manifests = await list_evaluation_result_manifests(
        db,
        evaluation_id=evaluation.id,
        limit=limit,
    )
    return [
        _result_manifest_out(
            manifest,
            evaluation_public_id=evaluation.public_id,
        )
        for manifest in manifests
    ]


@evaluation_router.post(
    "/{public_id}/complete",
    response_model=EvaluationOut,
)
async def complete_evaluation_run(
    public_id: str,
    body: EvaluationComplete,
    db: DB,
    current_user: CurrentUser,
):
    try:
        evaluation = await get_evaluation(db, public_id=public_id)
        await _hide_forbidden(evaluation, db, current_user)
        await require_namespace_writer(
            current_user,
            evaluation.namespace_id,
            db,
        )
        completed = await complete_evaluation(
            db,
            evaluation=evaluation,
            request=body,
            actor=current_user,
        )
        return await _evaluation_out(db, completed)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@regression_policy_router.post(
    "",
    response_model=RegressionPolicyOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_regression_policy_definition(
    body: RegressionPolicyCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_writer(current_user, body.namespace_id, db)
    try:
        policy = await create_regression_policy(
            db,
            request=body,
            actor=current_user,
        )
        response = _regression_policy_out(policy, versions=[])
        await db.commit()
        return response
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@regression_policy_router.get(
    "",
    response_model=list[RegressionPolicyOut],
)
async def list_regression_policy_definitions(
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    policies = await list_regression_policies(
        db,
        namespace_id=namespace_id,
        limit=limit,
    )
    return [_regression_policy_out(policy) for policy in policies]


@regression_policy_router.get(
    "/{public_id}",
    response_model=RegressionPolicyOut,
)
async def get_regression_policy_definition(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    try:
        policy = await get_regression_policy(db, public_id=public_id)
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc
    await _hide_forbidden(policy, db, current_user)
    return _regression_policy_out(policy)


@regression_policy_router.post(
    "/{public_id}/versions",
    response_model=RegressionPolicyVersionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_regression_policy_definition_version(
    public_id: str,
    body: RegressionPolicyVersionCreate,
    db: DB,
    current_user: CurrentUser,
):
    try:
        policy = await get_regression_policy(db, public_id=public_id)
        await _hide_forbidden(policy, db, current_user)
        await require_namespace_writer(
            current_user,
            policy.namespace_id,
            db,
        )
        version = await create_regression_policy_version(
            db,
            policy=policy,
            request=body,
            actor=current_user,
        )
        response = RegressionPolicyVersionOut.model_validate(version)
        await db.commit()
        return response
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@evaluation_comparison_router.post(
    "",
    response_model=EvaluationComparisonOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_evaluation_comparison_result(
    body: EvaluationComparisonCreate,
    db: DB,
    current_user: CurrentUser,
    idempotency_key: ComparisonIdempotencyKey,
):
    await require_namespace_writer(current_user, body.namespace_id, db)
    try:
        comparison = await create_evaluation_comparison(
            db,
            request=body,
            idempotency_key=idempotency_key,
            actor=current_user,
        )
        response = await _comparison_out(db, comparison)
        await db.commit()
        return response
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc


@evaluation_comparison_router.get(
    "",
    response_model=list[EvaluationComparisonOut],
)
async def list_evaluation_comparison_results(
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    comparisons = await list_evaluation_comparisons(
        db,
        namespace_id=namespace_id,
        limit=limit,
    )
    return [await _comparison_out(db, comparison) for comparison in comparisons]


@evaluation_comparison_router.get(
    "/{public_id}",
    response_model=EvaluationComparisonOut,
)
async def get_evaluation_comparison_result(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    try:
        comparison = await get_evaluation_comparison(
            db,
            public_id=public_id,
        )
    except EvaluationHubError as exc:
        raise _http_error(exc) from exc
    await _hide_forbidden(comparison, db, current_user)
    return await _comparison_out(db, comparison)
