import axios from "axios";
import { message } from "antd";
import { useAuthStore } from "../store/auth";

const api = axios.create({ baseURL: "/api/v1" });
const publicApi = axios.create({ baseURL: "/api/v1" });

// 403 全局提示节流:权限收口(specs/003)后未绑角色的用户会集中撞 403,避免 toast 刷屏
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
  due_at: string | null;
}>;

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

export interface DemoDataCleanupResult {
  deleted: Record<string, number>;
  protected: Record<string, number>;
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
  last_seen_at: string | null;
  created_at: string;
  updated_at: string;
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

// 交接项:LLM/规则顾问产出的建议(T042 加 confidence;原则 V——仅建议,需人工审批)
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

// 证据条目(列表已按 ownership 过滤敏感项,specs/003 FR-002)
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

// 证据限时下载链接(FR-009)。指向报告包内部时,download_url 为所在归档,
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
  cleanupDemoData: () => api.delete<DemoDataCleanupResult>("/demo-data"),
  createHandover: (data: {
    namespace_id: number;
    case_type: HandoverCaseType;
    title: string;
    subject_user_id?: number | null;
    receiver_user_id?: number | null;
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
  executeHandover: (caseId: number, data: { selected_item_ids?: number[] | null; execution_mode?: ExecutionMode; idempotency_key?: string | null }) =>
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
  // FR-009:reason 必填(≥5 字,进审计);敏感证据非创建者需 evidence.sensitive.read。
  requestEvidenceDownloadLink: (evidenceId: number, data: { reason: string; expires_in?: number }) =>
    api.post<EvidenceDownloadLink>(`/evidence/${evidenceId}/download-link`, data),
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

export const iamApi = {
  getMyPermissions: (params?: { namespace_id?: number; org_unit_id?: number }) =>
    api.get<EffectivePermission>("/iam/me/permissions", { params }),
  listUsers: () => api.get<UserProfile[]>("/iam/users"),
  listOrgUnits: () => api.get<OrgUnit[]>("/iam/org-units"),
  listRoles: () => api.get<Role[]>("/iam/roles"),
  listSSOProviderConfigs: () => api.get<SSOProviderConfig[]>("/iam/sso/providers"),
  listIdentityLinks: (params?: { user_id?: number; provider_id?: number }) =>
    api.get<IdentityLink[]>("/iam/sso/identity-links", { params }),
  listSSORoleMappings: (params?: { provider_id?: number }) =>
    api.get<SSORoleMapping[]>("/iam/sso/role-mappings", { params }),
  createSSORoleMapping: (data: SSORoleMappingCreate) =>
    api.post<SSORoleMapping>("/iam/sso/role-mappings", data),
  updateSSORoleMapping: (mappingId: number, data: SSORoleMappingUpdate) =>
    api.patch<SSORoleMapping>(`/iam/sso/role-mappings/${mappingId}`, data),
  deleteSSORoleMapping: (mappingId: number) =>
    api.delete(`/iam/sso/role-mappings/${mappingId}`),
};
