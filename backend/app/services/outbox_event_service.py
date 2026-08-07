from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.control_plane import ReporterCredential, WorkTrace
from app.models.execution import AgentRun, AgentSession
from app.models.evaluation import (
    EvaluationAnnotationDispatch,
    EvaluationCaseRoutingRun,
    EvaluationComparison,
    EvaluationDatasetCurationBatch,
    EvaluationDatasetCurationMaterialization,
    EvaluationDatasetCurationReview,
    EvaluationDatasetMaterialization,
    EvaluationExperienceAsset,
    EvaluationExperienceAssetVersion,
    EvaluationExperienceCandidate,
    EvaluationExperienceExtractionRun,
    EvaluationPromotionRun,
    EvaluationResultManifest,
    EvaluationSamplingRun,
    EvaluationSemanticClusteringRun,
    EvaluationSemanticMonitor,
    EvaluationSemanticMonitorAlert,
    EvaluationSemanticMonitorRun,
    EvaluationSemanticRegressionComparison,
)
from app.models.outbox import OutboxEvent, OutboxEventStatus
from app.models.package_registry import (
    AgentPackage,
    AgentPackageVersion,
    PackageSigningKey,
)
from app.models.release import (
    ReleaseCandidateEvaluationBinding,
    ReleaseCandidateEvaluationReview,
)
from app.models.release_control import (
    ReleaseCandidate,
    ReleaseCandidateApproval,
    ReleaseCanaryEvaluation,
    ReleaseDeploymentReceipt,
    ReleaseEnvironmentRelease,
    ReleasePolicyDecision,
    ReleasePolicyException,
    ReleasePolicyExceptionReview,
    ReleasePolicyVersion,
    ReleasePromotion,
    ReleaseRollback,
)
from app.models.handover import (
    HandoverAcceptance,
    HandoverEvidenceSnapshot,
    HandoverObligationReceipt,
    HandoverSignedPackage,
)
from app.models.iam import DirectoryLifecycleEvent
from app.models.telemetry import AgentRunArtifact


OUTBOX_SCHEMA_VERSION = "1.0"
MAX_OUTBOX_PAYLOAD_BYTES = 16_384
MAX_OUTBOX_PAYLOAD_KEYS = 32

AGENT_SESSION_STARTED = "AgentSessionStarted"
AGENT_SESSION_COMPLETED = "AgentSessionCompleted"
AGENT_RUN_REGISTERED = "AgentRunRegistered"
AGENT_RUN_COMPLETED = "AgentRunCompleted"
AGENT_RUN_TRUST_DEGRADED = "AgentRunTrustDegraded"
TRACE_ARTIFACT_STORED = "TraceArtifactStored"
STRUCTURED_REPORT_RECORDED = "StructuredReportRecorded"
EVALUATION_RESULT_REPLAYED = "EvaluationResultReplayed"
EVALUATION_QUEUED = "EvaluationQueued"
EVALUATION_DATASET_MATERIALIZED = "EvaluationDatasetMaterialized"
EVALUATION_DATASET_CURATION_SUBMITTED = "EvaluationDatasetCurationSubmitted"
EVALUATION_DATASET_CURATION_REVIEWED = "EvaluationDatasetCurationReviewed"
EVALUATION_DATASET_CURATION_MATERIALIZED = "EvaluationDatasetCurationMaterialized"
EVALUATION_SAMPLING_RUN_CREATED = "EvaluationSamplingRunCreated"
EVALUATION_ANNOTATION_DISPATCH_REQUESTED = "EvaluationAnnotationDispatchRequested"
EVALUATION_ANNOTATION_DISPATCH_SYNCHRONIZED = "EvaluationAnnotationDispatchSynchronized"
EVALUATION_PROMOTION_RUN_CREATED = "EvaluationPromotionRunCreated"
EVALUATION_CASE_ROUTING_RUN_CREATED = "EvaluationCaseRoutingRunCreated"
EVALUATION_SEMANTIC_CLUSTERING_RUN_CREATED = "EvaluationSemanticClusteringRunCreated"
EVALUATION_SEMANTIC_REGRESSION_COMPARISON_CREATED = "EvaluationSemanticRegressionComparisonCreated"
EVALUATION_SEMANTIC_MONITOR_CREATED = "EvaluationSemanticMonitorCreated"
EVALUATION_SEMANTIC_MONITOR_RUN_QUEUED = "EvaluationSemanticMonitorRunQueued"
EVALUATION_SEMANTIC_MONITOR_RUN_COMPLETED = "EvaluationSemanticMonitorRunCompleted"
EVALUATION_SEMANTIC_MONITOR_ALERT_OPENED = "EvaluationSemanticMonitorAlertOpened"
EVALUATION_SEMANTIC_MONITOR_ALERT_ACKNOWLEDGED = "EvaluationSemanticMonitorAlertAcknowledged"
EVALUATION_EXPERIENCE_EXTRACTION_RUN_CREATED = "EvaluationExperienceExtractionRunCreated"
EVALUATION_EXPERIENCE_CANDIDATE_REVIEWED = "EvaluationExperienceCandidateReviewed"
EVALUATION_EXPERIENCE_ASSET_VERSION_CREATED = "EvaluationExperienceAssetVersionCreated"
EVALUATION_EXPERIENCE_ACTIVATION_REQUESTED = "EvaluationExperienceActivationRequested"
EVALUATION_EXPERIENCE_ACTIVATION_REVIEWED = "EvaluationExperienceActivationReviewed"
EVALUATION_RESULT_MANIFEST_REGISTERED = "EvaluationResultManifestRegistered"
EVALUATION_COMPARISON_CREATED = "EvaluationComparisonCreated"
RELEASE_CANDIDATE_EVALUATION_BOUND = "ReleaseCandidateEvaluationBound"
RELEASE_CANDIDATE_EVALUATION_REVIEWED = "ReleaseCandidateEvaluationReviewed"
AGENT_PACKAGE_CREATED = "AgentPackageCreated"
PACKAGE_SIGNING_KEY_REGISTERED = "PackageSigningKeyRegistered"
PACKAGE_SIGNING_KEY_REVOKED = "PackageSigningKeyRevoked"
PACKAGE_SIGNING_KEY_ROTATED = "PackageSigningKeyRotated"
AGENT_PACKAGE_VERSION_REGISTERED = "AgentPackageVersionRegistered"
RELEASE_POLICY_VERSION_CREATED = "ReleasePolicyVersionCreated"
RELEASE_CANDIDATE_CREATED = "ReleaseCandidateCreated"
RELEASE_POLICY_DECISION_RECORDED = "ReleasePolicyDecisionRecorded"
RELEASE_CANDIDATE_APPROVAL_RECORDED = "ReleaseCandidateApprovalRecorded"
RELEASE_POLICY_EXCEPTION_REQUESTED = "ReleasePolicyExceptionRequested"
RELEASE_POLICY_EXCEPTION_REVIEWED = "ReleasePolicyExceptionReviewed"
RELEASE_PROMOTION_DISPATCHED = "ReleasePromotionDispatched"
RELEASE_RECEIPT_RECORDED = "ReleaseReceiptRecorded"
RELEASE_ENVIRONMENT_ACTIVATED = "ReleaseEnvironmentActivated"
RELEASE_CANARY_EVALUATED = "ReleaseCanaryEvaluated"
RELEASE_ROLLBACK_DISPATCHED = "ReleaseRollbackDispatched"
HANDOVER_SNAPSHOT_CREATED = "HandoverSnapshotCreated"
HANDOVER_OBLIGATION_RECEIPT_RECORDED = "HandoverObligationReceiptRecorded"
HANDOVER_ACCEPTANCE_RECORDED = "HandoverAcceptanceRecorded"
HANDOVER_SIGNED_PACKAGE_CREATED = "HandoverSignedPackageCreated"
DIRECTORY_USER_DISABLED = "DirectoryUserDisabled"
WORKLOAD_IDENTITY_ISSUED = "WorkloadIdentityIssued"
WORKLOAD_IDENTITY_ROTATED = "WorkloadIdentityRotated"
WORKLOAD_IDENTITY_REVOKED = "WorkloadIdentityRevoked"

EVENT_PAYLOAD_FIELDS: dict[str, frozenset[str]] = {
    AGENT_SESSION_STARTED: frozenset(
        {
            "namespace_id",
            "runtime_id",
            "session_public_id",
            "status",
            "started_at",
            "content_capture_mode",
            "sensitivity",
        }
    ),
    AGENT_SESSION_COMPLETED: frozenset(
        {
            "namespace_id",
            "runtime_id",
            "session_public_id",
            "status",
            "started_at",
            "ended_at",
            "run_count",
            "error_count",
        }
    ),
    AGENT_RUN_REGISTERED: frozenset(
        {
            "namespace_id",
            "runtime_id",
            "run_public_id",
            "session_public_id",
            "status",
            "trust_level",
            "trust_source",
            "source_schema",
            "source_schema_version",
            "started_at",
            "attempt",
        }
    ),
    AGENT_RUN_COMPLETED: frozenset(
        {
            "namespace_id",
            "runtime_id",
            "run_public_id",
            "status",
            "started_at",
            "ended_at",
            "duration_ms",
            "step_count",
            "model_call_count",
            "tool_call_count",
            "input_token_count",
            "output_token_count",
            "error_type",
        }
    ),
    AGENT_RUN_TRUST_DEGRADED: frozenset(
        {
            "namespace_id",
            "runtime_id",
            "run_public_id",
            "trust_level",
            "trust_source",
            "reason_code",
        }
    ),
    TRACE_ARTIFACT_STORED: frozenset(
        {
            "namespace_id",
            "run_public_id",
            "artifact_public_id",
            "kind",
            "schema_name",
            "schema_version",
            "sha256",
            "size_bytes",
            "sensitivity",
            "completeness",
        }
    ),
    STRUCTURED_REPORT_RECORDED: frozenset(
        {
            "namespace_id",
            "runtime_id",
            "work_trace_id",
            "report_id",
            "report_type",
            "started_at",
            "ended_at",
        }
    ),
    EVALUATION_RESULT_REPLAYED: frozenset(
        {
            "namespace_id",
            "runtime_id",
            "run_public_id",
            "artifact_public_id",
            "schema_name",
            "schema_version",
            "sha256",
            "size_bytes",
            "sensitivity",
            "completeness",
        }
    ),
    EVALUATION_QUEUED: frozenset(
        {
            "namespace_id",
            "evaluation_public_id",
            "experiment_public_id",
            "evaluator_version_public_id",
            "status",
            "available_at",
        }
    ),
    EVALUATION_DATASET_MATERIALIZED: frozenset(
        {
            "namespace_id",
            "materialization_public_id",
            "dataset_public_id",
            "dataset_version_public_id",
            "source_type",
            "source_trace_ref",
            "source_observation_ref",
            "provider_dataset_item_ref",
            "provider_version_ref",
            "manifest_digest",
            "item_count",
            "schema_name",
            "schema_version",
        }
    ),
    EVALUATION_DATASET_CURATION_SUBMITTED: frozenset(
        {
            "namespace_id",
            "batch_public_id",
            "dataset_public_id",
            "selection_digest",
            "item_count",
            "schema_name",
            "schema_version",
        }
    ),
    EVALUATION_DATASET_CURATION_REVIEWED: frozenset(
        {
            "namespace_id",
            "review_public_id",
            "batch_public_id",
            "dataset_public_id",
            "decision",
            "review_digest",
            "item_count",
        }
    ),
    EVALUATION_DATASET_CURATION_MATERIALIZED: frozenset(
        {
            "namespace_id",
            "materialization_public_id",
            "batch_public_id",
            "dataset_public_id",
            "dataset_version_public_id",
            "provider_version_ref",
            "manifest_digest",
            "item_count",
        }
    ),
    EVALUATION_SAMPLING_RUN_CREATED: frozenset(
        {
            "namespace_id",
            "sampling_run_public_id",
            "policy_public_id",
            "policy_version_public_id",
            "dataset_public_id",
            "curation_batch_public_id",
            "strategy",
            "from_start_time",
            "to_start_time",
            "candidate_count",
            "eligible_count",
            "selected_count",
            "selection_digest",
            "run_digest",
            "schema_name",
            "schema_version",
        }
    ),
    EVALUATION_ANNOTATION_DISPATCH_REQUESTED: frozenset(
        {
            "namespace_id",
            "dispatch_public_id",
            "binding_public_id",
            "provider_queue_ref",
            "curation_batch_public_id",
            "sampling_run_public_id",
            "request_digest",
            "item_count",
            "schema_name",
            "schema_version",
        }
    ),
    EVALUATION_ANNOTATION_DISPATCH_SYNCHRONIZED: frozenset(
        {
            "namespace_id",
            "dispatch_public_id",
            "binding_public_id",
            "provider_queue_ref",
            "curation_batch_public_id",
            "sampling_run_public_id",
            "request_digest",
            "status",
            "item_count",
            "synced_count",
            "completed_count",
            "attempt_count",
            "schema_name",
            "schema_version",
            "synced_at",
        }
    ),
    EVALUATION_PROMOTION_RUN_CREATED: frozenset(
        {
            "namespace_id",
            "promotion_run_public_id",
            "policy_public_id",
            "policy_version_public_id",
            "binding_public_id",
            "dispatch_public_id",
            "curation_batch_public_id",
            "request_digest",
            "evidence_digest",
            "outcome",
            "reason_codes_csv",
            "item_count",
            "completed_count",
            "scored_count",
            "passed_count",
            "distinct_bucket_count",
            "schema_name",
            "schema_version",
        }
    ),
    EVALUATION_CASE_ROUTING_RUN_CREATED: frozenset(
        {
            "namespace_id",
            "routing_run_public_id",
            "policy_public_id",
            "policy_version_public_id",
            "source_promotion_policy_version_public_id",
            "golden_dataset_public_id",
            "bad_case_dataset_public_id",
            "golden_curation_batch_public_id",
            "bad_case_curation_batch_public_id",
            "request_digest",
            "evidence_digest",
            "routing_digest",
            "outcome",
            "reason_codes_csv",
            "source_run_count",
            "candidate_count",
            "golden_candidate_count",
            "bad_case_candidate_count",
            "excluded_count",
            "golden_selected_count",
            "bad_case_selected_count",
            "golden_cluster_count",
            "bad_case_cluster_count",
            "schema_name",
            "schema_version",
        }
    ),
    EVALUATION_SEMANTIC_CLUSTERING_RUN_CREATED: frozenset(
        {
            "namespace_id",
            "clustering_run_public_id",
            "policy_public_id",
            "policy_version_public_id",
            "source_case_routing_run_public_id",
            "embedding_profile",
            "model_ref",
            "dimensions",
            "similarity_threshold",
            "request_digest",
            "evidence_digest",
            "clustering_digest",
            "outcome",
            "reason_codes_csv",
            "source_item_count",
            "cluster_count",
            "eligible_cluster_count",
            "schema_name",
            "schema_version",
        }
    ),
    EVALUATION_SEMANTIC_REGRESSION_COMPARISON_CREATED: frozenset(
        {
            "namespace_id",
            "comparison_public_id",
            "baseline_run_public_id",
            "candidate_run_public_id",
            "policy_public_id",
            "policy_version_public_id",
            "outcome",
            "reason_codes_csv",
            "source_item_count",
            "pairwise_assignment_agreement",
            "cluster_count_change_ratio",
            "eligible_cluster_ratio_drop",
            "mean_centroid_similarity_drop",
            "assignment_agreement_breached",
            "cluster_count_change_breached",
            "eligible_cluster_ratio_drop_breached",
            "centroid_similarity_drop_breached",
            "reproducibility_digest",
            "schema_name",
            "schema_version",
        }
    ),
    EVALUATION_SEMANTIC_MONITOR_CREATED: frozenset(
        {
            "namespace_id",
            "monitor_public_id",
            "baseline_run_public_id",
            "candidate_policy_version_public_id",
            "regression_policy_version_public_id",
            "interval_seconds",
            "config_digest",
            "status",
            "next_run_at",
        }
    ),
    EVALUATION_SEMANTIC_MONITOR_RUN_QUEUED: frozenset(
        {
            "namespace_id",
            "monitor_run_public_id",
            "monitor_public_id",
            "scheduled_for",
            "status",
        }
    ),
    EVALUATION_SEMANTIC_MONITOR_RUN_COMPLETED: frozenset(
        {
            "namespace_id",
            "monitor_run_public_id",
            "monitor_public_id",
            "candidate_run_public_id",
            "comparison_public_id",
            "outcome",
            "attempt_count",
            "finished_at",
        }
    ),
    EVALUATION_SEMANTIC_MONITOR_ALERT_OPENED: frozenset(
        {
            "namespace_id",
            "alert_public_id",
            "monitor_public_id",
            "monitor_run_public_id",
            "comparison_public_id",
            "severity",
            "status",
            "reason_codes_csv",
            "error_code",
        }
    ),
    EVALUATION_SEMANTIC_MONITOR_ALERT_ACKNOWLEDGED: frozenset(
        {
            "namespace_id",
            "alert_public_id",
            "monitor_public_id",
            "monitor_run_public_id",
            "status",
            "acknowledged_by_user_id",
            "acknowledged_at",
        }
    ),
    EVALUATION_EXPERIENCE_EXTRACTION_RUN_CREATED: frozenset(
        {
            "namespace_id",
            "extraction_run_public_id",
            "policy_public_id",
            "policy_version_public_id",
            "source_case_routing_run_public_id",
            "source_semantic_clustering_run_public_id",
            "request_digest",
            "evidence_digest",
            "extraction_digest",
            "outcome",
            "reason_codes_csv",
            "source_bad_case_count",
            "cluster_count",
            "eligible_cluster_count",
            "candidate_count",
            "schema_name",
            "schema_version",
        }
    ),
    EVALUATION_EXPERIENCE_CANDIDATE_REVIEWED: frozenset(
        {
            "namespace_id",
            "candidate_public_id",
            "extraction_run_public_id",
            "category",
            "status",
            "decision",
            "cluster_digest",
            "evidence_digest",
            "review_digest",
            "source_item_count",
            "source_run_count",
        }
    ),
    EVALUATION_EXPERIENCE_ASSET_VERSION_CREATED: frozenset(
        {
            "namespace_id",
            "asset_public_id",
            "version_public_id",
            "source_candidate_public_id",
            "version",
            "status",
            "content_digest",
            "source_evidence_digest",
            "schema_name",
            "schema_version",
        }
    ),
    EVALUATION_EXPERIENCE_ACTIVATION_REQUESTED: frozenset(
        {
            "namespace_id",
            "asset_public_id",
            "version_public_id",
            "activation_request_public_id",
            "version",
            "status",
            "content_digest",
            "request_digest",
        }
    ),
    EVALUATION_EXPERIENCE_ACTIVATION_REVIEWED: frozenset(
        {
            "namespace_id",
            "asset_public_id",
            "version_public_id",
            "activation_request_public_id",
            "activation_review_public_id",
            "version",
            "status",
            "decision",
            "content_digest",
            "request_digest",
            "review_digest",
        }
    ),
    EVALUATION_RESULT_MANIFEST_REGISTERED: frozenset(
        {
            "namespace_id",
            "evaluation_public_id",
            "result_manifest_public_id",
            "provider",
            "provider_dataset_ref",
            "provider_experiment_ref",
            "schema_name",
            "schema_version",
            "content_digest",
            "score",
            "expected_count",
            "processed_count",
            "scored_count",
            "passed_count",
            "failed_count",
            "error_count",
            "completeness",
            "loss_reason",
        }
    ),
    EVALUATION_COMPARISON_CREATED: frozenset(
        {
            "namespace_id",
            "comparison_public_id",
            "baseline_evaluation_public_id",
            "baseline_manifest_public_id",
            "candidate_evaluation_public_id",
            "candidate_manifest_public_id",
            "policy_version_public_id",
            "outcome",
            "reason_code",
            "baseline_score",
            "candidate_score",
            "score_delta",
            "baseline_pass_rate",
            "candidate_pass_rate",
            "pass_rate_delta",
            "score_floor_breached",
            "score_drop_breached",
            "pass_rate_drop_breached",
            "reproducibility_digest",
        }
    ),
    RELEASE_CANDIDATE_EVALUATION_BOUND: frozenset(
        {
            "namespace_id",
            "binding_public_id",
            "release_candidate_ref",
            "deployment_public_id",
            "deployment_revision",
            "deployment_configuration_digest",
            "evaluation_comparison_public_id",
            "comparison_outcome",
            "comparison_reproducibility_digest",
            "binding_digest",
        }
    ),
    RELEASE_CANDIDATE_EVALUATION_REVIEWED: frozenset(
        {
            "namespace_id",
            "review_public_id",
            "binding_public_id",
            "release_candidate_ref",
            "deployment_public_id",
            "deployment_revision",
            "decision",
            "review_digest",
            "reviewed_by_user_id",
        }
    ),
    AGENT_PACKAGE_CREATED: frozenset(
        {
            "namespace_id",
            "package_public_id",
            "name",
            "agent_asset_ref",
            "status",
            "created_at",
        }
    ),
    PACKAGE_SIGNING_KEY_REGISTERED: frozenset(
        {
            "namespace_id",
            "signing_key_public_id",
            "key_id",
            "algorithm",
            "public_key_fingerprint",
            "status",
            "created_at",
        }
    ),
    PACKAGE_SIGNING_KEY_REVOKED: frozenset(
        {
            "namespace_id",
            "signing_key_public_id",
            "key_id",
            "public_key_fingerprint",
            "status",
            "revoked_at",
        }
    ),
    PACKAGE_SIGNING_KEY_ROTATED: frozenset(
        {
            "namespace_id",
            "previous_signing_key_public_id",
            "successor_signing_key_public_id",
            "previous_fingerprint",
            "successor_fingerprint",
            "rotation_sequence",
            "rotated_at",
        }
    ),
    AGENT_PACKAGE_VERSION_REGISTERED: frozenset(
        {
            "namespace_id",
            "package_public_id",
            "package_version_public_id",
            "version",
            "status",
            "manifest_digest",
            "graph_digest",
            "provenance_digest",
            "signing_key_public_id",
            "public_key_fingerprint",
            "signature_digest",
            "sbom_public_id",
            "sbom_document_sha256",
            "component_count",
            "dependency_count",
            "sbom_component_count",
            "signature_verified_at",
        }
    ),
    RELEASE_POLICY_VERSION_CREATED: frozenset(
        {
            "namespace_id",
            "policy_version_public_id",
            "version",
            "target_environment_public_id",
            "mode",
            "rule_count",
            "rules_digest",
            "content_digest",
            "schema_name",
            "schema_version",
        }
    ),
    RELEASE_CANDIDATE_CREATED: frozenset(
        {
            "namespace_id",
            "candidate_public_id",
            "package_version_public_id",
            "package_manifest_digest",
            "deployment_public_id",
            "deployment_revision",
            "deployment_configuration_digest",
            "target_environment_public_id",
            "baseline_candidate_public_id",
            "candidate_digest",
            "schema_name",
            "schema_version",
        }
    ),
    RELEASE_POLICY_DECISION_RECORDED: frozenset(
        {
            "namespace_id",
            "decision_public_id",
            "candidate_public_id",
            "policy_version_public_id",
            "policy_mode",
            "raw_outcome",
            "enforcement_outcome",
            "would_block",
            "reason_codes_csv",
            "rule_count",
            "evidence_snapshot_digest",
            "decision_digest",
            "evaluation_duration_ms",
            "schema_name",
            "schema_version",
        }
    ),
    RELEASE_CANDIDATE_APPROVAL_RECORDED: frozenset(
        {
            "namespace_id",
            "approval_public_id",
            "candidate_public_id",
            "policy_decision_public_id",
            "decision",
            "role",
            "approval_digest",
            "reviewed_by_user_id",
        }
    ),
    RELEASE_POLICY_EXCEPTION_REQUESTED: frozenset(
        {
            "namespace_id",
            "exception_public_id",
            "candidate_public_id",
            "policy_decision_public_id",
            "waived_rule_ids_csv",
            "waived_rule_count",
            "expires_at",
            "exception_digest",
        }
    ),
    RELEASE_POLICY_EXCEPTION_REVIEWED: frozenset(
        {
            "namespace_id",
            "review_public_id",
            "exception_public_id",
            "decision",
            "review_digest",
            "reviewed_by_user_id",
        }
    ),
    RELEASE_PROMOTION_DISPATCHED: frozenset(
        {
            "namespace_id",
            "promotion_public_id",
            "dispatch_public_id",
            "candidate_public_id",
            "policy_decision_public_id",
            "source_environment_public_id",
            "target_environment_public_id",
            "runtime_id",
            "strategy",
            "status",
            "exception_digest",
            "approval_digest",
            "dispatch_digest",
        }
    ),
    RELEASE_RECEIPT_RECORDED: frozenset(
        {
            "namespace_id",
            "receipt_public_id",
            "dispatch_public_id",
            "kind",
            "runtime_id",
            "reporter_credential_id",
            "status",
            "observed_package_version_public_id",
            "observed_deployment_revision",
            "observed_configuration_digest",
            "runtime_release_ref",
            "error_code",
            "receipt_digest",
            "occurred_at",
        }
    ),
    RELEASE_ENVIRONMENT_ACTIVATED: frozenset(
        {
            "namespace_id",
            "environment_release_public_id",
            "environment_public_id",
            "candidate_public_id",
            "promotion_public_id",
            "rollback_public_id",
            "receipt_public_id",
            "previous_release_public_id",
            "status",
            "activation_digest",
            "activated_at",
        }
    ),
    RELEASE_CANARY_EVALUATED: frozenset(
        {
            "namespace_id",
            "canary_evaluation_public_id",
            "promotion_public_id",
            "outcome",
            "reason_codes_csv",
            "completed_run_count",
            "failed_run_count",
            "untrusted_run_count",
            "failure_rate",
            "untrusted_rate",
            "evidence_digest",
            "decision_digest",
        }
    ),
    RELEASE_ROLLBACK_DISPATCHED: frozenset(
        {
            "namespace_id",
            "rollback_public_id",
            "dispatch_public_id",
            "promotion_public_id",
            "source_candidate_public_id",
            "target_environment_release_public_id",
            "target_candidate_public_id",
            "runtime_id",
            "reason_code",
            "status",
            "dispatch_digest",
        }
    ),
    HANDOVER_SNAPSHOT_CREATED: frozenset(
        {
            "namespace_id",
            "snapshot_public_id",
            "handover_case_id",
            "sequence",
            "readiness_outcome",
            "node_count",
            "edge_count",
            "check_count",
            "snapshot_digest",
        }
    ),
    HANDOVER_OBLIGATION_RECEIPT_RECORDED: frozenset(
        {
            "namespace_id",
            "obligation_public_id",
            "receipt_public_id",
            "decision",
            "evidence_count",
            "receipt_digest",
            "decided_by_user_id",
        }
    ),
    HANDOVER_ACCEPTANCE_RECORDED: frozenset(
        {
            "namespace_id",
            "acceptance_public_id",
            "snapshot_public_id",
            "handover_case_id",
            "decision",
            "acknowledges_failures",
            "obligation_receipt_count",
            "acceptance_digest",
            "accepted_by_user_id",
        }
    ),
    HANDOVER_SIGNED_PACKAGE_CREATED: frozenset(
        {
            "namespace_id",
            "package_public_id",
            "snapshot_public_id",
            "acceptance_public_id",
            "handover_case_id",
            "evidence_item_id",
            "signing_key_public_id",
            "manifest_digest",
            "archive_digest",
            "signature_digest",
            "attestation_digest",
        }
    ),
    DIRECTORY_USER_DISABLED: frozenset(
        {
            "namespace_id",
            "directory_event_public_id",
            "provider_id",
            "user_id",
            "action",
            "credentials_revoked",
            "runtime_tokens_revoked",
            "memberships_removed",
            "role_bindings_removed",
            "handover_case_id",
            "outcome_digest",
        }
    ),
    WORKLOAD_IDENTITY_ISSUED: frozenset(
        {
            "namespace_id",
            "workload_identity_public_id",
            "runtime_id",
            "principal_kind",
            "generation",
            "scope_count",
            "expires_at",
        }
    ),
    WORKLOAD_IDENTITY_ROTATED: frozenset(
        {
            "namespace_id",
            "previous_identity_public_id",
            "successor_identity_public_id",
            "runtime_id",
            "principal_kind",
            "generation",
            "scope_count",
            "rotated_at",
        }
    ),
    WORKLOAD_IDENTITY_REVOKED: frozenset(
        {
            "namespace_id",
            "workload_identity_public_id",
            "runtime_id",
            "principal_kind",
            "generation",
            "revoked_at",
        }
    ),
}
EVENT_REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    event_type: fields for event_type, fields in EVENT_PAYLOAD_FIELDS.items()
}

_FORBIDDEN_PAYLOAD_KEYS = {
    "prompt",
    "completion",
    "messages",
    "toolarguments",
    "toolresult",
    "retrievedcontent",
    "chainofthought",
    "rawcontent",
    "payload",
    "secret",
    "token",
    "authorization",
    "credential",
    "stacktrace",
    "spanevents",
    "filebody",
    "screenshot",
    "audio",
}
_SAFE_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,255}$")


class OutboxEventError(ValueError):
    pass


class OutboxPayloadError(OutboxEventError):
    pass


class OutboxConflictError(OutboxEventError):
    pass


@dataclass(frozen=True, slots=True)
class DomainEvent:
    namespace_id: int
    aggregate_type: str
    aggregate_public_id: str
    event_type: str
    idempotency_key: str
    occurred_at: datetime
    payload: dict[str, Any]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _utc(value).isoformat().replace("+00:00", "Z")


def _normalized_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def serialize_event_payload(
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    allowed_fields = EVENT_PAYLOAD_FIELDS.get(event_type)
    if allowed_fields is None:
        raise OutboxPayloadError("event_type is not allowlisted")
    if len(payload) > MAX_OUTBOX_PAYLOAD_KEYS:
        raise OutboxPayloadError("outbox payload has too many fields")
    unsupported = set(payload) - allowed_fields
    if unsupported:
        raise OutboxPayloadError(f"outbox payload contains unsupported fields: {sorted(unsupported)}")
    missing = EVENT_REQUIRED_FIELDS[event_type] - set(payload)
    if missing:
        raise OutboxPayloadError(f"outbox payload is missing required fields: {sorted(missing)}")
    for key, value in payload.items():
        if _normalized_key(key) in _FORBIDDEN_PAYLOAD_KEYS:
            raise OutboxPayloadError("outbox payload contains a sensitive field")
        if isinstance(value, (dict, list, tuple, set, bytes, bytearray)):
            raise OutboxPayloadError("outbox payload values must be scalar")
        if isinstance(value, str) and len(value) > 512:
            raise OutboxPayloadError("outbox payload string exceeds 512 characters")
        if not isinstance(value, (str, int, float, bool, type(None))):
            raise OutboxPayloadError("outbox payload contains an unsupported value")
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > MAX_OUTBOX_PAYLOAD_BYTES:
        raise OutboxPayloadError("outbox payload exceeds 16 KiB")
    return json.loads(encoded.decode("utf-8"))


def _event(
    *,
    namespace_id: int,
    aggregate_type: str,
    aggregate_public_id: str,
    event_type: str,
    occurred_at: datetime,
    payload: dict[str, Any],
) -> DomainEvent:
    idempotency_key = f"{aggregate_type}:{aggregate_public_id}:{event_type}"
    if _SAFE_IDEMPOTENCY_KEY.fullmatch(idempotency_key) is None:
        raise OutboxPayloadError("event idempotency key is invalid")
    return DomainEvent(
        namespace_id=namespace_id,
        aggregate_type=aggregate_type,
        aggregate_public_id=aggregate_public_id,
        event_type=event_type,
        idempotency_key=idempotency_key,
        occurred_at=_utc(occurred_at),
        payload=serialize_event_payload(event_type, payload),
    )


def build_agent_session_started(session: AgentSession) -> DomainEvent:
    return _event(
        namespace_id=session.namespace_id,
        aggregate_type="AgentSession",
        aggregate_public_id=session.public_id,
        event_type=AGENT_SESSION_STARTED,
        occurred_at=session.started_at,
        payload={
            "namespace_id": session.namespace_id,
            "runtime_id": session.runtime_id,
            "session_public_id": session.public_id,
            "status": session.status.value,
            "started_at": _iso(session.started_at),
            "content_capture_mode": session.content_capture_mode.value,
            "sensitivity": session.sensitivity.name,
        },
    )


def build_agent_session_completed(session: AgentSession) -> DomainEvent:
    return _event(
        namespace_id=session.namespace_id,
        aggregate_type="AgentSession",
        aggregate_public_id=session.public_id,
        event_type=AGENT_SESSION_COMPLETED,
        occurred_at=session.ended_at or session.updated_at,
        payload={
            "namespace_id": session.namespace_id,
            "runtime_id": session.runtime_id,
            "session_public_id": session.public_id,
            "status": session.status.value,
            "started_at": _iso(session.started_at),
            "ended_at": _iso(session.ended_at),
            "run_count": session.run_count,
            "error_count": session.error_count,
        },
    )


def build_agent_run_registered(
    run: AgentRun,
    *,
    session_public_id: str | None = None,
) -> DomainEvent:
    return _event(
        namespace_id=run.namespace_id,
        aggregate_type="AgentRun",
        aggregate_public_id=run.public_id,
        event_type=AGENT_RUN_REGISTERED,
        occurred_at=run.started_at,
        payload={
            "namespace_id": run.namespace_id,
            "runtime_id": run.runtime_id,
            "run_public_id": run.public_id,
            "session_public_id": session_public_id,
            "status": run.status.value,
            "trust_level": run.trust_level.value,
            "trust_source": run.trust_source.value,
            "source_schema": run.source_schema,
            "source_schema_version": run.source_schema_version,
            "started_at": _iso(run.started_at),
            "attempt": run.attempt,
        },
    )


def build_agent_run_completed(run: AgentRun) -> DomainEvent:
    return _event(
        namespace_id=run.namespace_id,
        aggregate_type="AgentRun",
        aggregate_public_id=run.public_id,
        event_type=AGENT_RUN_COMPLETED,
        occurred_at=run.ended_at or run.updated_at,
        payload={
            "namespace_id": run.namespace_id,
            "runtime_id": run.runtime_id,
            "run_public_id": run.public_id,
            "status": run.status.value,
            "started_at": _iso(run.started_at),
            "ended_at": _iso(run.ended_at),
            "duration_ms": run.duration_ms,
            "step_count": run.step_count,
            "model_call_count": run.model_call_count,
            "tool_call_count": run.tool_call_count,
            "input_token_count": run.input_token_count,
            "output_token_count": run.output_token_count,
            "error_type": run.error_type,
        },
    )


def build_agent_run_trust_degraded(
    run: AgentRun,
    *,
    reason_code: str,
) -> DomainEvent:
    return _event(
        namespace_id=run.namespace_id,
        aggregate_type="AgentRun",
        aggregate_public_id=run.public_id,
        event_type=AGENT_RUN_TRUST_DEGRADED,
        occurred_at=run.updated_at,
        payload={
            "namespace_id": run.namespace_id,
            "runtime_id": run.runtime_id,
            "run_public_id": run.public_id,
            "trust_level": run.trust_level.value,
            "trust_source": run.trust_source.value,
            "reason_code": reason_code,
        },
    )


def build_trace_artifact_stored(
    artifact: AgentRunArtifact,
    *,
    run_public_id: str,
) -> DomainEvent:
    return _event(
        namespace_id=artifact.namespace_id,
        aggregate_type="AgentRunArtifact",
        aggregate_public_id=artifact.public_id,
        event_type=TRACE_ARTIFACT_STORED,
        occurred_at=artifact.created_at,
        payload={
            "namespace_id": artifact.namespace_id,
            "run_public_id": run_public_id,
            "artifact_public_id": artifact.public_id,
            "kind": artifact.kind.value,
            "schema_name": artifact.schema_name,
            "schema_version": artifact.schema_version,
            "sha256": artifact.sha256,
            "size_bytes": artifact.size_bytes,
            "sensitivity": artifact.sensitivity.name,
            "completeness": artifact.completeness.value,
        },
    )


def build_structured_report_recorded(trace: WorkTrace) -> DomainEvent:
    metadata = trace.metadata_json if isinstance(trace.metadata_json, dict) else {}
    report_id = metadata.get("report_id")
    report_type = metadata.get("report_type")
    if trace.runtime_id is None:
        raise OutboxPayloadError("structured report WorkTrace requires runtime_id")
    if not isinstance(report_id, str) or not report_id:
        raise OutboxPayloadError("structured report WorkTrace requires report_id")
    if not isinstance(report_type, str) or not report_type:
        raise OutboxPayloadError("structured report WorkTrace requires report_type")
    return _event(
        namespace_id=trace.namespace_id,
        aggregate_type="WorkTrace",
        aggregate_public_id=str(trace.id),
        event_type=STRUCTURED_REPORT_RECORDED,
        occurred_at=(trace.ended_at or trace.started_at or trace.created_at),
        payload={
            "namespace_id": trace.namespace_id,
            "runtime_id": trace.runtime_id,
            "work_trace_id": trace.id,
            "report_id": report_id,
            "report_type": report_type,
            "started_at": _iso(trace.started_at),
            "ended_at": _iso(trace.ended_at),
        },
    )


def build_evaluation_queued(
    *,
    namespace_id: int,
    evaluation_public_id: str,
    experiment_public_id: str,
    evaluator_version_public_id: str,
    status: str,
    available_at: datetime,
) -> DomainEvent:
    return _event(
        namespace_id=namespace_id,
        aggregate_type="Evaluation",
        aggregate_public_id=evaluation_public_id,
        event_type=EVALUATION_QUEUED,
        occurred_at=available_at,
        payload={
            "namespace_id": namespace_id,
            "evaluation_public_id": evaluation_public_id,
            "experiment_public_id": experiment_public_id,
            "evaluator_version_public_id": evaluator_version_public_id,
            "status": status,
            "available_at": _iso(available_at),
        },
    )


def build_evaluation_dataset_materialized(
    materialization: EvaluationDatasetMaterialization,
) -> DomainEvent:
    dataset = materialization.dataset
    version = materialization.dataset_version
    return _event(
        namespace_id=materialization.namespace_id,
        aggregate_type="EvaluationDatasetMaterialization",
        aggregate_public_id=materialization.public_id,
        event_type=EVALUATION_DATASET_MATERIALIZED,
        occurred_at=materialization.created_at,
        payload={
            "namespace_id": materialization.namespace_id,
            "materialization_public_id": materialization.public_id,
            "dataset_public_id": dataset.public_id,
            "dataset_version_public_id": version.public_id,
            "source_type": materialization.source_type.value,
            "source_trace_ref": materialization.source_trace_ref,
            "source_observation_ref": (materialization.source_observation_ref),
            "provider_dataset_item_ref": (materialization.provider_dataset_item_ref),
            "provider_version_ref": version.provider_version_ref,
            "manifest_digest": version.content_digest,
            "item_count": version.item_count,
            "schema_name": materialization.schema_name,
            "schema_version": materialization.schema_version,
        },
    )


def build_evaluation_dataset_curation_submitted(
    batch: EvaluationDatasetCurationBatch,
) -> DomainEvent:
    return _event(
        namespace_id=batch.namespace_id,
        aggregate_type="EvaluationDatasetCurationBatch",
        aggregate_public_id=batch.public_id,
        event_type=EVALUATION_DATASET_CURATION_SUBMITTED,
        occurred_at=batch.created_at,
        payload={
            "namespace_id": batch.namespace_id,
            "batch_public_id": batch.public_id,
            "dataset_public_id": batch.dataset.public_id,
            "selection_digest": batch.selection_digest,
            "item_count": batch.item_count,
            "schema_name": batch.schema_name,
            "schema_version": batch.schema_version,
        },
    )


def build_evaluation_dataset_curation_reviewed(
    review: EvaluationDatasetCurationReview,
) -> DomainEvent:
    batch = review.batch
    return _event(
        namespace_id=review.namespace_id,
        aggregate_type="EvaluationDatasetCurationReview",
        aggregate_public_id=review.public_id,
        event_type=EVALUATION_DATASET_CURATION_REVIEWED,
        occurred_at=review.created_at,
        payload={
            "namespace_id": review.namespace_id,
            "review_public_id": review.public_id,
            "batch_public_id": batch.public_id,
            "dataset_public_id": batch.dataset.public_id,
            "decision": review.decision.value,
            "review_digest": review.review_digest,
            "item_count": batch.item_count,
        },
    )


def build_evaluation_dataset_curation_materialized(
    materialization: EvaluationDatasetCurationMaterialization,
) -> DomainEvent:
    batch = materialization.batch
    version = materialization.dataset_version
    return _event(
        namespace_id=materialization.namespace_id,
        aggregate_type="EvaluationDatasetCurationMaterialization",
        aggregate_public_id=materialization.public_id,
        event_type=EVALUATION_DATASET_CURATION_MATERIALIZED,
        occurred_at=materialization.created_at,
        payload={
            "namespace_id": materialization.namespace_id,
            "materialization_public_id": materialization.public_id,
            "batch_public_id": batch.public_id,
            "dataset_public_id": batch.dataset.public_id,
            "dataset_version_public_id": version.public_id,
            "provider_version_ref": version.provider_version_ref,
            "manifest_digest": version.content_digest,
            "item_count": materialization.item_count,
        },
    )


def build_evaluation_sampling_run_created(
    run: EvaluationSamplingRun,
) -> DomainEvent:
    version = run.policy_version
    return _event(
        namespace_id=run.namespace_id,
        aggregate_type="EvaluationSamplingRun",
        aggregate_public_id=run.public_id,
        event_type=EVALUATION_SAMPLING_RUN_CREATED,
        occurred_at=run.created_at,
        payload={
            "namespace_id": run.namespace_id,
            "sampling_run_public_id": run.public_id,
            "policy_public_id": version.policy.public_id,
            "policy_version_public_id": version.public_id,
            "dataset_public_id": run.dataset.public_id,
            "curation_batch_public_id": run.curation_batch.public_id,
            "strategy": version.strategy.value,
            "from_start_time": _iso(run.from_start_time),
            "to_start_time": _iso(run.to_start_time),
            "candidate_count": run.candidate_count,
            "eligible_count": run.eligible_count,
            "selected_count": run.selected_count,
            "selection_digest": run.selection_digest,
            "run_digest": run.run_digest,
            "schema_name": run.schema_name,
            "schema_version": run.schema_version,
        },
    )


def build_evaluation_annotation_dispatch_requested(
    dispatch: EvaluationAnnotationDispatch,
) -> DomainEvent:
    return _event(
        namespace_id=dispatch.namespace_id,
        aggregate_type="EvaluationAnnotationDispatch",
        aggregate_public_id=dispatch.public_id,
        event_type=EVALUATION_ANNOTATION_DISPATCH_REQUESTED,
        occurred_at=dispatch.created_at,
        payload={
            "namespace_id": dispatch.namespace_id,
            "dispatch_public_id": dispatch.public_id,
            "binding_public_id": dispatch.binding.public_id,
            "provider_queue_ref": dispatch.provider_queue_ref,
            "curation_batch_public_id": dispatch.curation_batch.public_id,
            "sampling_run_public_id": (dispatch.sampling_run.public_id if dispatch.sampling_run is not None else None),
            "request_digest": dispatch.request_digest,
            "item_count": dispatch.item_count,
            "schema_name": dispatch.schema_name,
            "schema_version": dispatch.schema_version,
        },
    )


def build_evaluation_annotation_dispatch_synchronized(
    dispatch: EvaluationAnnotationDispatch,
) -> DomainEvent:
    return _event(
        namespace_id=dispatch.namespace_id,
        aggregate_type="EvaluationAnnotationDispatch",
        aggregate_public_id=dispatch.public_id,
        event_type=EVALUATION_ANNOTATION_DISPATCH_SYNCHRONIZED,
        occurred_at=dispatch.synced_at or dispatch.updated_at,
        payload={
            "namespace_id": dispatch.namespace_id,
            "dispatch_public_id": dispatch.public_id,
            "binding_public_id": dispatch.binding.public_id,
            "provider_queue_ref": dispatch.provider_queue_ref,
            "curation_batch_public_id": dispatch.curation_batch.public_id,
            "sampling_run_public_id": (dispatch.sampling_run.public_id if dispatch.sampling_run is not None else None),
            "request_digest": dispatch.request_digest,
            "status": dispatch.status.value,
            "item_count": dispatch.item_count,
            "synced_count": dispatch.synced_count,
            "completed_count": dispatch.completed_count,
            "attempt_count": dispatch.attempt_count,
            "schema_name": dispatch.schema_name,
            "schema_version": dispatch.schema_version,
            "synced_at": _iso(dispatch.synced_at),
        },
    )


def build_evaluation_promotion_run_created(
    run: EvaluationPromotionRun,
) -> DomainEvent:
    version = run.policy_version
    return _event(
        namespace_id=run.namespace_id,
        aggregate_type="EvaluationPromotionRun",
        aggregate_public_id=run.public_id,
        event_type=EVALUATION_PROMOTION_RUN_CREATED,
        occurred_at=run.created_at,
        payload={
            "namespace_id": run.namespace_id,
            "promotion_run_public_id": run.public_id,
            "policy_public_id": version.policy.public_id,
            "policy_version_public_id": version.public_id,
            "binding_public_id": version.binding.public_id,
            "dispatch_public_id": run.dispatch.public_id,
            "curation_batch_public_id": run.dispatch.curation_batch.public_id,
            "request_digest": run.request_digest,
            "evidence_digest": run.evidence_digest,
            "outcome": run.outcome.value,
            "reason_codes_csv": ",".join(run.reason_codes_json),
            "item_count": run.item_count,
            "completed_count": run.completed_count,
            "scored_count": run.scored_count,
            "passed_count": run.passed_count,
            "distinct_bucket_count": run.distinct_bucket_count,
            "schema_name": run.schema_name,
            "schema_version": run.schema_version,
        },
    )


def build_evaluation_case_routing_run_created(
    run: EvaluationCaseRoutingRun,
) -> DomainEvent:
    version = run.policy_version
    return _event(
        namespace_id=run.namespace_id,
        aggregate_type="EvaluationCaseRoutingRun",
        aggregate_public_id=run.public_id,
        event_type=EVALUATION_CASE_ROUTING_RUN_CREATED,
        occurred_at=run.created_at,
        payload={
            "namespace_id": run.namespace_id,
            "routing_run_public_id": run.public_id,
            "policy_public_id": version.policy.public_id,
            "policy_version_public_id": version.public_id,
            "source_promotion_policy_version_public_id": (version.source_promotion_policy_version.public_id),
            "golden_dataset_public_id": run.golden_dataset.public_id,
            "bad_case_dataset_public_id": run.bad_case_dataset.public_id,
            "golden_curation_batch_public_id": (
                run.golden_curation_batch.public_id if run.golden_curation_batch is not None else None
            ),
            "bad_case_curation_batch_public_id": (
                run.bad_case_curation_batch.public_id if run.bad_case_curation_batch is not None else None
            ),
            "request_digest": run.request_digest,
            "evidence_digest": run.evidence_digest,
            "routing_digest": run.routing_digest,
            "outcome": run.outcome.value,
            "reason_codes_csv": ",".join(run.reason_codes_json),
            "source_run_count": run.source_run_count,
            "candidate_count": run.candidate_count,
            "golden_candidate_count": run.golden_candidate_count,
            "bad_case_candidate_count": run.bad_case_candidate_count,
            "excluded_count": run.excluded_count,
            "golden_selected_count": run.golden_selected_count,
            "bad_case_selected_count": run.bad_case_selected_count,
            "golden_cluster_count": run.golden_cluster_count,
            "bad_case_cluster_count": run.bad_case_cluster_count,
            "schema_name": run.schema_name,
            "schema_version": run.schema_version,
        },
    )


def build_evaluation_semantic_clustering_run_created(
    run: EvaluationSemanticClusteringRun,
) -> DomainEvent:
    version = run.policy_version
    return _event(
        namespace_id=run.namespace_id,
        aggregate_type="EvaluationSemanticClusteringRun",
        aggregate_public_id=run.public_id,
        event_type=EVALUATION_SEMANTIC_CLUSTERING_RUN_CREATED,
        occurred_at=run.created_at,
        payload={
            "namespace_id": run.namespace_id,
            "clustering_run_public_id": run.public_id,
            "policy_public_id": version.policy.public_id,
            "policy_version_public_id": version.public_id,
            "source_case_routing_run_public_id": (run.source_case_routing_run.public_id),
            "embedding_profile": version.embedding_profile,
            "model_ref": version.model_ref,
            "dimensions": version.dimensions,
            "similarity_threshold": version.similarity_threshold,
            "request_digest": run.request_digest,
            "evidence_digest": run.evidence_digest,
            "clustering_digest": run.clustering_digest,
            "outcome": run.outcome.value,
            "reason_codes_csv": ",".join(run.reason_codes_json),
            "source_item_count": run.source_item_count,
            "cluster_count": run.cluster_count,
            "eligible_cluster_count": run.eligible_cluster_count,
            "schema_name": run.schema_name,
            "schema_version": run.schema_version,
        },
    )


def build_evaluation_semantic_regression_comparison_created(
    comparison: EvaluationSemanticRegressionComparison,
) -> DomainEvent:
    version = comparison.policy_version
    return _event(
        namespace_id=comparison.namespace_id,
        aggregate_type="EvaluationSemanticRegressionComparison",
        aggregate_public_id=comparison.public_id,
        event_type=EVALUATION_SEMANTIC_REGRESSION_COMPARISON_CREATED,
        occurred_at=comparison.created_at,
        payload={
            "namespace_id": comparison.namespace_id,
            "comparison_public_id": comparison.public_id,
            "baseline_run_public_id": comparison.baseline_run.public_id,
            "candidate_run_public_id": comparison.candidate_run.public_id,
            "policy_public_id": version.policy.public_id,
            "policy_version_public_id": version.public_id,
            "outcome": comparison.outcome.value,
            "reason_codes_csv": ",".join(comparison.reason_codes_json),
            "source_item_count": comparison.source_item_count,
            "pairwise_assignment_agreement": (comparison.pairwise_assignment_agreement),
            "cluster_count_change_ratio": comparison.cluster_count_change_ratio,
            "eligible_cluster_ratio_drop": comparison.eligible_cluster_ratio_drop,
            "mean_centroid_similarity_drop": (comparison.mean_centroid_similarity_drop),
            "assignment_agreement_breached": (comparison.assignment_agreement_breached),
            "cluster_count_change_breached": (comparison.cluster_count_change_breached),
            "eligible_cluster_ratio_drop_breached": (comparison.eligible_cluster_ratio_drop_breached),
            "centroid_similarity_drop_breached": (comparison.centroid_similarity_drop_breached),
            "reproducibility_digest": comparison.reproducibility_digest,
            "schema_name": comparison.schema_name,
            "schema_version": comparison.schema_version,
        },
    )


def build_evaluation_semantic_monitor_created(
    monitor: EvaluationSemanticMonitor,
) -> DomainEvent:
    return _event(
        namespace_id=monitor.namespace_id,
        aggregate_type="EvaluationSemanticMonitor",
        aggregate_public_id=monitor.public_id,
        event_type=EVALUATION_SEMANTIC_MONITOR_CREATED,
        occurred_at=monitor.created_at,
        payload={
            "namespace_id": monitor.namespace_id,
            "monitor_public_id": monitor.public_id,
            "baseline_run_public_id": monitor.baseline_run.public_id,
            "candidate_policy_version_public_id": monitor.candidate_policy_version.public_id,
            "regression_policy_version_public_id": monitor.regression_policy_version.public_id,
            "interval_seconds": monitor.interval_seconds,
            "config_digest": monitor.config_digest,
            "status": monitor.status.value,
            "next_run_at": _iso(monitor.next_run_at),
        },
    )


def build_evaluation_semantic_monitor_run_queued(
    run: EvaluationSemanticMonitorRun,
) -> DomainEvent:
    return _event(
        namespace_id=run.namespace_id,
        aggregate_type="EvaluationSemanticMonitorRun",
        aggregate_public_id=run.public_id,
        event_type=EVALUATION_SEMANTIC_MONITOR_RUN_QUEUED,
        occurred_at=run.created_at,
        payload={
            "namespace_id": run.namespace_id,
            "monitor_run_public_id": run.public_id,
            "monitor_public_id": run.monitor.public_id,
            "scheduled_for": _iso(run.scheduled_for),
            "status": run.status.value,
        },
    )


def build_evaluation_semantic_monitor_run_completed(
    run: EvaluationSemanticMonitorRun,
) -> DomainEvent:
    if run.candidate_run is None or run.comparison is None or run.outcome is None:
        raise ValueError("completed semantic monitor run requires result pins")
    return _event(
        namespace_id=run.namespace_id,
        aggregate_type="EvaluationSemanticMonitorRun",
        aggregate_public_id=run.public_id,
        event_type=EVALUATION_SEMANTIC_MONITOR_RUN_COMPLETED,
        occurred_at=run.finished_at or run.updated_at,
        payload={
            "namespace_id": run.namespace_id,
            "monitor_run_public_id": run.public_id,
            "monitor_public_id": run.monitor.public_id,
            "candidate_run_public_id": run.candidate_run.public_id,
            "comparison_public_id": run.comparison.public_id,
            "outcome": run.outcome.value,
            "attempt_count": run.attempt_count,
            "finished_at": _iso(run.finished_at),
        },
    )


def build_evaluation_semantic_monitor_alert_opened(
    alert: EvaluationSemanticMonitorAlert,
) -> DomainEvent:
    return _event(
        namespace_id=alert.namespace_id,
        aggregate_type="EvaluationSemanticMonitorAlert",
        aggregate_public_id=alert.public_id,
        event_type=EVALUATION_SEMANTIC_MONITOR_ALERT_OPENED,
        occurred_at=alert.created_at,
        payload={
            "namespace_id": alert.namespace_id,
            "alert_public_id": alert.public_id,
            "monitor_public_id": alert.monitor.public_id,
            "monitor_run_public_id": alert.monitor_run.public_id,
            "comparison_public_id": (
                alert.comparison.public_id if alert.comparison is not None else None
            ),
            "severity": alert.severity.value,
            "status": alert.status.value,
            "reason_codes_csv": ",".join(alert.reason_codes_json),
            "error_code": alert.error_code,
        },
    )


def build_evaluation_semantic_monitor_alert_acknowledged(
    alert: EvaluationSemanticMonitorAlert,
) -> DomainEvent:
    if alert.acknowledged_at is None or alert.acknowledged_by_user_id is None:
        raise ValueError("acknowledged semantic monitor alert requires actor and time")
    return _event(
        namespace_id=alert.namespace_id,
        aggregate_type="EvaluationSemanticMonitorAlert",
        aggregate_public_id=alert.public_id,
        event_type=EVALUATION_SEMANTIC_MONITOR_ALERT_ACKNOWLEDGED,
        occurred_at=alert.acknowledged_at,
        payload={
            "namespace_id": alert.namespace_id,
            "alert_public_id": alert.public_id,
            "monitor_public_id": alert.monitor.public_id,
            "monitor_run_public_id": alert.monitor_run.public_id,
            "status": alert.status.value,
            "acknowledged_by_user_id": alert.acknowledged_by_user_id,
            "acknowledged_at": _iso(alert.acknowledged_at),
        },
    )


def build_evaluation_experience_extraction_run_created(
    run: EvaluationExperienceExtractionRun,
) -> DomainEvent:
    version = run.policy_version
    return _event(
        namespace_id=run.namespace_id,
        aggregate_type="EvaluationExperienceExtractionRun",
        aggregate_public_id=run.public_id,
        event_type=EVALUATION_EXPERIENCE_EXTRACTION_RUN_CREATED,
        occurred_at=run.created_at,
        payload={
            "namespace_id": run.namespace_id,
            "extraction_run_public_id": run.public_id,
            "policy_public_id": version.policy.public_id,
            "policy_version_public_id": version.public_id,
            "source_case_routing_run_public_id": (run.source_case_routing_run.public_id),
            "source_semantic_clustering_run_public_id": (
                run.source_semantic_clustering_run.public_id if run.source_semantic_clustering_run is not None else None
            ),
            "request_digest": run.request_digest,
            "evidence_digest": run.evidence_digest,
            "extraction_digest": run.extraction_digest,
            "outcome": run.outcome.value,
            "reason_codes_csv": ",".join(run.reason_codes_json),
            "source_bad_case_count": run.source_bad_case_count,
            "cluster_count": run.cluster_count,
            "eligible_cluster_count": run.eligible_cluster_count,
            "candidate_count": run.candidate_count,
            "schema_name": run.schema_name,
            "schema_version": run.schema_version,
        },
    )


def build_evaluation_experience_candidate_reviewed(
    candidate: EvaluationExperienceCandidate,
) -> DomainEvent:
    review = candidate.review
    if review is None:
        raise ValueError("Experience candidate review is required")
    run = candidate.run
    return _event(
        namespace_id=run.namespace_id,
        aggregate_type="EvaluationExperienceCandidate",
        aggregate_public_id=candidate.public_id,
        event_type=EVALUATION_EXPERIENCE_CANDIDATE_REVIEWED,
        occurred_at=review.created_at,
        payload={
            "namespace_id": run.namespace_id,
            "candidate_public_id": candidate.public_id,
            "extraction_run_public_id": run.public_id,
            "category": candidate.category.value,
            "status": candidate.status.value,
            "decision": review.decision.value,
            "cluster_digest": candidate.cluster_digest,
            "evidence_digest": candidate.evidence_digest,
            "review_digest": review.review_digest,
            "source_item_count": candidate.source_item_count,
            "source_run_count": candidate.source_run_count,
        },
    )


def build_evaluation_experience_asset_version_created(
    asset: EvaluationExperienceAsset,
    version: EvaluationExperienceAssetVersion,
) -> DomainEvent:
    return _event(
        namespace_id=asset.namespace_id,
        aggregate_type="EvaluationExperienceAssetVersion",
        aggregate_public_id=version.public_id,
        event_type=EVALUATION_EXPERIENCE_ASSET_VERSION_CREATED,
        occurred_at=version.created_at,
        payload={
            "namespace_id": asset.namespace_id,
            "asset_public_id": asset.public_id,
            "version_public_id": version.public_id,
            "source_candidate_public_id": asset.source_candidate.public_id,
            "version": version.version,
            "status": version.status.value,
            "content_digest": version.content_digest,
            "source_evidence_digest": version.source_evidence_digest,
            "schema_name": version.schema_name,
            "schema_version": version.schema_version,
        },
    )


def build_evaluation_experience_activation_requested(
    version: EvaluationExperienceAssetVersion,
) -> DomainEvent:
    activation_request = version.activation_request
    if activation_request is None:
        raise ValueError("Experience activation request is required")
    asset = version.asset
    return _event(
        namespace_id=asset.namespace_id,
        aggregate_type="EvaluationExperienceActivationRequest",
        aggregate_public_id=activation_request.public_id,
        event_type=EVALUATION_EXPERIENCE_ACTIVATION_REQUESTED,
        occurred_at=activation_request.created_at,
        payload={
            "namespace_id": asset.namespace_id,
            "asset_public_id": asset.public_id,
            "version_public_id": version.public_id,
            "activation_request_public_id": activation_request.public_id,
            "version": version.version,
            "status": version.status.value,
            "content_digest": version.content_digest,
            "request_digest": activation_request.request_digest,
        },
    )


def build_evaluation_experience_activation_reviewed(
    version: EvaluationExperienceAssetVersion,
) -> DomainEvent:
    activation_request = version.activation_request
    review = activation_request.review if activation_request is not None else None
    if activation_request is None or review is None:
        raise ValueError("Experience activation review is required")
    asset = version.asset
    return _event(
        namespace_id=asset.namespace_id,
        aggregate_type="EvaluationExperienceActivationReview",
        aggregate_public_id=review.public_id,
        event_type=EVALUATION_EXPERIENCE_ACTIVATION_REVIEWED,
        occurred_at=review.created_at,
        payload={
            "namespace_id": asset.namespace_id,
            "asset_public_id": asset.public_id,
            "version_public_id": version.public_id,
            "activation_request_public_id": activation_request.public_id,
            "activation_review_public_id": review.public_id,
            "version": version.version,
            "status": version.status.value,
            "decision": review.decision.value,
            "content_digest": version.content_digest,
            "request_digest": activation_request.request_digest,
            "review_digest": review.review_digest,
        },
    )


def build_evaluation_result_manifest_registered(
    manifest: EvaluationResultManifest,
    *,
    evaluation_public_id: str,
) -> DomainEvent:
    return _event(
        namespace_id=manifest.namespace_id,
        aggregate_type="EvaluationResultManifest",
        aggregate_public_id=manifest.public_id,
        event_type=EVALUATION_RESULT_MANIFEST_REGISTERED,
        occurred_at=manifest.created_at,
        payload={
            "namespace_id": manifest.namespace_id,
            "evaluation_public_id": evaluation_public_id,
            "result_manifest_public_id": manifest.public_id,
            "provider": manifest.provider.value,
            "provider_dataset_ref": manifest.provider_dataset_ref,
            "provider_experiment_ref": manifest.provider_experiment_ref,
            "schema_name": manifest.schema_name,
            "schema_version": manifest.schema_version,
            "content_digest": manifest.content_digest,
            "score": manifest.score,
            "expected_count": manifest.expected_count,
            "processed_count": manifest.processed_count,
            "scored_count": manifest.scored_count,
            "passed_count": manifest.passed_count,
            "failed_count": manifest.failed_count,
            "error_count": manifest.error_count,
            "completeness": manifest.completeness.value,
            "loss_reason": manifest.loss_reason,
        },
    )


def build_evaluation_comparison_created(
    comparison: EvaluationComparison,
    *,
    baseline_evaluation_public_id: str,
    baseline_manifest_public_id: str,
    candidate_evaluation_public_id: str,
    candidate_manifest_public_id: str,
    policy_version_public_id: str,
) -> DomainEvent:
    return _event(
        namespace_id=comparison.namespace_id,
        aggregate_type="EvaluationComparison",
        aggregate_public_id=comparison.public_id,
        event_type=EVALUATION_COMPARISON_CREATED,
        occurred_at=comparison.created_at,
        payload={
            "namespace_id": comparison.namespace_id,
            "comparison_public_id": comparison.public_id,
            "baseline_evaluation_public_id": (baseline_evaluation_public_id),
            "baseline_manifest_public_id": baseline_manifest_public_id,
            "candidate_evaluation_public_id": (candidate_evaluation_public_id),
            "candidate_manifest_public_id": candidate_manifest_public_id,
            "policy_version_public_id": policy_version_public_id,
            "outcome": comparison.outcome.value,
            "reason_code": comparison.reason_code,
            "baseline_score": comparison.baseline_score,
            "candidate_score": comparison.candidate_score,
            "score_delta": comparison.score_delta,
            "baseline_pass_rate": comparison.baseline_pass_rate,
            "candidate_pass_rate": comparison.candidate_pass_rate,
            "pass_rate_delta": comparison.pass_rate_delta,
            "score_floor_breached": comparison.score_floor_breached,
            "score_drop_breached": comparison.score_drop_breached,
            "pass_rate_drop_breached": (comparison.pass_rate_drop_breached),
            "reproducibility_digest": comparison.reproducibility_digest,
        },
    )


def build_release_candidate_evaluation_bound(
    binding: ReleaseCandidateEvaluationBinding,
    *,
    evaluation_comparison_public_id: str,
    comparison_outcome: str,
    comparison_reproducibility_digest: str,
) -> DomainEvent:
    return _event(
        namespace_id=binding.namespace_id,
        aggregate_type="ReleaseCandidateEvaluationBinding",
        aggregate_public_id=binding.public_id,
        event_type=RELEASE_CANDIDATE_EVALUATION_BOUND,
        occurred_at=binding.created_at,
        payload={
            "namespace_id": binding.namespace_id,
            "binding_public_id": binding.public_id,
            "release_candidate_ref": binding.release_candidate_ref,
            "deployment_public_id": binding.deployment_public_id,
            "deployment_revision": binding.deployment_revision,
            "deployment_configuration_digest": (binding.deployment_configuration_digest),
            "evaluation_comparison_public_id": (evaluation_comparison_public_id),
            "comparison_outcome": comparison_outcome,
            "comparison_reproducibility_digest": (comparison_reproducibility_digest),
            "binding_digest": binding.binding_digest,
        },
    )


def build_release_candidate_evaluation_reviewed(
    review: ReleaseCandidateEvaluationReview,
    *,
    binding: ReleaseCandidateEvaluationBinding,
) -> DomainEvent:
    return _event(
        namespace_id=review.namespace_id,
        aggregate_type="ReleaseCandidateEvaluationReview",
        aggregate_public_id=review.public_id,
        event_type=RELEASE_CANDIDATE_EVALUATION_REVIEWED,
        occurred_at=review.created_at,
        payload={
            "namespace_id": review.namespace_id,
            "review_public_id": review.public_id,
            "binding_public_id": binding.public_id,
            "release_candidate_ref": binding.release_candidate_ref,
            "deployment_public_id": binding.deployment_public_id,
            "deployment_revision": binding.deployment_revision,
            "decision": review.decision.value,
            "review_digest": review.review_digest,
            "reviewed_by_user_id": review.reviewed_by_user_id,
        },
    )


def build_agent_package_created(package: AgentPackage) -> DomainEvent:
    return _event(
        namespace_id=package.namespace_id,
        aggregate_type="AgentPackage",
        aggregate_public_id=package.public_id,
        event_type=AGENT_PACKAGE_CREATED,
        occurred_at=package.created_at,
        payload={
            "namespace_id": package.namespace_id,
            "package_public_id": package.public_id,
            "name": package.name,
            "agent_asset_ref": package.agent_asset_ref,
            "status": package.status.value,
            "created_at": _iso(package.created_at),
        },
    )


def build_package_signing_key_registered(key: PackageSigningKey) -> DomainEvent:
    return _event(
        namespace_id=key.namespace_id,
        aggregate_type="PackageSigningKey",
        aggregate_public_id=key.public_id,
        event_type=PACKAGE_SIGNING_KEY_REGISTERED,
        occurred_at=key.created_at,
        payload={
            "namespace_id": key.namespace_id,
            "signing_key_public_id": key.public_id,
            "key_id": key.key_id,
            "algorithm": key.algorithm.value,
            "public_key_fingerprint": key.public_key_fingerprint,
            "status": key.status.value,
            "created_at": _iso(key.created_at),
        },
    )


def build_package_signing_key_revoked(key: PackageSigningKey) -> DomainEvent:
    if key.revoked_at is None:
        raise ValueError("revoked Package signing key requires revoked_at")
    return _event(
        namespace_id=key.namespace_id,
        aggregate_type="PackageSigningKey",
        aggregate_public_id=key.public_id,
        event_type=PACKAGE_SIGNING_KEY_REVOKED,
        occurred_at=key.revoked_at,
        payload={
            "namespace_id": key.namespace_id,
            "signing_key_public_id": key.public_id,
            "key_id": key.key_id,
            "public_key_fingerprint": key.public_key_fingerprint,
            "status": key.status.value,
            "revoked_at": _iso(key.revoked_at),
        },
    )


def build_package_signing_key_rotated(
    previous: PackageSigningKey, successor: PackageSigningKey
) -> DomainEvent:
    if previous.revoked_at is None:
        raise ValueError("rotated Package signing key requires predecessor revoked_at")
    return _event(
        namespace_id=successor.namespace_id,
        aggregate_type="PackageSigningKeyRotation",
        aggregate_public_id=successor.public_id,
        event_type=PACKAGE_SIGNING_KEY_ROTATED,
        occurred_at=previous.revoked_at,
        payload={
            "namespace_id": successor.namespace_id,
            "previous_signing_key_public_id": previous.public_id,
            "successor_signing_key_public_id": successor.public_id,
            "previous_fingerprint": previous.public_key_fingerprint,
            "successor_fingerprint": successor.public_key_fingerprint,
            "rotation_sequence": successor.rotation_sequence,
            "rotated_at": _iso(previous.revoked_at),
        },
    )


def build_agent_package_version_registered(version: AgentPackageVersion) -> DomainEvent:
    return _event(
        namespace_id=version.namespace_id,
        aggregate_type="AgentPackageVersion",
        aggregate_public_id=version.public_id,
        event_type=AGENT_PACKAGE_VERSION_REGISTERED,
        occurred_at=version.created_at,
        payload={
            "namespace_id": version.namespace_id,
            "package_public_id": version.package.public_id,
            "package_version_public_id": version.public_id,
            "version": version.version,
            "status": version.status.value,
            "manifest_digest": version.manifest_digest,
            "graph_digest": version.graph_digest,
            "provenance_digest": version.provenance_digest,
            "signing_key_public_id": version.signing_key.public_id,
            "public_key_fingerprint": version.signing_key.public_key_fingerprint,
            "signature_digest": version.signature_digest,
            "sbom_public_id": version.sbom.public_id,
            "sbom_document_sha256": version.sbom.document_sha256,
            "component_count": len(version.components),
            "dependency_count": len(version.dependencies),
            "sbom_component_count": version.sbom.component_count,
            "signature_verified_at": _iso(version.signature_verified_at),
        },
    )


def build_release_policy_version_created(version: ReleasePolicyVersion) -> DomainEvent:
    return _event(
        namespace_id=version.namespace_id,
        aggregate_type="ReleasePolicyVersion",
        aggregate_public_id=version.public_id,
        event_type=RELEASE_POLICY_VERSION_CREATED,
        occurred_at=version.created_at,
        payload={
            "namespace_id": version.namespace_id,
            "policy_version_public_id": version.public_id,
            "version": version.version,
            "target_environment_public_id": version.target_environment.public_id,
            "mode": version.mode.value,
            "rule_count": version.rule_count,
            "rules_digest": version.rules_digest,
            "content_digest": version.content_digest,
            "schema_name": version.schema_name,
            "schema_version": version.schema_version,
        },
    )


def build_release_candidate_created(candidate: ReleaseCandidate) -> DomainEvent:
    return _event(
        namespace_id=candidate.namespace_id,
        aggregate_type="ReleaseCandidate",
        aggregate_public_id=candidate.public_id,
        event_type=RELEASE_CANDIDATE_CREATED,
        occurred_at=candidate.created_at,
        payload={
            "namespace_id": candidate.namespace_id,
            "candidate_public_id": candidate.public_id,
            "package_version_public_id": candidate.package_version.public_id,
            "package_manifest_digest": candidate.package_version.manifest_digest,
            "deployment_public_id": candidate.deployment.public_id,
            "deployment_revision": candidate.deployment.revision,
            "deployment_configuration_digest": candidate.deployment.configuration_digest,
            "target_environment_public_id": candidate.target_environment.public_id,
            "baseline_candidate_public_id": (
                candidate.baseline_candidate.public_id
                if candidate.baseline_candidate is not None
                else None
            ),
            "candidate_digest": candidate.candidate_digest,
            "schema_name": candidate.schema_name,
            "schema_version": candidate.schema_version,
        },
    )


def build_release_policy_decision_recorded(decision: ReleasePolicyDecision) -> DomainEvent:
    return _event(
        namespace_id=decision.namespace_id,
        aggregate_type="ReleasePolicyDecision",
        aggregate_public_id=decision.public_id,
        event_type=RELEASE_POLICY_DECISION_RECORDED,
        occurred_at=decision.created_at,
        payload={
            "namespace_id": decision.namespace_id,
            "decision_public_id": decision.public_id,
            "candidate_public_id": decision.candidate.public_id,
            "policy_version_public_id": decision.policy_version.public_id,
            "policy_mode": decision.policy_version.mode.value,
            "raw_outcome": decision.raw_outcome.value,
            "enforcement_outcome": decision.enforcement_outcome.value,
            "would_block": decision.would_block,
            "reason_codes_csv": ",".join(decision.reason_codes_json),
            "rule_count": len(decision.rule_results),
            "evidence_snapshot_digest": decision.evidence_snapshot_digest,
            "decision_digest": decision.decision_digest,
            "evaluation_duration_ms": decision.evaluation_duration_ms,
            "schema_name": decision.schema_name,
            "schema_version": decision.schema_version,
        },
    )


def build_release_candidate_approval_recorded(approval: ReleaseCandidateApproval) -> DomainEvent:
    return _event(
        namespace_id=approval.namespace_id,
        aggregate_type="ReleaseCandidateApproval",
        aggregate_public_id=approval.public_id,
        event_type=RELEASE_CANDIDATE_APPROVAL_RECORDED,
        occurred_at=approval.created_at,
        payload={
            "namespace_id": approval.namespace_id,
            "approval_public_id": approval.public_id,
            "candidate_public_id": approval.candidate.public_id,
            "policy_decision_public_id": approval.policy_decision.public_id,
            "decision": approval.decision.value,
            "role": approval.role.value,
            "approval_digest": approval.approval_digest,
            "reviewed_by_user_id": approval.reviewed_by_user_id,
        },
    )


def build_release_policy_exception_requested(exception: ReleasePolicyException) -> DomainEvent:
    return _event(
        namespace_id=exception.namespace_id,
        aggregate_type="ReleasePolicyException",
        aggregate_public_id=exception.public_id,
        event_type=RELEASE_POLICY_EXCEPTION_REQUESTED,
        occurred_at=exception.created_at,
        payload={
            "namespace_id": exception.namespace_id,
            "exception_public_id": exception.public_id,
            "candidate_public_id": exception.candidate.public_id,
            "policy_decision_public_id": exception.policy_decision.public_id,
            "waived_rule_ids_csv": ",".join(exception.waived_rule_ids_json),
            "waived_rule_count": exception.waived_rule_count,
            "expires_at": _iso(exception.expires_at),
            "exception_digest": exception.exception_digest,
        },
    )


def build_release_policy_exception_reviewed(
    review: ReleasePolicyExceptionReview,
) -> DomainEvent:
    return _event(
        namespace_id=review.namespace_id,
        aggregate_type="ReleasePolicyExceptionReview",
        aggregate_public_id=review.public_id,
        event_type=RELEASE_POLICY_EXCEPTION_REVIEWED,
        occurred_at=review.created_at,
        payload={
            "namespace_id": review.namespace_id,
            "review_public_id": review.public_id,
            "exception_public_id": review.exception.public_id,
            "decision": review.decision.value,
            "review_digest": review.review_digest,
            "reviewed_by_user_id": review.reviewed_by_user_id,
        },
    )


def build_release_promotion_dispatched(promotion: ReleasePromotion) -> DomainEvent:
    return _event(
        namespace_id=promotion.namespace_id,
        aggregate_type="ReleasePromotion",
        aggregate_public_id=promotion.public_id,
        event_type=RELEASE_PROMOTION_DISPATCHED,
        occurred_at=promotion.created_at,
        payload={
            "namespace_id": promotion.namespace_id,
            "promotion_public_id": promotion.public_id,
            "dispatch_public_id": promotion.dispatch_public_id,
            "candidate_public_id": promotion.candidate.public_id,
            "policy_decision_public_id": promotion.policy_decision.public_id,
            "source_environment_public_id": (
                promotion.source_environment.public_id
                if promotion.source_environment is not None
                else None
            ),
            "target_environment_public_id": promotion.target_environment.public_id,
            "runtime_id": promotion.candidate.deployment.runtime_id,
            "strategy": promotion.strategy.value,
            "status": promotion.status.value,
            "exception_digest": promotion.exception_digest,
            "approval_digest": promotion.approval_digest,
            "dispatch_digest": promotion.dispatch_digest,
        },
    )


def build_release_receipt_recorded(receipt: ReleaseDeploymentReceipt) -> DomainEvent:
    if receipt.promotion is not None:
        dispatch_public_id = receipt.promotion.dispatch_public_id
    else:
        if receipt.rollback is None:
            raise OutboxPayloadError(
                "release receipt has neither promotion nor rollback"
            )
        dispatch_public_id = receipt.rollback.dispatch_public_id
    return _event(
        namespace_id=receipt.namespace_id,
        aggregate_type="ReleaseDeploymentReceipt",
        aggregate_public_id=receipt.public_id,
        event_type=RELEASE_RECEIPT_RECORDED,
        occurred_at=receipt.occurred_at,
        payload={
            "namespace_id": receipt.namespace_id,
            "receipt_public_id": receipt.public_id,
            "dispatch_public_id": dispatch_public_id,
            "kind": receipt.kind.value,
            "runtime_id": receipt.runtime_id,
            "reporter_credential_id": receipt.reporter_credential_id,
            "status": receipt.status.value,
            "observed_package_version_public_id": receipt.observed_package_version_public_id,
            "observed_deployment_revision": receipt.observed_deployment_revision,
            "observed_configuration_digest": receipt.observed_configuration_digest,
            "runtime_release_ref": receipt.runtime_release_ref,
            "error_code": receipt.error_code,
            "receipt_digest": receipt.receipt_digest,
            "occurred_at": _iso(receipt.occurred_at),
        },
    )


def build_release_environment_activated(release: ReleaseEnvironmentRelease) -> DomainEvent:
    return _event(
        namespace_id=release.namespace_id,
        aggregate_type="ReleaseEnvironmentRelease",
        aggregate_public_id=release.public_id,
        event_type=RELEASE_ENVIRONMENT_ACTIVATED,
        occurred_at=release.activated_at,
        payload={
            "namespace_id": release.namespace_id,
            "environment_release_public_id": release.public_id,
            "environment_public_id": release.environment.public_id,
            "candidate_public_id": release.candidate.public_id,
            "promotion_public_id": release.promotion.public_id if release.promotion is not None else None,
            "rollback_public_id": release.rollback.public_id if release.rollback is not None else None,
            "receipt_public_id": release.receipt.public_id,
            "previous_release_public_id": (
                release.previous_release.public_id if release.previous_release is not None else None
            ),
            "status": release.status.value,
            "activation_digest": release.activation_digest,
            "activated_at": _iso(release.activated_at),
        },
    )


def build_release_canary_evaluated(evaluation: ReleaseCanaryEvaluation) -> DomainEvent:
    return _event(
        namespace_id=evaluation.namespace_id,
        aggregate_type="ReleaseCanaryEvaluation",
        aggregate_public_id=evaluation.public_id,
        event_type=RELEASE_CANARY_EVALUATED,
        occurred_at=evaluation.created_at,
        payload={
            "namespace_id": evaluation.namespace_id,
            "canary_evaluation_public_id": evaluation.public_id,
            "promotion_public_id": evaluation.promotion.public_id,
            "outcome": evaluation.outcome.value,
            "reason_codes_csv": ",".join(evaluation.reason_codes_json),
            "completed_run_count": evaluation.completed_run_count,
            "failed_run_count": evaluation.failed_run_count,
            "untrusted_run_count": evaluation.untrusted_run_count,
            "failure_rate": evaluation.failure_rate,
            "untrusted_rate": evaluation.untrusted_rate,
            "evidence_digest": evaluation.evidence_digest,
            "decision_digest": evaluation.decision_digest,
        },
    )


def build_release_rollback_dispatched(rollback: ReleaseRollback) -> DomainEvent:
    return _event(
        namespace_id=rollback.namespace_id,
        aggregate_type="ReleaseRollback",
        aggregate_public_id=rollback.public_id,
        event_type=RELEASE_ROLLBACK_DISPATCHED,
        occurred_at=rollback.created_at,
        payload={
            "namespace_id": rollback.namespace_id,
            "rollback_public_id": rollback.public_id,
            "dispatch_public_id": rollback.dispatch_public_id,
            "promotion_public_id": rollback.promotion.public_id,
            "source_candidate_public_id": rollback.source_candidate.public_id,
            "target_environment_release_public_id": rollback.target_environment_release.public_id,
            "target_candidate_public_id": rollback.target_environment_release.candidate.public_id,
            "runtime_id": rollback.source_candidate.deployment.runtime_id,
            "reason_code": rollback.reason_code,
            "status": rollback.status.value,
            "dispatch_digest": rollback.dispatch_digest,
        },
    )


def build_handover_snapshot_created(snapshot: HandoverEvidenceSnapshot) -> DomainEvent:
    return _event(
        namespace_id=snapshot.namespace_id,
        aggregate_type="HandoverEvidenceSnapshot",
        aggregate_public_id=snapshot.public_id,
        event_type=HANDOVER_SNAPSHOT_CREATED,
        occurred_at=snapshot.created_at,
        payload={
            "namespace_id": snapshot.namespace_id,
            "snapshot_public_id": snapshot.public_id,
            "handover_case_id": snapshot.handover_case_id,
            "sequence": snapshot.sequence,
            "readiness_outcome": snapshot.readiness_outcome.value,
            "node_count": snapshot.node_count,
            "edge_count": snapshot.edge_count,
            "check_count": snapshot.check_count,
            "snapshot_digest": snapshot.snapshot_digest,
        },
    )


def build_handover_obligation_receipt_recorded(
    receipt: HandoverObligationReceipt,
) -> DomainEvent:
    return _event(
        namespace_id=receipt.namespace_id,
        aggregate_type="HandoverObligationReceipt",
        aggregate_public_id=receipt.public_id,
        event_type=HANDOVER_OBLIGATION_RECEIPT_RECORDED,
        occurred_at=receipt.created_at,
        payload={
            "namespace_id": receipt.namespace_id,
            "obligation_public_id": receipt.obligation.public_id,
            "receipt_public_id": receipt.public_id,
            "decision": receipt.decision.value,
            "evidence_count": len(receipt.evidence_ids_json or []),
            "receipt_digest": receipt.receipt_digest,
            "decided_by_user_id": receipt.decided_by_user_id,
        },
    )


def build_handover_acceptance_recorded(acceptance: HandoverAcceptance) -> DomainEvent:
    return _event(
        namespace_id=acceptance.namespace_id,
        aggregate_type="HandoverAcceptance",
        aggregate_public_id=acceptance.public_id,
        event_type=HANDOVER_ACCEPTANCE_RECORDED,
        occurred_at=acceptance.created_at,
        payload={
            "namespace_id": acceptance.namespace_id,
            "acceptance_public_id": acceptance.public_id,
            "snapshot_public_id": acceptance.snapshot.public_id,
            "handover_case_id": acceptance.handover_case_id,
            "decision": acceptance.decision.value,
            "acknowledges_failures": acceptance.acknowledges_failures,
            "obligation_receipt_count": len(acceptance.obligation_receipt_digests_json or []),
            "acceptance_digest": acceptance.acceptance_digest,
            "accepted_by_user_id": acceptance.accepted_by_user_id,
        },
    )


def build_handover_signed_package_created(package: HandoverSignedPackage) -> DomainEvent:
    return _event(
        namespace_id=package.namespace_id,
        aggregate_type="HandoverSignedPackage",
        aggregate_public_id=package.public_id,
        event_type=HANDOVER_SIGNED_PACKAGE_CREATED,
        occurred_at=package.created_at,
        payload={
            "namespace_id": package.namespace_id,
            "package_public_id": package.public_id,
            "snapshot_public_id": package.acceptance.snapshot.public_id,
            "acceptance_public_id": package.acceptance.public_id,
            "handover_case_id": package.handover_case_id,
            "evidence_item_id": package.evidence_item_id,
            "signing_key_public_id": package.signing_key.public_id,
            "manifest_digest": package.manifest_digest,
            "archive_digest": package.archive_digest,
            "signature_digest": package.signature_digest,
            "attestation_digest": package.attestation_digest,
        },
    )


def build_directory_user_disabled(
    event: DirectoryLifecycleEvent,
    *,
    namespace_id: int,
    handover_case_id: int,
) -> DomainEvent:
    return _event(
        namespace_id=namespace_id,
        aggregate_type="DirectoryLifecycleEvent",
        aggregate_public_id=f"{event.public_id}.ns.{namespace_id}",
        event_type=DIRECTORY_USER_DISABLED,
        occurred_at=event.occurred_at,
        payload={
            "namespace_id": namespace_id,
            "directory_event_public_id": event.public_id,
            "provider_id": event.provider_id,
            "user_id": event.user_id,
            "action": event.action.value,
            "credentials_revoked": event.credentials_revoked,
            "runtime_tokens_revoked": event.runtime_tokens_revoked,
            "memberships_removed": event.memberships_removed,
            "role_bindings_removed": event.role_bindings_removed,
            "handover_case_id": handover_case_id,
            "outcome_digest": event.outcome_digest,
        },
    )


def build_workload_identity_issued(
    identity: ReporterCredential, *, namespace_id: int
) -> DomainEvent:
    return _event(
        namespace_id=namespace_id,
        aggregate_type="WorkloadIdentity",
        aggregate_public_id=identity.public_id,
        event_type=WORKLOAD_IDENTITY_ISSUED,
        occurred_at=identity.created_at,
        payload={
            "namespace_id": namespace_id,
            "workload_identity_public_id": identity.public_id,
            "runtime_id": identity.runtime_id,
            "principal_kind": identity.principal_kind.value,
            "generation": identity.generation,
            "scope_count": len(identity.scopes or []),
            "expires_at": _iso(identity.expires_at),
        },
    )


def build_workload_identity_rotated(
    previous: ReporterCredential,
    successor: ReporterCredential,
    *,
    namespace_id: int,
) -> DomainEvent:
    if previous.revoked_at is None:
        raise ValueError("rotated Workload identity requires predecessor revoked_at")
    return _event(
        namespace_id=namespace_id,
        aggregate_type="WorkloadIdentityRotation",
        aggregate_public_id=successor.public_id,
        event_type=WORKLOAD_IDENTITY_ROTATED,
        occurred_at=previous.revoked_at,
        payload={
            "namespace_id": namespace_id,
            "previous_identity_public_id": previous.public_id,
            "successor_identity_public_id": successor.public_id,
            "runtime_id": successor.runtime_id,
            "principal_kind": successor.principal_kind.value,
            "generation": successor.generation,
            "scope_count": len(successor.scopes or []),
            "rotated_at": _iso(previous.revoked_at),
        },
    )


def build_workload_identity_revoked(
    identity: ReporterCredential, *, namespace_id: int
) -> DomainEvent:
    if identity.revoked_at is None:
        raise ValueError("revoked Workload identity requires revoked_at")
    return _event(
        namespace_id=namespace_id,
        aggregate_type="WorkloadIdentity",
        aggregate_public_id=identity.public_id,
        event_type=WORKLOAD_IDENTITY_REVOKED,
        occurred_at=identity.revoked_at,
        payload={
            "namespace_id": namespace_id,
            "workload_identity_public_id": identity.public_id,
            "runtime_id": identity.runtime_id,
            "principal_kind": identity.principal_kind.value,
            "generation": identity.generation,
            "revoked_at": _iso(identity.revoked_at),
        },
    )


async def enqueue_domain_event(
    db: AsyncSession,
    event: DomainEvent,
    *,
    guaranteed_new: bool = False,
) -> OutboxEvent:
    if not guaranteed_new:
        existing = (
            await db.execute(select(OutboxEvent).where(OutboxEvent.idempotency_key == event.idempotency_key))
        ).scalar_one_or_none()
        if existing is not None:
            if (
                existing.event_type == event.event_type
                and existing.aggregate_type == event.aggregate_type
                and existing.aggregate_public_id == event.aggregate_public_id
                and existing.payload_json == event.payload
            ):
                return existing
            raise OutboxConflictError("outbox idempotency key is already bound to another event")
    now = datetime.now(timezone.utc)
    row = OutboxEvent(
        event_id=str(uuid.uuid4()),
        namespace_id=event.namespace_id,
        aggregate_type=event.aggregate_type,
        aggregate_public_id=event.aggregate_public_id,
        event_type=event.event_type,
        schema_version=OUTBOX_SCHEMA_VERSION,
        payload_json=event.payload,
        idempotency_key=event.idempotency_key,
        status=OutboxEventStatus.PENDING,
        occurred_at=event.occurred_at,
        available_at=now,
    )
    db.add(row)
    await db.flush()
    return row
