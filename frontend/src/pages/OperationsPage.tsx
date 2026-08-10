import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ClipboardCheck,
  Clock3,
  DatabaseBackup,
  ExternalLink,
  Gauge,
  RefreshCw,
  ShieldAlert,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  operationsApi,
  type GAReadiness,
  type OpsIncident,
  type OpsOverview,
  type OpsRecoveryDrill,
  type OpsSLOEvaluation,
} from "../api/client";
import { Badge, Button, Card, EmptyState, MetricCard, MonoPill, PageHeader } from "../components/ui";

function operationKey(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "-";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function shortDigest(value: string): string {
  return `${value.slice(0, 9)}…${value.slice(-7)}`;
}

function errorDetail(error: unknown): string {
  if (typeof error === "object" && error !== null) {
    const response = (error as { response?: { data?: { detail?: unknown } } }).response;
    if (typeof response?.data?.detail === "string") return response.data.detail;
  }
  return "操作失败，请检查 API、权限与本地运维服务状态。";
}

function statusTone(value: string): "emerald" | "amber" | "rose" | "neutral" {
  if (["HEALTHY", "PASSED", "RESOLVED", "READY", "PASS"].includes(value)) return "emerald";
  if (["DEGRADED", "ACKNOWLEDGED", "WARNING", "READY_WITH_GAPS", "WARN"].includes(value)) return "amber";
  if (["BREACHED", "FAILED", "OPEN", "CRITICAL", "BLOCKED", "BLOCK"].includes(value)) return "rose";
  return "neutral";
}

export default function OperationsPage() {
  const prometheusUrl = (import.meta.env.VITE_PROMETHEUS_URL ?? "").trim();
  const [readiness, setReadiness] = useState<GAReadiness | null>(null);
  const [overview, setOverview] = useState<OpsOverview | null>(null);
  const [evaluations, setEvaluations] = useState<OpsSLOEvaluation[]>([]);
  const [incidents, setIncidents] = useState<OpsIncident[]>([]);
  const [drills, setDrills] = useState<OpsRecoveryDrill[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [readinessResponse, overviewResponse, evaluationResponse, incidentResponse, drillResponse] = await Promise.all([
        operationsApi.getGAReadiness(),
        operationsApi.getOverview(),
        operationsApi.listEvaluations(),
        operationsApi.listIncidents(),
        operationsApi.listRecoveryDrills(),
      ]);
      setReadiness(readinessResponse.data);
      setOverview(overviewResponse.data);
      setEvaluations(evaluationResponse.data);
      setIncidents(incidentResponse.data);
      setDrills(drillResponse.data);
    } catch (requestError) {
      setError(errorDetail(requestError));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  async function evaluateNow() {
    setBusy("evaluate");
    setError("");
    try {
      await operationsApi.evaluate(operationKey("ops-ui-slo"));
      await loadAll();
    } catch (requestError) {
      setError(errorDetail(requestError));
    } finally {
      setBusy("");
    }
  }

  async function actOnIncident(incident: OpsIncident, action: "acknowledge" | "resolve") {
    setBusy(`${action}:${incident.public_id}`);
    setError("");
    try {
      if (action === "acknowledge") {
        await operationsApi.acknowledgeIncident(incident.public_id, "Acknowledged from Operations console");
      } else {
        await operationsApi.resolveIncident(incident.public_id, "Resolved from Operations console");
      }
      await loadAll();
    } catch (requestError) {
      setError(errorDetail(requestError));
    } finally {
      setBusy("");
    }
  }

  const latest = overview?.latest_evaluation ?? evaluations[0] ?? null;
  const latestDrill = overview?.latest_recovery_drill ?? drills[0] ?? null;
  const activeIncidents = incidents.filter((item) => item.status !== "RESOLVED");

  return (
    <div className="app-page space-y-6">
      <PageHeader
        eyebrow="OPERATIONS & RELIABILITY"
        title="Operations & SLO"
        description="统一查看 DuckDock 路由级 Prometheus 指标、发布 SLO、内容安全告警和真实 backup→restore 演练回执。"
        actions={
          <>
            {prometheusUrl ? (
              <a
                href={prometheusUrl}
                target="_blank"
                rel="noreferrer"
                className="button-secondary inline-flex items-center gap-2"
              >
                Prometheus <ExternalLink className="h-4 w-4" />
              </a>
            ) : null}
            <Button
              variant="secondary"
              icon={<RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />}
              onClick={() => void loadAll()}
              disabled={loading}
            >
              刷新
            </Button>
            <Button icon={<Gauge className="h-4 w-4" />} onClick={() => void evaluateNow()} disabled={busy === "evaluate"}>
              立即评估 SLO
            </Button>
          </>
        }
      />

      {error ? (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>
      ) : null}

      <Card
        title="Application Release Readiness"
        description="汇总应用级迁移、API 契约、Runtime、发布、交接、身份、对账、SLO 与恢复证据。READY 不代表目标环境 GA 或生产授权。"
        padded
      >
        {readiness ? (
          <div className="space-y-5">
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-800">
              正式 GA 还必须通过目标 TLS、Secrets、网络、告警、异地恢复、容量、跨故障域、独立安全评估及四方签名门禁。
            </div>
            <div className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-slate-200 bg-slate-50 px-4 py-4">
              <div className="flex flex-wrap items-center gap-3">
                <Badge tone={statusTone(readiness.status)}>{readiness.status}</Badge>
                <span className="text-sm font-semibold text-slate-900">
                  {readiness.pass_count} pass · {readiness.warn_count} warn · {readiness.block_count} block
                </span>
              </div>
              <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                <Badge>{readiness.contract_version}</Badge>
                <MonoPill>{readiness.current_db_revision ?? "migration-missing"}</MonoPill>
                <span>{formatDate(readiness.checked_at)}</span>
              </div>
            </div>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {readiness.checks.map((check) => (
                <div key={check.key} className="rounded-lg border border-slate-200 px-4 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="text-sm font-semibold text-slate-900">{check.title}</div>
                    <Badge tone={statusTone(check.status)}>{check.status}</Badge>
                  </div>
                  <div className="mt-2 break-all font-mono text-xs text-slate-600">{check.observed}</div>
                  <div className="mt-2 text-xs leading-5 text-slate-500">{check.detail}</div>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <EmptyState text="正在汇总发布门禁" icon={ClipboardCheck} />
        )}
      </Card>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="最新 SLO"
          value={latest?.status ?? "NO DATA"}
          detail={latest ? `${latest.window_minutes} 分钟窗口 · ${formatDate(latest.evaluated_at)}` : "尚未生成评估"}
          icon={Activity}
          tone={latest?.status === "BREACHED" ? "rose" : "indigo"}
          loading={loading}
        />
        <MetricCard
          label="未解决事件"
          value={activeIncidents.length}
          detail={activeIncidents.some((item) => item.severity === "CRITICAL") ? "存在 Critical 事件" : "OPEN + ACKNOWLEDGED"}
          icon={ShieldAlert}
          tone={activeIncidents.length ? "rose" : "neutral"}
          loading={loading}
        />
        <MetricCard
          label="Outbox failed"
          value={overview?.outbox.failed_count ?? 0}
          detail={`oldest pending ${overview?.outbox.oldest_pending_age_seconds ?? 0}s`}
          icon={AlertTriangle}
          tone={overview?.outbox.failed_count ? "rose" : "neutral"}
          loading={loading}
        />
        <MetricCard
          label="最近恢复演练"
          value={latestDrill?.status ?? "NO DATA"}
          detail={latestDrill ? `RPO ${latestDrill.rpo_seconds}s · RTO ${latestDrill.rto_seconds}s` : "等待真实演练"}
          icon={DatabaseBackup}
          tone={latestDrill?.status === "FAILED" ? "rose" : "indigo"}
          loading={loading}
        />
      </div>

      <div className="grid gap-6 xl:grid-cols-[1.15fr_0.85fr]">
        <Card
          title="Release SLO profile"
          description={overview?.profile_version ?? "duckdock-ga-slo-v1"}
          padded
        >
          {overview ? (
            <div className="grid gap-3 sm:grid-cols-2">
              {[
                ["5xx error ratio", `≤ ${(overview.thresholds.http_error_ratio_max * 100).toFixed(1)}%`],
                ["Evidence ingest p95", `≤ ${overview.thresholds.evidence_ingest_p95_ms_max} ms`],
                ["Run timeline p95", `≤ ${overview.thresholds.run_timeline_p95_ms_max} ms`],
                ["Policy decision p95", `≤ ${overview.thresholds.policy_decision_p95_ms_max} ms`],
                ["Outbox oldest pending", `≤ ${overview.thresholds.outbox_pending_age_seconds_max} s`],
                ["Recovery objective", `RPO ≤ ${overview.thresholds.recovery_rpo_seconds_max}s · RTO ≤ ${overview.thresholds.recovery_rto_seconds_max}s`],
              ].map(([label, value]) => (
                <div key={label} className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
                  <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
                  <div className="mt-1 text-sm font-semibold text-slate-900">{value}</div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState text="正在加载 SLO profile" />
          )}
        </Card>

        <Card title="最新评估证据" description="不可变、幂等、摘要绑定" padded>
          {latest ? (
            <div className="space-y-4 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={statusTone(latest.status)}>{latest.status}</Badge>
                <MonoPill>{latest.public_id}</MonoPill>
              </div>
              <dl className="grid grid-cols-2 gap-3">
                <div><dt className="text-slate-500">HTTP samples</dt><dd className="mt-1 font-semibold">{latest.request_count}</dd></div>
                <div><dt className="text-slate-500">5xx</dt><dd className="mt-1 font-semibold">{latest.error_count}</dd></div>
                <div><dt className="text-slate-500">Ingest p95</dt><dd className="mt-1 font-semibold">{latest.evidence_ingest_p95_ms ?? "no traffic"}</dd></div>
                <div><dt className="text-slate-500">Timeline p95</dt><dd className="mt-1 font-semibold">{latest.run_timeline_p95_ms ?? "no traffic"}</dd></div>
              </dl>
              <div>
                <div className="text-slate-500">Reason codes</div>
                <div className="mt-2 flex flex-wrap gap-2">
                  {latest.reason_codes.length ? latest.reason_codes.map((code) => <Badge key={code} tone="amber">{code}</Badge>) : <Badge tone="emerald">ALL_THRESHOLDS_PASS</Badge>}
                </div>
              </div>
              <div className="text-xs text-slate-500">digest <MonoPill>{shortDigest(latest.evidence_digest)}</MonoPill></div>
            </div>
          ) : (
            <EmptyState text="尚无 SLO 评估" icon={Gauge} />
          )}
        </Card>
      </div>

      <Card title="Route-template metrics" description="标签只使用 HTTP method、路由模板和状态类别，不含 Namespace、ID 或正文。">
        {overview?.route_metrics.length ? (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr><th className="px-5 py-3">Method</th><th className="px-5 py-3">Route</th><th className="px-5 py-3">Samples</th><th className="px-5 py-3">5xx</th><th className="px-5 py-3">p95</th></tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {overview.route_metrics.map((metric) => (
                  <tr key={`${metric.method}:${metric.route}`}>
                    <td className="px-5 py-3"><Badge>{metric.method}</Badge></td>
                    <td className="px-5 py-3"><MonoPill>{metric.route}</MonoPill></td>
                    <td className="px-5 py-3">{metric.request_count}</td>
                    <td className="px-5 py-3">{metric.error_count}</td>
                    <td className="px-5 py-3">{metric.p95_ms == null ? "-" : `${metric.p95_ms.toFixed(1)} ms`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState text="尚无请求采样；访问控制面后刷新即可看到指标。" icon={Activity} />
        )}
      </Card>

      <Card title="Operations incidents" description="BREACHED 评估自动开事件；确认与解决均写入审计。">
        {incidents.length ? (
          <div className="divide-y divide-slate-100">
            {incidents.map((incident) => (
              <div key={incident.public_id} className="flex flex-col gap-4 px-5 py-4 lg:flex-row lg:items-center lg:justify-between">
                <div className="min-w-0 space-y-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={statusTone(incident.severity)}>{incident.severity}</Badge>
                    <Badge tone={statusTone(incident.status)}>{incident.status}</Badge>
                    <MonoPill>{incident.public_id}</MonoPill>
                  </div>
                  <div className="flex flex-wrap gap-2">{incident.reason_codes.map((code) => <span key={code} className="text-xs text-slate-600">{code}</span>)}</div>
                  <div className="text-xs text-slate-500">{formatDate(incident.created_at)} · {shortDigest(incident.evidence_digest)}</div>
                </div>
                <div className="flex gap-2">
                  {incident.status === "OPEN" ? <Button size="sm" variant="secondary" onClick={() => void actOnIncident(incident, "acknowledge")} disabled={busy === `acknowledge:${incident.public_id}`}>确认</Button> : null}
                  {incident.status !== "RESOLVED" ? <Button size="sm" onClick={() => void actOnIncident(incident, "resolve")} disabled={busy === `resolve:${incident.public_id}`}>解决</Button> : null}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState text="没有 Operations incidents" icon={CheckCircle2} />
        )}
      </Card>

      <Card title="Recovery drill receipts" description="真实 MySQL 与 MinIO backup→delete→restore 摘要，只保存元数据和结果。">
        {drills.length ? (
          <div className="divide-y divide-slate-100">
            {drills.map((drill) => (
              <div key={drill.public_id} className="grid gap-4 px-5 py-4 lg:grid-cols-[1fr_auto_auto] lg:items-center">
                <div className="min-w-0 space-y-2">
                  <div className="flex flex-wrap items-center gap-2"><Badge tone={statusTone(drill.status)}>{drill.status}</Badge><MonoPill>{drill.public_id}</MonoPill><Badge>{drill.environment}</Badge></div>
                  <div className="text-xs text-slate-500">backup set {shortDigest(drill.backup_set_digest)} · Git {drill.git_head.slice(0, 10)}</div>
                </div>
                <div className="text-sm text-slate-600">MySQL {drill.mysql_row_count} rows · Objects {drill.object_count}</div>
                <div className="flex items-center gap-2 text-sm font-semibold text-slate-800"><Clock3 className="h-4 w-4 text-slate-400" />RPO {drill.rpo_seconds}s · RTO {drill.rto_seconds}s</div>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState text="尚无恢复演练回执" icon={DatabaseBackup} />
        )}
      </Card>
    </div>
  );
}
