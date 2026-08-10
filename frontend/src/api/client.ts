import axios from "axios";
import { message } from "antd";
import { useAuthStore } from "../store/auth";

const api = axios.create({ baseURL: "/api/v1" });
const publicApi = axios.create({ baseURL: "/api/v1" });

// 对密集的 403 响应做全局提示节流，避免 toast 刷屏。
let lastForbiddenToastAt = 0;

api.interceptors.request.use((config) => {
  const token = useAuthStore.getState().accessToken;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config;
    if (error.response?.status === 401 && original && !original._retry) {
      original._retry = true;
      const refreshToken = useAuthStore.getState().refreshToken;
      if (refreshToken) {
        try {
          const { data } = await axios.post("/api/v1/auth/refresh", {
            refresh_token: refreshToken,
          });
          useAuthStore.getState().setTokens(data.access_token, data.refresh_token);
          original.headers.Authorization = `Bearer ${data.access_token}`;
          return api(original);
        } catch {
          useAuthStore.getState().logout();
        }
      }
    }
    if (error.response?.status === 403) {
      const detail = (error.response.data as { detail?: string } | undefined)?.detail;
      const now = Date.now();
      if (now - lastForbiddenToastAt > 3000) {
        lastForbiddenToastAt = now;
        message.warning(
          detail
            ? `权限不足:${detail}`
            : "权限不足:请联系管理员在 IAM 中为你的账号绑定相应角色(如 enterprise-admin)"
        );
      }
    }
    return Promise.reject(error);
  }
);

export default api;

export type SystemRole = "admin" | "user";
export type NamespaceRole = "admin" | "developer" | "readonly";
export type SSOProviderType = "oidc" | "ldap";
export type RoleScope = "system" | "org" | "namespace";
export type SkillVersionStatus = "quarantine" | "scanning" | "review" | "production" | "rejected";
export type SkillVersionReviewStatus = "not_required" | "pending" | "approved" | "rejected";
export type PublicReleaseApprovalStatus = "pending" | "approved" | "rejected";
export type ScanStatus = "pending" | "running" | "passed" | "warned" | "failed";
export type SandboxValidationStatus = "pending" | "running" | "passed" | "failed" | "skipped";
export type EvalStatus = "pending" | "running" | "completed" | "failed";
export interface AiAssist {
  mode: "llm" | "baseline";
  degraded: boolean;
  reason: string | null;
}
export type ReplicationTrigger = "manual" | "on_publish";
export type ReplicationJobStatus = "pending" | "running" | "completed" | "failed";
export type IssueSeverity = "critical" | "high" | "medium" | "low" | "info";
export type EmploymentStatus = "active" | "onboarding" | "leave" | "offboarded";
export type RuntimeProvider = "openclaw" | "arkclaw" | "workbuddy" | "jvs" | "custom";
export type RuntimeDeployType = "saas" | "private" | "on_prem" | "offline";
export type RuntimeStatus = "active" | "degraded" | "disabled";
export type AssetType =
  | "skill"
  | "agent"
  | "prompt"
  | "workflow"
  | "mcp"
  | "tool"
  | "knowledge_base"
  | "scheduled_task"
  | "credential_ref"
  | "workspace"
  | "other";
export type AssetStatus = "active" | "inactive" | "archived" | "orphaned" | "risky" | "transferred";
export type Criticality = "low" | "medium" | "high" | "critical";
export type TraceType = "session" | "task_run" | "automation_run" | "file_change" | "approval" | "deployment";
export type Sensitivity = "public" | "internal" | "confidential" | "restricted";
export type HandoverStatus =
  | "draft"
  | "collecting"
  | "analyzing"
  | "pending_approval"
  | "approved"
  | "executing"
  | "verifying"
  | "completed"
  | "rejected"
  | "cancelled";
export type OwnerType = "creator" | "maintainer" | "business_owner" | "steward" | "receiver";
export type HandoverCaseType = "employee_offboarding" | "project_handover" | "vendor_exit" | "incident_takeover";
export type HandoverAction =
  | "transfer_owner"
  | "archive"
  | "disable"
  | "rotate_secret"
  | "export_package"
  | "manual_review"
  | "ignore";
export type HandoverItemStatus = "proposed" | "approved" | "executing" | "done" | "failed" | "skipped";
export type ApprovalType = "manager" | "receiver" | "security" | "platform_admin";
export type ApprovalStatus = "pending" | "approved" | "rejected" | "delegated";
export type ExecutionStatus = "pending" | "running" | "succeeded" | "failed" | "requires_manual";
export type ExecutionMode = "manual" | "auto";
export type EvidenceVisibility = "normal" | "sensitive" | "restricted";
export type EvidenceSourceType =
  | "api"
  | "backup_package"
  | "browser_snapshot"
  | "audit_log"
  | "user_confirm"
  | "llm_analysis";
export type JobStatus = "pending" | "running" | "succeeded" | "failed" | "partial_failed" | "cancelled";
export type ReportUploadStatus =
  | "pending"
  | "uploaded"
  | "verifying"
  | "ingesting"
  | "succeeded"
  | "failed"
  | "expired";
export type AnalysisWorkerStatus = "active" | "disabled" | "stale";
export type AnalysisJobStatus = "pending" | "leased" | "running" | "succeeded" | "failed" | "cancelled";
export type AgentInsightStatus = "pending" | "running" | "succeeded" | "failed";
export type AnalysisResultArtifactKind =
  | "analysis_result"
  | "asset_cards"
  | "worktrace_summary"
  | "memory_candidates"
  | "handover_signals"
  | "redaction_report"
  | "other";
export type MemoryCandidateType =
  | "asset_summary"
  | "worktrace_summary"
  | "project_context"
  | "ownership_signal"
  | "handover_signal"
  | "risk_signal"
  | "knowledge_note";
export type MemoryCandidateStatus = "candidate" | "confirmed" | "rejected" | "superseded";
export type ManagedComponentStatus =
  | "not_installed"
  | "stopped"
  | "starting"
  | "running"
  | "degraded"
  | "unavailable"
  | "error";

export interface ManagedComponentService {
  name: string;
  container_name: string | null;
  state: string;
  status: string | null;
  health: string | null;
  required: boolean;
}

export interface ManagedComponentDependency {
  name: string;
  ready: boolean;
  state: string;
  health: string | null;
}

export interface ManagedComponent {
  key: string;
  name: string;
  category: string;
  description: string;
  status: ManagedComponentStatus;
  installed: boolean;
  enabled: boolean;
  core_ready: boolean;
  profile: string;
  url: string | null;
  message: string | null;
  services: ManagedComponentService[];
  dependencies: ManagedComponentDependency[];
  commands: Record<string, string>;
  last_checked_at: string;
}

export interface ManagedComponentActionResult {
  ok: boolean;
  action: "start" | "stop";
  component: ManagedComponent;
  output: string | null;
}

export interface RuntimeInstance {
  id: number;
  public_id: string;
  namespace_id: number | null;
  provider: RuntimeProvider;
  name: string;
  base_url: string | null;
  deploy_type: RuntimeDeployType;
  status: RuntimeStatus;
  credential_ref: string | null;
  capabilities: Record<string, unknown> | null;
  metadata_json: Record<string, unknown> | null;
  last_sync_at: string | null;
  created_at: string;
  updated_at: string;
}

export type AdapterProfile =
  | "openclaw-reporter"
  | "hermes-reporter"
  | "generic-otlp-bridge"
  | "pack-atif-import";
export type AdapterConfigDrift = "NONE" | "CONFIG_CHANGED" | "BOOT_CHANGED" | "CAPABILITY_CHANGED";

export interface FleetHeartbeat {
  status: string;
  config_drift: AdapterConfigDrift;
  collector_status: string | null;
  collector_version: string | null;
  observed_at: string;
}

export interface FleetRuntime {
  runtime_public_id: string;
  provider: RuntimeProvider;
  name: string;
  runtime_status: RuntimeStatus;
  profile: AdapterProfile | null;
  adapter_id: string | null;
  adapter_version: string | null;
  certified_capability_level: string | null;
  accepted_capabilities: string[];
  rejected_capabilities: string[];
  handshake_status: "ACTIVE" | "DEGRADED" | "EXPIRED" | "SUPERSEDED" | "NONE";
  heartbeat_state: "HEALTHY" | "STALE" | "NEVER" | "ERROR";
  last_heartbeat_at: string | null;
  handshake_expires_at: string | null;
  config_drift: AdapterConfigDrift | null;
  collector_status: string | null;
  collector_version: string | null;
  heartbeat_history: FleetHeartbeat[];
  namespace_telemetry_sink_count: number;
  namespace_active_telemetry_sink_count: number;
  latest_run_at: string | null;
  pending_pack_import_count: number;
  quarantined_item_count: number;
}

export interface FleetSummary {
  namespace_id: number;
  runtime_count: number;
  healthy_count: number;
  stale_count: number;
  degraded_count: number;
  drifted_count: number;
  runtimes: FleetRuntime[];
}

export type EvaluationProvider = "LANGFUSE" | "DEEPEVAL" | "CUSTOM";
export type EvaluationComparisonOutcome = "PASS" | "REGRESSION" | "INCONCLUSIVE";
export type ReleaseCandidateReviewDecision = "APPROVED" | "REJECTED";

export interface EvaluationDatasetVersion {
  public_id: string;
  version: number;
  content_digest: string;
  item_count: number;
  schema_name: string;
  schema_version: string;
  provider_version_ref: string | null;
  created_at: string;
}

export interface EvaluationDataset {
  public_id: string;
  namespace_id: number;
  name: string;
  description: string | null;
  provider: EvaluationProvider;
  provider_dataset_ref: string | null;
  status: string;
  versions: EvaluationDatasetVersion[];
  created_at: string;
  updated_at: string;
}

export interface EvaluationDatasetMaterialization {
  public_id: string;
  namespace_id: number;
  dataset_public_id: string;
  dataset_version_public_id: string;
  dataset_version: number;
  source_type: "LANGFUSE_TRACE";
  source_trace_ref: string;
  source_observation_ref: string;
  provider_dataset_item_ref: string;
  provider_version_ref: string;
  manifest_digest: string;
  item_count: number;
  schema_name: string;
  schema_version: string;
  created_by_user_id: number;
  created_at: string;
}

export interface TraceDatasetCandidate {
  source_trace_ref: string;
  source_observation_ref: string;
  name: string;
  observation_type: string;
  start_time: string;
  end_time: string | null;
  environment: string | null;
  already_materialized: boolean;
  already_governed: boolean;
}

export type EvaluationDatasetCurationStatus =
  | "PENDING_REVIEW"
  | "APPROVED"
  | "REJECTED"
  | "MATERIALIZED";

export interface EvaluationDatasetCurationBatch {
  public_id: string;
  namespace_id: number;
  dataset_public_id: string;
  status: EvaluationDatasetCurationStatus;
  selection_digest: string;
  item_count: number;
  schema_name: string;
  schema_version: string;
  submitted_by_user_id: number;
  created_at: string;
  items: Array<{
    position: number;
    source_trace_ref: string;
    source_observation_ref: string;
    provider_dataset_item_ref: string | null;
  }>;
  review: {
    public_id: string;
    decision: "APPROVED" | "REJECTED";
    comment: string | null;
    review_digest: string;
    reviewed_by_user_id: number;
    created_at: string;
  } | null;
  materialization: {
    public_id: string;
    dataset_version_public_id: string;
    dataset_version: number;
    provider_version_ref: string;
    manifest_digest: string;
    selected_item_count: number;
    dataset_item_count: number;
    created_by_user_id: number;
    created_at: string;
  } | null;
}

export interface EvaluationSamplingPolicyVersion {
  public_id: string;
  version: number;
  config_digest: string;
  strategy: "STABLE_HASH";
  sample_size: number;
  minimum_sample_size: number;
  candidate_limit: number;
  observation_name: string | null;
  observation_type: string | null;
  environment: string | null;
  root_only: boolean;
  exclude_governed: boolean;
  schema_name: string;
  schema_version: string;
  created_by_user_id: number;
  created_at: string;
}

export interface EvaluationSamplingPolicy {
  public_id: string;
  namespace_id: number;
  name: string;
  description: string | null;
  status: "ACTIVE" | "RETIRED";
  versions: EvaluationSamplingPolicyVersion[];
  created_at: string;
  updated_at: string;
}

export interface EvaluationSamplingRun {
  public_id: string;
  namespace_id: number;
  policy_public_id: string;
  policy_version_public_id: string;
  policy_version: number;
  dataset_public_id: string;
  curation_batch_public_id: string;
  from_start_time: string;
  to_start_time: string;
  candidate_count: number;
  eligible_count: number;
  selected_count: number;
  selection_digest: string;
  run_digest: string;
  schema_name: string;
  schema_version: string;
  created_by_user_id: number;
  created_at: string;
  items: Array<{
    position: number;
    source_trace_ref: string;
    source_observation_ref: string;
    rank_digest: string;
  }>;
}

export interface EvaluationProviderAnnotationQueue {
  provider_queue_ref: string;
  name: string;
  description: string | null;
  score_config_ids: string[];
  created_at: string;
  updated_at: string;
  already_bound: boolean;
}

export interface EvaluationAnnotationQueueBinding {
  public_id: string;
  namespace_id: number;
  provider: "LANGFUSE";
  provider_queue_ref: string;
  provider_queue_name: string;
  score_config_ids: string[];
  provider_updated_at: string;
  status: "ACTIVE" | "RETIRED";
  schema_name: string;
  schema_version: string;
  created_by_user_id: number;
  created_at: string;
  updated_at: string;
}

export interface EvaluationAnnotationDispatch {
  public_id: string;
  namespace_id: number;
  binding_public_id: string;
  provider_queue_ref: string;
  provider_queue_name: string;
  curation_batch_public_id: string;
  sampling_run_public_id: string | null;
  request_digest: string;
  status: "PENDING" | "RUNNING" | "SYNCED" | "FAILED";
  item_count: number;
  synced_count: number;
  completed_count: number;
  failed_count: number;
  attempt_count: number;
  error_code: string | null;
  schema_name: string;
  schema_version: string;
  created_by_user_id: number;
  started_at: string | null;
  synced_at: string | null;
  last_reconciled_at: string | null;
  created_at: string;
  updated_at: string;
  items: Array<{
    position: number;
    source_trace_ref: string;
    source_observation_ref: string;
    sync_status: "PENDING" | "SYNCED" | "FAILED";
    provider_queue_item_ref: string | null;
    provider_annotation_status: "PENDING" | "COMPLETED" | null;
    attempt_count: number;
    error_code: string | null;
    provider_created_at: string | null;
    provider_updated_at: string | null;
    provider_completed_at: string | null;
    last_reconciled_at: string | null;
  }>;
}

export type EvaluationPromotionScoreDataType =
  | "NUMERIC"
  | "BOOLEAN"
  | "CATEGORICAL";
export type EvaluationPromotionDiversityDimension =
  | "NONE"
  | "OBSERVATION_NAME"
  | "OBSERVATION_TYPE"
  | "ENVIRONMENT";

export interface EvaluationPromotionPolicyVersion {
  public_id: string;
  version: number;
  binding_public_id: string;
  provider_queue_ref: string;
  config_digest: string;
  score_config_id: string;
  score_data_type: EvaluationPromotionScoreDataType;
  minimum_numeric_score: number | null;
  accepted_values: Array<string | boolean>;
  diversity_dimension: EvaluationPromotionDiversityDimension;
  min_distinct_buckets: number;
  schema_name: string;
  schema_version: string;
  created_by_user_id: number;
  created_at: string;
}

export interface EvaluationPromotionPolicy {
  public_id: string;
  namespace_id: number;
  name: string;
  description: string | null;
  status: "ACTIVE" | "RETIRED";
  versions: EvaluationPromotionPolicyVersion[];
  created_at: string;
  updated_at: string;
}

export interface EvaluationPromotionRun {
  public_id: string;
  namespace_id: number;
  policy_public_id: string;
  policy_version_public_id: string;
  policy_version: number;
  binding_public_id: string;
  dispatch_public_id: string;
  curation_batch_public_id: string;
  request_digest: string;
  evidence_digest: string;
  outcome: "RECOMMENDED" | "BLOCKED";
  reason_codes: string[];
  item_count: number;
  completed_count: number;
  scored_count: number;
  passed_count: number;
  distinct_bucket_count: number;
  schema_name: string;
  schema_version: string;
  created_by_user_id: number;
  created_at: string;
  items: Array<{
    position: number;
    source_trace_ref: string;
    source_observation_ref: string;
    score_present: boolean;
    quality_passed: boolean;
    diversity_bucket_present: boolean;
    score_evidence_digest: string | null;
    diversity_bucket_digest: string | null;
    reason_code: string;
  }>;
}

export type EvaluationCaseRoutingStrategy = "CLUSTER_ROUND_ROBIN";
export type EvaluationCaseRoutingLane = "GOLDEN" | "BAD_CASE" | "EXCLUDED";
export type EvaluationCaseRoutingOutcome = "ROUTED" | "BLOCKED";

export interface EvaluationCaseRoutingPolicyVersion {
  public_id: string;
  version: number;
  source_promotion_policy_public_id: string;
  source_promotion_policy_version_public_id: string;
  source_promotion_policy_version: number;
  config_digest: string;
  strategy: EvaluationCaseRoutingStrategy;
  golden_target_size: number;
  golden_min_items: number;
  bad_case_target_size: number;
  bad_case_min_items: number;
  schema_name: string;
  schema_version: string;
  created_by_user_id: number;
  created_at: string;
}

export interface EvaluationCaseRoutingPolicy {
  public_id: string;
  namespace_id: number;
  name: string;
  description: string | null;
  status: "ACTIVE" | "RETIRED";
  versions: EvaluationCaseRoutingPolicyVersion[];
  created_at: string;
  updated_at: string;
}

export interface EvaluationCaseRoutingRun {
  public_id: string;
  namespace_id: number;
  policy_public_id: string;
  policy_version_public_id: string;
  policy_version: number;
  source_promotion_policy_version_public_id: string;
  golden_dataset_public_id: string;
  bad_case_dataset_public_id: string;
  golden_curation_batch_public_id: string | null;
  bad_case_curation_batch_public_id: string | null;
  request_digest: string;
  evidence_digest: string;
  routing_digest: string;
  outcome: EvaluationCaseRoutingOutcome;
  reason_codes: string[];
  source_run_count: number;
  candidate_count: number;
  golden_candidate_count: number;
  bad_case_candidate_count: number;
  excluded_count: number;
  golden_selected_count: number;
  bad_case_selected_count: number;
  golden_cluster_count: number;
  bad_case_cluster_count: number;
  schema_name: string;
  schema_version: string;
  created_by_user_id: number;
  created_at: string;
  items: Array<{
    position: number;
    source_promotion_run_public_id: string;
    source_trace_ref: string;
    source_observation_ref: string;
    lane: EvaluationCaseRoutingLane;
    selected: boolean;
    cluster_digest: string | null;
    rank_digest: string | null;
    reason_code: string;
  }>;
}

export interface EvaluationSemanticClusteringPolicyVersion {
  public_id: string;
  version: number;
  source_case_routing_policy_public_id: string;
  source_case_routing_policy_version_public_id: string;
  source_case_routing_policy_version: number;
  config_digest: string;
  embedding_profile: string;
  model_ref: string;
  dimensions: number;
  similarity_threshold: number;
  min_cluster_size: number;
  max_items: number;
  max_content_chars: number;
  schema_name: string;
  schema_version: string;
  created_by_user_id: number;
  created_at: string;
}

export interface EvaluationSemanticClusteringPolicy {
  public_id: string;
  namespace_id: number;
  name: string;
  description: string | null;
  status: "ACTIVE" | "RETIRED";
  versions: EvaluationSemanticClusteringPolicyVersion[];
  created_at: string;
  updated_at: string;
}

export interface EvaluationSemanticClusteringRun {
  public_id: string;
  namespace_id: number;
  policy_public_id: string;
  policy_version_public_id: string;
  policy_version: number;
  source_case_routing_run_public_id: string;
  request_digest: string;
  evidence_digest: string;
  clustering_digest: string;
  outcome: "CLUSTERED" | "BLOCKED";
  reason_codes: string[];
  source_item_count: number;
  cluster_count: number;
  eligible_cluster_count: number;
  schema_name: string;
  schema_version: string;
  created_by_user_id: number;
  created_at: string;
  items: Array<{
    position: number;
    source_case_routing_item_position: number;
    source_trace_ref: string;
    source_observation_ref: string;
    semantic_cluster_digest: string;
    cluster_size: number;
    similarity_to_centroid: number;
    content_digest: string;
    embedding_digest: string;
  }>;
}

export interface EvaluationSemanticRegressionPolicyVersion {
  public_id: string;
  version: number;
  config_digest: string;
  minimum_pairwise_assignment_agreement: number;
  maximum_cluster_count_change_ratio: number;
  maximum_mean_centroid_similarity_drop: number;
  maximum_eligible_cluster_ratio_drop: number;
  require_exact_source_content: boolean;
  schema_name: string;
  schema_version: string;
  created_by_user_id: number;
  created_at: string;
}

export interface EvaluationSemanticRegressionPolicy {
  public_id: string;
  namespace_id: number;
  name: string;
  description: string | null;
  status: "ACTIVE" | "RETIRED";
  versions: EvaluationSemanticRegressionPolicyVersion[];
  created_at: string;
  updated_at: string;
}

export interface EvaluationSemanticRegressionComparison {
  public_id: string;
  namespace_id: number;
  baseline_run_public_id: string;
  baseline_policy_version_public_id: string;
  candidate_run_public_id: string;
  candidate_policy_version_public_id: string;
  policy_public_id: string;
  policy_name: string;
  policy_version_public_id: string;
  policy_version: number;
  outcome: "PASS" | "DRIFTED" | "INCONCLUSIVE";
  reason_codes: string[];
  source_item_count: number;
  pairwise_assignment_agreement: number | null;
  baseline_cluster_count: number;
  candidate_cluster_count: number;
  cluster_count_change_ratio: number;
  baseline_eligible_cluster_ratio: number;
  candidate_eligible_cluster_ratio: number;
  eligible_cluster_ratio_drop: number;
  baseline_mean_centroid_similarity: number;
  candidate_mean_centroid_similarity: number;
  mean_centroid_similarity_drop: number;
  assignment_agreement_breached: boolean;
  cluster_count_change_breached: boolean;
  eligible_cluster_ratio_drop_breached: boolean;
  centroid_similarity_drop_breached: boolean;
  reproducibility_digest: string;
  schema_name: string;
  schema_version: string;
  created_by_user_id: number;
  created_at: string;
}

export interface EvaluationSemanticMonitor {
  public_id: string;
  namespace_id: number;
  name: string;
  description: string | null;
  baseline_run_public_id: string;
  candidate_policy_version_public_id: string;
  regression_policy_version_public_id: string;
  interval_seconds: number;
  config_digest: string;
  status: "ACTIVE" | "PAUSED" | "RETIRED";
  next_run_at: string;
  created_by_user_id: number;
  created_at: string;
  updated_at: string;
}

export interface EvaluationSemanticMonitorRun {
  public_id: string;
  namespace_id: number;
  monitor_public_id: string;
  scheduled_for: string;
  status: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED";
  attempt_count: number;
  candidate_run_public_id: string | null;
  comparison_public_id: string | null;
  alert_public_id: string | null;
  outcome: "PASS" | "DRIFTED" | "INCONCLUSIVE" | null;
  error_code: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface EvaluationSemanticMonitorAlert {
  public_id: string;
  namespace_id: number;
  monitor_public_id: string;
  monitor_run_public_id: string;
  comparison_public_id: string | null;
  severity: "WARNING" | "CRITICAL";
  status: "OPEN" | "ACKNOWLEDGED";
  reason_codes: string[];
  error_code: string | null;
  acknowledged_by_user_id: number | null;
  acknowledged_at: string | null;
  acknowledgement_note: string | null;
  created_at: string;
  updated_at: string;
}

export type EvaluationFailureCategory =
  | "CROSS_RUN_RECURRING"
  | "SINGLE_RUN_RECURRING"
  | "ISOLATED";
export type EvaluationExperienceCandidateStatus =
  | "PENDING_REVIEW"
  | "APPROVED"
  | "REJECTED";
export type EvaluationExperienceReviewDecision = "APPROVED" | "REJECTED";
export type EvaluationExperienceAssetVersionStatus =
  | "DRAFT"
  | "PENDING_ACTIVATION"
  | "ACTIVE"
  | "REJECTED"
  | "RETIRED";
export type EvaluationExperienceActivationDecision = "APPROVED" | "REJECTED";

export interface EvaluationFailureTaxonomyPolicyVersion {
  public_id: string;
  version: number;
  source_case_routing_policy_public_id: string;
  source_case_routing_policy_version_public_id: string;
  source_case_routing_policy_version: number;
  source_semantic_clustering_policy_public_id: string | null;
  source_semantic_clustering_policy_version_public_id: string | null;
  source_semantic_clustering_policy_version: number | null;
  config_digest: string;
  min_cluster_occurrences: number;
  min_source_runs: number;
  include_isolated: boolean;
  max_candidates: number;
  schema_name: string;
  schema_version: string;
  created_by_user_id: number;
  created_at: string;
}

export interface EvaluationFailureTaxonomyPolicy {
  public_id: string;
  namespace_id: number;
  name: string;
  description: string | null;
  status: "ACTIVE" | "RETIRED";
  versions: EvaluationFailureTaxonomyPolicyVersion[];
  created_at: string;
  updated_at: string;
}

export interface EvaluationExperienceCandidate {
  public_id: string;
  position: number;
  category: EvaluationFailureCategory;
  status: EvaluationExperienceCandidateStatus;
  cluster_digest: string;
  rank_digest: string;
  evidence_digest: string;
  source_item_count: number;
  source_run_count: number;
  reason_code: string;
  created_at: string;
  evidence_items: Array<{
    position: number;
    source_case_routing_item_position: number;
    source_promotion_run_public_id: string;
    source_trace_ref: string;
    source_observation_ref: string;
  }>;
  review: {
    public_id: string;
    decision: EvaluationExperienceReviewDecision;
    comment: string | null;
    review_digest: string;
    reviewed_by_user_id: number;
    created_at: string;
  } | null;
}

export interface EvaluationExperienceExtractionRun {
  public_id: string;
  namespace_id: number;
  policy_public_id: string;
  policy_version_public_id: string;
  policy_version: number;
  source_case_routing_run_public_id: string;
  source_semantic_clustering_run_public_id: string | null;
  request_digest: string;
  evidence_digest: string;
  extraction_digest: string;
  outcome: "EXTRACTED" | "BLOCKED";
  reason_codes: string[];
  source_bad_case_count: number;
  cluster_count: number;
  eligible_cluster_count: number;
  candidate_count: number;
  schema_name: string;
  schema_version: string;
  created_by_user_id: number;
  created_at: string;
  candidates: EvaluationExperienceCandidate[];
}

export interface EvaluationExperienceAssetVersion {
  public_id: string;
  version: number;
  status: EvaluationExperienceAssetVersionStatus;
  body: string;
  applicability: string;
  change_summary: string | null;
  content_digest: string;
  source_evidence_digest: string;
  schema_name: string;
  schema_version: string;
  created_by_user_id: number;
  created_at: string;
  activation_request: {
    public_id: string;
    request_note: string | null;
    request_digest: string;
    requested_by_user_id: number;
    created_at: string;
    review: {
      public_id: string;
      decision: EvaluationExperienceActivationDecision;
      comment: string | null;
      review_digest: string;
      reviewed_by_user_id: number;
      created_at: string;
    } | null;
  } | null;
}

export interface EvaluationExperienceAsset {
  public_id: string;
  namespace_id: number;
  source_candidate_public_id: string;
  source_candidate_category: EvaluationFailureCategory;
  source_candidate_evidence_digest: string;
  name: string;
  description: string | null;
  created_by_user_id: number;
  created_at: string;
  versions: EvaluationExperienceAssetVersion[];
}

export interface EvaluatorDefinition {
  public_id: string;
  namespace_id: number;
  name: string;
  kind: string;
  provider: EvaluationProvider;
  provider_evaluator_ref: string | null;
  status: string;
  versions: Array<{
    public_id: string;
    version: number;
    config_digest: string;
    implementation_ref: string;
    rubric_version: string | null;
    provider_version_ref: string | null;
    created_at: string;
  }>;
  created_at: string;
  updated_at: string;
}

export interface EvaluationRun {
  public_id: string;
  namespace_id: number;
  experiment_public_id: string;
  evaluator_version_public_id: string;
  status: string;
  provider_evaluation_ref: string | null;
  score: number | null;
  total_count: number;
  processed_count: number;
  scored_count: number;
  passed_count: number;
  failed_count: number;
  error_count: number;
  result_completeness: string | null;
  result_manifest_public_id: string | null;
  error_code: string | null;
  attempt_count: number;
  available_at: string | null;
  lease_expires_at: string | null;
  cancel_requested_at: string | null;
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface EvaluationComparisonPin {
  evaluation_public_id: string;
  experiment_public_id: string;
  manifest_public_id: string;
  dataset_version_public_id: string;
  dataset_content_digest: string;
  evaluator_version_public_id: string;
  evaluator_config_digest: string;
  target_type: string;
  target_ref: string;
  target_digest: string;
  provider: EvaluationProvider;
  provider_dataset_ref: string;
  provider_experiment_ref: string;
  result_content_digest: string;
  result_completeness: string;
  score: number | null;
  scored_count: number;
  passed_count: number;
  pass_rate: number | null;
}

export interface EvaluationComparison {
  public_id: string;
  namespace_id: number;
  outcome: EvaluationComparisonOutcome;
  reason_code: string;
  baseline: EvaluationComparisonPin;
  candidate: EvaluationComparisonPin;
  policy: {
    policy_public_id: string;
    policy_name: string;
    version_public_id: string;
    version: number;
    content_digest: string;
    minimum_candidate_score: number | null;
    maximum_score_drop: number;
    maximum_pass_rate_drop: number;
    require_complete_results: boolean;
  };
  score_delta: number | null;
  pass_rate_delta: number | null;
  score_floor_breached: boolean;
  score_drop_breached: boolean;
  pass_rate_drop_breached: boolean;
  schema_name: string;
  schema_version: string;
  reproducibility_digest: string;
  created_at: string;
}

export interface ReleaseCandidateEvaluationReview {
  public_id: string;
  namespace_id: number;
  binding_public_id: string;
  decision: ReleaseCandidateReviewDecision;
  comment: string | null;
  schema_name: string;
  schema_version: string;
  review_digest: string;
  reviewed_by_user_id: number;
  created_at: string;
}

export interface ReleaseCandidateEvaluationBinding {
  public_id: string;
  namespace_id: number;
  release_candidate_ref: string;
  deployment_public_id: string;
  deployment_revision: string;
  deployment_configuration_digest: string;
  evaluation_comparison_public_id: string;
  comparison_outcome: EvaluationComparisonOutcome;
  comparison_reproducibility_digest: string;
  candidate_evaluation_public_id: string;
  candidate_manifest_public_id: string;
  schema_name: string;
  schema_version: string;
  binding_digest: string;
  created_by_user_id: number;
  created_at: string;
  review: ReleaseCandidateEvaluationReview | null;
}

export interface ReleaseCandidateGateDecision {
  schema_name: string;
  schema_version: string;
  namespace_id: number;
  release_candidate_ref: string;
  deployment_public_id: string;
  deployment_revision: string;
  outcome: "PASS" | "BLOCKED";
  reason_codes: string[];
  evidence_count: number;
  evidence: Array<{
    evidence_id: string;
    evidence_kind: string;
    verdict: string;
    evidence_digest: string;
    observed_at: string;
  }>;
  decision_digest: string;
}

export interface AIAsset {
  id: number;
  namespace_id: number | null;
  asset_type: AssetType;
  name: string;
  description: string | null;
  source_provider: RuntimeProvider;
  source_runtime_id: number | null;
  external_id: string | null;
  status: AssetStatus;
  criticality: Criticality;
  metadata_json: Record<string, unknown> | null;
  content_hash: string | null;
  first_seen_at: string;
  last_seen_at: string;
  created_at: string;
  updated_at: string;
}

export interface HandoverCase {
  id: number;
  case_type: "employee_offboarding" | "project_handover" | "vendor_exit" | "incident_takeover";
  title: string;
  subject_user_id: number | null;
  namespace_id: number | null;
  receiver_user_id: number | null;
  fallback_owner_user_id: number | null;
  status: HandoverStatus;
  risk_level: Criticality;
  due_at: string | null;
  summary_json: Record<string, unknown> | null;
  created_by: number | null;
  created_at: string;
  updated_at: string;
}

export type HandoverCaseUpdate = Partial<{
  title: string;
  subject_user_id: number | null;
  receiver_user_id: number | null;
  fallback_owner_user_id: number | null;
  due_at: string | null;
}>;

export type HandoverReadinessOutcome = "READY" | "BLOCKED";
export type HandoverObligationType =
  | "RECEIVER_ACCESS"
  | "OWNER_OR_FALLBACK"
  | "PRODUCTION_VERSION"
  | "EVALUATION_BASELINE"
  | "RUNBOOK"
  | "RISK_EVIDENCE"
  | "FAILED_ACTION_ACKNOWLEDGEMENT";
export type HandoverObligationReceiptDecision = "FULFILLED" | "FAILED" | "WAIVED";

export interface HandoverObligationReceiptV2 {
  public_id: string;
  namespace_id: number;
  decision: HandoverObligationReceiptDecision;
  note: string;
  evidence_ids: number[];
  receipt_digest: string;
  decided_by_user_id: number;
  created_at: string;
}

export interface HandoverObligationV2 {
  public_id: string;
  namespace_id: number;
  handover_case_id: number;
  snapshot_public_id: string;
  obligation_key: string;
  obligation_type: HandoverObligationType;
  severity: "BLOCKING" | "ADVISORY";
  title: string;
  requirement: Record<string, unknown>;
  requires_evidence: boolean;
  obligation_digest: string;
  receipt: HandoverObligationReceiptV2 | null;
  created_at: string;
}

export interface HandoverEvidenceSnapshotV2 {
  public_id: string;
  namespace_id: number;
  handover_case_id: number;
  sequence: number;
  subject: Record<string, unknown>;
  nodes: Array<Record<string, unknown>>;
  edges: Array<Record<string, unknown>>;
  evidence_summary: Record<string, unknown>;
  readiness: Array<Record<string, unknown>>;
  node_count: number;
  edge_count: number;
  check_count: number;
  readiness_outcome: HandoverReadinessOutcome;
  current_readiness_outcome: HandoverReadinessOutcome;
  blocking_obligation_count: number;
  open_blocking_obligation_count: number;
  snapshot_digest: string;
  obligations: HandoverObligationV2[];
  created_by_user_id: number;
  created_at: string;
}

export interface HandoverAcceptanceV2 {
  public_id: string;
  namespace_id: number;
  handover_case_id: number;
  snapshot_public_id: string;
  decision: "ACCEPTED" | "REJECTED";
  acknowledges_failures: boolean;
  comment: string;
  obligation_receipt_digests: string[];
  acceptance_digest: string;
  accepted_by_user_id: number;
  created_at: string;
}

export interface HandoverSigningPayloadV2 {
  schema_name: string;
  schema_version: string;
  acceptance_public_id: string;
  snapshot_public_id: string;
  manifest_digest: string;
  payload_base64: string;
}

export interface HandoverSignedPackageV2 {
  public_id: string;
  namespace_id: number;
  handover_case_id: number;
  snapshot_public_id: string;
  acceptance_public_id: string;
  evidence_item_id: number;
  evidence_object_uri: string | null;
  signing_key_public_id: string;
  signing_key_fingerprint: string;
  manifest_digest: string;
  archive_digest: string;
  signature_algorithm: string;
  signature: string;
  signature_digest: string;
  attestation_digest: string;
  created_by_user_id: number;
  created_at: string;
}

export interface CollectionJob {
  id: number;
  runtime_id: number | null;
  trigger_type: "manual" | "scheduled" | "offboarding" | "project_handover" | "webhook";
  status: JobStatus;
  scope_json: Record<string, unknown> | null;
  summary_json: Record<string, unknown> | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export interface RuntimeReportToken {
  id: number;
  runtime_id: number;
  name: string;
  token_prefix: string;
  is_active: boolean;
  expires_at: string | null;
  last_used_at: string | null;
  created_by: number | null;
  created_at: string;
  updated_at: string;
}

export interface RuntimeReportTokenCreated extends RuntimeReportToken {
  token: string;
}

export interface ReportUploadSession {
  id: number;
  report_id: string;
  runtime_id: number;
  collection_job_id: number | null;
  status: ReportUploadStatus;
  schema_version: string;
  report_type: string;
  period_start: string | null;
  period_end: string | null;
  bucket: string;
  object_key: string;
  filename: string;
  content_type: string;
  expected_size_bytes: number | null;
  expected_sha256: string | null;
  actual_size_bytes: number | null;
  actual_sha256: string | null;
  manifest_json: Record<string, unknown> | null;
  metadata_json: Record<string, unknown> | null;
  idempotency_key: string | null;
  upload_expires_at: string;
  finalized_at: string | null;
  error_message: string | null;
  created_by: number | null;
  created_via: string;
  created_at: string;
  updated_at: string;
  upload_url?: string | null;
  expires_in?: number | null;
  max_size_mb?: number | null;
}

export interface AnalysisWorker {
  id: number;
  name: string;
  worker_key: string;
  token_prefix: string;
  status: AnalysisWorkerStatus;
  capabilities_json: Record<string, unknown> | null;
  last_seen_at: string | null;
  created_by: number | null;
  created_at: string;
  updated_at: string;
}

export interface AnalysisWorkerCreated extends AnalysisWorker {
  token: string;
}

export interface AnalysisWorkerDisableResult {
  worker: AnalysisWorker;
  requeued_job_count: number;
  failed_job_count: number;
}

export interface AnalysisQueueMetrics {
  status_counts: Record<string, number>;
  worker_status_counts: Record<string, number>;
  pending_count: number;
  active_job_count: number;
  backlog_count: number;
  terminal_job_count: number;
  expired_lease_count: number;
  retryable_expired_lease_count: number;
  registered_worker_count: number;
  online_worker_count: number;
  stale_worker_count: number;
  disabled_worker_count: number;
  oldest_pending_seconds: number | null;
  queued_per_online_worker: number;
  saturation_level: "healthy" | "watch" | "saturated";
  saturation_reason: string | null;
}

export interface ReportAnalysisJob {
  id: number;
  report_upload_session_id: number;
  runtime_id: number;
  status: AnalysisJobStatus;
  priority: number;
  worker_id: number | null;
  lease_owner: string | null;
  lease_expires_at: string | null;
  attempts: number;
  max_attempts: number;
  input_bucket: string;
  input_object_key: string;
  input_sha256: string | null;
  input_size_bytes: number | null;
  result_bucket: string | null;
  result_object_key: string | null;
  result_sha256: string | null;
  result_size_bytes: number | null;
  result_content_type: string | null;
  summary_json: Record<string, unknown> | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AnalysisResultArtifact {
  id: number;
  analysis_job_id: number;
  report_upload_session_id: number | null;
  runtime_id: number | null;
  kind: AnalysisResultArtifactKind;
  bucket: string;
  object_key: string;
  filename: string;
  content_type: string;
  sha256: string | null;
  size_bytes: number | null;
  summary_json: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface AnalysisResultArtifactDownloadLink {
  filename: string;
  content_type: string;
  download_url: string;
  expires_in: number;
  expires_at: string;
}

export interface MemoryCandidate {
  id: number;
  analysis_job_id: number | null;
  report_upload_session_id: number | null;
  runtime_id: number | null;
  candidate_type: MemoryCandidateType;
  status: MemoryCandidateStatus;
  subject_type: string;
  subject_key: string | null;
  title: string;
  summary: string | null;
  confidence: number;
  sensitivity: Sensitivity;
  source_object_uri: string | null;
  source_sha256: string | null;
  payload_json: Record<string, unknown> | null;
  reviewed_by: number | null;
  reviewed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface WorkTrace {
  id: number;
  namespace_id: number | null;
  runtime_id: number | null;
  asset_id: number | null;
  external_session_id: string | null;
  actor_user_id: number | null;
  title: string;
  summary: string | null;
  trace_type: TraceType;
  started_at: string | null;
  ended_at: string | null;
  sensitivity: Sensitivity;
  metadata_json: Record<string, unknown> | null;
  created_at: string;
}

export type AssetFeedbackAction = "confirm" | "supplement" | "exclude";

export interface MyWorkspaceProjectContext {
  key: string;
  name: string;
  asset_count: number;
  trace_count: number;
  last_seen_at: string | null;
  sources: string[];
}

export interface MyWorkspaceReporterStatus {
  runtime: RuntimeInstance;
  latest_report: ReportUploadSession | null;
  state: "healthy" | "waiting" | "failed" | "unknown";
  message: string;
}

export interface AgentOverviewUser {
  id: number;
  username: string;
  email: string;
  full_name: string | null;
}

export interface AgentOverviewReporter {
  credential_id: number | null;
  token_prefix: string | null;
  state: "healthy" | "waiting" | "failed" | "unknown";
  message: string;
  last_heartbeat_at: string | null;
  last_used_at: string | null;
  heartbeat_json: Record<string, unknown> | null;
}

export interface AgentOverviewMetrics {
  asset_count: number;
  work_trace_count: number;
  report_count: number;
  structured_report_count: number;
  pack_report_count: number;
  risk_signal_count: number;
  handover_signal_count: number;
  blocker_count: number;
  latest_report_at: string | null;
  latest_activity_at: string | null;
}

export interface AgentReportTimelineItem {
  report_id: string;
  source: "structured_report" | "report_pack";
  report_type: string;
  status: string;
  title: string;
  summary: string | null;
  period_start: string | null;
  period_end: string | null;
  created_at: string;
  collection_job_id: number | null;
  work_trace_id: number | null;
  upload_session_id: number | null;
  analysis_job_id: number | null;
  highlights: string[];
  blockers: string[];
  next_actions: string[];
  project_refs: string[];
  asset_count: number;
  memory_candidate_count: number;
  risk_signal_count: number;
  handover_signal_count: number;
}

export interface AgentInsightJob {
  id: number;
  user_id: number;
  runtime_id: number;
  requested_by: number | null;
  status: AgentInsightStatus;
  prompt_version: string;
  model: string | null;
  input_hash: string | null;
  ai_assist_json: AiAssist | (Record<string, unknown> & { mode?: string; degraded?: boolean; reason?: string | null }) | null;
  result_json: Record<string, unknown> | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentOverview {
  user: AgentOverviewUser;
  runtime: RuntimeInstance;
  reporter: AgentOverviewReporter;
  metrics: AgentOverviewMetrics;
  timeline: AgentReportTimelineItem[];
  assets: AIAsset[];
  work_traces: WorkTrace[];
  memory_candidates: MemoryCandidate[];
  latest_insight: AgentInsightJob | null;
}

export interface AgentInsightCreate {
  job: AgentInsightJob;
  queued: boolean;
}

export interface MyWorkspaceReadiness {
  score: number;
  reviewed_assets: number;
  total_assets: number;
  missing_items: string[];
  next_actions: string[];
}

export interface MyAIWorkspace {
  user_id: number;
  username: string;
  employment_status: EmploymentStatus | null;
  offboarding_visible: boolean;
  offboarding_case_count: number;
  assets: AIAsset[];
  work_traces: WorkTrace[];
  handovers: HandoverCase[];
  project_contexts: MyWorkspaceProjectContext[];
  reporter_statuses: MyWorkspaceReporterStatus[];
  readiness: MyWorkspaceReadiness;
}

export interface UserProfile {
  id: number;
  username: string;
  email: string;
  system_role: SystemRole;
  full_name?: string | null;
  auth_source?: "local" | "oidc" | "ldap";
  enterprise_uid?: string | null;
  external_subject?: string | null;
  last_login_at?: string | null;
}

export interface PersonHandoverProfile {
  id: number;
  enterprise_uid: string | null;
  username: string;
  email: string;
  full_name: string | null;
  system_role: SystemRole;
  auth_source: "local" | "oidc" | "ldap";
  is_active: boolean;
  last_login_at: string | null;
  position_title: string | null;
  employee_no: string | null;
  manager_user_id: number | null;
  manager_username: string | null;
  handover_receiver_user_id: number | null;
  handover_receiver_username: string | null;
  employment_status: EmploymentStatus;
  note: string | null;
  profile_updated_at: string | null;
  asset_count: number;
  work_trace_count: number;
  handover_count: number;
  offboarding_visible: boolean;
}

export type PersonHandoverProfileUpdate = Partial<{
  position_title: string | null;
  employee_no: string | null;
  manager_user_id: number | null;
  handover_receiver_user_id: number | null;
  employment_status: EmploymentStatus;
  note: string | null;
}>;

export interface EffectivePermission {
  permission_keys: string[];
}

export interface OrgUnit {
  id: number;
  name: string;
  code: string | null;
  unit_type: string;
  parent_id: number | null;
  description: string | null;
  path: string;
  legal_entity: string | null;
  region: string | null;
  cost_center: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface Role {
  id: number;
  key: string;
  name: string;
  scope: RoleScope;
  description: string | null;
  is_system: boolean;
  created_at: string;
  permissions: string[];
}

export interface PublicSSOProvider {
  id: number;
  name: string;
  provider_type: SSOProviderType;
  login_url: string | null;
}

export interface SSOProviderConfig {
  id: number;
  provider_type: SSOProviderType;
  name: string;
  enabled: boolean;
  issuer_url: string | null;
  client_id: string | null;
  ldap_server_url: string | null;
  ldap_bind_dn: string | null;
  ldap_user_search_base: string | null;
  ldap_user_search_filter: string | null;
  ldap_group_search_base: string | null;
  attribute_mapping: Record<string, unknown> | null;
  extra_config: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface IdentityLink {
  id: number;
  user_id: number;
  provider_id: number;
  source: "local" | "oidc" | "ldap";
  issuer: string | null;
  external_subject: string;
  external_uid: string | null;
  username: string | null;
  email: string | null;
  full_name: string | null;
  claims_json: Record<string, unknown> | null;
  is_active: boolean;
  disabled_at: string | null;
  last_seen_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface DirectoryCredential {
  public_id: string;
  provider_id: number;
  name: string;
  token_prefix: string;
  scopes_json: string[];
  is_active: boolean;
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  created_by_user_id: number;
  created_at: string;
}

export interface DirectoryCredentialCreated extends DirectoryCredential {
  token: string;
}

export interface DirectoryLifecycleEvent {
  public_id: string;
  provider_id: number;
  external_event_id: string;
  subject_digest: string;
  payload_digest: string;
  action: "UPSERT" | "DISABLE" | "REENABLE";
  source: "SCIM" | "STAGING";
  user_id: number;
  user_was_active: boolean;
  credentials_revoked: number;
  runtime_tokens_revoked: number;
  memberships_removed: number;
  role_bindings_removed: number;
  handover_case_ids_json: number[];
  outcome_digest: string;
  occurred_at: string;
}

export interface WorkloadIdentity {
  id: number;
  public_id: string;
  runtime_id: number;
  user_id: number | null;
  device_id: string;
  name: string;
  token_prefix: string;
  scopes: string[] | null;
  principal_kind: "DEVICE" | "SERVICE";
  generation: number;
  is_active: boolean;
  expires_at: string | null;
  revoked_at: string | null;
  rotated_from_id: number | null;
  last_used_at: string | null;
  last_heartbeat_at: string | null;
  created_at: string;
}

export interface SSORoleMapping {
  id: number;
  provider_id: number;
  claim_name: string;
  claim_value: string;
  role_id: number;
  namespace_id: number | null;
  org_unit_id: number | null;
  enabled: boolean;
  priority: number;
  description: string | null;
  created_at: string;
  updated_at: string;
}

export interface SSORoleMappingCreate {
  provider_id: number;
  claim_name?: string;
  claim_value: string;
  role_id: number;
  namespace_id?: number | null;
  org_unit_id?: number | null;
  enabled?: boolean;
  priority?: number;
  description?: string | null;
}

export type SSORoleMappingUpdate = Partial<Omit<SSORoleMappingCreate, "provider_id">>;

export interface Namespace {
  id: number;
  name: string;
  description: string | null;
  owner_id: number;
  created_at: string;
  deleted_at?: string | null;
}

export interface Skill {
  id: number;
  namespace_id: number;
  name: string;
  description: string | null;
  git_repo_path: string;
  created_at: string;
  deleted_at?: string | null;
  latest_tag: string | null;
  version_count: number;
}

export interface ReleaseGateIssue {
  code?: string;
  severity?: string;
  message?: string;
}

export interface ReleaseGateClinicPayload {
  enabled: boolean;
  evaluation_id: number | null;
  score: number | null;
  grade: string | null;
  completed_at: string | null;
  age_hours?: number;
  ai_assist?: AiAssist | null;
  blocking_issue: ReleaseGateIssue | null;
}

export interface ReleaseGateResult {
  final_status?: SkillVersionStatus | string;
  issue_count?: number;
  issues?: ReleaseGateIssue[];
  clinic?: ReleaseGateClinicPayload | null;
  technical_checks?: { ai_assist?: AiAssist | null } & Record<string, unknown>;
  policy?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface SkillVersion {
  id: number;
  skill_id: number;
  tag: string;
  commit_sha: string;
  status: SkillVersionStatus;
  review_status: SkillVersionReviewStatus;
  review_required: boolean;
  review_notes: string | null;
  review_requested_at: string | null;
  reviewed_at: string | null;
  gate_result: ReleaseGateResult | null;
  skill_metadata: Record<string, string> | null;
  changelog?: string | null;
  publish_tags?: string[] | null;
  content_fingerprint?: string | null;
  file_count?: number;
  created_at: string;
  is_public_shared: boolean;
  public_shared_at: string | null;
}

export interface SkillVersionDetail extends SkillVersion {
  files: Record<string, string>;
}

export interface SkillVersionSharingState {
  namespace: string;
  skill: string;
  tag: string;
  is_public_shared: boolean;
  public_shared_at: string | null;
  public_ready: boolean;
  approval_status: PublicReleaseApprovalStatus | null;
  approved_at: string | null;
  expires_at: string | null;
  requires_approval: boolean;
  license_name: string | null;
  license_attested: boolean;
  risk_acknowledged: boolean;
  public_slug: string;
  public_download_url: string;
  public_inspect_url: string;
}

export interface PublicRegistryVersionEntry {
  namespace: string;
  skill: string;
  description: string | null;
  tag: string;
  commit_sha: string;
  status: SkillVersionStatus;
  created_at: string;
  updated_at: string;
  distribution_ready: boolean;
  artifact_sha256: string | null;
  artifact_size_bytes: number | null;
  skill_metadata: Record<string, unknown> | null;
}

export interface PublicRegistryIndexResponse {
  schema_version: string;
  generated_at: string;
  changed_since: string | null;
  next_cursor: string;
  include_urls: boolean;
  expires_in: number | null;
  latest_only: boolean;
  include_non_production: boolean;
  items: PublicRegistryVersionEntry[];
}

export interface PublicRegistryManifestResponse {
  manifest: Record<string, any>;
  manifest_url: string | null;
  artifact_url: string | null;
  expires_at: string | null;
  expires_in: number | null;
}

export interface DiffResponse {
  from_tag: string;
  to_tag: string;
  diff: string;
}

export interface SearchResult {
  items: Skill[];
  total: number;
}

export interface ScanIssue {
  rule: string;
  severity: IssueSeverity;
  message: string;
  file: string | null;
  snippet: string | null;
  line: number | null;
}

export interface SkillGenerateDraftRequest {
  name: string;
  description?: string;
  tag: string;
  prompt: string;
  create_if_missing?: boolean;
  template_key?: string;
}

export interface SkillGenerateDraftResponse {
  skill_name: string;
  description: string | null;
  tag: string;
  skill_md: string;
  system_prompt: string | null;
  package_files: Record<string, string>;
  changelog?: string | null;
  publish_tags?: string[];
  created_skill: boolean;
  validation_status: ScanStatus;
  validation_errors: string[];
  validation_issues: ScanIssue[];
  critical_count: number;
  high_count: number;
  medium_count: number;
  low_count: number;
  ready_to_publish: boolean;
  model: string;
}

export interface NamespaceGovernancePolicy {
  id?: number | null;
  namespace_id: number;
  manual_review_required: boolean;
  require_examples: boolean;
  require_validation_spec: boolean;
  require_sandbox_success: boolean;
  clinic_gate_enabled: boolean;
  min_clinic_score: number | null;
  clinic_max_age_hours: number;
  public_sharing_requires_approval: boolean;
  public_share_default_expiry_days: number | null;
  require_license_attestation: boolean;
  allowed_public_licenses: string[] | null;
  sandbox_network_mode: string;
  sandbox_workspace_mode: string;
  sandbox_agent_smoke_enabled: boolean;
  sandbox_agent_smoke_timeout_seconds: number | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SkillPackageTemplate {
  key: string;
  name: string;
  description: string;
  recommended_files: string[];
  package_files: Record<string, string>;
}

export interface PackageValidationResponse {
  ok: boolean;
  metadata: Record<string, unknown> | null;
  warnings: string[];
  errors: string[];
  validation_spec_present: boolean;
  example_file_count: number;
  file_count: number;
}

export interface ScanResult {
  id: number;
  version_id: number;
  status: ScanStatus;
  issues: ScanIssue[] | null;
  critical_count: number;
  high_count: number;
  medium_count: number;
  low_count: number;
  scanner_version: string;
  ai_assist?: AiAssist | null;
  technical_checks?: { ai_assist?: AiAssist | null } & Record<string, unknown>;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface SandboxCheck {
  name: string;
  status: SandboxValidationStatus;
  summary: string;
  details: Record<string, unknown> | null;
}

export interface SandboxValidationRun {
  id: number;
  version_id: number;
  status: SandboxValidationStatus;
  engine: string;
  summary: string | null;
  checks: SandboxCheck[] | null;
  logs: string[] | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface EvaluationSummary {
  id: number;
  status: EvalStatus;
  overall_score: number | null;
  grade: string | null;
  ai_assist?: AiAssist | null;
  created_at: string;
  completed_at: string | null;
}

export interface DimensionScore {
  score: number;
  weight: number;
  issues: string[];
  details: string;
  trend: "up" | "down" | "stable";
  confidence?: number | null;
  reasoning_summary?: string | null;
  evidence?: Array<{
    skill?: string | null;
    reason: string;
    snippet?: string | null;
  }>;
}

export interface Recommendation {
  dimension: string;
  priority: "critical" | "high" | "medium" | "low";
  title: string;
  description: string;
  action: "auto" | "suggest" | "manual";
  auto_fixable: boolean;
}

export interface EvaluationFull extends EvaluationSummary {
  namespace_id: number;
  dimension_scores: Record<string, DimensionScore> | null;
  recommendations: Recommendation[] | null;
  langfuse_trace_id?: string | null;
  langfuse_trace_url?: string | null;
  triggered_by: number | null;
  started_at: string | null;
  error_message: string | null;
}

export interface DimensionMeta {
  id: string;
  name_zh: string;
  name_en: string;
  weight: number;
}

export interface AuditLog {
  id: number;
  user_id: number | null;
  username: string | null;
  action: string;
  resource_type: string | null;
  resource_id: number | null;
  namespace_id: number | null;
  details: Record<string, unknown> | null;
  ip_address: string | null;
  created_at: string;
}

export interface Webhook {
  id: number;
  namespace_id: number;
  name: string;
  url: string;
  events: string[];
  is_active: boolean;
  created_by: number | null;
  created_at: string;
}

export interface WebhookDelivery {
  id: number;
  webhook_id: number;
  event: string;
  response_status: number | null;
  response_body: string | null;
  payload: Record<string, unknown> | null;
  success: boolean;
  attempted_at: string;
}

export interface RobotAccount {
  id: number;
  namespace_id: number;
  name: string;
  description: string | null;
  token_prefix: string;
  role: NamespaceRole;
  is_active: boolean;
  expires_at: string | null;
  last_used_at: string | null;
  created_at: string;
}

export interface RobotAccountCreated extends RobotAccount {
  token: string;
}

export interface NamespaceQuota {
  id: number | null;
  namespace_id: number;
  max_skills: number;
  max_versions_per_skill: number;
  max_total_versions: number;
  max_storage_bytes: number;
  current_skills: number;
  current_total_versions: number;
  current_storage_bytes: number;
}

export interface RetentionPolicy {
  id: number;
  namespace_id: number;
  keep_last_n: number | null;
  keep_days: number | null;
  delete_rejected: boolean;
  updated_at: string;
}

export interface ReplicationRule {
  id: number;
  name: string;
  src_namespace_id: number;
  dst_namespace_id: number;
  src_namespace_name: string;
  dst_namespace_name: string;
  filter_pattern: string | null;
  trigger: ReplicationTrigger;
  is_active: boolean;
  created_at: string;
  last_job_status: ReplicationJobStatus | null;
  last_job_at: string | null;
}

export interface ReplicationJob {
  id: number;
  rule_id: number;
  status: ReplicationJobStatus;
  skills_copied: number;
  skills_skipped: number;
  skills_failed: number;
  log: string[] | null;
  started_at: string | null;
  completed_at: string | null;
  error_message: string | null;
  created_at: string;
}

export const authApi = {
  register: (data: { username: string; email: string; password: string }) =>
    api.post<UserProfile>("/auth/register", data),
  login: (data: { username: string; password: string }) =>
    api.post<{ access_token: string; refresh_token: string }>("/auth/login", data),
  ldapLogin: (data: { provider_id: number; username: string; password: string }) =>
    publicApi.post<{ access_token: string; refresh_token: string }>("/auth/sso/ldap/login", data),
  exchangeSSO: (data: { token: string }) =>
    publicApi.post<{ access_token: string; refresh_token: string }>("/auth/sso/exchange", data),
  listSSOProviders: () => publicApi.get<PublicSSOProvider[]>("/auth/sso/providers"),
  me: () => api.get<UserProfile>("/auth/me"),
};

export const namespacesApi = {
  list: () => api.get<Namespace[]>("/namespaces"),
  listDeleted: () => api.get<Namespace[]>("/namespaces/deleted"),
  create: (data: { name: string; description?: string }) =>
    api.post<Namespace>("/namespaces", data),
  get: (name: string) => api.get<Namespace>(`/namespaces/${name}`),
  getGovernance: (name: string) => api.get<NamespaceGovernancePolicy>(`/namespaces/${name}/governance`),
  updateGovernance: (name: string, data: NamespaceGovernancePolicy) =>
    api.put<NamespaceGovernancePolicy>(`/namespaces/${name}/governance`, data),
  update: (name: string, data: { description?: string | null }) =>
    api.patch<Namespace>(`/namespaces/${name}`, data),
  delete: (name: string) => api.delete(`/namespaces/${name}`),
  restore: (name: string) => api.post<Namespace>(`/namespaces/${name}/restore`),
  addMember: (ns: string, data: { username: string; role: NamespaceRole }) =>
    api.post(`/namespaces/${ns}/members`, data),
};

export const skillsApi = {
  list: (ns: string) => api.get<Skill[]>(`/namespaces/${ns}/skills`),
  listDeleted: (ns: string) => api.get<Skill[]>(`/namespaces/${ns}/skills/deleted`),
  create: (ns: string, data: { name: string; description?: string }) =>
    api.post<Skill>(`/namespaces/${ns}/skills`, data),
  get: (ns: string, name: string) => api.get<Skill>(`/namespaces/${ns}/skills/${name}`),
  update: (ns: string, name: string, data: { description?: string | null }) =>
    api.patch<Skill>(`/namespaces/${ns}/skills/${name}`, data),
  delete: (ns: string, name: string) => api.delete(`/namespaces/${ns}/skills/${name}`),
  purge: (ns: string, name: string) => api.delete(`/namespaces/${ns}/skills/${name}/purge`),
  restore: (ns: string, name: string) => api.post<Skill>(`/namespaces/${ns}/skills/${name}/restore`),
  listVersions: (ns: string, name: string) =>
    api.get<SkillVersion[]>(`/namespaces/${ns}/skills/${name}/versions`),
  getVersion: (ns: string, name: string, tag: string) =>
    api.get<SkillVersionDetail>(`/namespaces/${ns}/skills/${name}/versions/${tag}`),
  publishVersion: (
    ns: string,
    name: string,
    data: {
      tag: string;
      package_files: Record<string, string>;
      message?: string;
      changelog?: string;
      publish_tags?: string[];
      share_public?: boolean;
      license_name?: string;
      license_attested?: boolean;
      risk_acknowledged?: boolean;
      public_expires_in_days?: number | null;
    }
  ) => api.post<SkillVersion>(`/namespaces/${ns}/skills/${name}/versions`, data),
  listPackageTemplates: () => api.get<SkillPackageTemplate[]>("/skills/package-templates"),
  validatePackage: (data: { package_files: Record<string, string>; expected_tag?: string }) =>
    api.post<PackageValidationResponse>("/skills/package-validate", data),
  generateDraft: (ns: string, data: SkillGenerateDraftRequest) =>
    api.post<SkillGenerateDraftResponse>(`/namespaces/${ns}/skills/generate-draft`, data),
  downloadUrl: (ns: string, name: string, tag: string) =>
    `/api/v1/namespaces/${ns}/skills/${name}/versions/${tag}/download`,
  diff: (ns: string, name: string, fromTag: string, toTag: string) =>
    api.get<DiffResponse>(`/namespaces/${ns}/skills/${name}/diff`, {
      params: { from_tag: fromTag, to_tag: toTag },
    }),
  getSharing: (ns: string, name: string, tag: string) =>
    api.get<SkillVersionSharingState>(`/namespaces/${ns}/skills/${name}/versions/${tag}/sharing`),
  updateSharing: (
    ns: string,
    name: string,
    tag: string,
    data: {
      is_public_shared: boolean;
      license_name?: string | null;
      license_attested?: boolean;
      risk_acknowledged?: boolean;
      public_expires_in_days?: number | null;
    }
  ) =>
    api.put<SkillVersionSharingState>(`/namespaces/${ns}/skills/${name}/versions/${tag}/sharing`, data),
  updateReview: (ns: string, name: string, tag: string, data: { decision: "approve" | "reject" | "reset"; notes?: string | null }) =>
    api.put<SkillVersion>(`/namespaces/${ns}/skills/${name}/versions/${tag}/review`, data),
  updateSharingApproval: (
    ns: string,
    name: string,
    tag: string,
    data: { approval_status: PublicReleaseApprovalStatus; approval_notes?: string | null }
  ) => api.put<SkillVersionSharingState>(`/namespaces/${ns}/skills/${name}/versions/${tag}/sharing/approval`, data),
  search: (q: string, namespace?: string, skip = 0, limit = 20) =>
    api.get<SearchResult>("/skills/search", { params: { q, namespace, skip, limit } }),
};

export const scansApi = {
  get: (ns: string, skill: string, tag: string) =>
    api.get<ScanResult>(`/namespaces/${ns}/skills/${skill}/versions/${tag}/scan`),
  getSandbox: (ns: string, skill: string, tag: string) =>
    api.get<SandboxValidationRun>(`/namespaces/${ns}/skills/${skill}/versions/${tag}/sandbox`),
  trigger: (ns: string, skill: string, tag: string) =>
    api.post<ScanResult>(`/namespaces/${ns}/skills/${skill}/versions/${tag}/scan`),
};

export const clinicApi = {
  triggerEvaluation: (ns: string) => api.post<EvaluationSummary>(`/clinic/namespaces/${ns}/evaluate`),
  listEvaluations: (ns: string) => api.get<EvaluationSummary[]>(`/clinic/namespaces/${ns}/evaluations`),
  getEvaluation: (id: number) => api.get<EvaluationFull>(`/clinic/evaluations/${id}`),
  getDimensions: () => api.get<DimensionMeta[]>("/clinic/dimensions"),
};

export const auditApi = {
  list: (params?: { namespace?: string; action?: string; limit?: number }) =>
    api.get<AuditLog[]>("/audit/logs", { params }),
};

export const webhooksApi = {
  events: () => api.get<string[]>("/webhooks/events"),
  list: (ns: string) => api.get<Webhook[]>(`/namespaces/${ns}/webhooks`),
  create: (ns: string, data: { name: string; url: string; secret?: string; events: string[]; is_active: boolean }) =>
    api.post<Webhook>(`/namespaces/${ns}/webhooks`, data),
  update: (
    ns: string,
    id: number,
    data: Partial<{ name: string; url: string; secret: string; events: string[]; is_active: boolean }>
  ) => api.patch<Webhook>(`/namespaces/${ns}/webhooks/${id}`, data),
  remove: (ns: string, id: number) => api.delete(`/namespaces/${ns}/webhooks/${id}`),
  deliveries: (ns: string, id: number) =>
    api.get<WebhookDelivery[]>(`/namespaces/${ns}/webhooks/${id}/deliveries`),
};

export const robotsApi = {
  list: (ns: string) => api.get<RobotAccount[]>(`/namespaces/${ns}/robots`),
  create: (
    ns: string,
    data: { name: string; description?: string; role: NamespaceRole; expires_days?: number | null }
  ) => api.post<RobotAccountCreated>(`/namespaces/${ns}/robots`, data),
  disable: (ns: string, id: number) => api.patch<RobotAccount>(`/namespaces/${ns}/robots/${id}/disable`),
  remove: (ns: string, id: number) => api.delete(`/namespaces/${ns}/robots/${id}`),
};

export const lifecycleApi = {
  getQuota: (ns: string) => api.get<NamespaceQuota>(`/namespaces/${ns}/quota`),
  updateQuota: (
    ns: string,
    data: {
      max_skills: number;
      max_versions_per_skill: number;
      max_total_versions: number;
      max_storage_bytes: number;
    }
  ) => api.put<NamespaceQuota>(`/namespaces/${ns}/quota`, data),
  getRetention: (ns: string) => api.get<RetentionPolicy>(`/namespaces/${ns}/retention`),
  updateRetention: (
    ns: string,
    data: { keep_last_n: number | null; keep_days: number | null; delete_rejected: boolean }
  ) => api.put<RetentionPolicy>(`/namespaces/${ns}/retention`, data),
  triggerGC: (ns: string) => api.post<{ message: string }>(`/namespaces/${ns}/retention/gc`),
};

export const replicationApi = {
  listRules: (ns: string) => api.get<ReplicationRule[]>(`/namespaces/${ns}/replication/rules`),
  createRule: (
    ns: string,
    data: {
      name: string;
      destination_namespace: string;
      filter_pattern?: string;
      trigger: ReplicationTrigger;
      is_active: boolean;
    }
  ) => api.post<ReplicationRule>(`/namespaces/${ns}/replication/rules`, data),
  updateRule: (
    ns: string,
    id: number,
    data: Partial<{
      name: string;
      destination_namespace: string;
      filter_pattern: string | null;
      trigger: ReplicationTrigger;
      is_active: boolean;
    }>
  ) => api.patch<ReplicationRule>(`/namespaces/${ns}/replication/rules/${id}`, data),
  deleteRule: (ns: string, id: number) => api.delete(`/namespaces/${ns}/replication/rules/${id}`),
  runRule: (ns: string, id: number) =>
    api.post<ReplicationJob>(`/namespaces/${ns}/replication/rules/${id}/run`),
  listJobs: (ns: string, id: number) =>
    api.get<ReplicationJob[]>(`/namespaces/${ns}/replication/rules/${id}/jobs`),
};

export const publicRegistryApi = {
  index: (params?: { namespace?: string; latest_only?: boolean; include_urls?: boolean }) =>
    publicApi.get<PublicRegistryIndexResponse>("/registry/index", { params }),
  manifest: (ns: string, skill: string, tag: string) =>
    publicApi.get<PublicRegistryManifestResponse>(`/registry/namespaces/${ns}/skills/${skill}/versions/${tag}/manifest`),
  downloadLink: (ns: string, skill: string, tag: string) =>
    publicApi.get<{
      namespace: string;
      skill: string;
      tag: string;
      artifact_url: string;
      manifest_url: string;
      expires_at: string;
      expires_in: number;
    }>(`/registry/namespaces/${ns}/skills/${skill}/versions/${tag}/download-link`),
};

export const analysisApi = {
  listWorkers: () => api.get<AnalysisWorker[]>("/analysis/workers"),
  createWorker: (data: { name: string; capabilities_json?: Record<string, unknown> | null }) =>
    api.post<AnalysisWorkerCreated>("/analysis/workers", data),
  disableWorker: (workerId: number) =>
    api.post<AnalysisWorkerDisableResult>(`/analysis/workers/${workerId}/disable`),
  getQueueMetrics: () => api.get<AnalysisQueueMetrics>("/analysis/queue/metrics"),
  listJobs: (params?: { status_filter?: AnalysisJobStatus; runtime_id?: number }) =>
    api.get<ReportAnalysisJob[]>("/analysis/jobs", { params }),
  cancelJob: (jobId: number, reason?: string) =>
    api.post<ReportAnalysisJob>(`/analysis/jobs/${jobId}/cancel`, {
      reason: reason || "Cancelled by administrator",
    }),
  retryJob: (jobId: number, data?: { reason?: string; reset_attempts?: boolean }) =>
    api.post<ReportAnalysisJob>(`/analysis/jobs/${jobId}/retry`, {
      reason: data?.reason || "Retried by administrator",
      reset_attempts: data?.reset_attempts ?? true,
    }),
  listJobArtifacts: (jobId: number) => api.get<AnalysisResultArtifact[]>(`/analysis/jobs/${jobId}/artifacts`),
  getArtifactDownloadLink: (artifactId: number) =>
    api.get<AnalysisResultArtifactDownloadLink>(`/analysis/artifacts/${artifactId}/download-link`),
  listMemoryCandidates: (params?: { status_filter?: MemoryCandidateStatus; runtime_id?: number }) =>
    api.get<MemoryCandidate[]>("/memory/candidates", { params }),
  reviewMemoryCandidate: (candidateId: number, status: Exclude<MemoryCandidateStatus, "candidate">) =>
    api.patch<MemoryCandidate>(`/memory/candidates/${candidateId}`, { status }),
};

// 交接项：LLM 或规则顾问只生成建议，仍需人工审批。
export interface HandoverItem {
  id: number;
  handover_case_id: number;
  asset_id: number;
  recommended_action: HandoverAction;
  receiver_user_id: number | null;
  risk_reason: string | null;
  confidence: number; // 0~1;规则版固定 0
  evidence_id: number | null;
  status: HandoverItemStatus;
  requires_evidence: boolean;
  created_at: string;
}

export interface ApprovalTask {
  id: number;
  handover_case_id: number;
  approver_user_id: number;
  approval_type: ApprovalType;
  status: ApprovalStatus;
  comment: string | null;
  decided_at: string | null;
  created_at: string;
}

export interface ExecutionAction {
  id: number;
  handover_case_id: number;
  handover_item_id: number | null;
  action_type: string;
  provider: RuntimeProvider;
  status: ExecutionStatus;
  execution_mode: ExecutionMode;
  request_json: Record<string, unknown> | null;
  result_json: Record<string, unknown> | null;
  evidence_ids: number[];
  requires_evidence: boolean;
  idempotency_key: string | null;
  created_at: string;
  updated_at: string;
}

// 证据条目；服务端已按归属和权限过滤敏感项。
export interface EvidenceItem {
  id: number;
  namespace_id: number | null;
  work_trace_id: number | null;
  source_type: EvidenceSourceType;
  source_provider: RuntimeProvider;
  collection_job_id: number | null;
  object_uri: string | null;
  sha256: string | null;
  summary: string;
  confidence: number;
  visibility: EvidenceVisibility;
  created_by: number | null;
  created_at: string;
}

// 证据限时下载链接。指向报告包内部时，download_url 为所在归档，
// archive_path 为归档内路径、is_archive_member=true,需客户端下载后自取。
export interface EvidenceDownloadLink {
  evidence_id: number;
  visibility: EvidenceVisibility;
  download_url: string;
  expires_in: number;
  expires_at: string;
  sha256: string | null;
  archive_path: string | null;
  is_archive_member: boolean;
}

export const controlPlaneApi = {
  getMyAIWorkspace: () => api.get<MyAIWorkspace>("/me/ai-workspace"),
  getAgentOverview: (userId: number, runtimeId: number) =>
    api.get<AgentOverview>(`/agent-overview/users/${userId}/runtimes/${runtimeId}`),
  createAgentInsight: (userId: number, runtimeId: number) =>
    api.post<AgentInsightCreate>(`/agent-overview/users/${userId}/runtimes/${runtimeId}/analyze`),
  getAgentInsight: (jobId: number) => api.get<AgentInsightJob>(`/agent-overview/insights/${jobId}`),
  listRuntimes: (params?: { provider?: RuntimeProvider }) => api.get<RuntimeInstance[]>("/runtimes", { params }),
  createRuntime: (data: {
    namespace_id: number;
    provider: RuntimeProvider;
    name: string;
    base_url?: string | null;
    deploy_type: RuntimeDeployType;
    credential_ref?: string | null;
    metadata_json?: Record<string, unknown> | null;
  }) => api.post<RuntimeInstance>("/runtimes", data),
  testRuntime: (runtimeId: number) =>
    api.post<{ status: string; provider: RuntimeProvider; capabilities: Record<string, unknown>; message: string }>(
      `/runtimes/${runtimeId}/test`
    ),
  listRuntimeReportTokens: (runtimeId: number) =>
    api.get<RuntimeReportToken[]>(`/runtimes/${runtimeId}/report-tokens`),
  createRuntimeReportToken: (runtimeId: number, data: { name?: string; expires_at?: string | null }) =>
    api.post<RuntimeReportTokenCreated>(`/runtimes/${runtimeId}/report-tokens`, data),
  revokeRuntimeReportToken: (tokenId: number) => api.delete(`/runtime-report-tokens/${tokenId}`),
  listReportUploadSessions: (params?: { runtime_id?: number; status_filter?: ReportUploadStatus }) =>
    api.get<ReportUploadSession[]>("/reports/upload-sessions", { params }),
  ingestReportUploadSession: (reportId: string) => api.post<ReportUploadSession>(`/reports/${reportId}/ingest`),
  listAssets: (params?: { provider?: RuntimeProvider; status_filter?: AssetStatus }) =>
    api.get<AIAsset[]>("/assets", { params }),
  createAsset: (data: {
    namespace_id: number;
    asset_type: AssetType;
    name: string;
    description?: string | null;
    source_provider: RuntimeProvider;
    source_runtime_id?: number | null;
    external_id?: string | null;
    status?: AssetStatus;
    criticality?: Criticality;
    metadata_json?: Record<string, unknown> | null;
    content_hash?: string | null;
  }) => api.post<AIAsset>("/assets", data),
  submitAssetFeedback: (
    assetId: number,
    data: { action: AssetFeedbackAction; note?: string | null; metadata_json?: Record<string, unknown> | null }
  ) => api.post<AIAsset>(`/assets/${assetId}/feedback`, data),
  createAssetOwnership: (
    assetId: number,
    data: {
      owner_type: OwnerType;
      user_id?: number | null;
      org_unit_id?: number | null;
      namespace_id?: number | null;
      confidence?: number;
      evidence_id?: number | null;
      is_primary?: boolean;
    }
  ) => api.post(`/assets/${assetId}/ownership`, data),
  listHandovers: (params?: { status_filter?: HandoverStatus }) =>
    api.get<HandoverCase[]>("/handovers", { params }),
  getHandover: (caseId: number) => api.get<HandoverCase>(`/handovers/${caseId}`),
  updateHandover: (caseId: number, data: HandoverCaseUpdate) =>
    api.patch<HandoverCase>(`/handovers/${caseId}`, data),
  listCollectionJobs: (params?: { runtime_id?: number }) =>
    api.get<CollectionJob[]>("/collection-jobs", { params }),
  createHandover: (data: {
    namespace_id: number;
    case_type: HandoverCaseType;
    title: string;
    subject_user_id?: number | null;
    receiver_user_id?: number | null;
    fallback_owner_user_id?: number | null;
    due_at?: string | null;
    runtime_ids?: number[];
    collection_scope?: {
      users?: number[] | null;
      asset_types?: AssetType[] | null;
      include_work_traces?: boolean;
      include_artifacts?: boolean;
      lookback_days?: number | null;
    };
    metadata_json?: Record<string, unknown> | null;
  }) => api.post<HandoverCase>("/handovers", data),
  collectHandover: (caseId: number) => api.post(`/handovers/${caseId}/collect`),
  analyzeHandover: (caseId: number) => api.post<HandoverItem[]>(`/handovers/${caseId}/analyze`),
  listHandoverItems: (caseId: number) => api.get<HandoverItem[]>(`/handovers/${caseId}/items`),
  submitHandover: (caseId: number, approvals: Array<{ approval_type: ApprovalType; approver_user_id: number }>) =>
    api.post<ApprovalTask[]>(`/handovers/${caseId}/submit`, approvals),
  listHandoverApprovals: (caseId: number) => api.get<ApprovalTask[]>(`/handovers/${caseId}/approvals`),
  decideApproval: (
    caseId: number,
    approvalId: number,
    data: { decision: Extract<ApprovalStatus, "approved" | "rejected">; comment?: string | null }
  ) => api.post<ApprovalTask>(`/handovers/${caseId}/approvals/${approvalId}/decide`, data),
  executeHandover: (caseId: number, data: { selected_item_ids?: number[] | null; execution_mode?: "manual"; idempotency_key?: string | null }) =>
    api.post<ExecutionAction[]>(`/handovers/${caseId}/execute`, data),
  listExecutionActions: (caseId: number) => api.get<ExecutionAction[]>(`/handovers/${caseId}/actions`),
  completeExecutionAction: (
    caseId: number,
    actionId: number,
    data: { result: Extract<ExecutionStatus, "succeeded" | "failed">; note: string; evidence_ids?: number[]; idempotency_key?: string | null }
  ) => api.post<ExecutionAction>(`/handovers/${caseId}/actions/${actionId}/complete`, data),
  uploadHandoverEvidence: (caseId: number, data: FormData) =>
    api.post<EvidenceItem>(`/handovers/${caseId}/evidence`, data),
  verifyHandover: (caseId: number, data: { note?: string | null; acknowledge_failures?: boolean }) =>
    api.post<HandoverCase>(`/handovers/${caseId}/verify`, data),
  createHandoverPackage: (caseId: number) => api.post<EvidenceItem>(`/handovers/${caseId}/package`),
  downloadHandoverPackageLink: (caseId: number, data: { reason: string; expires_in?: number }) =>
    api.post<EvidenceDownloadLink>(`/handovers/${caseId}/package/download-link`, data),
  listEvidence: (params?: { collection_job_id?: number }) =>
    api.get<EvidenceItem[]>("/evidence", { params }),
  // reason 至少 5 个字符并写入审计；非创建者需敏感证据读取权限。
  requestEvidenceDownloadLink: (evidenceId: number, data: { reason: string; expires_in?: number }) =>
    api.post<EvidenceDownloadLink>(`/evidence/${evidenceId}/download-link`, data),
  listHandoverSnapshotsV2: (namespaceId: number, caseId: number) =>
    api.get<HandoverEvidenceSnapshotV2[]>("/handover-evidence-snapshots", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId, handover_case_id: caseId },
    }),
  createHandoverSnapshotV2: (caseId: number, idempotencyKey: string) =>
    api.post<HandoverEvidenceSnapshotV2>(
      "/handover-evidence-snapshots",
      { handover_case_id: caseId, idempotency_key: idempotencyKey },
      { baseURL: "/api/v2" },
    ),
  recordHandoverObligationReceiptV2: (
    obligationPublicId: string,
    data: {
      decision: HandoverObligationReceiptDecision;
      note: string;
      evidence_ids: number[];
      idempotency_key: string;
    },
  ) =>
    api.post<HandoverObligationReceiptV2>(
      `/handover-obligations/${obligationPublicId}/receipts`,
      data,
      { baseURL: "/api/v2" },
    ),
  listHandoverAcceptancesV2: (namespaceId: number, caseId: number) =>
    api.get<HandoverAcceptanceV2[]>("/handover-acceptances", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId, handover_case_id: caseId },
    }),
  createHandoverAcceptanceV2: (data: {
    snapshot_public_id: string;
    decision: "ACCEPTED" | "REJECTED";
    comment: string;
    acknowledges_failures: boolean;
    idempotency_key: string;
  }) => api.post<HandoverAcceptanceV2>("/handover-acceptances", data, { baseURL: "/api/v2" }),
  getHandoverSigningPayloadV2: (acceptancePublicId: string) =>
    api.get<HandoverSigningPayloadV2>(
      `/handover-acceptances/${acceptancePublicId}/signing-payload`,
      { baseURL: "/api/v2" },
    ),
  listHandoverSignedPackagesV2: (namespaceId: number, caseId: number) =>
    api.get<HandoverSignedPackageV2[]>("/handover-signed-packages", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId, handover_case_id: caseId },
    }),
  createHandoverSignedPackageV2: (data: {
    acceptance_public_id: string;
    signing_key_public_id: string;
    signature: string;
    idempotency_key: string;
  }) => api.post<HandoverSignedPackageV2>("/handover-signed-packages", data, { baseURL: "/api/v2" }),
};

export const peopleApi = {
  listPeople: () => api.get<PersonHandoverProfile[]>("/people"),
  updatePerson: (userId: number, data: PersonHandoverProfileUpdate) =>
    api.patch<PersonHandoverProfile>(`/people/${userId}`, data),
};

export const componentsApi = {
  list: () => api.get<ManagedComponent[]>("/components"),
  get: (componentKey: string) => api.get<ManagedComponent>(`/components/${componentKey}`),
  start: (componentKey: string) => api.post<ManagedComponentActionResult>(`/components/${componentKey}/start`),
  stop: (componentKey: string) => api.post<ManagedComponentActionResult>(`/components/${componentKey}/stop`),
};

export const fleetApi = {
  summary: (namespaceId: number) =>
    api.get<FleetSummary>("/fleet/runtimes", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
  }),
};

export const evalHubApi = {
  listDatasets: (namespaceId: number) =>
    api.get<EvaluationDataset[]>("/evaluation-datasets", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  listEvaluators: (namespaceId: number) =>
    api.get<EvaluatorDefinition[]>("/evaluators", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  listDatasetMaterializations: (namespaceId: number) =>
    api.get<EvaluationDatasetMaterialization[]>(
      "/evaluation-dataset-materializations",
      {
        baseURL: "/api/v2",
        params: { namespace_id: namespaceId },
      },
    ),
  materializeTrace: (
    datasetPublicId: string,
    data: { trace_id: string; observation_id?: string | null },
    idempotencyKey: string,
  ) =>
    api.post<EvaluationDatasetMaterialization>(
      `/evaluation-datasets/${datasetPublicId}/trace-materializations`,
      data,
      {
        baseURL: "/api/v2",
        headers: { "Idempotency-Key": idempotencyKey },
      },
    ),
  listTraceCandidates: (
    datasetPublicId: string,
    params: {
      from_start_time: string;
      to_start_time: string;
      name?: string;
      observation_type?: string;
      environment?: string;
      root_only?: boolean;
      limit?: number;
    },
  ) =>
    api.get<TraceDatasetCandidate[]>("/evaluation-trace-candidates", {
      baseURL: "/api/v2",
      params: {
        dataset_public_id: datasetPublicId,
        ...params,
      },
    }),
  listCurationBatches: (namespaceId: number) =>
    api.get<EvaluationDatasetCurationBatch[]>(
      "/evaluation-dataset-curation-batches",
      {
        baseURL: "/api/v2",
        params: { namespace_id: namespaceId },
      },
    ),
  listAnnotationProviderQueues: (namespaceId: number) =>
    api.get<EvaluationProviderAnnotationQueue[]>(
      "/evaluation-annotation-queue-bindings/provider-queues",
      {
        baseURL: "/api/v2",
        params: { namespace_id: namespaceId },
      },
    ),
  listAnnotationQueueBindings: (namespaceId: number) =>
    api.get<EvaluationAnnotationQueueBinding[]>(
      "/evaluation-annotation-queue-bindings",
      {
        baseURL: "/api/v2",
        params: { namespace_id: namespaceId },
      },
    ),
  bindAnnotationQueue: (data: {
    namespace_id: number;
    provider_queue_ref: string;
  }) =>
    api.post<EvaluationAnnotationQueueBinding>(
      "/evaluation-annotation-queue-bindings",
      data,
      { baseURL: "/api/v2" },
    ),
  listAnnotationDispatches: (namespaceId: number) =>
    api.get<EvaluationAnnotationDispatch[]>(
      "/evaluation-annotation-dispatches",
      {
        baseURL: "/api/v2",
        params: { namespace_id: namespaceId },
      },
    ),
  dispatchAnnotationBatch: (
    bindingPublicId: string,
    curationBatchPublicId: string,
    idempotencyKey: string,
  ) =>
    api.post<EvaluationAnnotationDispatch>(
      `/evaluation-annotation-queue-bindings/${bindingPublicId}/dispatches`,
      { curation_batch_public_id: curationBatchPublicId },
      {
        baseURL: "/api/v2",
        headers: { "Idempotency-Key": idempotencyKey },
      },
    ),
  retryAnnotationDispatch: (dispatchPublicId: string) =>
    api.post<EvaluationAnnotationDispatch>(
      `/evaluation-annotation-dispatches/${dispatchPublicId}/retry`,
      undefined,
      { baseURL: "/api/v2" },
    ),
  reconcileAnnotationDispatch: (dispatchPublicId: string) =>
    api.post<EvaluationAnnotationDispatch>(
      `/evaluation-annotation-dispatches/${dispatchPublicId}/reconcile`,
      undefined,
      { baseURL: "/api/v2" },
    ),
  listPromotionPolicies: (namespaceId: number) =>
    api.get<EvaluationPromotionPolicy[]>("/evaluation-promotion-policies", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  createPromotionPolicy: (data: {
    namespace_id: number;
    name: string;
    description?: string | null;
  }) =>
    api.post<EvaluationPromotionPolicy>("/evaluation-promotion-policies", data, {
      baseURL: "/api/v2",
    }),
  createPromotionPolicyVersion: (
    policyPublicId: string,
    data: {
      binding_public_id: string;
      score_config_id: string;
      score_data_type: EvaluationPromotionScoreDataType;
      minimum_numeric_score?: number | null;
      accepted_values?: Array<string | boolean>;
      diversity_dimension: EvaluationPromotionDiversityDimension;
      min_distinct_buckets: number;
    },
  ) =>
    api.post<EvaluationPromotionPolicyVersion>(
      `/evaluation-promotion-policies/${policyPublicId}/versions`,
      data,
      { baseURL: "/api/v2" },
    ),
  listPromotionRuns: (namespaceId: number) =>
    api.get<EvaluationPromotionRun[]>("/evaluation-promotion-runs", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  runPromotionPolicyVersion: (
    versionPublicId: string,
    dispatchPublicId: string,
    idempotencyKey: string,
  ) =>
    api.post<EvaluationPromotionRun>(
      `/evaluation-promotion-policy-versions/${versionPublicId}/runs`,
      { dispatch_public_id: dispatchPublicId },
      {
        baseURL: "/api/v2",
        headers: { "Idempotency-Key": idempotencyKey },
      },
    ),
  listCaseRoutingPolicies: (namespaceId: number) =>
    api.get<EvaluationCaseRoutingPolicy[]>("/evaluation-case-routing-policies", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  createCaseRoutingPolicy: (data: {
    namespace_id: number;
    name: string;
    description?: string | null;
  }) =>
    api.post<EvaluationCaseRoutingPolicy>(
      "/evaluation-case-routing-policies",
      data,
      { baseURL: "/api/v2" },
    ),
  createCaseRoutingPolicyVersion: (
    policyPublicId: string,
    data: {
      source_promotion_policy_version_public_id: string;
      strategy: EvaluationCaseRoutingStrategy;
      golden_target_size: number;
      golden_min_items: number;
      bad_case_target_size: number;
      bad_case_min_items: number;
    },
  ) =>
    api.post<EvaluationCaseRoutingPolicyVersion>(
      `/evaluation-case-routing-policies/${policyPublicId}/versions`,
      data,
      { baseURL: "/api/v2" },
    ),
  listCaseRoutingRuns: (namespaceId: number) =>
    api.get<EvaluationCaseRoutingRun[]>("/evaluation-case-routing-runs", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  runCaseRoutingPolicyVersion: (
    versionPublicId: string,
    data: {
      promotion_run_public_ids: string[];
      golden_dataset_public_id: string;
      bad_case_dataset_public_id: string;
    },
    idempotencyKey: string,
  ) =>
    api.post<EvaluationCaseRoutingRun>(
      `/evaluation-case-routing-policy-versions/${versionPublicId}/runs`,
      data,
      {
        baseURL: "/api/v2",
        headers: { "Idempotency-Key": idempotencyKey },
      },
    ),
  listSemanticClusteringPolicies: (namespaceId: number) =>
    api.get<EvaluationSemanticClusteringPolicy[]>(
      "/evaluation-semantic-clustering-policies",
      { baseURL: "/api/v2", params: { namespace_id: namespaceId } },
    ),
  createSemanticClusteringPolicy: (data: {
    namespace_id: number;
    name: string;
    description?: string | null;
  }) =>
    api.post<EvaluationSemanticClusteringPolicy>(
      "/evaluation-semantic-clustering-policies",
      data,
      { baseURL: "/api/v2" },
    ),
  createSemanticClusteringPolicyVersion: (
    policyPublicId: string,
    data: {
      source_case_routing_policy_version_public_id: string;
      embedding_profile: string;
      model_ref: string;
      dimensions: number;
      similarity_threshold: number;
      min_cluster_size: number;
      max_items: number;
      max_content_chars: number;
    },
  ) =>
    api.post<EvaluationSemanticClusteringPolicyVersion>(
      `/evaluation-semantic-clustering-policies/${policyPublicId}/versions`,
      data,
      { baseURL: "/api/v2" },
    ),
  listSemanticClusteringRuns: (namespaceId: number) =>
    api.get<EvaluationSemanticClusteringRun[]>(
      "/evaluation-semantic-clustering-runs",
      { baseURL: "/api/v2", params: { namespace_id: namespaceId } },
    ),
  runSemanticClusteringPolicyVersion: (
    versionPublicId: string,
    sourceCaseRoutingRunPublicId: string,
    idempotencyKey: string,
  ) =>
    api.post<EvaluationSemanticClusteringRun>(
      `/evaluation-semantic-clustering-policy-versions/${versionPublicId}/runs`,
      { source_case_routing_run_public_id: sourceCaseRoutingRunPublicId },
      {
        baseURL: "/api/v2",
        headers: { "Idempotency-Key": idempotencyKey },
      },
    ),
  listSemanticRegressionPolicies: (namespaceId: number) =>
    api.get<EvaluationSemanticRegressionPolicy[]>(
      "/evaluation-semantic-regression-policies",
      { baseURL: "/api/v2", params: { namespace_id: namespaceId } },
    ),
  createSemanticRegressionPolicy: (data: {
    namespace_id: number;
    name: string;
    description?: string | null;
  }) =>
    api.post<EvaluationSemanticRegressionPolicy>(
      "/evaluation-semantic-regression-policies",
      data,
      { baseURL: "/api/v2" },
    ),
  createSemanticRegressionPolicyVersion: (
    policyPublicId: string,
    data: {
      minimum_pairwise_assignment_agreement: number;
      maximum_cluster_count_change_ratio: number;
      maximum_mean_centroid_similarity_drop: number;
      maximum_eligible_cluster_ratio_drop: number;
    },
  ) =>
    api.post<EvaluationSemanticRegressionPolicyVersion>(
      `/evaluation-semantic-regression-policies/${policyPublicId}/versions`,
      data,
      { baseURL: "/api/v2" },
    ),
  listSemanticRegressionComparisons: (namespaceId: number) =>
    api.get<EvaluationSemanticRegressionComparison[]>(
      "/evaluation-semantic-regression-comparisons",
      { baseURL: "/api/v2", params: { namespace_id: namespaceId } },
    ),
  createSemanticRegressionComparison: (
    data: {
      namespace_id: number;
      baseline_run_public_id: string;
      candidate_run_public_id: string;
      policy_version_public_id: string;
    },
    idempotencyKey: string,
  ) =>
    api.post<EvaluationSemanticRegressionComparison>(
      "/evaluation-semantic-regression-comparisons",
      data,
      {
        baseURL: "/api/v2",
        headers: { "Idempotency-Key": idempotencyKey },
      },
    ),
  listSemanticMonitors: (namespaceId: number) =>
    api.get<EvaluationSemanticMonitor[]>("/evaluation-semantic-monitors", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  createSemanticMonitor: (data: {
    namespace_id: number;
    name: string;
    description?: string | null;
    baseline_run_public_id: string;
    candidate_policy_version_public_id: string;
    regression_policy_version_public_id: string;
    interval_seconds: number;
  }) =>
    api.post<EvaluationSemanticMonitor>("/evaluation-semantic-monitors", data, {
      baseURL: "/api/v2",
    }),
  pauseSemanticMonitor: (monitorPublicId: string) =>
    api.post<EvaluationSemanticMonitor>(
      `/evaluation-semantic-monitors/${monitorPublicId}/pause`,
      undefined,
      { baseURL: "/api/v2" },
    ),
  resumeSemanticMonitor: (monitorPublicId: string) =>
    api.post<EvaluationSemanticMonitor>(
      `/evaluation-semantic-monitors/${monitorPublicId}/resume`,
      undefined,
      { baseURL: "/api/v2" },
    ),
  runSemanticMonitorNow: (monitorPublicId: string, idempotencyKey: string) =>
    api.post<EvaluationSemanticMonitorRun>(
      `/evaluation-semantic-monitors/${monitorPublicId}/run-now`,
      undefined,
      {
        baseURL: "/api/v2",
        headers: { "Idempotency-Key": idempotencyKey },
      },
    ),
  listSemanticMonitorRuns: (namespaceId: number) =>
    api.get<EvaluationSemanticMonitorRun[]>("/evaluation-semantic-monitor-runs", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  listSemanticMonitorAlerts: (namespaceId: number) =>
    api.get<EvaluationSemanticMonitorAlert[]>("/evaluation-semantic-monitor-alerts", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  acknowledgeSemanticMonitorAlert: (alertPublicId: string, note?: string | null) =>
    api.post<EvaluationSemanticMonitorAlert>(
      `/evaluation-semantic-monitor-alerts/${alertPublicId}/acknowledge`,
      { note: note || null },
      { baseURL: "/api/v2" },
    ),
  listFailureTaxonomyPolicies: (namespaceId: number) =>
    api.get<EvaluationFailureTaxonomyPolicy[]>(
      "/evaluation-failure-taxonomy-policies",
      {
        baseURL: "/api/v2",
        params: { namespace_id: namespaceId },
      },
    ),
  createFailureTaxonomyPolicy: (data: {
    namespace_id: number;
    name: string;
    description?: string | null;
  }) =>
    api.post<EvaluationFailureTaxonomyPolicy>(
      "/evaluation-failure-taxonomy-policies",
      data,
      { baseURL: "/api/v2" },
    ),
  createFailureTaxonomyPolicyVersion: (
    policyPublicId: string,
    data: {
      source_case_routing_policy_version_public_id: string;
      source_semantic_clustering_policy_version_public_id?: string | null;
      min_cluster_occurrences: number;
      min_source_runs: number;
      include_isolated: boolean;
      max_candidates: number;
    },
  ) =>
    api.post<EvaluationFailureTaxonomyPolicyVersion>(
      `/evaluation-failure-taxonomy-policies/${policyPublicId}/versions`,
      data,
      { baseURL: "/api/v2" },
    ),
  listExperienceExtractionRuns: (namespaceId: number) =>
    api.get<EvaluationExperienceExtractionRun[]>(
      "/evaluation-experience-extraction-runs",
      {
        baseURL: "/api/v2",
        params: { namespace_id: namespaceId },
      },
    ),
  runFailureTaxonomyPolicyVersion: (
    versionPublicId: string,
    sourceCaseRoutingRunPublicId: string,
    sourceSemanticClusteringRunPublicId: string | null,
    idempotencyKey: string,
  ) =>
    api.post<EvaluationExperienceExtractionRun>(
      `/evaluation-failure-taxonomy-policy-versions/${versionPublicId}/runs`,
      {
        source_case_routing_run_public_id: sourceCaseRoutingRunPublicId,
        source_semantic_clustering_run_public_id:
          sourceSemanticClusteringRunPublicId,
      },
      {
        baseURL: "/api/v2",
        headers: { "Idempotency-Key": idempotencyKey },
      },
    ),
  reviewExperienceCandidate: (
    candidatePublicId: string,
    data: {
      decision: EvaluationExperienceReviewDecision;
      comment?: string | null;
    },
  ) =>
    api.post<EvaluationExperienceCandidate>(
      `/evaluation-experience-candidates/${candidatePublicId}/reviews`,
      data,
      { baseURL: "/api/v2" },
    ),
  listExperienceAssets: (namespaceId: number) =>
    api.get<EvaluationExperienceAsset[]>("/evaluation-experience-assets", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  createExperienceAsset: (data: {
    namespace_id: number;
    source_candidate_public_id: string;
    name: string;
    description?: string | null;
  }) =>
    api.post<EvaluationExperienceAsset>("/evaluation-experience-assets", data, {
      baseURL: "/api/v2",
    }),
  createExperienceAssetVersion: (
    assetPublicId: string,
    data: {
      body: string;
      applicability: string;
      change_summary?: string | null;
    },
  ) =>
    api.post<EvaluationExperienceAsset>(
      `/evaluation-experience-assets/${assetPublicId}/versions`,
      data,
      { baseURL: "/api/v2" },
    ),
  requestExperienceActivation: (
    versionPublicId: string,
    data: { request_note?: string | null },
  ) =>
    api.post<EvaluationExperienceAsset>(
      `/evaluation-experience-asset-versions/${versionPublicId}/activation-requests`,
      data,
      { baseURL: "/api/v2" },
    ),
  reviewExperienceActivation: (
    requestPublicId: string,
    data: {
      decision: EvaluationExperienceActivationDecision;
      comment?: string | null;
    },
  ) =>
    api.post<EvaluationExperienceAsset>(
      `/evaluation-experience-activation-requests/${requestPublicId}/reviews`,
      data,
      { baseURL: "/api/v2" },
    ),
  listSamplingPolicies: (namespaceId: number) =>
    api.get<EvaluationSamplingPolicy[]>("/evaluation-sampling-policies", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  createSamplingPolicy: (data: {
    namespace_id: number;
    name: string;
    description?: string | null;
  }) =>
    api.post<EvaluationSamplingPolicy>("/evaluation-sampling-policies", data, {
      baseURL: "/api/v2",
    }),
  createSamplingPolicyVersion: (
    policyPublicId: string,
    data: {
      strategy?: "STABLE_HASH";
      sample_size: number;
      minimum_sample_size: number;
      candidate_limit: number;
      observation_name?: string;
      observation_type?: string;
      environment?: string;
      root_only: boolean;
    },
  ) =>
    api.post<EvaluationSamplingPolicyVersion>(
      `/evaluation-sampling-policies/${policyPublicId}/versions`,
      data,
      { baseURL: "/api/v2" },
    ),
  runSamplingPolicyVersion: (
    versionPublicId: string,
    data: {
      dataset_public_id: string;
      from_start_time: string;
      to_start_time: string;
    },
    idempotencyKey: string,
  ) =>
    api.post<EvaluationSamplingRun>(
      `/evaluation-sampling-policy-versions/${versionPublicId}/runs`,
      data,
      {
        baseURL: "/api/v2",
        headers: { "Idempotency-Key": idempotencyKey },
      },
    ),
  listSamplingRuns: (namespaceId: number) =>
    api.get<EvaluationSamplingRun[]>("/evaluation-sampling-runs", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  createCurationBatch: (
    datasetPublicId: string,
    data: {
      items: Array<{ trace_id: string; observation_id: string }>;
    },
    idempotencyKey: string,
  ) =>
    api.post<EvaluationDatasetCurationBatch>(
      `/evaluation-datasets/${datasetPublicId}/curation-batches`,
      data,
      {
        baseURL: "/api/v2",
        headers: { "Idempotency-Key": idempotencyKey },
      },
    ),
  reviewCurationBatch: (
    batchPublicId: string,
    data: { decision: "APPROVED" | "REJECTED"; comment?: string | null },
    idempotencyKey: string,
  ) =>
    api.post<EvaluationDatasetCurationBatch>(
      `/evaluation-dataset-curation-batches/${batchPublicId}/reviews`,
      data,
      {
        baseURL: "/api/v2",
        headers: { "Idempotency-Key": idempotencyKey },
      },
    ),
  materializeCurationBatch: (
    batchPublicId: string,
    idempotencyKey: string,
  ) =>
    api.post<EvaluationDatasetCurationBatch>(
      `/evaluation-dataset-curation-batches/${batchPublicId}/materializations`,
      undefined,
      {
        baseURL: "/api/v2",
        headers: { "Idempotency-Key": idempotencyKey },
      },
    ),
  listEvaluations: (namespaceId: number) =>
    api.get<EvaluationRun[]>("/evaluations", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  listComparisons: (namespaceId: number) =>
    api.get<EvaluationComparison[]>("/evaluation-comparisons", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  listBindings: (namespaceId: number) =>
    api.get<ReleaseCandidateEvaluationBinding[]>("/release-evidence/bindings", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  reviewBinding: (
    data: {
      namespace_id: number;
      binding_public_id: string;
      decision: ReleaseCandidateReviewDecision;
      comment?: string | null;
    },
    idempotencyKey: string,
  ) =>
    api.post<ReleaseCandidateEvaluationReview>("/release-evidence/reviews", data, {
      baseURL: "/api/v2",
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  evaluateGate: (data: {
    namespace_id: number;
    release_candidate_ref: string;
    deployment_public_id: string;
    deployment_revision: string;
  }) =>
    api.post<ReleaseCandidateGateDecision>("/release-gates/evaluations", data, {
      baseURL: "/api/v2",
    }),
};

export type AgentPackageStatus = "ACTIVE" | "RETIRED";
export type AgentPackageVersionStatus = "VERIFIED";
export type PackageSigningKeyStatus = "ACTIVE" | "REVOKED";
export type PackageComponentType = "skill" | "prompt" | "tool" | "config" | "memory_schema" | "runtime_bundle";
export type PackageDependencyRelationship = "depends_on" | "uses" | "contains";
export type PackageSbomFormat = "cyclonedx-json" | "spdx-json";

export interface AgentPackage {
  public_id: string;
  namespace_id: number;
  namespace_ref: string;
  name: string;
  description: string | null;
  agent_asset_id: number;
  agent_asset_ref: string;
  status: AgentPackageStatus;
  created_by_user_id: number;
  created_at: string;
  updated_at: string;
  version_count: number;
}

export interface PackageSigningKey {
  public_id: string;
  namespace_id: number;
  key_id: string;
  algorithm: "ED25519";
  public_key_fingerprint: string;
  status: PackageSigningKeyStatus;
  created_by_user_id: number;
  revoked_by_user_id: number | null;
  revoked_at: string | null;
  rotated_from_id: number | null;
  rotation_sequence: number;
  created_at: string;
}

export interface AgentPackageArtifactManifest {
  type: PackageComponentType;
  asset_id: string;
  version_id: string;
  uri: string;
  sha256: string;
  media_type: string;
}

export interface AgentPackageManifest {
  schema_version: "2.0";
  package_id: string;
  package_version: string;
  namespace_id: string;
  agent: { asset_id: string; name: string; description?: string | null };
  runtime: {
    harness: string;
    entrypoint: string;
    minimum_harness_version: string;
    capabilities: string[];
  };
  artifacts: AgentPackageArtifactManifest[];
  provenance: {
    source_repository: string;
    source_revision: string;
    built_at: string;
    builder_id: string;
    build_id: string;
  };
  telemetry: {
    schema_version: "1.0";
    content_policy: "disabled" | "metadata_only" | "sampled_content" | "full_content";
    required_attributes: string[];
  };
  evaluation_policy_id: string;
  component_graph: {
    edges: Array<{
      from_component: string;
      to_component: string;
      relationship: PackageDependencyRelationship;
    }>;
  };
  sbom: {
    format: PackageSbomFormat;
    spec_version: string;
    document_sha256: string;
    media_type: "application/vnd.cyclonedx+json" | "application/spdx+json";
  };
  annotations?: Record<string, string | null> | null;
}

export interface AgentPackageComponent {
  position: number;
  component_ref: string;
  component_type: PackageComponentType;
  asset_ref: string;
  version_ref: string;
  uri: string;
  sha256: string;
  media_type: string;
}

export interface AgentPackageDependency {
  from_component: string;
  to_component: string;
  relationship: PackageDependencyRelationship;
}

export interface AgentPackageSbom {
  public_id: string;
  format: PackageSbomFormat;
  spec_version: string;
  media_type: string;
  document_sha256: string;
  size_bytes: number;
  component_count: number;
  stored_at: string;
}

export interface AgentPackageVersion {
  public_id: string;
  namespace_id: number;
  package_public_id: string;
  package_name: string;
  version: string;
  status: AgentPackageVersionStatus;
  manifest_schema_version: string;
  manifest: AgentPackageManifest;
  manifest_digest: string;
  graph_digest: string;
  evaluation_policy_id: string;
  provenance_digest: string;
  signing_key_public_id: string;
  signing_key_id: string;
  signing_key_fingerprint: string;
  signing_key_status: PackageSigningKeyStatus;
  signature_algorithm: "ED25519";
  signature: string;
  signature_digest: string;
  signature_verified_at: string;
  idempotency_key: string;
  created_by_user_id: number;
  created_at: string;
  components: AgentPackageComponent[];
  dependencies: AgentPackageDependency[];
  sbom: AgentPackageSbom;
}

export interface AgentPackageVerification {
  package_version_public_id: string;
  manifest_digest: string;
  graph_digest: string;
  provenance_digest: string;
  signature_valid: boolean;
  signature_verified_at: string;
  signing_key_status: PackageSigningKeyStatus;
  signing_key_fingerprint: string;
  sbom_digest_valid: boolean;
  sbom_component_coverage_valid: boolean;
  sbom_document_sha256: string;
  verified: boolean;
}

export interface PackageSbomDownload {
  package_version_public_id: string;
  sbom_public_id: string;
  document_sha256: string;
  download_url: string;
  expires_in: number;
  expires_at: string;
}

export type AgentDeploymentStatus = "REGISTERED" | "ACTIVE" | "FAILED" | "RETIRED";

export interface AgentDeployment {
  public_id: string;
  namespace_id: number;
  runtime_id: number;
  agent_asset_id: number | null;
  package_version_public_id: string | null;
  external_deployment_id: string;
  environment: string;
  revision: string;
  configuration_digest: string;
  status: AgentDeploymentStatus;
  activated_at: string | null;
  retired_at: string | null;
  created_by_user_id: number;
  created_at: string;
  components: Array<{
    id: number;
    component_key: string;
    component_role: string;
    ai_asset_id: number | null;
    skill_version_id: number | null;
    external_version: string | null;
    content_digest: string | null;
    configuration_json: Record<string, unknown> | null;
    created_at: string;
  }>;
}

export type ReleaseEnvironmentKind = "DEVELOPMENT" | "STAGING" | "CANARY" | "PRODUCTION";
export type ReleasePolicyMode = "SHADOW" | "WARN" | "ENFORCE";
export type ReleasePolicyRawOutcome = "PASS" | "FAIL";
export type ReleasePolicyEnforcementOutcome = "ALLOW" | "WARN" | "BLOCK";
export type ReleasePolicyRuleVerdict = "PASS" | "FAIL" | "UNKNOWN" | "NOT_APPLICABLE";
export type ReleasePromotionStrategy = "ALL_AT_ONCE" | "CANARY" | "ROLLING";
export type ReleasePromotionStatus =
  | "DISPATCHED"
  | "OBSERVING"
  | "SUCCEEDED"
  | "FAILED"
  | "ROLLBACK_REQUESTED"
  | "ROLLED_BACK";
export type ReleaseReceiptStatus = "APPLIED" | "FAILED" | "MISMATCH";

export interface ReleaseEnvironment {
  public_id: string;
  namespace_id: number;
  name: string;
  kind: ReleaseEnvironmentKind;
  promotion_order: number;
  protected: boolean;
  minimum_approvals: number;
  requires_canary: boolean;
  status: "ACTIVE" | "RETIRED";
  created_by_user_id: number;
  created_at: string;
}

export interface ReleasePolicyRule {
  rule_id: string;
  rule_type:
    | "PACKAGE_EVIDENCE_VERIFIED"
    | "SIGNING_KEY_ACTIVE"
    | "EVALUATION_GATE_PASS"
    | "RISK_TIER_ALLOWED"
    | "MAX_TOOL_ADDITIONS"
    | "FORBID_CAPABILITY_EXPANSION"
    | "MAX_VULNERABILITY_SEVERITY"
    | "ROLLBACK_TARGET_REQUIRED";
  allowed_risk_tiers?: string[] | null;
  maximum_additions?: number | null;
  allowed_capabilities?: string[] | null;
  maximum_severity?: "NONE" | "UNKNOWN" | "LOW" | "MEDIUM" | "HIGH" | "CRITICAL" | null;
  required?: boolean;
}

export interface ReleasePolicyVersion {
  public_id: string;
  namespace_id: number;
  policy_public_id: string;
  version: number;
  target_environment_public_id: string;
  target_environment_name: string;
  mode: ReleasePolicyMode;
  rules: ReleasePolicyRule[];
  rule_count: number;
  rules_digest: string;
  content_digest: string;
  schema_name: string;
  schema_version: string;
  created_by_user_id: number;
  created_at: string;
}

export interface ReleasePolicy {
  public_id: string;
  namespace_id: number;
  name: string;
  description: string | null;
  status: "ACTIVE" | "RETIRED";
  created_by_user_id: number;
  created_at: string;
  versions: ReleasePolicyVersion[];
}

export interface ReleaseCandidate {
  public_id: string;
  namespace_id: number;
  package_public_id: string;
  package_name: string;
  package_version_public_id: string;
  package_version: string;
  package_manifest_digest: string;
  deployment_public_id: string;
  deployment_revision: string;
  deployment_configuration_digest: string;
  runtime_id: number;
  target_environment_public_id: string;
  target_environment_name: string;
  target_environment_kind: ReleaseEnvironmentKind;
  baseline_candidate_public_id: string | null;
  idempotency_key: string;
  candidate_digest: string;
  schema_name: string;
  schema_version: string;
  created_by_user_id: number;
  created_at: string;
}

export interface ReleasePolicyRuleResult {
  position: number;
  rule_id: string;
  rule_type: ReleasePolicyRule["rule_type"];
  verdict: ReleasePolicyRuleVerdict;
  reason_code: string;
  evidence_kind: string;
  evidence_ref: string;
  evidence_digest: string;
  metrics: Record<string, unknown>;
  observed_at: string;
}

export interface ReleasePolicyDecision {
  public_id: string;
  namespace_id: number;
  candidate_public_id: string;
  policy_public_id: string;
  policy_version_public_id: string;
  policy_version: number;
  policy_mode: ReleasePolicyMode;
  raw_outcome: ReleasePolicyRawOutcome;
  enforcement_outcome: ReleasePolicyEnforcementOutcome;
  would_block: boolean;
  reason_codes: string[];
  evidence_snapshot_digest: string;
  decision_digest: string;
  evaluation_duration_ms: number;
  schema_name: string;
  schema_version: string;
  evaluated_by_user_id: number;
  created_at: string;
  rule_results: ReleasePolicyRuleResult[];
}

export interface ReleaseCandidateApproval {
  public_id: string;
  namespace_id: number;
  candidate_public_id: string;
  policy_decision_public_id: string;
  decision: "APPROVED" | "REJECTED";
  role: "OWNER" | "REVIEWER" | "SECURITY" | "OPERATIONS" | "RELEASE_MANAGER";
  comment: string | null;
  approval_digest: string;
  reviewed_by_user_id: number;
  created_at: string;
}

export interface ReleasePolicyException {
  public_id: string;
  namespace_id: number;
  candidate_public_id: string;
  policy_decision_public_id: string;
  waived_rule_ids: string[];
  waived_rule_count: number;
  reason: string;
  expires_at: string;
  exception_digest: string;
  requested_by_user_id: number;
  created_at: string;
  effective: boolean;
  review: {
    public_id: string;
    decision: "APPROVED" | "REJECTED";
    comment: string | null;
    review_digest: string;
    reviewed_by_user_id: number;
    created_at: string;
  } | null;
}

export interface ReleaseCanaryConfig {
  minimum_completed_runs: number;
  maximum_failure_rate: number;
  maximum_untrusted_rate: number;
  observation_window_seconds: number;
}

export interface ReleasePromotion {
  public_id: string;
  dispatch_public_id: string;
  namespace_id: number;
  candidate_public_id: string;
  policy_decision_public_id: string;
  source_environment_public_id: string | null;
  target_environment_public_id: string;
  target_environment_name: string;
  strategy: ReleasePromotionStrategy;
  status: ReleasePromotionStatus;
  acknowledge_warnings: boolean;
  canary: ReleaseCanaryConfig | null;
  exception_digest: string | null;
  approval_digest: string;
  dispatch_digest: string;
  requested_by_user_id: number;
  created_at: string;
  updated_at: string;
}

export interface ReleaseDeploymentReceipt {
  public_id: string;
  namespace_id: number;
  runtime_id: number;
  reporter_credential_id: number;
  kind: "PROMOTION" | "ROLLBACK";
  dispatch_public_id: string;
  external_receipt_id: string;
  status: ReleaseReceiptStatus;
  observed_package_version_public_id: string;
  observed_deployment_revision: string;
  observed_configuration_digest: string;
  runtime_release_ref: string | null;
  error_code: string | null;
  receipt_digest: string;
  occurred_at: string;
  created_at: string;
}

export interface ReleaseReceiptCredential {
  id: number;
  runtime_id: number;
  user_id: number | null;
  device_id: string;
  name: string;
  token_prefix: string;
  scopes: string[] | null;
  is_active: boolean;
  expires_at: string | null;
  revoked_at: string | null;
  revoked_by: number | null;
  revoked_reason: string | null;
  rotated_from_id: number | null;
  last_used_at: string | null;
  last_heartbeat_at: string | null;
  heartbeat_json: Record<string, unknown> | null;
  metadata_json: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface ReleaseReceiptCredentialCreated extends ReleaseReceiptCredential {
  token: string;
}

export interface ReleaseEnvironmentRelease {
  public_id: string;
  namespace_id: number;
  environment_public_id: string;
  candidate_public_id: string;
  promotion_public_id: string | null;
  rollback_public_id: string | null;
  receipt_public_id: string;
  previous_release_public_id: string | null;
  status: "ACTIVE" | "SUPERSEDED" | "ROLLED_BACK";
  activation_digest: string;
  activated_at: string;
  deactivated_at: string | null;
}

export interface ReleaseCanaryEvaluation {
  public_id: string;
  namespace_id: number;
  promotion_public_id: string;
  outcome: "PASS" | "FAIL" | "INCONCLUSIVE";
  reason_codes: string[];
  window_start: string;
  window_end: string;
  completed_run_count: number;
  failed_run_count: number;
  untrusted_run_count: number;
  failure_rate: number;
  untrusted_rate: number;
  evidence_digest: string;
  decision_digest: string;
  evaluated_by_user_id: number;
  created_at: string;
}

export interface ReleaseRollback {
  public_id: string;
  dispatch_public_id: string;
  namespace_id: number;
  promotion_public_id: string;
  environment_public_id: string;
  source_candidate_public_id: string;
  target_environment_release_public_id: string;
  target_candidate_public_id: string;
  status: "DISPATCHED" | "SUCCEEDED" | "FAILED";
  reason_code: string;
  dispatch_digest: string;
  requested_by_user_id: number;
  created_at: string;
  completed_at: string | null;
}

export const deploymentApi = {
  list: (namespaceId: number) =>
    api.get<AgentDeployment[]>("/deployments", { params: { namespace_id: namespaceId } }),
};

export const packageRegistryApi = {
  listPackages: (namespaceId: number) =>
    api.get<AgentPackage[]>("/agent-packages", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  createPackage: (data: {
    namespace_id: number;
    name: string;
    description?: string | null;
    agent_asset_id: number;
  }) => api.post<AgentPackage>("/agent-packages", data, { baseURL: "/api/v2" }),
  getPackage: (publicId: string) =>
    api.get<AgentPackage>(`/agent-packages/${publicId}`, { baseURL: "/api/v2" }),
  listVersions: (packagePublicId: string) =>
    api.get<AgentPackageVersion[]>(`/agent-packages/${packagePublicId}/versions`, {
      baseURL: "/api/v2",
    }),
  getVersion: (publicId: string) =>
    api.get<AgentPackageVersion>(`/agent-package-versions/${publicId}`, { baseURL: "/api/v2" }),
  createVersion: (data: {
    namespace_id: number;
    package_public_id: string;
    signing_key_public_id: string;
    signature: string;
    idempotency_key: string;
    manifest: AgentPackageManifest;
    sbom_document: Record<string, unknown>;
  }) => api.post<AgentPackageVersion>("/agent-package-versions", data, { baseURL: "/api/v2" }),
  verifyVersion: (publicId: string) =>
    api.post<AgentPackageVerification>(`/agent-package-versions/${publicId}/verify`, undefined, {
      baseURL: "/api/v2",
    }),
  getSbomDownload: (publicId: string) =>
    api.get<PackageSbomDownload>(`/agent-package-versions/${publicId}/sbom/download`, {
      baseURL: "/api/v2",
    }),
  listSigningKeys: (namespaceId: number) =>
    api.get<PackageSigningKey[]>("/package-signing-keys", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  createSigningKey: (data: {
    namespace_id: number;
    key_id: string;
    algorithm: "ED25519";
    public_key_pem: string;
  }) => api.post<PackageSigningKey>("/package-signing-keys", data, { baseURL: "/api/v2" }),
  revokeSigningKey: (publicId: string) =>
    api.post<PackageSigningKey>(`/package-signing-keys/${publicId}/revoke`, undefined, {
      baseURL: "/api/v2",
    }),
  rotateSigningKey: (
    publicId: string,
    data: { key_id: string; algorithm: "ED25519"; public_key_pem: string },
  ) =>
    api.post<PackageSigningKey>(`/package-signing-keys/${publicId}/rotate`, data, {
      baseURL: "/api/v2",
    }),
  canonicalizeManifest: (namespaceId: number, manifest: AgentPackageManifest) =>
    api.post<{ schema_version: string; manifest_digest: string; canonical_manifest: string; size_bytes: number }>(
      "/agent-package-manifests/canonicalize",
      manifest,
      { baseURL: "/api/v2", params: { namespace_id: namespaceId } },
    ),
};

export const releaseControlApi = {
  listEnvironments: (namespaceId: number) =>
    api.get<ReleaseEnvironment[]>("/release-environments", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  createEnvironment: (data: {
    namespace_id: number;
    name: string;
    kind: ReleaseEnvironmentKind;
    promotion_order: number;
    protected: boolean;
    minimum_approvals: number;
    requires_canary: boolean;
  }) => api.post<ReleaseEnvironment>("/release-environments", data, { baseURL: "/api/v2" }),
  listPolicies: (namespaceId: number) =>
    api.get<ReleasePolicy[]>("/release-policies", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  createPolicy: (data: { namespace_id: number; name: string; description?: string | null }) =>
    api.post<ReleasePolicy>("/release-policies", data, { baseURL: "/api/v2" }),
  createPolicyVersion: (
    policyPublicId: string,
    data: {
      namespace_id: number;
      target_environment_public_id: string;
      mode: ReleasePolicyMode;
      rules: ReleasePolicyRule[];
    },
  ) =>
    api.post<ReleasePolicyVersion>(`/release-policies/${policyPublicId}/versions`, data, {
      baseURL: "/api/v2",
    }),
  listCandidates: (namespaceId: number) =>
    api.get<ReleaseCandidate[]>("/release-candidates", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  createCandidate: (data: {
    namespace_id: number;
    package_version_public_id: string;
    deployment_public_id: string;
    target_environment_public_id: string;
    baseline_candidate_public_id?: string | null;
    idempotency_key: string;
  }) => api.post<ReleaseCandidate>("/release-candidates", data, { baseURL: "/api/v2" }),
  listDecisions: (namespaceId: number) =>
    api.get<ReleasePolicyDecision[]>("/release-policy-decisions", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  evaluateCandidate: (
    candidatePublicId: string,
    data: {
      namespace_id: number;
      policy_version_public_id: string;
      idempotency_key: string;
    },
  ) =>
    api.post<ReleasePolicyDecision>(
      `/release-candidates/${candidatePublicId}/policy-evaluations`,
      data,
      { baseURL: "/api/v2" },
    ),
  listApprovals: (namespaceId: number, candidatePublicId: string) =>
    api.get<ReleaseCandidateApproval[]>(`/release-candidates/${candidatePublicId}/approvals`, {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  approveCandidate: (
    candidatePublicId: string,
    data: {
      namespace_id: number;
      policy_decision_public_id: string;
      decision: "APPROVED" | "REJECTED";
      role: ReleaseCandidateApproval["role"];
      comment?: string | null;
      idempotency_key: string;
    },
  ) =>
    api.post<ReleaseCandidateApproval>(`/release-candidates/${candidatePublicId}/approvals`, data, {
      baseURL: "/api/v2",
    }),
  listExceptions: (namespaceId: number) =>
    api.get<ReleasePolicyException[]>("/release-policy-exceptions", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  createException: (data: {
    namespace_id: number;
    policy_decision_public_id: string;
    waived_rule_ids: string[];
    reason: string;
    expires_at: string;
    idempotency_key: string;
  }) => api.post<ReleasePolicyException>("/release-policy-exceptions", data, { baseURL: "/api/v2" }),
  reviewException: (
    publicId: string,
    data: {
      namespace_id: number;
      decision: "APPROVED" | "REJECTED";
      comment?: string | null;
      idempotency_key: string;
    },
  ) =>
    api.post<ReleasePolicyException>(`/release-policy-exceptions/${publicId}/review`, data, {
      baseURL: "/api/v2",
    }),
  listPromotions: (namespaceId: number) =>
    api.get<ReleasePromotion[]>("/release-promotions", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  createPromotion: (data: {
    namespace_id: number;
    candidate_public_id: string;
    policy_decision_public_id: string;
    strategy: ReleasePromotionStrategy;
    acknowledge_warnings: boolean;
    canary?: ReleaseCanaryConfig | null;
    idempotency_key: string;
  }) => api.post<ReleasePromotion>("/release-promotions", data, { baseURL: "/api/v2" }),
  evaluateCanary: (promotionPublicId: string, namespaceId: number, idempotencyKey: string) =>
    api.post<ReleaseCanaryEvaluation>(
      `/release-promotions/${promotionPublicId}/canary-evaluations`,
      { namespace_id: namespaceId, idempotency_key: idempotencyKey },
      { baseURL: "/api/v2" },
    ),
  requestRollback: (promotionPublicId: string, namespaceId: number, reasonCode: string, idempotencyKey: string) =>
    api.post<ReleaseRollback>(
      `/release-promotions/${promotionPublicId}/rollback`,
      { namespace_id: namespaceId, reason_code: reasonCode, idempotency_key: idempotencyKey },
      { baseURL: "/api/v2" },
    ),
  listRollbacks: (namespaceId: number) =>
    api.get<ReleaseRollback[]>("/release-rollbacks", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  listCanaryEvaluations: (namespaceId: number) =>
    api.get<ReleaseCanaryEvaluation[]>("/release-canary-evaluations", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  listEnvironmentReleases: (namespaceId: number) =>
    api.get<ReleaseEnvironmentRelease[]>("/release-environment-releases", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  listReceipts: (namespaceId: number) =>
    api.get<ReleaseDeploymentReceipt[]>("/release-deployment-receipts", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  listReceiptCredentials: (namespaceId: number) =>
    api.get<ReleaseReceiptCredential[]>("/release-receipt-credentials", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  createReceiptCredential: (data: {
    namespace_id: number;
    runtime_id: number;
    device_id: string;
    name: string;
    expires_at?: string | null;
  }) =>
    api.post<ReleaseReceiptCredentialCreated>("/release-receipt-credentials", data, {
      baseURL: "/api/v2",
    }),
  revokeReceiptCredential: (credentialId: number, reason: string) =>
    api.post<ReleaseReceiptCredential>(
      `/release-receipt-credentials/${credentialId}/revoke`,
      { reason },
      { baseURL: "/api/v2" },
    ),
};

export type OpsSLOEvaluationStatus = "HEALTHY" | "DEGRADED" | "BREACHED";
export type OpsIncidentStatus = "OPEN" | "ACKNOWLEDGED" | "RESOLVED";
export type OpsIncidentSeverity = "WARNING" | "CRITICAL";
export type OpsRecoveryDrillStatus = "PASSED" | "FAILED";
export type GACheckStatus = "PASS" | "WARN" | "BLOCK";
export type GAReadinessStatus = "READY" | "READY_WITH_GAPS" | "BLOCKED";

export interface GAReadinessCheck {
  key: string;
  title: string;
  status: GACheckStatus;
  observed: string;
  expected: string;
  detail: string;
}

export interface GAReadiness {
  profile_version: string;
  contract_version: string;
  contract_digest: string;
  expected_db_revision: string;
  current_db_revision: string | null;
  status: GAReadinessStatus;
  pass_count: number;
  warn_count: number;
  block_count: number;
  checked_at: string;
  checks: GAReadinessCheck[];
}

export interface OpsSLOEvaluation {
  public_id: string;
  idempotency_key: string;
  profile_version: string;
  window_minutes: number;
  request_count: number;
  error_count: number;
  http_error_ratio: number | null;
  evidence_ingest_p95_ms: number | null;
  run_timeline_p95_ms: number | null;
  policy_decision_p95_ms: number | null;
  outbox_failed_count: number;
  outbox_oldest_pending_age_seconds: number | null;
  status: OpsSLOEvaluationStatus;
  reason_codes: string[];
  evidence_digest: string;
  evaluated_by_user_id: number;
  evaluated_at: string;
  created_at: string;
}

export interface OpsIncident {
  public_id: string;
  slo_evaluation_id: number;
  severity: OpsIncidentSeverity;
  status: OpsIncidentStatus;
  reason_codes: string[];
  evidence_digest: string;
  acknowledged_by_user_id: number | null;
  acknowledged_at: string | null;
  resolved_by_user_id: number | null;
  resolved_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface OpsRecoveryDrill {
  public_id: string;
  idempotency_key: string;
  environment: string;
  git_head: string;
  backup_set_digest: string;
  mysql_digest: string;
  object_store_digest: string;
  mysql_row_count: number;
  object_count: number;
  rpo_seconds: number;
  rto_seconds: number;
  status: OpsRecoveryDrillStatus;
  reason_codes: string[];
  evidence_digest: string;
  executed_by_user_id: number;
  started_at: string;
  finished_at: string;
  created_at: string;
}

export interface OpsRouteMetric {
  route: string;
  method: string;
  request_count: number;
  error_count: number;
  p95_ms: number | null;
}

export interface OpsOverview {
  profile_version: string;
  metrics_path: string;
  thresholds: {
    http_error_ratio_max: number;
    evidence_ingest_p95_ms_max: number;
    run_timeline_p95_ms_max: number;
    policy_decision_p95_ms_max: number;
    outbox_failed_count_max: number;
    outbox_pending_age_seconds_max: number;
    recovery_rpo_seconds_max: number;
    recovery_rto_seconds_max: number;
  };
  route_metrics: OpsRouteMetric[];
  outbox: {
    pending_count: number;
    leased_count: number;
    expired_lease_count: number;
    failed_count: number;
    published_count: number;
    oldest_pending_age_seconds: number | null;
  };
  latest_evaluation: OpsSLOEvaluation | null;
  open_incidents: OpsIncident[];
  latest_recovery_drill: OpsRecoveryDrill | null;
}

export const operationsApi = {
  getGAReadiness: () =>
    api.get<GAReadiness>("/operations/ga-readiness", { baseURL: "/api/v2" }),
  getOverview: () => api.get<OpsOverview>("/operations/overview", { baseURL: "/api/v2" }),
  listEvaluations: () =>
    api.get<OpsSLOEvaluation[]>("/operations/slo-evaluations", { baseURL: "/api/v2" }),
  evaluate: (idempotencyKey: string, windowMinutes = 15) =>
    api.post<OpsSLOEvaluation>(
      "/operations/slo-evaluations",
      { idempotency_key: idempotencyKey, window_minutes: windowMinutes },
      { baseURL: "/api/v2" },
    ),
  listIncidents: () =>
    api.get<OpsIncident[]>("/operations/incidents", { baseURL: "/api/v2" }),
  acknowledgeIncident: (publicId: string, note: string) =>
    api.post<OpsIncident>(
      `/operations/incidents/${publicId}/acknowledge`,
      { note },
      { baseURL: "/api/v2" },
    ),
  resolveIncident: (publicId: string, note: string) =>
    api.post<OpsIncident>(
      `/operations/incidents/${publicId}/resolve`,
      { note },
      { baseURL: "/api/v2" },
    ),
  listRecoveryDrills: () =>
    api.get<OpsRecoveryDrill[]>("/operations/recovery-drills", { baseURL: "/api/v2" }),
};

export const iamApi = {
  getMyPermissions: (params?: { namespace_id?: number; org_unit_id?: number }) =>
    api.get<EffectivePermission>("/iam/me/permissions", { params }),
  listUsers: () => api.get<UserProfile[]>("/iam/users"),
  listOrgUnits: () => api.get<OrgUnit[]>("/iam/org-units"),
  listRoles: () => api.get<Role[]>("/iam/roles"),
  listSSOProviderConfigs: () => api.get<SSOProviderConfig[]>("/iam/sso/providers"),
  listIdentityLinks: (params?: { user_id?: number; provider_id?: number }) =>
    api.get<IdentityLink[]>("/iam/sso/identity-links", { params }),
  listDirectoryCredentials: (params?: { provider_id?: number }) =>
    api.get<DirectoryCredential[]>("/identity/directory-credentials", {
      baseURL: "/api/v2",
      params,
    }),
  createDirectoryCredential: (data: { provider_id: number; name: string; expires_at?: string | null }) =>
    api.post<DirectoryCredentialCreated>("/identity/directory-credentials", data, {
      baseURL: "/api/v2",
    }),
  revokeDirectoryCredential: (publicId: string, reason: string) =>
    api.post<DirectoryCredential>(
      `/identity/directory-credentials/${publicId}/revoke`,
      { reason },
      { baseURL: "/api/v2" },
    ),
  listDirectoryEvents: (params?: { provider_id?: number; user_id?: number }) =>
    api.get<DirectoryLifecycleEvent[]>("/identity/directory-events", {
      baseURL: "/api/v2",
      params,
    }),
  listWorkloadIdentities: (namespaceId: number) =>
    api.get<WorkloadIdentity[]>("/identity/workload-identities", {
      baseURL: "/api/v2",
      params: { namespace_id: namespaceId },
    }),
  listSSORoleMappings: (params?: { provider_id?: number }) =>
    api.get<SSORoleMapping[]>("/iam/sso/role-mappings", { params }),
  createSSORoleMapping: (data: SSORoleMappingCreate) =>
    api.post<SSORoleMapping>("/iam/sso/role-mappings", data),
  updateSSORoleMapping: (mappingId: number, data: SSORoleMappingUpdate) =>
    api.patch<SSORoleMapping>(`/iam/sso/role-mappings/${mappingId}`, data),
  deleteSSORoleMapping: (mappingId: number) =>
    api.delete(`/iam/sso/role-mappings/${mappingId}`),
};
