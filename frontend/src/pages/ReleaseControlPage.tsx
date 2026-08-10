import {
  AlertTriangle,
  CheckCircle2,
  GitBranch,
  History,
  Play,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  deploymentApi,
  namespacesApi,
  packageRegistryApi,
  releaseControlApi,
  type AgentDeployment,
  type AgentPackageVersion,
  type Namespace,
  type ReleaseCandidate,
  type ReleaseCanaryEvaluation,
  type ReleaseDeploymentReceipt,
  type ReleaseEnvironment,
  type ReleaseEnvironmentKind,
  type ReleaseEnvironmentRelease,
  type ReleasePolicy,
  type ReleasePolicyDecision,
  type ReleasePolicyException,
  type ReleasePolicyMode,
  type ReleasePolicyRule,
  type ReleasePromotion,
  type ReleasePromotionStrategy,
  type ReleaseReceiptCredential,
  type ReleaseRollback,
} from "../api/client";
import { Badge, Button, Card, EmptyState, MetricCard, MonoPill, PageHeader } from "../components/ui";

function errorDetail(error: unknown): string {
  if (typeof error === "object" && error !== null) {
    const response = (error as { response?: { data?: { detail?: unknown } } }).response;
    if (typeof response?.data?.detail === "string") return response.data.detail;
    if (Array.isArray(response?.data?.detail)) return "提交内容不符合 Release Control 契约。";
  }
  return "操作失败，请检查权限、服务状态与输入。";
}

function operationKey(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function formatDate(value: string | null): string {
  if (!value) return "-";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function shortDigest(value: string): string {
  return `${value.slice(0, 9)}…${value.slice(-7)}`;
}

function statusTone(value: string): "emerald" | "amber" | "rose" | "indigo" | "violet" | "neutral" {
  if (["PASS", "ALLOW", "APPROVED", "APPLIED", "ACTIVE", "SUCCEEDED"].includes(value)) return "emerald";
  if (["WARN", "OBSERVING", "DISPATCHED", "ROLLBACK_REQUESTED", "INCONCLUSIVE"].includes(value)) return "amber";
  if (["FAIL", "BLOCK", "FAILED", "REJECTED", "MISMATCH"].includes(value)) return "rose";
  if (value === "ROLLED_BACK") return "violet";
  return "neutral";
}

const STANDARD_RULES: ReleasePolicyRule[] = [
  { rule_id: "package-evidence", rule_type: "PACKAGE_EVIDENCE_VERIFIED" },
  { rule_id: "signing-key", rule_type: "SIGNING_KEY_ACTIVE" },
  { rule_id: "evaluation-gate", rule_type: "EVALUATION_GATE_PASS" },
  {
    rule_id: "risk-tier",
    rule_type: "RISK_TIER_ALLOWED",
    allowed_risk_tiers: ["low", "medium", "high"],
  },
  { rule_id: "tool-additions", rule_type: "MAX_TOOL_ADDITIONS", maximum_additions: 2 },
  {
    rule_id: "capability-expansion",
    rule_type: "FORBID_CAPABILITY_EXPANSION",
    allowed_capabilities: ["model_call", "tool_call"],
  },
  {
    rule_id: "vulnerability-threshold",
    rule_type: "MAX_VULNERABILITY_SEVERITY",
    maximum_severity: "HIGH",
  },
  { rule_id: "rollback-target", rule_type: "ROLLBACK_TARGET_REQUIRED", required: false },
];

export default function ReleaseControlPage() {
  const [namespaces, setNamespaces] = useState<Namespace[]>([]);
  const [namespaceId, setNamespaceId] = useState<number | null>(null);
  const [environments, setEnvironments] = useState<ReleaseEnvironment[]>([]);
  const [policies, setPolicies] = useState<ReleasePolicy[]>([]);
  const [candidates, setCandidates] = useState<ReleaseCandidate[]>([]);
  const [decisions, setDecisions] = useState<ReleasePolicyDecision[]>([]);
  const [exceptions, setExceptions] = useState<ReleasePolicyException[]>([]);
  const [promotions, setPromotions] = useState<ReleasePromotion[]>([]);
  const [rollbacks, setRollbacks] = useState<ReleaseRollback[]>([]);
  const [canaryEvaluations, setCanaryEvaluations] = useState<ReleaseCanaryEvaluation[]>([]);
  const [releases, setReleases] = useState<ReleaseEnvironmentRelease[]>([]);
  const [receipts, setReceipts] = useState<ReleaseDeploymentReceipt[]>([]);
  const [receiptCredentials, setReceiptCredentials] = useState<ReleaseReceiptCredential[]>([]);
  const [deployments, setDeployments] = useState<AgentDeployment[]>([]);
  const [packageVersions, setPackageVersions] = useState<AgentPackageVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [environmentName, setEnvironmentName] = useState("staging");
  const [environmentKind, setEnvironmentKind] = useState<ReleaseEnvironmentKind>("STAGING");
  const [promotionOrder, setPromotionOrder] = useState(20);
  const [protectedEnvironment, setProtectedEnvironment] = useState(false);
  const [minimumApprovals, setMinimumApprovals] = useState(0);
  const [requiresCanary, setRequiresCanary] = useState(false);
  const [policyName, setPolicyName] = useState("standard-release");
  const [policyEnvironmentId, setPolicyEnvironmentId] = useState("");
  const [policyMode, setPolicyMode] = useState<ReleasePolicyMode>("SHADOW");
  const [candidateDeploymentId, setCandidateDeploymentId] = useState("");
  const [candidatePackageVersionId, setCandidatePackageVersionId] = useState("");
  const [candidateEnvironmentId, setCandidateEnvironmentId] = useState("");
  const [baselineCandidateId, setBaselineCandidateId] = useState("");
  const [selectedCandidateId, setSelectedCandidateId] = useState("");
  const [selectedPolicyVersionId, setSelectedPolicyVersionId] = useState("");
  const [selectedDecisionId, setSelectedDecisionId] = useState("");
  const [promotionStrategy, setPromotionStrategy] = useState<ReleasePromotionStrategy>("ALL_AT_ONCE");
  const [acknowledgeWarnings, setAcknowledgeWarnings] = useState(false);
  const [exceptionReason, setExceptionReason] = useState("");
  const [credentialRuntimeId, setCredentialRuntimeId] = useState("");
  const [credentialName, setCredentialName] = useState("release-receipt-reporter");
  const [generatedCredentialToken, setGeneratedCredentialToken] = useState("");

  const loadAll = useCallback(async (targetNamespaceId: number) => {
    setLoading(true);
    setError("");
    try {
      const [
        environmentResponse,
        policyResponse,
        candidateResponse,
        decisionResponse,
        exceptionResponse,
        promotionResponse,
        rollbackResponse,
        canaryResponse,
        releaseResponse,
        receiptResponse,
        receiptCredentialResponse,
        deploymentResponse,
        packageResponse,
      ] = await Promise.all([
        releaseControlApi.listEnvironments(targetNamespaceId),
        releaseControlApi.listPolicies(targetNamespaceId),
        releaseControlApi.listCandidates(targetNamespaceId),
        releaseControlApi.listDecisions(targetNamespaceId),
        releaseControlApi.listExceptions(targetNamespaceId),
        releaseControlApi.listPromotions(targetNamespaceId),
        releaseControlApi.listRollbacks(targetNamespaceId),
        releaseControlApi.listCanaryEvaluations(targetNamespaceId),
        releaseControlApi.listEnvironmentReleases(targetNamespaceId),
        releaseControlApi.listReceipts(targetNamespaceId),
        // Credential metadata is intentionally namespace-admin-only. Keep the
        // rest of Release Control usable for read-only management viewers.
        releaseControlApi
          .listReceiptCredentials(targetNamespaceId)
          .catch(() => ({ data: [] as ReleaseReceiptCredential[] })),
        deploymentApi.list(targetNamespaceId),
        packageRegistryApi.listPackages(targetNamespaceId),
      ]);
      const versionResponses = await Promise.all(
        packageResponse.data.map((item) => packageRegistryApi.listVersions(item.public_id)),
      );
      setEnvironments(environmentResponse.data);
      setPolicies(policyResponse.data);
      setCandidates(candidateResponse.data);
      setDecisions(decisionResponse.data);
      setExceptions(exceptionResponse.data);
      setPromotions(promotionResponse.data);
      setRollbacks(rollbackResponse.data);
      setCanaryEvaluations(canaryResponse.data);
      setReleases(releaseResponse.data);
      setReceipts(receiptResponse.data);
      setReceiptCredentials(receiptCredentialResponse.data);
      setDeployments(deploymentResponse.data);
      setPackageVersions(versionResponses.flatMap((response) => response.data));
    } catch (err) {
      setError(errorDetail(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void namespacesApi
      .list()
      .then(({ data }) => {
        setNamespaces(data);
        setNamespaceId((current) => current ?? data[0]?.id ?? null);
      })
      .catch((err) => {
        setError(errorDetail(err));
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    if (namespaceId) void loadAll(namespaceId);
  }, [namespaceId, loadAll]);

  const policyVersions = useMemo(
    () => policies.flatMap((policy) => policy.versions),
    [policies],
  );
  const selectedCandidate = candidates.find((item) => item.public_id === selectedCandidateId) ?? null;
  const selectedDecision =
    decisions.find((item) => item.public_id === selectedDecisionId) ??
    decisions.find((item) => item.candidate_public_id === selectedCandidateId) ??
    null;
  const registeredDeployments = deployments.filter((item) => item.status === "REGISTERED");
  const selectedDeployment = deployments.find((item) => item.public_id === candidateDeploymentId) ?? null;
  const compatiblePackageVersions = selectedDeployment?.package_version_public_id
    ? packageVersions.filter((item) => item.public_id === selectedDeployment.package_version_public_id)
    : packageVersions;
  const selectedEnvironment = environments.find((item) => item.public_id === candidateEnvironmentId) ?? null;
  const availableBaselines = candidates.filter((item) => {
    const sourceEnvironment = environments.find(
      (environment) => environment.public_id === item.target_environment_public_id,
    );
    return Boolean(
      selectedEnvironment &&
        sourceEnvironment &&
        sourceEnvironment.promotion_order < selectedEnvironment.promotion_order,
    );
  });
  const failedRules = selectedDecision?.rule_results.filter((item) => item.verdict === "FAIL") ?? [];
  const runtimeIds = useMemo(
    () => Array.from(new Set(deployments.map((item) => item.runtime_id))),
    [deployments],
  );

  useEffect(() => {
    setPolicyEnvironmentId((current) => current || environments[0]?.public_id || "");
    setCandidateEnvironmentId((current) => current || environments[0]?.public_id || "");
  }, [environments]);

  useEffect(() => {
    setCandidateDeploymentId((current) => current || registeredDeployments[0]?.public_id || "");
  }, [registeredDeployments]);

  useEffect(() => {
    setCredentialRuntimeId((current) => current || String(runtimeIds[0] ?? ""));
  }, [runtimeIds]);

  useEffect(() => {
    if (selectedDeployment?.package_version_public_id) {
      setCandidatePackageVersionId(selectedDeployment.package_version_public_id);
    } else {
      setCandidatePackageVersionId((current) => current || compatiblePackageVersions[0]?.public_id || "");
    }
  }, [selectedDeployment, compatiblePackageVersions]);

  useEffect(() => {
    setSelectedCandidateId((current) => current || candidates[0]?.public_id || "");
  }, [candidates]);

  useEffect(() => {
    if (!selectedCandidate) return;
    const matching = policyVersions.find(
      (item) => item.target_environment_public_id === selectedCandidate.target_environment_public_id,
    );
    setSelectedPolicyVersionId((current) =>
      policyVersions.some((item) => item.public_id === current) ? current : matching?.public_id ?? "",
    );
    const latestDecision = decisions.find((item) => item.candidate_public_id === selectedCandidate.public_id);
    setSelectedDecisionId(latestDecision?.public_id ?? "");
  }, [selectedCandidate, policyVersions, decisions]);

  async function runAction(key: string, action: () => Promise<unknown>, message: string) {
    if (!namespaceId) return;
    setBusy(key);
    setError("");
    setNotice("");
    try {
      await action();
      setNotice(message);
      await loadAll(namespaceId);
    } catch (err) {
      setError(errorDetail(err));
    } finally {
      setBusy("");
    }
  }

  function createEnvironment() {
    if (!namespaceId || !environmentName) return;
    void runAction(
      "environment",
      () =>
        releaseControlApi.createEnvironment({
          namespace_id: namespaceId,
          name: environmentName,
          kind: environmentKind,
          promotion_order: promotionOrder,
          protected: protectedEnvironment,
          minimum_approvals: protectedEnvironment ? minimumApprovals : 0,
          requires_canary: requiresCanary,
        }),
      `环境 ${environmentName} 已创建。`,
    );
  }

  async function createPolicyWithVersion() {
    if (!namespaceId || !policyName || !policyEnvironmentId) return;
    await runAction(
      "policy",
      async () => {
        const { data: policy } = await releaseControlApi.createPolicy({
          namespace_id: namespaceId,
          name: policyName,
          description: "DuckDock Release Control standard evidence policy",
        });
        await releaseControlApi.createPolicyVersion(policy.public_id, {
          namespace_id: namespaceId,
          target_environment_public_id: policyEnvironmentId,
          mode: policyMode,
          rules: STANDARD_RULES,
        });
      },
      `策略 ${policyName} 与首个 ${policyMode} 版本已创建。`,
    );
  }

  function createCandidate() {
    if (!namespaceId || !candidateDeploymentId || !candidatePackageVersionId || !candidateEnvironmentId) return;
    void runAction(
      "candidate",
      () =>
        releaseControlApi.createCandidate({
          namespace_id: namespaceId,
          package_version_public_id: candidatePackageVersionId,
          deployment_public_id: candidateDeploymentId,
          target_environment_public_id: candidateEnvironmentId,
          baseline_candidate_public_id: baselineCandidateId || null,
          idempotency_key: operationKey("ui-candidate"),
        }),
      "不可变 ReleaseCandidate 已创建，Deployment 已冻结。",
    );
  }

  function evaluateCandidate() {
    if (!namespaceId || !selectedCandidate || !selectedPolicyVersionId) return;
    void runAction(
      "evaluate",
      () =>
        releaseControlApi.evaluateCandidate(selectedCandidate.public_id, {
          namespace_id: namespaceId,
          policy_version_public_id: selectedPolicyVersionId,
          idempotency_key: operationKey("ui-policy-eval"),
        }),
      "策略证据快照与可解释判定已生成。",
    );
  }

  function approveCandidate() {
    if (!namespaceId || !selectedCandidate || !selectedDecision) return;
    void runAction(
      "approve",
      () =>
        releaseControlApi.approveCandidate(selectedCandidate.public_id, {
          namespace_id: namespaceId,
          policy_decision_public_id: selectedDecision.public_id,
          decision: "APPROVED",
          role: "RELEASE_MANAGER",
          idempotency_key: operationKey("ui-approval"),
        }),
      "候选审批已记录；受保护环境会强制四眼原则。",
    );
  }

  function requestException() {
    if (!namespaceId || !selectedDecision || !failedRules.length || exceptionReason.trim().length < 10) return;
    void runAction(
      "exception",
      () =>
        releaseControlApi.createException({
          namespace_id: namespaceId,
          policy_decision_public_id: selectedDecision.public_id,
          waived_rule_ids: failedRules.map((item) => item.rule_id),
          reason: exceptionReason.trim(),
          expires_at: new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString(),
          idempotency_key: operationKey("ui-exception"),
        }),
      "24 小时限时例外已请求，等待另一位管理员复核。",
    );
  }

  function reviewException(item: ReleasePolicyException) {
    if (!namespaceId) return;
    void runAction(
      `exception-${item.public_id}`,
      () =>
        releaseControlApi.reviewException(item.public_id, {
          namespace_id: namespaceId,
          decision: "APPROVED",
          idempotency_key: operationKey("ui-exception-review"),
        }),
      "例外已独立复核；到期后自动失效。",
    );
  }

  function dispatchPromotion() {
    if (!namespaceId || !selectedCandidate || !selectedDecision) return;
    void runAction(
      "promote",
      () =>
        releaseControlApi.createPromotion({
          namespace_id: namespaceId,
          candidate_public_id: selectedCandidate.public_id,
          policy_decision_public_id: selectedDecision.public_id,
          strategy: promotionStrategy,
          acknowledge_warnings: acknowledgeWarnings,
          canary:
            promotionStrategy === "CANARY"
              ? {
                  minimum_completed_runs: 5,
                  maximum_failure_rate: 0.1,
                  maximum_untrusted_rate: 0,
                  observation_window_seconds: 300,
                }
              : null,
          idempotency_key: operationKey("ui-promotion"),
        }),
      "推广已派发；只有 Runtime 身份回执精确匹配后才会推进状态。",
    );
  }

  function evaluateCanary(item: ReleasePromotion) {
    if (!namespaceId) return;
    void runAction(
      `canary-${item.public_id}`,
      () => releaseControlApi.evaluateCanary(item.public_id, namespaceId, operationKey("ui-canary")),
      "金丝雀窗口已按精确 Deployment 的 Run 元数据完成评估。",
    );
  }

  function rollback(item: ReleasePromotion) {
    if (!namespaceId) return;
    void runAction(
      `rollback-${item.public_id}`,
      () =>
        releaseControlApi.requestRollback(
          item.public_id,
          namespaceId,
          "operator_requested",
          operationKey("ui-rollback"),
        ),
      "精确回滚已派发，等待 Runtime 回执。",
    );
  }

  async function issueReceiptCredential() {
    if (!namespaceId || !credentialRuntimeId || !credentialName.trim()) return;
    setBusy("receipt-credential");
    setError("");
    setNotice("");
    setGeneratedCredentialToken("");
    try {
      const { data } = await releaseControlApi.createReceiptCredential({
        namespace_id: namespaceId,
        runtime_id: Number(credentialRuntimeId),
        device_id: `release-runtime-${credentialRuntimeId}`,
        name: credentialName.trim(),
        expires_at: new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString(),
      });
      setGeneratedCredentialToken(data.token);
      setNotice("release.receipt credential 已签发；明文 token 只显示这一次。安全保存后交给目标 Runtime Reporter。");
      await loadAll(namespaceId);
    } catch (err) {
      setError(errorDetail(err));
    } finally {
      setBusy("");
    }
  }

  function revokeReceiptCredential(item: ReleaseReceiptCredential) {
    if (!namespaceId) return;
    void runAction(
      `revoke-credential-${item.id}`,
      () => releaseControlApi.revokeReceiptCredential(item.id, "release_control_operator_revoked"),
      "release.receipt credential 已吊销。",
    );
  }

  return (
    <div className="app-page max-w-7xl space-y-6">
      <PageHeader
        eyebrow="RELEASE GOVERNANCE"
        title="Agent Release Control"
        description="以不可变 PackageVersion、Deployment revision、PolicyDecision 与 Runtime 身份回执构成可审计的发布闭环。"
        actions={
          <Button
            variant="secondary"
            icon={<RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />}
            disabled={loading || !namespaceId}
            onClick={() => namespaceId && void loadAll(namespaceId)}
          >
            刷新
          </Button>
        }
      />

      <div className="flex items-center gap-3 rounded-lg border border-slate-200 bg-white p-4">
        <label className="text-sm font-medium text-slate-700" htmlFor="release-namespace">命名空间</label>
        <select
          id="release-namespace"
          aria-label="Release Control 命名空间"
          className="field-control max-w-xs"
          value={namespaceId ?? ""}
          onChange={(event) => setNamespaceId(Number(event.target.value))}
        >
          {namespaces.map((namespace) => <option key={namespace.id} value={namespace.id}>{namespace.name}</option>)}
        </select>
        <span className="text-xs text-slate-500">管理 API 使用用户身份；Runtime 回执只接受 release.receipt credential。</span>
      </div>
      {error ? <div className="soft-rose rounded-lg px-4 py-3 text-sm">{error}</div> : null}
      {notice ? <div className="soft-emerald rounded-lg px-4 py-3 text-sm">{notice}</div> : null}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard label="Environments" value={environments.length} detail="有序推广图" icon={GitBranch} />
        <MetricCard label="Candidates" value={candidates.length} detail="不可变发布主体" icon={ShieldCheck} tone="indigo" />
        <MetricCard label="Active releases" value={releases.filter((item) => item.status === "ACTIVE").length} detail="Runtime 已确认" icon={CheckCircle2} />
        <MetricCard label="Blocked / failed" value={decisions.filter((item) => item.enforcement_outcome === "BLOCK").length + promotions.filter((item) => item.status === "FAILED").length} detail="需要处置" icon={AlertTriangle} tone="rose" />
      </div>

      <div className="grid gap-6 xl:grid-cols-2 2xl:grid-cols-4">
        <Card title="1. 环境与策略" description="先定义 promotion order，再创建绑定目标环境的版本化策略。" padded>
          <div className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-3">
              <input className="field-control" aria-label="环境名称" value={environmentName} onChange={(event) => setEnvironmentName(event.target.value)} placeholder="staging" />
              <select className="field-control" aria-label="环境类型" value={environmentKind} onChange={(event) => {
                const value = event.target.value as ReleaseEnvironmentKind;
                setEnvironmentKind(value);
                if (value === "PRODUCTION") { setProtectedEnvironment(true); setRequiresCanary(true); setMinimumApprovals(Math.max(1, minimumApprovals)); }
              }}>
                {(["DEVELOPMENT", "STAGING", "CANARY", "PRODUCTION"] as const).map((kind) => <option key={kind}>{kind}</option>)}
              </select>
              <input className="field-control" aria-label="推广顺序" type="number" min={0} max={100} value={promotionOrder} onChange={(event) => setPromotionOrder(Number(event.target.value))} />
            </div>
            <div className="flex flex-wrap gap-4 text-sm text-slate-600">
              <label className="flex items-center gap-2"><input type="checkbox" checked={protectedEnvironment} onChange={(event) => { setProtectedEnvironment(event.target.checked); if (!event.target.checked) setMinimumApprovals(0); }} />受保护</label>
              <label className="flex items-center gap-2">审批数<input className="field-control w-20" aria-label="最少审批数" type="number" min={0} max={10} disabled={!protectedEnvironment} value={minimumApprovals} onChange={(event) => setMinimumApprovals(Number(event.target.value))} /></label>
              <label className="flex items-center gap-2"><input type="checkbox" checked={requiresCanary} onChange={(event) => setRequiresCanary(event.target.checked)} />必须金丝雀</label>
            </div>
            <Button onClick={createEnvironment} disabled={busy === "environment" || !namespaceId || !environmentName}>创建环境</Button>

            <div className="border-t border-slate-200 pt-4">
              <div className="grid gap-3 sm:grid-cols-3">
                <input className="field-control" aria-label="策略名称" value={policyName} onChange={(event) => setPolicyName(event.target.value)} />
                <select className="field-control" aria-label="策略目标环境" value={policyEnvironmentId} onChange={(event) => setPolicyEnvironmentId(event.target.value)}>
                  <option value="">选择目标环境</option>
                  {environments.map((item) => <option key={item.public_id} value={item.public_id}>{item.name}</option>)}
                </select>
                <select className="field-control" aria-label="策略模式" value={policyMode} onChange={(event) => setPolicyMode(event.target.value as ReleasePolicyMode)}>
                  {(["SHADOW", "WARN", "ENFORCE"] as const).map((mode) => <option key={mode}>{mode}</option>)}
                </select>
              </div>
              <div className="mt-3 flex items-center justify-between gap-3">
                <span className="text-xs text-slate-500">标准模板：Package/签名/Eval/风险/工具/能力/SBOM/回滚 8 类证据。</span>
                <Button onClick={() => void createPolicyWithVersion()} disabled={busy === "policy" || !policyEnvironmentId}>创建策略与版本</Button>
              </div>
            </div>
          </div>
        </Card>

        <Card title="2. 不可变候选" description="只接受 REGISTERED Deployment 与精确 VERIFIED PackageVersion。" padded>
          <div className="space-y-3">
            <select className="field-control" aria-label="候选 Deployment" value={candidateDeploymentId} onChange={(event) => setCandidateDeploymentId(event.target.value)}>
              <option value="">选择 REGISTERED Deployment</option>
              {registeredDeployments.map((item) => <option key={item.public_id} value={item.public_id}>{item.environment} · {item.revision} · runtime #{item.runtime_id}</option>)}
            </select>
            <select className="field-control" aria-label="候选 PackageVersion" value={candidatePackageVersionId} onChange={(event) => setCandidatePackageVersionId(event.target.value)}>
              <option value="">选择精确 PackageVersion</option>
              {compatiblePackageVersions.map((item) => <option key={item.public_id} value={item.public_id}>{item.package_name} v{item.version} · {item.public_id}</option>)}
            </select>
            <div className="grid gap-3 sm:grid-cols-2">
              <select className="field-control" aria-label="候选目标环境" value={candidateEnvironmentId} onChange={(event) => { setCandidateEnvironmentId(event.target.value); setBaselineCandidateId(""); }}>
                <option value="">选择目标环境</option>
                {environments.map((item) => <option key={item.public_id} value={item.public_id}>{item.promotion_order}. {item.name}</option>)}
              </select>
              <select className="field-control" aria-label="基线候选" value={baselineCandidateId} onChange={(event) => setBaselineCandidateId(event.target.value)}>
                <option value="">无基线（首环境或同环境迭代）</option>
                {availableBaselines.map((item) => <option key={item.public_id} value={item.public_id}>{item.target_environment_name} · {item.package_name} v{item.package_version}</option>)}
              </select>
            </div>
            {registeredDeployments.length === 0 ? <div className="soft-amber rounded-md px-3 py-2 text-sm">当前没有可用的 REGISTERED Deployment。请先在 Deployment Inventory 登记精确 revision。</div> : null}
            <Button onClick={createCandidate} disabled={busy === "candidate" || !candidateDeploymentId || !candidatePackageVersionId || !candidateEnvironmentId}>创建并冻结候选</Button>
          </div>
        </Card>
      </div>

      <Card title="3. 判定、审批与例外" description="判定绑定精确候选和策略版本；受保护环境要求另一位管理员审批。" padded>
        {candidates.length === 0 ? <EmptyState text="暂无 ReleaseCandidate；先完成环境、策略和候选创建。" /> : (
          <div className="space-y-5">
            <div className="grid gap-3 lg:grid-cols-3">
              <select className="field-control" aria-label="选择候选" value={selectedCandidateId} onChange={(event) => setSelectedCandidateId(event.target.value)}>
                {candidates.map((item) => <option key={item.public_id} value={item.public_id}>{item.target_environment_name} · {item.package_name} v{item.package_version} · {item.deployment_revision}</option>)}
              </select>
              <select className="field-control" aria-label="选择策略版本" value={selectedPolicyVersionId} onChange={(event) => setSelectedPolicyVersionId(event.target.value)}>
                <option value="">选择同环境 PolicyVersion</option>
                {policyVersions.filter((item) => !selectedCandidate || item.target_environment_public_id === selectedCandidate.target_environment_public_id).map((item) => <option key={item.public_id} value={item.public_id}>{item.target_environment_name} · v{item.version} · {item.mode}</option>)}
              </select>
              <Button icon={<Play className="h-4 w-4" />} onClick={evaluateCandidate} disabled={busy === "evaluate" || !selectedPolicyVersionId}>执行策略判定</Button>
            </div>
            {decisions.filter((item) => item.candidate_public_id === selectedCandidateId).length > 0 ? (
              <select className="field-control" aria-label="选择策略判定" value={selectedDecisionId} onChange={(event) => setSelectedDecisionId(event.target.value)}>
                {decisions.filter((item) => item.candidate_public_id === selectedCandidateId).map((item) => <option key={item.public_id} value={item.public_id}>{item.policy_mode} · {item.raw_outcome}/{item.enforcement_outcome} · {formatDate(item.created_at)}</option>)}
              </select>
            ) : null}
            {selectedDecision ? (
              <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={statusTone(selectedDecision.raw_outcome)}>{selectedDecision.raw_outcome}</Badge>
                  <Badge tone={statusTone(selectedDecision.enforcement_outcome)}>{selectedDecision.policy_mode} → {selectedDecision.enforcement_outcome}</Badge>
                  <MonoPill>{shortDigest(selectedDecision.decision_digest)}</MonoPill>
                  <span className="text-xs text-slate-500">{selectedDecision.evaluation_duration_ms} ms</span>
                </div>
                <div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-4">
                  {selectedDecision.rule_results.map((result) => (
                    <div key={result.rule_id} className="rounded-md border border-slate-200 bg-white p-3">
                      <div className="flex items-center justify-between gap-2"><span className="text-xs font-semibold text-slate-700">{result.rule_id}</span><Badge tone={statusTone(result.verdict)}>{result.verdict}</Badge></div>
                      <div className="mt-1 text-xs text-slate-500">{result.reason_code}</div>
                    </div>
                  ))}
                </div>
                <div className="mt-4 flex flex-wrap items-end gap-3">
                  <Button variant="secondary" onClick={approveCandidate} disabled={busy === "approve"}>记录 APPROVED</Button>
                  {selectedDecision.enforcement_outcome === "BLOCK" ? (
                    <>
                      <input className="field-control min-w-72 flex-1" aria-label="例外原因" value={exceptionReason} onChange={(event) => setExceptionReason(event.target.value)} placeholder="至少 10 字；正文只存管理域，不进入 Outbox" />
                      <Button variant="secondary" onClick={requestException} disabled={busy === "exception" || exceptionReason.trim().length < 10}>申请 24h 例外</Button>
                    </>
                  ) : null}
                </div>
              </div>
            ) : null}
            {exceptions.length > 0 ? (
              <div className="space-y-2">
                <div className="text-sm font-semibold text-slate-800">策略例外</div>
                {exceptions.map((item) => (
                  <div key={item.public_id} className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-slate-200 px-4 py-3">
                    <div><div className="flex items-center gap-2"><MonoPill>{item.public_id}</MonoPill><Badge tone={item.effective ? "emerald" : item.review ? statusTone(item.review.decision) : "amber"}>{item.effective ? "EFFECTIVE" : item.review?.decision ?? "PENDING"}</Badge></div><div className="mt-1 text-xs text-slate-500">rules={item.waived_rule_ids.join(", ")} · expires {formatDate(item.expires_at)}</div></div>
                    {!item.review ? <Button size="sm" variant="secondary" onClick={() => reviewException(item)} disabled={busy === `exception-${item.public_id}`}>独立批准</Button> : null}
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        )}
      </Card>

      <Card title="4. 推广与 Runtime 回执" description="派发不是成功；精确匹配 PackageVersion/revision/config digest 的 Runtime 身份回执才会激活环境。" padded>
        <div className="space-y-5">
          <div className="flex flex-wrap items-center gap-3">
            <select className="field-control max-w-xs" aria-label="推广策略" value={promotionStrategy} onChange={(event) => setPromotionStrategy(event.target.value as ReleasePromotionStrategy)}>
              {(["ALL_AT_ONCE", "CANARY", "ROLLING"] as const).map((strategy) => <option key={strategy}>{strategy}</option>)}
            </select>
            <label className="flex items-center gap-2 text-sm text-slate-600"><input type="checkbox" checked={acknowledgeWarnings} onChange={(event) => setAcknowledgeWarnings(event.target.checked)} />确认 WARN</label>
            <Button icon={<Play className="h-4 w-4" />} onClick={dispatchPromotion} disabled={busy === "promote" || !selectedDecision}>派发选中候选</Button>
          </div>
          {promotions.length === 0 ? <EmptyState text="暂无推广；策略与审批满足后即可派发。" /> : (
            <div className="space-y-2">
              {promotions.map((item) => (
                <div key={item.public_id} className="grid gap-3 rounded-lg border border-slate-200 px-4 py-3 lg:grid-cols-[1.4fr_1fr_auto] lg:items-center">
                  <div><div className="flex flex-wrap items-center gap-2"><span className="font-semibold text-slate-800">{item.target_environment_name}</span><Badge tone={statusTone(item.status)}>{item.status}</Badge><Badge tone="neutral">{item.strategy}</Badge></div><div className="mt-1 text-xs text-slate-500">dispatch {item.dispatch_public_id} · candidate {item.candidate_public_id}</div></div>
                  <div className="text-xs text-slate-500">policy {item.policy_decision_public_id}<br />{formatDate(item.updated_at)}</div>
                  <div className="flex gap-2">{item.status === "OBSERVING" ? <Button size="sm" variant="secondary" onClick={() => evaluateCanary(item)} disabled={busy === `canary-${item.public_id}`}>评估金丝雀</Button> : null}{item.status === "SUCCEEDED" ? <Button size="sm" variant="secondary" danger icon={<RotateCcw className="h-3.5 w-3.5" />} onClick={() => rollback(item)} disabled={busy === `rollback-${item.public_id}`}>回滚</Button> : null}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </Card>

      <Card title="Runtime 回执凭据" description="独立最小权限凭据，仅含 release.receipt；不复用 execution.write token。" padded>
        <div className="space-y-4">
          <div className="grid gap-3 md:grid-cols-[180px_1fr_auto]">
            <select className="field-control" aria-label="回执 Runtime" value={credentialRuntimeId} onChange={(event) => setCredentialRuntimeId(event.target.value)}>
              <option value="">选择 Runtime</option>
              {runtimeIds.map((runtimeId) => <option key={runtimeId} value={runtimeId}>Runtime #{runtimeId}</option>)}
            </select>
            <input className="field-control" aria-label="回执凭据名称" value={credentialName} onChange={(event) => setCredentialName(event.target.value)} />
            <Button onClick={() => void issueReceiptCredential()} disabled={busy === "receipt-credential" || !credentialRuntimeId}>签发 30 天凭据</Button>
          </div>
          {generatedCredentialToken ? (
            <div className="soft-amber rounded-lg p-4">
              <div className="text-sm font-semibold">仅显示一次的 Runtime token</div>
              <div className="mt-2 flex gap-2">
                <input className="field-control font-mono text-xs" aria-label="新签发回执 token" readOnly value={generatedCredentialToken} />
                <Button variant="secondary" onClick={() => void navigator.clipboard.writeText(generatedCredentialToken)}>复制</Button>
              </div>
            </div>
          ) : null}
          <div className="space-y-2">
            {receiptCredentials.map((item) => (
              <div key={item.id} className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-slate-200 px-4 py-3">
                <div><div className="flex items-center gap-2"><span className="font-medium text-slate-800">{item.name}</span><Badge tone={item.is_active ? "emerald" : "neutral"}>{item.is_active ? "ACTIVE" : "REVOKED"}</Badge></div><div className="mt-1 text-xs text-slate-500">runtime #{item.runtime_id} · dkr_report_{item.token_prefix}_… · expires {formatDate(item.expires_at)}</div></div>
                {item.is_active ? <Button size="sm" variant="secondary" danger onClick={() => revokeReceiptCredential(item)} disabled={busy === `revoke-credential-${item.id}`}>吊销</Button> : null}
              </div>
            ))}
          </div>
        </div>
      </Card>

      <div className="grid gap-6 xl:grid-cols-2">
        <Card title="环境激活历史" description="Append-only，保留 supersede / rollback 因果链。" action={<History className="h-4 w-4 text-slate-400" />}>
          {releases.length === 0 ? <div className="p-5"><EmptyState text="暂无已确认发布；等待 Runtime APPLIED 回执。" /></div> : releases.map((item) => {
            const environment = environments.find((row) => row.public_id === item.environment_public_id);
            return <div key={item.public_id} className="border-b border-slate-100 px-5 py-4 last:border-0"><div className="flex items-center justify-between gap-3"><div><span className="font-medium text-slate-800">{environment?.name ?? item.environment_public_id}</span><div className="mt-1 text-xs text-slate-500">candidate {item.candidate_public_id}</div></div><Badge tone={statusTone(item.status)}>{item.status}</Badge></div><div className="mt-2 text-xs text-slate-500">receipt {item.receipt_public_id} · {formatDate(item.activated_at)}</div></div>;
          })}
        </Card>
        <Card title="Canary 评估" description="只聚合精确 Deployment revision 的 metadata-only Run。">
          {canaryEvaluations.length === 0 ? <div className="p-5"><EmptyState text="暂无 Canary 评估。" /></div> : canaryEvaluations.map((item) => (
            <div key={item.public_id} className="border-b border-slate-100 px-5 py-4 last:border-0">
              <div className="flex items-center justify-between gap-3">
                <Badge tone={statusTone(item.outcome)}>{item.outcome}</Badge>
                <span className="text-xs text-slate-500">{formatDate(item.created_at)}</span>
              </div>
              <div className="mt-2 text-xs text-slate-500">runs {item.completed_run_count} · failed {item.failed_run_count} · untrusted {item.untrusted_run_count}</div>
              <div className="mt-1 text-xs text-slate-400">{item.reason_codes.join(", ")}</div>
            </div>
          ))}
        </Card>
        <Card title="Rollback 记录" description="展示精确恢复目标、触发原因与 Runtime 完成状态。">
          {rollbacks.length === 0 ? <div className="p-5"><EmptyState text="暂无回滚记录。" /></div> : rollbacks.map((item) => (
            <div key={item.public_id} className="border-b border-slate-100 px-5 py-4 last:border-0">
              <div className="flex items-center justify-between gap-3">
                <Badge tone={statusTone(item.status)}>{item.status}</Badge>
                <span className="text-xs text-slate-500">{formatDate(item.completed_at ?? item.created_at)}</span>
              </div>
              <div className="mt-2 text-xs text-slate-500">{item.reason_code} · target {item.target_candidate_public_id}</div>
              <div className="mt-1 text-xs text-slate-400">dispatch {item.dispatch_public_id}</div>
            </div>
          ))}
        </Card>
        <Card title="Runtime 回执历史" description="身份由 credential 服务端推导；MISMATCH 不会激活环境。">
          {receipts.length === 0 ? <div className="p-5"><EmptyState text="暂无 Runtime 回执；Reporter 需具备 release.receipt scope。" /></div> : receipts.map((item) => (
            <div key={item.public_id} className="border-b border-slate-100 px-5 py-4 last:border-0"><div className="flex items-center justify-between gap-3"><div className="flex items-center gap-2"><Badge tone={statusTone(item.status)}>{item.status}</Badge><Badge tone="neutral">{item.kind}</Badge></div><span className="text-xs text-slate-500">runtime #{item.runtime_id}</span></div><div className="mt-2 text-xs text-slate-500">{item.observed_package_version_public_id} · {item.observed_deployment_revision}</div><div className="mt-1 text-xs text-slate-400">dispatch {item.dispatch_public_id} · {formatDate(item.occurred_at)}{item.error_code ? ` · ${item.error_code}` : ""}</div></div>
          ))}
        </Card>
      </div>
    </div>
  );
}
