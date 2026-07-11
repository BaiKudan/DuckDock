import { App as AntApp, Input, Modal } from "antd";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import {
  Archive,
  CheckCircle2,
  CircleAlert,
  ClipboardCheck,
  FileClock,
  FolderKanban,
  History,
  RefreshCw,
  ShieldCheck,
  UploadCloud,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  controlPlaneApi,
  type AIAsset,
  type AssetFeedbackAction,
  type AssetType,
  type Criticality,
  type HandoverCase,
  type MyAIWorkspace,
  type MyWorkspaceProjectContext,
  type MyWorkspaceReporterStatus,
  type RuntimeProvider,
  type WorkTrace,
} from "../api/client";
import { canAccessManagement } from "../authRoutes";
import { Badge, Button, Card, IconTile, MonoPill, type Tone } from "../components/ui";
import { useAuthStore } from "../store/auth";

interface AssetFeedbackEntry {
  action?: AssetFeedbackAction;
  note?: string | null;
  updated_at?: string | null;
}

const providerLabels: Record<RuntimeProvider, string> = {
  openclaw: "OpenClaw",
  arkclaw: "ArkClaw",
  workbuddy: "WorkBuddy",
  jvs: "JVS",
  custom: "Custom",
};

const assetTypeLabels: Record<AssetType, string> = {
  skill: "Skill",
  agent: "Agent",
  prompt: "Prompt",
  workflow: "Workflow",
  mcp: "MCP",
  tool: "Tool",
  knowledge_base: "Knowledge Base",
  scheduled_task: "Scheduled Task",
  credential_ref: "Credential Ref",
  workspace: "Workspace",
  other: "Other",
};

const criticalityTones: Record<Criticality, Tone> = {
  low: "neutral",
  medium: "indigo",
  high: "amber",
  critical: "rose",
};

const feedbackLabels: Record<AssetFeedbackAction, string> = {
  confirm: "已确认",
  supplement: "已补充",
  exclude: "已排除",
};

const feedbackTones: Record<AssetFeedbackAction, Tone> = {
  confirm: "emerald",
  supplement: "indigo",
  exclude: "neutral",
};

export default function PersonalWorkspacePage() {
  const { message } = AntApp.useApp();
  const user = useAuthStore((state) => state.user);
  const permissionKeys = useAuthStore((state) => state.permissionKeys);
  const [supplementAsset, setSupplementAsset] = useState<AIAsset | null>(null);
  const [supplementNote, setSupplementNote] = useState("");
  const [feedbackBusy, setFeedbackBusy] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["my-ai-workspace"],
    queryFn: async () => (await controlPlaneApi.getMyAIWorkspace()).data,
  });

  return (
    <PersonalWorkspaceContent
      query={query}
      userId={user?.id}
      username={user?.username ?? "-"}
      canOpenManagement={canAccessManagement(user, permissionKeys)}
      supplementAsset={supplementAsset}
      supplementNote={supplementNote}
      feedbackBusy={feedbackBusy}
      setSupplementAsset={setSupplementAsset}
      setSupplementNote={setSupplementNote}
      setFeedbackBusy={setFeedbackBusy}
      message={message}
    />
  );
}

function PersonalWorkspaceContent({
  query,
  userId,
  username,
  canOpenManagement,
  supplementAsset,
  supplementNote,
  feedbackBusy,
  setSupplementAsset,
  setSupplementNote,
  setFeedbackBusy,
  message,
}: {
  query: UseQueryResult<MyAIWorkspace, Error>;
  userId?: number;
  username: string;
  canOpenManagement: boolean;
  supplementAsset: AIAsset | null;
  supplementNote: string;
  feedbackBusy: string | null;
  setSupplementAsset: (asset: AIAsset | null) => void;
  setSupplementNote: (value: string) => void;
  setFeedbackBusy: (value: string | null) => void;
  message: ReturnType<typeof AntApp.useApp>["message"];
}) {
  const data = query.data;
  const assets = data?.assets ?? [];
  const traces = data?.work_traces ?? [];
  const handovers = data?.handovers ?? [];
  const contexts = data?.project_contexts ?? [];
  const reporterStatuses = data?.reporter_statuses ?? [];
  const readiness = data?.readiness;
  const offboardingVisible = data?.offboarding_visible ?? false;

  const stats = useMemo(() => {
    const skills = assets.filter((asset) => asset.asset_type === "skill").length;
    const agents = assets.filter((asset) => asset.asset_type === "agent").length;
    const prompts = assets.filter((asset) => asset.asset_type === "prompt").length;
    const workflows = assets.filter((asset) => asset.asset_type === "workflow").length;
    const pendingAssets = userId ? assets.filter((asset) => !getAssetFeedback(asset, userId)).length : assets.length;
    const confirmedAssets = Math.max(assets.length - pendingAssets, 0);
    const healthyReporters = reporterStatuses.filter((item) => item.state === "healthy").length;
    return { skills, agents, prompts, workflows, pendingAssets, confirmedAssets, healthyReporters };
  }, [assets, reporterStatuses, userId]);

  const prioritizedAssets = useMemo(() => {
    return [...assets].sort((left, right) => {
      const leftPending = userId && !getAssetFeedback(left, userId) ? 0 : 1;
      const rightPending = userId && !getAssetFeedback(right, userId) ? 0 : 1;
      if (leftPending !== rightPending) {
        return leftPending - rightPending;
      }
      return new Date(right.last_seen_at).getTime() - new Date(left.last_seen_at).getTime();
    });
  }, [assets, userId]);

  const pendingAssets = useMemo(() => {
    if (!userId) {
      return prioritizedAssets;
    }
    return prioritizedAssets.filter((asset) => !getAssetFeedback(asset, userId));
  }, [prioritizedAssets, userId]);

  async function submitFeedback(asset: AIAsset, action: AssetFeedbackAction, note?: string | null) {
    const busyKey = `${asset.id}:${action}`;
    setFeedbackBusy(busyKey);
    try {
      await controlPlaneApi.submitAssetFeedback(asset.id, { action, note });
      await query.refetch();
      message.success(action === "exclude" ? "已标记为排除" : "已保存反馈");
      if (supplementAsset?.id === asset.id) {
        setSupplementAsset(null);
        setSupplementNote("");
      }
    } catch (err: unknown) {
      message.error(getErrorDetail(err) ?? "保存反馈失败");
    } finally {
      setFeedbackBusy(null);
    }
  }

  return (
    <div className="app-page max-w-[1180px] space-y-5">
      <Card className="overflow-hidden">
        <div className="grid lg:grid-cols-[minmax(0,1fr)_340px]">
          <div className="px-7 py-7 lg:px-8 lg:py-8">
            <div className="section-kicker">MY AI WORKSPACE</div>
            <div className="mt-4 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
              <div className="min-w-0">
                <h1 className="text-3xl font-bold tracking-tight text-slate-950">个人工作台 / 我的 AI 资产</h1>
                <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-500">
                  当前登录为 {username}。这里用于确认你创建、维护或使用过的 AI 资产，补充必要上下文，并检查 Reporter 上报状态。
                </p>
              </div>
              <div className="flex shrink-0 gap-2">
                <Button
                  variant="secondary"
                  onClick={() => void query.refetch()}
                  disabled={query.isFetching}
                  icon={<RefreshCw className={`h-4 w-4 ${query.isFetching ? "animate-spin" : ""}`} />}
                >
                  刷新
                </Button>
                {canOpenManagement ? (
                  <Link to="/control-plane">
                    <Button variant="secondary">管理入口</Button>
                  </Link>
                ) : null}
              </div>
            </div>

            <div className="mt-7 grid gap-3 sm:grid-cols-3">
              <HeroStat label="待确认资产" value={String(stats.pendingAssets)} detail={`共 ${assets.length} 个资产`} icon={Archive} />
              <HeroStat label="最近工作历程" value={String(traces.length)} detail="会话与任务摘要" icon={History} />
              <HeroStat label="Reporter" value={`${stats.healthyReporters}/${reporterStatuses.length}`} detail="健康 / 关联运行时" icon={UploadCloud} />
            </div>
          </div>

          <div className="border-t border-slate-200 bg-slate-50 px-7 py-7 lg:border-l lg:border-t-0 lg:px-8 lg:py-8">
            {offboardingVisible ? (
              <OffboardingPanel readiness={readiness} handoverCount={handovers.length} />
            ) : (
              <ActiveUserPanel
                pendingAssets={stats.pendingAssets}
                confirmedAssets={stats.confirmedAssets}
                totalAssets={assets.length}
                healthyReporters={stats.healthyReporters}
                reporterCount={reporterStatuses.length}
              />
            )}
          </div>
        </div>
      </Card>

      {query.error ? (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">个人工作台加载失败</div>
      ) : null}

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
        <Card title="当前最重要的事" description="优先确认待归属资产。确认、补充或排除后，系统会把资产关系记录到你的个人资产档案。">
          {prioritizedAssets.length === 0 ? (
            <GuidedEmpty onRefresh={() => void query.refetch()} refreshing={query.isFetching} />
          ) : (
            <div className="divide-y divide-slate-200">
              {prioritizedAssets.slice(0, 8).map((asset) => (
                <AssetRow
                  key={asset.id}
                  asset={asset}
                  feedback={userId ? getAssetFeedback(asset, userId) : null}
                  busy={feedbackBusy?.startsWith(`${asset.id}:`) ?? false}
                  onConfirm={() => void submitFeedback(asset, "confirm")}
                  onSupplement={() => {
                    setSupplementAsset(asset);
                    setSupplementNote(getAssetFeedback(asset, userId ?? 0)?.note ?? "");
                  }}
                  onExclude={() => void submitFeedback(asset, "exclude")}
                />
              ))}
            </div>
          )}
        </Card>

        <aside className="space-y-5">
          <Card title="资产类型" description="按采集到的资产类型汇总。" padded>
            <div className="space-y-4">
              <SideNumber label="Skills" value={stats.skills} />
              <SideNumber label="Agents" value={stats.agents} />
              <SideNumber label="Prompts" value={stats.prompts} />
              <SideNumber label="Workflows" value={stats.workflows} />
            </div>
          </Card>

          <Card title="Reporter 自报" description="只显示与你的资产或工作历程有关联的运行时。">
            {reporterStatuses.length === 0 ? (
              <div className="px-6 py-7 text-sm leading-6 text-slate-500">
                暂未发现与你关联的 Reporter 上报。让 WorkBuddy/OpenClaw 执行接入对话并上传一次 dry-run 包后，这里会出现状态。
              </div>
            ) : (
              <div className="divide-y divide-slate-200">
                {reporterStatuses.map((item) => (
                  <ReporterRow key={item.runtime.id} item={item} />
                ))}
              </div>
            )}
          </Card>
        </aside>
      </div>

      <Card title="最近信号" description="把项目上下文、工作历程和待处理事项压缩到一处，避免在空数据时铺满页面。">
        <div className="grid divide-y divide-slate-200 lg:grid-cols-3 lg:divide-x lg:divide-y-0">
          <SignalColumn
            title="项目上下文"
            icon={FolderKanban}
            emptyText="暂无项目上下文"
            items={contexts.slice(0, 5)}
            renderItem={(item) => <ProjectContextItem item={item} />}
          />
          <SignalColumn
            title="最近工作历程"
            icon={FileClock}
            emptyText="暂无工作历程"
            items={traces.slice(0, 5)}
            renderItem={(item) => <TraceItem trace={item} />}
          />
          {offboardingVisible ? (
            <SignalColumn
              title="离职交接事项"
              icon={ClipboardCheck}
              emptyText="暂无离职交接事项"
              items={handovers.slice(0, 5)}
              renderItem={(item) => <HandoverItem item={item} />}
            />
          ) : (
            <SignalColumn
              title="待确认资产"
              icon={ClipboardCheck}
              emptyText="暂无待确认资产"
              items={pendingAssets.slice(0, 5)}
              renderItem={(item) => <AssetSignalItem asset={item} />}
            />
          )}
        </div>
      </Card>

      <Modal
        title="补充资产说明"
        open={supplementAsset !== null}
        okText="保存补充"
        cancelText="取消"
        confirmLoading={feedbackBusy?.endsWith(":supplement") ?? false}
        onCancel={() => {
          setSupplementAsset(null);
          setSupplementNote("");
        }}
        onOk={() => {
          if (supplementAsset) {
            void submitFeedback(supplementAsset, "supplement", supplementNote.trim() || null);
          }
        }}
      >
        <div className="mb-3 text-sm text-slate-500">
          {supplementAsset?.name ?? "-"} 的说明会写入 DuckDock 资产反馈，用于后续归属分析和上下文补全。
        </div>
        <Input.TextArea
          value={supplementNote}
          onChange={(event) => setSupplementNote(event.target.value)}
          rows={5}
          maxLength={1000}
          showCount
          placeholder="例如：这个 Prompt 只用于测试，不应进入正式资产范围；或这个 Agent 由项目组共同维护。"
        />
      </Modal>
    </div>
  );
}

function ActiveUserPanel({
  pendingAssets,
  confirmedAssets,
  totalAssets,
  healthyReporters,
  reporterCount,
}: {
  pendingAssets: number;
  confirmedAssets: number;
  totalAssets: number;
  healthyReporters: number;
  reporterCount: number;
}) {
  const reviewedPercent = totalAssets ? Math.round((confirmedAssets / totalAssets) * 100) : 0;
  return (
    <div>
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="text-sm font-medium text-slate-500">个人资产状态</div>
          <div className="mt-3 text-5xl font-bold tracking-tight text-slate-950">{pendingAssets}</div>
          <div className="mt-2 text-sm text-slate-500">个资产等待你确认</div>
        </div>
        <Archive className="h-11 w-11 text-indigo-500" />
      </div>
      <div className="mt-5 h-2 overflow-hidden rounded-full bg-white">
        <div className="h-full rounded-full bg-indigo-600" style={{ width: `${reviewedPercent}%` }} />
      </div>
      <div className="mt-5 grid gap-3 text-sm text-slate-500">
        <div className="flex items-center justify-between">
          <span>已确认资产</span>
          <span className="font-mono font-semibold text-slate-900">
            {confirmedAssets}/{totalAssets}
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span>Reporter 健康</span>
          <span className="font-mono font-semibold text-slate-900">
            {healthyReporters}/{reporterCount}
          </span>
        </div>
      </div>
      <div className="mt-5 flex gap-2 text-xs leading-5 text-slate-500">
        <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" />
        <span>保持 Reporter 定期上报，并及时处理待确认资产，可以让个人资产档案更准确。</span>
      </div>
    </div>
  );
}

function OffboardingPanel({ readiness, handoverCount }: { readiness: MyAIWorkspace["readiness"] | undefined; handoverCount: number }) {
  return (
    <div>
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="text-sm font-medium text-slate-500">离职交接进度</div>
          <div className="mt-3 text-5xl font-bold tracking-tight text-slate-950">{readiness?.score ?? 0}%</div>
        </div>
        <ShieldCheck className="h-11 w-11 text-indigo-500" />
      </div>
      <div className="mt-5 h-2 overflow-hidden rounded-full bg-white">
        <div className="h-full rounded-full bg-indigo-600" style={{ width: `${readiness?.score ?? 0}%` }} />
      </div>
      <div className="mt-5 text-sm leading-6 text-slate-500">
        {readiness?.reviewed_assets ?? 0}/{readiness?.total_assets ?? 0} 个资产已有个人反馈，当前关联 {handoverCount} 个交接单。
      </div>
      <div className="mt-4 space-y-2">
        {(readiness?.next_actions ?? []).slice(0, 2).map((item) => (
          <div key={item} className="flex gap-2 text-xs leading-5 text-slate-500">
            <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" />
            <span>{item}</span>
          </div>
        ))}
        {(readiness?.next_actions.length ?? 0) === 0 ? (
          <div className="flex gap-2 text-xs leading-5 text-slate-500">
            <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500" />
            <span>当前没有明显交接缺口。</span>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function HeroStat({ label, value, detail, icon: Icon }: { label: string; value: string; detail: string; icon: LucideIcon }) {
  return (
    <div className="flex items-center gap-3 border-l border-slate-200 pl-4">
      <IconTile size="sm">
        <Icon className="h-4 w-4" />
      </IconTile>
      <div className="min-w-0">
        <div className="text-xs font-medium text-slate-500">{label}</div>
        <div className="mt-0.5 flex items-baseline gap-2">
          <span className="text-xl font-bold tracking-tight text-slate-950">{value}</span>
          <span className="truncate text-xs text-slate-400">{detail}</span>
        </div>
      </div>
    </div>
  );
}

function GuidedEmpty({ onRefresh, refreshing }: { onRefresh: () => void; refreshing: boolean }) {
  return (
    <div className="px-7 py-10">
      <div className="mx-auto max-w-xl text-center">
        <div className="flex justify-center">
          <IconTile size="lg">
            <Archive className="h-5 w-5" />
          </IconTile>
        </div>
        <div className="mt-4 text-base font-semibold text-slate-900">还没有需要你确认的 AI 资产</div>
        <p className="mt-2 text-sm leading-6 text-slate-500">
          这通常意味着 Reporter 还没有完成第一次上报，或管理员尚未把运行时账号与你的账号关联。
        </p>
        <div className="mt-6 grid gap-3 text-left sm:grid-cols-3">
          {["Reporter 上传归档包", "DuckDock 识别资产和上下文", "你确认、补充或排除资产"].map((item, index) => (
            <div key={item} className="border-l border-slate-200 pl-3">
              <div className="text-xs font-semibold text-slate-400">0{index + 1}</div>
              <div className="mt-1 text-sm leading-5 text-slate-600">{item}</div>
            </div>
          ))}
        </div>
        <div className="mt-6 flex justify-center">
          <Button
            variant="secondary"
            onClick={onRefresh}
            disabled={refreshing}
            icon={<RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />}
          >
            重新检查
          </Button>
        </div>
      </div>
    </div>
  );
}

function SideNumber({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span className="text-sm text-slate-500">{label}</span>
      <span className="font-mono text-sm font-semibold text-slate-900">{value}</span>
    </div>
  );
}

function AssetRow({
  asset,
  feedback,
  busy,
  onConfirm,
  onSupplement,
  onExclude,
}: {
  asset: AIAsset;
  feedback: AssetFeedbackEntry | null;
  busy: boolean;
  onConfirm: () => void;
  onSupplement: () => void;
  onExclude: () => void;
}) {
  const isPending = !feedback?.action;
  return (
    <div className="data-row grid gap-4 lg:grid-cols-[minmax(0,1fr)_130px_220px] lg:items-center">
      <div className="min-w-0">
        <div className="flex min-w-0 items-center gap-2">
          {isPending ? <span className="h-2 w-2 shrink-0 rounded-full bg-amber-400" /> : null}
          <div className="truncate text-sm font-semibold text-slate-900">{asset.name}</div>
        </div>
        <div className="mt-2 flex flex-wrap gap-2">
          <MonoPill>{assetTypeLabels[asset.asset_type]}</MonoPill>
          <MonoPill>{providerLabels[asset.source_provider]}</MonoPill>
          <Badge tone={criticalityTones[asset.criticality]}>{asset.criticality}</Badge>
          <MonoPill>{formatDate(asset.last_seen_at)}</MonoPill>
        </div>
      </div>
      <div>
        {feedback?.action ? (
          <Badge tone={feedbackTones[feedback.action]}>{feedbackLabels[feedback.action]}</Badge>
        ) : (
          <Badge tone="amber">待确认</Badge>
        )}
      </div>
      <div className="flex flex-wrap gap-2 lg:justify-end">
        <Button variant="primary" size="sm" onClick={onConfirm} disabled={busy}>
          确认
        </Button>
        <Button variant="secondary" size="sm" onClick={onSupplement} disabled={busy}>
          补充
        </Button>
        <Button variant="secondary" size="sm" danger onClick={onExclude} disabled={busy}>
          排除
        </Button>
      </div>
    </div>
  );
}

function ReporterRow({ item }: { item: MyWorkspaceReporterStatus }) {
  const positive = item.state === "healthy";
  const failed = item.state === "failed";
  const Icon = positive ? CheckCircle2 : failed ? XCircle : CircleAlert;
  const tone: Tone = positive ? "emerald" : failed ? "rose" : "amber";
  return (
    <div className="data-row flex items-center justify-between gap-4">
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold text-slate-900">{item.runtime.name}</div>
        <div className="mt-1 text-xs text-slate-500">
          {providerLabels[item.runtime.provider]} / {item.latest_report?.report_id ?? "no report"}
        </div>
      </div>
      <Badge tone={tone} className="shrink-0" icon={<Icon className="h-3.5 w-3.5" />}>
        {item.state}
      </Badge>
    </div>
  );
}

function SignalColumn<T>({
  title,
  icon: Icon,
  emptyText,
  items,
  renderItem,
}: {
  title: string;
  icon: LucideIcon;
  emptyText: string;
  items: T[];
  renderItem: (item: T) => React.ReactNode;
}) {
  return (
    <div className="min-h-[220px] px-6 py-5">
      <div className="mb-4 flex items-center gap-2">
        <Icon className="h-4 w-4 text-slate-400" />
        <div className="text-sm font-semibold text-slate-900">{title}</div>
      </div>
      {items.length === 0 ? (
        <div className="flex h-32 items-center justify-center text-sm text-slate-400">{emptyText}</div>
      ) : (
        <div className="space-y-4">
          {items.map((item, index) => (
            <div key={index}>{renderItem(item)}</div>
          ))}
        </div>
      )}
    </div>
  );
}

function ProjectContextItem({ item }: { item: MyWorkspaceProjectContext }) {
  return (
    <div className="min-w-0">
      <div className="truncate text-sm font-medium text-slate-900">{item.name}</div>
      <div className="mt-1 text-xs text-slate-500">
        {item.asset_count} 个资产 / {item.trace_count} 条历程
      </div>
    </div>
  );
}

function TraceItem({ trace }: { trace: WorkTrace }) {
  return (
    <div className="min-w-0">
      <div className="truncate text-sm font-medium text-slate-900">{trace.title}</div>
      <div className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500">{trace.summary ?? "暂无摘要"}</div>
    </div>
  );
}

function HandoverItem({ item }: { item: HandoverCase }) {
  return (
    <div className="min-w-0">
      <div className="truncate text-sm font-medium text-slate-900">{item.title}</div>
      <div className="mt-1 text-xs text-slate-500">
        {item.status} / {formatDate(item.created_at)}
      </div>
    </div>
  );
}

function AssetSignalItem({ asset }: { asset: AIAsset }) {
  return (
    <div className="min-w-0">
      <div className="truncate text-sm font-medium text-slate-900">{asset.name}</div>
      <div className="mt-1 flex flex-wrap gap-2 text-xs text-slate-500">
        <MonoPill>{assetTypeLabels[asset.asset_type]}</MonoPill>
        <MonoPill>{providerLabels[asset.source_provider]}</MonoPill>
      </div>
    </div>
  );
}

function getAssetFeedback(asset: AIAsset, userId: number): AssetFeedbackEntry | null {
  const feedback = asset.metadata_json?.duckdock_user_feedback;
  if (!isRecord(feedback)) {
    return null;
  }
  const value = feedback[String(userId)];
  if (!isRecord(value)) {
    return null;
  }
  const action = value.action;
  if (action !== "confirm" && action !== "supplement" && action !== "exclude") {
    return null;
  }
  return {
    action,
    note: typeof value.note === "string" ? value.note : null,
    updated_at: typeof value.updated_at === "string" ? value.updated_at : null,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
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

function getErrorDetail(err: unknown) {
  if (!isRecord(err)) {
    return null;
  }
  const response = err.response;
  if (!isRecord(response)) {
    return null;
  }
  const data = response.data;
  if (!isRecord(data)) {
    return null;
  }
  return typeof data.detail === "string" ? data.detail : null;
}
