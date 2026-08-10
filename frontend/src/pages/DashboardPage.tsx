import {
  AlertTriangle,
  Archive,
  CheckCircle2,
  CircleDot,
  Database,
  FileClock,
  FolderGit2,
  Handshake,
  Network,
  RadioTower,
  RefreshCw,
  ShieldCheck,
  UploadCloud,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "../router";
import {
  controlPlaneApi,
  lifecycleApi,
  namespacesApi,
  skillsApi,
  type AIAsset,
  type CollectionJob,
  type HandoverCase,
  type Namespace,
  type NamespaceQuota,
  type ReportUploadSession,
  type RuntimeInstance,
  type RuntimeProvider,
  type Skill,
} from "../api/client";
import { EmptyState, MetricCard, PageHeader, SectionCard } from "../components/PageShell";
import { Badge, Button, IconTile, MonoPill, type Tone } from "../components/ui";
import { useAuthStore } from "../store/auth";

interface NamespaceSummary {
  namespace: Namespace;
  skills: Skill[];
  quota: NamespaceQuota | null;
}

interface DashboardState {
  namespaces: NamespaceSummary[];
  runtimes: RuntimeInstance[];
  assets: AIAsset[];
  handovers: HandoverCase[];
  jobs: CollectionJob[];
  reports: ReportUploadSession[];
}

const emptyState: DashboardState = {
  namespaces: [],
  runtimes: [],
  assets: [],
  handovers: [],
  jobs: [],
  reports: [],
};

const providerLabels: Record<RuntimeProvider, string> = {
  openclaw: "OpenClaw",
  arkclaw: "ArkClaw",
  workbuddy: "WorkBuddy",
  jvs: "JVS",
  custom: "Custom",
};

const providerTone: Record<RuntimeProvider, Tone> = {
  openclaw: "indigo",
  arkclaw: "amber",
  workbuddy: "violet",
  jvs: "emerald",
  custom: "neutral",
};

export default function DashboardPage() {
  const user = useAuthStore((state) => state.user);
  const [data, setData] = useState<DashboardState>(emptyState);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [namespaceResult, runtimeResult, assetResult, handoverResult, jobResult, reportResult] =
        await Promise.allSettled([
          namespacesApi.list(),
          controlPlaneApi.listRuntimes(),
          controlPlaneApi.listAssets(),
          controlPlaneApi.listHandovers(),
          controlPlaneApi.listCollectionJobs(),
          controlPlaneApi.listReportUploadSessions(),
        ]);

      const namespaces = namespaceResult.status === "fulfilled" ? namespaceResult.value.data : [];
      const namespaceSummaries = await Promise.all(
        namespaces.map(async (namespace) => {
          const [skillsResult, quotaResult] = await Promise.allSettled([
            skillsApi.list(namespace.name),
            lifecycleApi.getQuota(namespace.name),
          ]);
          return {
            namespace,
            skills: skillsResult.status === "fulfilled" ? skillsResult.value.data : [],
            quota: quotaResult.status === "fulfilled" ? quotaResult.value.data : null,
          };
        })
      );

      setData({
        namespaces: namespaceSummaries,
        runtimes: runtimeResult.status === "fulfilled" ? runtimeResult.value.data : [],
        assets: assetResult.status === "fulfilled" ? assetResult.value.data : [],
        handovers: handoverResult.status === "fulfilled" ? handoverResult.value.data : [],
        jobs: jobResult.status === "fulfilled" ? jobResult.value.data : [],
        reports: reportResult.status === "fulfilled" ? reportResult.value.data : [],
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "控制台加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const metrics = useMemo(() => {
    const namespaceCount = data.namespaces.length;
    const skillCount = data.namespaces.reduce((sum, item) => sum + item.skills.length, 0);
    const versionCount = data.namespaces.reduce(
      (sum, item) => sum + item.skills.reduce((inner, skill) => inner + skill.version_count, 0),
      0
    );
    const storageBytes = data.namespaces.reduce((sum, item) => sum + (item.quota?.current_storage_bytes ?? 0), 0);
    const activeRuntimes = data.runtimes.filter((runtime) => runtime.status === "active").length;
    const riskyAssets = data.assets.filter(
      (asset) =>
        asset.status === "risky" ||
        asset.status === "orphaned" ||
        asset.criticality === "high" ||
        asset.criticality === "critical"
    ).length;
    const activeHandovers = data.handovers.filter(
      (item) => !["completed", "rejected", "cancelled"].includes(item.status)
    ).length;
    const failedJobs = data.jobs.filter((job) => job.status === "failed" || job.status === "partial_failed").length;
    const runningJobs = data.jobs.filter((job) => job.status === "running" || job.status === "pending").length;
    const succeededReports = data.reports.filter((report) => report.status === "succeeded").length;
    const failedReports = data.reports.filter((report) => report.status === "failed").length;
    return {
      namespaceCount,
      skillCount,
      versionCount,
      storageBytes,
      activeRuntimes,
      riskyAssets,
      activeHandovers,
      failedJobs,
      runningJobs,
      succeededReports,
      failedReports,
    };
  }, [data]);

  const providerRows = useMemo(() => {
    const providers: RuntimeProvider[] = ["openclaw", "workbuddy", "arkclaw", "jvs", "custom"];
    return providers.map((provider) => ({
      provider,
      runtimes: data.runtimes.filter((runtime) => runtime.provider === provider).length,
      assets: data.assets.filter((asset) => asset.source_provider === provider).length,
      reports: data.reports.filter((report) => {
        const runtime = data.runtimes.find((item) => item.id === report.runtime_id);
        return runtime?.provider === provider;
      }).length,
    }));
  }, [data]);

  const recentReports = data.reports.slice(0, 5);
  const recentRuntimes = data.runtimes.slice(0, 5);

  return (
    <div className="app-page page-stack">
      <PageHeader
        eyebrow="CONTROL PLANE OVERVIEW"
        title="企业 AI Agent 资产控制台"
        description={
          <>
            当前登录为 {user?.username ?? "-"}。统一查看运行时、AI 资产、Reporter 上报、交接流程和 Skill 仓库底座能力。
          </>
        }
        actions={
          <>
            <Button
              variant="secondary"
              onClick={() => void load()}
              disabled={loading}
              icon={<RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />}
            >
              刷新
            </Button>
            <Link to="/control-plane" className="button-primary gap-2">
              <Network className="h-4 w-4" />
              打开 Agent 控制平面
            </Link>
          </>
        }
      />

      {error ? <div className="soft-rose rounded-md px-4 py-3 text-sm">{error}</div> : null}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="运行时实例"
          value={String(data.runtimes.length)}
          detail={`${metrics.activeRuntimes} 个 active，覆盖 OpenClaw / WorkBuddy / JVS 等运行时。`}
          icon={RadioTower}
          loading={loading}
        />
        <MetricCard
          label="AI 资产"
          value={String(data.assets.length)}
          detail={`${metrics.riskyAssets} 个高风险或待接管资产。`}
          icon={Database}
          loading={loading}
        />
        <MetricCard
          label="Reporter 上报"
          value={String(data.reports.length)}
          detail={`${metrics.succeededReports} 成功，${metrics.failedReports} 失败。`}
          icon={UploadCloud}
          loading={loading}
        />
        <MetricCard
          label="交接队列"
          value={String(metrics.activeHandovers)}
          detail="覆盖离职、项目交接、供应商退出和事故接管。"
          icon={Handshake}
          loading={loading}
        />
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <CompactMetric label="采集任务" value={`${metrics.runningJobs} 进行中`} detail={`${metrics.failedJobs} 个失败或部分失败`} icon={FileClock} />
        <CompactMetric label="Skill 仓库" value={`${metrics.skillCount} 个 Skill`} detail={`${metrics.versionCount} 个版本`} icon={FolderGit2} />
        <CompactMetric label="对象存储" value={formatBytes(metrics.storageBytes)} detail={`${metrics.namespaceCount} 个命名空间作为资产仓库底座`} icon={Archive} />
      </div>

      <div className="grid gap-5 xl:grid-cols-[1.15fr_0.85fr]">
        <SectionCard
          title="运行时与厂商覆盖"
          description="以运行时为接入入口，汇聚 OpenClaw、WorkBuddy、ArkClaw、JVS 和定制项目。"
          action={<Link to="/control-plane" className="button-secondary">管理运行时</Link>}
        >
          <div className="divide-y divide-slate-200">
            {providerRows.map((row) => (
              <div key={row.provider} className="data-row grid grid-cols-[minmax(120px,1fr)_72px_72px_72px] items-center gap-3 text-sm">
                <div className="min-w-0">
                  <Badge tone={providerTone[row.provider]}>{providerLabels[row.provider]}</Badge>
                </div>
                <NumberColumn label="运行时" value={row.runtimes} />
                <NumberColumn label="资产" value={row.assets} />
                <NumberColumn label="上报" value={row.reports} />
              </div>
            ))}
          </div>
        </SectionCard>

        <SectionCard
          title="Reporter 对话式部署"
          description="使用者在 WorkBuddy/OpenClaw 中说“帮我接入 DuckDock”后，自助登记 Reporter Credential 并完成自报任务配置。"
          action={<Link to="/reporter-setup" className="button-secondary">接入 SOP</Link>}
        >
          <div className="px-5 py-5 sm:px-6">
            <div className="space-y-3">
              {[
                "Reporter 登录 DuckDock 并自助 enroll",
                "拿到一次性显示的 dkr_report_* credential",
                "使用者通过对话安装 duckdock_reporter",
                "校验上报仅包含资产、会话摘要和脱敏统计",
                "确认后创建每周五 16:00 自报任务",
              ].map((text, index) => (
                <div key={text} className="flex gap-3">
                  <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-slate-900 text-xs font-semibold text-white">
                    {index + 1}
                  </div>
                  <div className="text-sm leading-7 text-slate-600">{text}</div>
                </div>
              ))}
            </div>
            <div className="mt-5 rounded-md border border-slate-200 bg-slate-50 px-4 py-3 font-mono text-xs text-slate-600">
              帮我把这个 WorkBuddy 接入 DuckDock，每周五下午四点自动上报。
            </div>
          </div>
        </SectionCard>
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        <SectionCard title="最近运行时" description="优先关注未同步、降级或没有 Reporter 上报的实例。">
          {recentRuntimes.length === 0 ? (
            <EmptyState text="还没有登记运行时。先在 Agent 控制平面创建 OpenClaw 或 WorkBuddy 实例。" />
          ) : (
            <div className="divide-y divide-slate-200">
              {recentRuntimes.map((runtime) => (
                <div key={runtime.id} className="data-row flex items-center justify-between gap-4">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-slate-900">{runtime.name}</div>
                    <div className="mt-1 flex flex-wrap gap-2">
                      <Badge tone={providerTone[runtime.provider]}>{providerLabels[runtime.provider]}</Badge>
                      <MonoPill>{runtime.deploy_type}</MonoPill>
                    </div>
                  </div>
                  <StatusPill status={runtime.status} />
                </div>
              ))}
            </div>
          )}
        </SectionCard>

        <SectionCard title="最近 Reporter 上报" description="上报会话代表 OpenClaw/WorkBuddy 的周期性自报包。">
          {recentReports.length === 0 ? (
            <EmptyState text="还没有上报会话。部署 duckdock_reporter 后，这里会显示 report_id 和解析状态。" />
          ) : (
            <div className="divide-y divide-slate-200">
              {recentReports.map((report) => (
                <div key={report.id} className="data-row flex items-center justify-between gap-4">
                  <div className="min-w-0">
                    <div className="truncate font-mono text-xs font-semibold text-slate-900">{report.report_id}</div>
                    <div className="mt-1 text-xs text-slate-500">
                      runtime #{report.runtime_id} · {report.report_type} · {formatDate(report.created_at)}
                    </div>
                  </div>
                  <ReportStatus status={report.status} />
                </div>
              ))}
            </div>
          )}
        </SectionCard>
      </div>

      <SectionCard
        title="命名空间与 Skill 仓库底座"
        description="原有私有 Skill 仓库仍作为 AI 资产包、模板、版本和分发能力的底层仓库。"
        action={<Link to="/namespaces" className="button-secondary">打开命名空间</Link>}
      >
        {loading ? (
          <EmptyState text="正在加载概览..." />
        ) : data.namespaces.length === 0 ? (
          <EmptyState text="还没有可用的命名空间。" />
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200">
              <thead className="bg-slate-50">
                <tr className="text-left text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">
                  <th className="px-6 py-4">Namespace</th>
                  <th className="px-6 py-4">Skills</th>
                  <th className="px-6 py-4">Versions</th>
                  <th className="px-6 py-4">Storage</th>
                  <th className="px-6 py-4 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 bg-white">
                {data.namespaces.map((item) => (
                  <tr key={item.namespace.id} className="transition hover:bg-slate-50/70">
                    <td className="px-6 py-5">
                      <div className="font-semibold text-slate-900">{item.namespace.name}</div>
                      <div className="mt-1 text-sm text-slate-500">{item.namespace.description ?? "暂无描述"}</div>
                    </td>
                    <td className="px-6 py-5 text-sm text-slate-700">{item.skills.length}</td>
                    <td className="px-6 py-5 text-sm text-slate-700">
                      {item.skills.reduce((sum, skill) => sum + skill.version_count, 0)}
                    </td>
                    <td className="px-6 py-5">
                      <div className="text-sm font-medium text-slate-700">
                        {formatBytes(item.quota?.current_storage_bytes ?? 0)} / {formatBytes(item.quota?.max_storage_bytes ?? 0)}
                      </div>
                      <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-slate-100">
                        <div
                          className="h-full rounded-full bg-indigo-500"
                          style={{
                            width: `${Math.min(
                              100,
                              ((item.quota?.current_storage_bytes ?? 0) / Math.max(item.quota?.max_storage_bytes ?? 1, 1)) * 100
                            )}%`,
                          }}
                        />
                      </div>
                    </td>
                    <td className="px-6 py-5 text-right">
                      <Link to={`/namespaces/${item.namespace.name}`} className="button-link">
                        打开
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>
    </div>
  );
}

function CompactMetric({
  label,
  value,
  detail,
  icon: Icon,
}: {
  label: string;
  value: string;
  detail: string;
  icon: typeof FileClock;
}) {
  return (
    <div className="surface-card flex min-h-[88px] items-center gap-4 px-5 py-4">
      <IconTile>
        <Icon className="h-5 w-5" />
      </IconTile>
      <div className="min-w-0">
        <div className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-400">{label}</div>
        <div className="mt-1 truncate text-sm font-semibold text-slate-900">{value}</div>
        <div className="mt-0.5 truncate text-xs text-slate-500">{detail}</div>
      </div>
    </div>
  );
}

function NumberColumn({ label, value }: { label: string; value: number }) {
  return (
    <div className="text-right">
      <div className="text-base font-semibold text-slate-900">{value}</div>
      <div className="mt-0.5 text-xs text-slate-400">{label}</div>
    </div>
  );
}

function StatusPill({ status }: { status: RuntimeInstance["status"] }) {
  const tone: Tone = status === "active" ? "emerald" : status === "degraded" ? "amber" : "rose";
  const Icon = status === "active" ? CheckCircle2 : status === "degraded" ? AlertTriangle : CircleDot;
  return (
    <Badge tone={tone} icon={<Icon className="h-3.5 w-3.5" />}>
      {status}
    </Badge>
  );
}

function ReportStatus({ status }: { status: ReportUploadSession["status"] }) {
  const positive = ["succeeded", "uploaded", "ingesting"].includes(status);
  const tone: Tone = positive ? "emerald" : status === "failed" || status === "expired" ? "rose" : "amber";
  const Icon = positive ? ShieldCheck : status === "failed" || status === "expired" ? AlertTriangle : CircleDot;
  return (
    <Badge tone={tone} icon={<Icon className="h-3.5 w-3.5" />}>
      {status}
    </Badge>
  );
}

function formatDate(value: string | null) {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "-";
  }
  return date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function formatBytes(bytes: number) {
  if (!bytes) {
    return "0 B";
  }
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(value >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}
