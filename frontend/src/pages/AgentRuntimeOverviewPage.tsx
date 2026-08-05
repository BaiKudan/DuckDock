import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import {
  ArrowLeft,
  Boxes,
  CalendarClock,
  CheckCircle2,
  Clock3,
  FileText,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  Wand2,
} from "lucide-react";
import { useNavigate, useParams, useSearchParams } from "../router";

import {
  controlPlaneApi,
  type AgentInsightJob,
  type AgentOverview,
  type AgentReportTimelineItem,
  type AiAssist,
  type MemoryCandidate,
} from "../api/client";
import { AiAssistBadge } from "../components/AiAssistBadge";
import { Badge, Button, Card, MetricCard, MonoPill, PageHeader, type Tone } from "../components/ui";

const reporterTone: Record<AgentOverview["reporter"]["state"], Tone> = {
  healthy: "emerald",
  waiting: "amber",
  failed: "rose",
  unknown: "neutral",
};

const insightTone: Record<AgentInsightJob["status"], Tone> = {
  pending: "amber",
  running: "indigo",
  succeeded: "emerald",
  failed: "rose",
};

const sourceTone: Record<AgentReportTimelineItem["source"], Tone> = {
  structured_report: "emerald",
  report_pack: "indigo",
};

export default function AgentRuntimeOverviewPage() {
  const { message } = AntApp.useApp();
  const navigate = useNavigate();
  const { runtimeId: runtimeIdParam } = useParams();
  const [searchParams] = useSearchParams();
  const runtimeId = Number(runtimeIdParam);
  const userId = Number(searchParams.get("user_id"));
  const [activeJobId, setActiveJobId] = useState<number | null>(null);
  const canLoad = Number.isInteger(runtimeId) && runtimeId > 0 && Number.isInteger(userId) && userId > 0;

  const overviewQuery = useQuery({
    queryKey: ["agent-overview", userId, runtimeId],
    enabled: canLoad,
    queryFn: async () => (await controlPlaneApi.getAgentOverview(userId, runtimeId)).data,
  });

  useEffect(() => {
    if (!activeJobId && overviewQuery.data?.latest_insight?.id) {
      setActiveJobId(overviewQuery.data.latest_insight.id);
    }
  }, [activeJobId, overviewQuery.data?.latest_insight?.id]);

  const insightQuery = useQuery({
    queryKey: ["agent-insight", activeJobId],
    enabled: Boolean(activeJobId),
    queryFn: async () => (await controlPlaneApi.getAgentInsight(activeJobId as number)).data,
    refetchInterval: (query) => {
      const job = query.state.data as AgentInsightJob | undefined;
      return job?.status === "pending" || job?.status === "running" ? 3000 : false;
    },
  });

  const analyzeMutation = useMutation({
    mutationFn: async () => (await controlPlaneApi.createAgentInsight(userId, runtimeId)).data,
    onSuccess: async (data) => {
      setActiveJobId(data.job.id);
      await overviewQuery.refetch();
      message.success("AI 概览已进入分析队列");
    },
    onError: (err: any) => {
      message.error(err.response?.data?.detail ?? "AI 概览生成失败");
    },
  });

  const overview = overviewQuery.data;
  const insight = insightQuery.data ?? overview?.latest_insight ?? null;
  const structuredPercent = overview?.metrics.report_count
    ? Math.round((overview.metrics.structured_report_count / overview.metrics.report_count) * 100)
    : 0;
  const packPercent = overview?.metrics.report_count ? 100 - structuredPercent : 0;

  const title = overview ? `${overview.user.full_name || overview.user.username} / ${overview.runtime.name}` : "Agent 概览";

  if (!canLoad) {
    return (
      <div className="app-page">
        <PageHeader
          eyebrow="Control Plane"
          title="Agent 概览"
          actions={
            <Button variant="secondary" icon={<ArrowLeft className="h-4 w-4" />} onClick={() => navigate("/control-plane")}>
              返回
            </Button>
          }
        />
        <Card padded>
          <div className="text-sm text-slate-600">缺少 runtime_id 或 user_id，无法定位员工 agent 时间线。</div>
        </Card>
      </div>
    );
  }

  return (
    <div className="app-page space-y-6">
      <PageHeader
        eyebrow="Agent Timeline"
        title={title}
        description={
          overview
            ? `${overview.user.email} · ${overview.runtime.provider} · ${overview.runtime.deploy_type}`
            : "正在加载员工 agent 时间线"
        }
        actions={
          <>
            <Button variant="secondary" icon={<ArrowLeft className="h-4 w-4" />} onClick={() => navigate("/control-plane")}>
              返回
            </Button>
            <Button
              variant="secondary"
              icon={<RefreshCw className="h-4 w-4" />}
              onClick={() => overviewQuery.refetch()}
              disabled={overviewQuery.isFetching}
            >
              刷新
            </Button>
            <Button
              icon={<Wand2 className="h-4 w-4" />}
              onClick={() => analyzeMutation.mutate()}
              disabled={analyzeMutation.isPending || overviewQuery.isLoading}
            >
              生成 AI 概览
            </Button>
          </>
        }
      />

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="报告"
          value={overview?.metrics.report_count ?? 0}
          detail={`${overview?.metrics.structured_report_count ?? 0} 结构化 · ${overview?.metrics.pack_report_count ?? 0} 交接包`}
          icon={FileText}
          loading={overviewQuery.isLoading}
        />
        <MetricCard
          label="资产"
          value={overview?.metrics.asset_count ?? 0}
          detail={`${overview?.metrics.work_trace_count ?? 0} 条工作历程`}
          icon={Boxes}
          loading={overviewQuery.isLoading}
        />
        <MetricCard
          label="风险信号"
          value={overview?.metrics.risk_signal_count ?? 0}
          detail={`${overview?.metrics.blocker_count ?? 0} 个 blocker`}
          icon={ShieldAlert}
          tone={(overview?.metrics.risk_signal_count ?? 0) > 0 ? "rose" : "neutral"}
          loading={overviewQuery.isLoading}
        />
        <MetricCard
          label="最近活动"
          value={formatDateShort(overview?.metrics.latest_activity_at)}
          detail={overview?.metrics.latest_report_at ? `最新报告 ${formatDateTime(overview.metrics.latest_report_at)}` : "暂无报告"}
          icon={CalendarClock}
          loading={overviewQuery.isLoading}
        />
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.35fr)_minmax(360px,0.65fr)]">
        <Card
          title="AI 概览"
          description="基于已入库的日报、周报、交接包、资产和信号生成"
          action={insight ? <AiAssistBadge aiAssist={normalizeAiAssist(insight.ai_assist_json)} /> : null}
          padded
        >
          <InsightPanel insight={insight} loading={Boolean(activeJobId && insightQuery.isFetching && !insight)} />
        </Card>

        <Card title="Reporter" description="员工侧长期上报凭证与心跳" padded>
          {overview ? (
            <div className="space-y-4">
              <div className="flex items-center justify-between gap-3">
                <Badge tone={reporterTone[overview.reporter.state]}>{overview.reporter.state}</Badge>
                {overview.reporter.token_prefix ? <MonoPill>{overview.reporter.token_prefix}</MonoPill> : null}
              </div>
              <p className="text-sm leading-6 text-slate-600">{overview.reporter.message}</p>
              <div className="grid gap-3 text-sm sm:grid-cols-2">
                <Fact label="最近心跳" value={formatDateTime(overview.reporter.last_heartbeat_at)} />
                <Fact label="最近使用" value={formatDateTime(overview.reporter.last_used_at)} />
              </div>
              <div>
                <div className="mb-2 flex items-center justify-between text-xs font-semibold uppercase text-slate-500">
                  <span>报告构成</span>
                  <span>{overview.metrics.report_count}</span>
                </div>
                <div className="flex h-3 overflow-hidden rounded-full bg-slate-100">
                  <div className="bg-emerald-500" style={{ width: `${structuredPercent}%` }} />
                  <div className="bg-indigo-500" style={{ width: `${packPercent}%` }} />
                </div>
                <div className="mt-2 flex flex-wrap gap-2 text-xs text-slate-500">
                  <span>结构化 {overview.metrics.structured_report_count}</span>
                  <span>交接包 {overview.metrics.pack_report_count}</span>
                </div>
              </div>
            </div>
          ) : (
            <SkeletonLines />
          )}
        </Card>
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.25fr)_minmax(360px,0.75fr)]">
        <Card title="报告时间线" description="日报/周报与交接包统一视图">
          <TimelineList items={overview?.timeline ?? []} loading={overviewQuery.isLoading} />
        </Card>

        <div className="space-y-6">
          <Card title="资产" description="该员工 agent 当前关联资产">
            <AssetList overview={overview} loading={overviewQuery.isLoading} />
          </Card>
          <Card title="信号" description="风险与交接候选项">
            <SignalList items={overview?.memory_candidates ?? []} loading={overviewQuery.isLoading} />
          </Card>
        </div>
      </div>
    </div>
  );
}

function InsightPanel({ insight, loading }: { insight: AgentInsightJob | null; loading: boolean }) {
  if (loading) {
    return <SkeletonLines />;
  }
  if (!insight) {
    return (
      <div className="flex min-h-[220px] flex-col items-center justify-center rounded-lg border border-dashed border-slate-200 text-center">
        <Sparkles className="mb-3 h-8 w-8 text-slate-300" />
        <div className="text-sm font-medium text-slate-700">尚未生成 AI 概览</div>
      </div>
    );
  }
  const result = insight.result_json ?? {};
  const pending = insight.status === "pending" || insight.status === "running";
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={insightTone[insight.status]} icon={pending ? <Clock3 className="h-3.5 w-3.5" /> : <CheckCircle2 className="h-3.5 w-3.5" />}>
          {insight.status}
        </Badge>
        <MonoPill>{insight.model || insight.prompt_version}</MonoPill>
        <span className="text-xs text-slate-500">{formatDateTime(insight.finished_at || insight.created_at)}</span>
      </div>
      {pending ? (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          分析任务正在排队或运行中。
        </div>
      ) : null}
      <section>
        <h2 className="text-sm font-semibold text-slate-900">摘要</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">{readText(result.executive_summary, "暂无摘要。")}</p>
      </section>
      <section>
        <h2 className="text-sm font-semibold text-slate-900">近期活动</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">{readText(result.recent_activity, "暂无近期活动。")}</p>
      </section>
      <ListSection title="风险" items={readList(result.risks)} empty="暂无风险信号。" tone="rose" />
      <section>
        <h2 className="text-sm font-semibold text-slate-900">交接就绪度</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">{readText(result.handover_readiness, "暂无交接判断。")}</p>
      </section>
      <ListSection title="建议" items={readList(result.recommendations)} empty="暂无建议。" tone="indigo" />
    </div>
  );
}

function TimelineList({ items, loading }: { items: AgentReportTimelineItem[]; loading: boolean }) {
  if (loading) return <SkeletonLines />;
  if (!items.length) {
    return <div className="p-6 text-sm text-slate-500">暂无报告时间线。</div>;
  }
  return (
    <div className="divide-y divide-slate-100">
      {items.map((item) => (
        <article key={`${item.source}-${item.report_id}`} className="grid gap-4 px-5 py-5 sm:grid-cols-[132px_minmax(0,1fr)] sm:px-6">
          <div className="text-sm text-slate-500">{formatDateShort(item.created_at)}</div>
          <div className="min-w-0 space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={sourceTone[item.source]}>{item.source === "structured_report" ? "结构化" : "交接包"}</Badge>
              <MonoPill>{item.report_type}</MonoPill>
              <Badge tone={item.status === "succeeded" ? "emerald" : "amber"}>{item.status}</Badge>
            </div>
            <div>
              <h2 className="text-base font-semibold text-slate-900">{item.title}</h2>
              {item.summary ? <p className="mt-1 line-clamp-3 text-sm leading-6 text-slate-600">{item.summary}</p> : null}
            </div>
            <div className="flex flex-wrap gap-2 text-xs text-slate-500">
              <span>{item.asset_count} assets</span>
              <span>{item.memory_candidate_count} candidates</span>
              <span>{item.risk_signal_count} risks</span>
              <span>{item.handover_signal_count} handover</span>
            </div>
            {item.blockers.length ? (
              <div className="rounded-md border border-rose-100 bg-rose-50 px-3 py-2 text-sm text-rose-800">
                {item.blockers.slice(0, 2).join(" · ")}
              </div>
            ) : null}
          </div>
        </article>
      ))}
    </div>
  );
}

function AssetList({ overview, loading }: { overview?: AgentOverview; loading: boolean }) {
  if (loading) return <SkeletonLines />;
  const items = overview?.assets ?? [];
  if (!items.length) return <div className="p-6 text-sm text-slate-500">暂无资产。</div>;
  return (
    <div className="divide-y divide-slate-100">
      {items.slice(0, 8).map((asset) => (
        <div key={asset.id} className="flex items-start justify-between gap-3 px-5 py-4 sm:px-6">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-slate-900">{asset.name}</div>
            <div className="mt-1 flex flex-wrap gap-2">
              <MonoPill>{asset.asset_type}</MonoPill>
              <Badge tone={asset.criticality === "critical" || asset.criticality === "high" ? "amber" : "neutral"}>
                {asset.criticality}
              </Badge>
            </div>
          </div>
          <Badge tone={asset.status === "active" ? "emerald" : "amber"}>{asset.status}</Badge>
        </div>
      ))}
    </div>
  );
}

function SignalList({ items, loading }: { items: MemoryCandidate[]; loading: boolean }) {
  if (loading) return <SkeletonLines />;
  const signals = items.filter((item) => item.candidate_type === "risk_signal" || item.candidate_type === "handover_signal");
  if (!signals.length) return <div className="p-6 text-sm text-slate-500">暂无风险或交接信号。</div>;
  return (
    <div className="divide-y divide-slate-100">
      {signals.slice(0, 8).map((item) => (
        <div key={item.id} className="px-5 py-4 sm:px-6">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={item.candidate_type === "risk_signal" ? "rose" : "amber"}>
              {item.candidate_type === "risk_signal" ? "risk" : "handover"}
            </Badge>
            <span className="text-xs text-slate-500">{Math.round(item.confidence * 100)}%</span>
          </div>
          <div className="mt-2 text-sm font-semibold text-slate-900">{item.title}</div>
          {item.summary ? <p className="mt-1 text-sm leading-6 text-slate-600">{item.summary}</p> : null}
        </div>
      ))}
    </div>
  );
}

function ListSection({ title, items, empty, tone }: { title: string; items: string[]; empty: string; tone: Tone }) {
  return (
    <section>
      <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
      {items.length ? (
        <ul className="mt-2 space-y-2">
          {items.map((item) => (
            <li key={item} className="flex gap-2 text-sm leading-6 text-slate-600">
              <span className={["mt-2 h-1.5 w-1.5 shrink-0 rounded-full", tone === "rose" ? "bg-rose-500" : "bg-indigo-500"].join(" ")} />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-sm text-slate-500">{empty}</p>
      )}
    </section>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-slate-50 px-3 py-2">
      <div className="text-xs font-medium text-slate-500">{label}</div>
      <div className="mt-1 truncate text-sm font-semibold text-slate-900">{value}</div>
    </div>
  );
}

function SkeletonLines() {
  return (
    <div className="space-y-3">
      <div className="h-4 w-2/3 animate-pulse rounded bg-slate-100" />
      <div className="h-4 w-full animate-pulse rounded bg-slate-100" />
      <div className="h-4 w-5/6 animate-pulse rounded bg-slate-100" />
    </div>
  );
}

function normalizeAiAssist(value: AgentInsightJob["ai_assist_json"]): AiAssist | null {
  if (!value || typeof value.mode !== "string" || typeof value.degraded !== "boolean") return null;
  return {
    mode: value.mode === "llm" ? "llm" : "baseline",
    degraded: value.degraded,
    reason: typeof value.reason === "string" ? value.reason : null,
  };
}

function readText(value: unknown, fallback: string): string {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function readList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => String(item).trim()).filter(Boolean).slice(0, 8);
}

function formatDateTime(value?: string | null): string {
  if (!value) return "暂无";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatDateShort(value?: string | null): string {
  if (!value) return "暂无";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(value));
}
