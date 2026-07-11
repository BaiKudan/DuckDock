import {
  AlertTriangle,
  CheckCircle2,
  CircleDot,
  Copy,
  ExternalLink,
  PlayCircle,
  Puzzle,
  RefreshCw,
  ServerCog,
  Square,
  TerminalSquare,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  componentsApi,
  type ManagedComponent,
  type ManagedComponentDependency,
  type ManagedComponentService,
  type ManagedComponentStatus,
} from "../api/client";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  IconTile,
  MonoPill,
  PageHeader,
  type Tone,
} from "../components/ui";

const statusLabels: Record<ManagedComponentStatus, string> = {
  not_installed: "未安装",
  stopped: "已停止",
  starting: "启动中",
  running: "运行中",
  degraded: "异常",
  unavailable: "不可用",
  error: "错误",
};

const serviceDescriptions: Record<string, string> = {
  mysql: "DuckDock 核心业务库，保存用户、运行时、AI 资产、工作历程、交接和上报记录。",
  redis: "DuckDock 核心队列与缓存，用于 Celery 任务、异步采集和上报包解析。",
  minio: "DuckDock 核心对象存储，保存 Skill 包、Reporter 上报包和证据对象。",
  postgres: "Langfuse 专用 PostgreSQL，不是 DuckDock 主库；仅在观测组件启用时使用。",
  clickhouse: "Langfuse 分析库，用于 Trace、事件和评估数据的分析查询。",
  "langfuse-db-init": "一次性初始化容器，在 Langfuse PostgreSQL 中创建数据库，执行完成后退出是正常状态。",
  "langfuse-minio-init": "一次性初始化容器，在 MinIO 中创建 Langfuse bucket，执行完成后退出是正常状态。",
  "langfuse-worker": "Langfuse 后台 Worker，处理 Trace、事件、队列和对象写入。",
  "langfuse-web": "Langfuse Web 控制台，用于查看 LLM Trace、Prompt 审计和 Clinic 评估链路。",
};

export default function ComponentManagementPage() {
  const [components, setComponents] = useState<ManagedComponent[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const langfuse = useMemo(() => components.find((component) => component.key === "langfuse") ?? null, [components]);

  async function loadComponents(options?: { silent?: boolean }) {
    if (!options?.silent) {
      setLoading(true);
    }
    setError("");
    try {
      const { data } = await componentsApi.list();
      setComponents(data);
    } catch (err: unknown) {
      setError(getErrorDetail(err) ?? "组件状态加载失败");
    } finally {
      if (!options?.silent) {
        setLoading(false);
      }
    }
  }

  async function runAction(componentKey: string, action: "start" | "stop") {
    setBusyKey(componentKey);
    setError("");
    setNotice("");
    try {
      const { data } =
        action === "start" ? await componentsApi.start(componentKey) : await componentsApi.stop(componentKey);
      setComponents((current) => current.map((component) => (component.key === componentKey ? data.component : component)));
      setNotice(action === "start" ? "组件启动命令已提交。" : "组件停止命令已提交。");
    } catch (err: unknown) {
      setError(getErrorDetail(err) ?? "组件操作失败");
    } finally {
      setBusyKey(null);
    }
  }

  async function copyCommand(command: string) {
    await navigator.clipboard.writeText(command);
    setNotice("命令已复制。");
  }

  useEffect(() => {
    void loadComponents();
    const timer = window.setInterval(() => {
      void loadComponents({ silent: true });
    }, 10000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className="app-page max-w-7xl space-y-6">
      <PageHeader
        eyebrow="OPTIONAL COMPONENTS"
        title="组件管理"
        description="DuckDock 核心服务默认不加载观测组件。管理员可以在核心服务健康后，按需安装并启动独立组件。"
        actions={
          <Button
            variant="secondary"
            onClick={() => void loadComponents()}
            disabled={loading}
            icon={<RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />}
          >
            刷新
          </Button>
        }
      />

      {error ? <div className="soft-rose rounded-lg px-4 py-3 text-sm">{error}</div> : null}
      {notice ? <div className="soft-emerald rounded-lg px-4 py-3 text-sm">{notice}</div> : null}

      <div className="grid gap-4 md:grid-cols-3">
        <SummaryTile
          label="核心依赖"
          value={langfuse?.core_ready ? "就绪" : "未就绪"}
          detail="MySQL、Redis、MinIO 必须先运行。"
          icon={ServerCog}
          tone={langfuse?.core_ready ? "emerald" : "amber"}
        />
        <SummaryTile
          label="观测组件"
          value={langfuse ? statusLabels[langfuse.status] : "-"}
          detail="Langfuse 属于可选启动组。"
          icon={Puzzle}
          tone={langfuse?.status === "running" ? "emerald" : "indigo"}
        />
        <SummaryTile
          label="启动组"
          value={langfuse?.profile ?? "observability"}
          detail="Compose profile 隔离核心与可选组件。"
          icon={TerminalSquare}
          tone="indigo"
          mono
        />
      </div>

      {loading ? (
        <Card title="组件" description="正在读取 Docker Compose 状态。">
          <EmptyState text="加载中..." />
        </Card>
      ) : null}

      {!loading && langfuse ? (
        <LangfuseCard
          component={langfuse}
          busy={busyKey === langfuse.key}
          onStart={() => void runAction(langfuse.key, "start")}
          onStop={() => void runAction(langfuse.key, "stop")}
          onCopy={(command) => void copyCommand(command)}
        />
      ) : null}
    </div>
  );
}

function LangfuseCard({
  component,
  busy,
  onStart,
  onStop,
  onCopy,
}: {
  component: ManagedComponent;
  busy: boolean;
  onStart: () => void;
  onStop: () => void;
  onCopy: (command: string) => void;
}) {
  const canStart = component.core_ready && !["running", "starting"].includes(component.status);
  const canStop = ["running", "starting", "degraded"].includes(component.status);

  return (
    <Card
      title="Langfuse Observability"
      description="用于 Clinic 评估、LLM Trace、Prompt 审计和诊断回放。它不参与 Reporter 上传、资产入库和交接主链路。"
      action={<StatusBadge status={component.status} />}
    >
      <div className="grid gap-0 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="space-y-6 border-b border-slate-200 p-6 lg:border-b-0 lg:border-r">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
            <div className="min-w-0">
              <div className="text-sm text-slate-500">{component.message}</div>
              <div className="mt-2 text-xs text-slate-400">最后检查：{formatDate(component.last_checked_at)}</div>
            </div>
            <div className="flex shrink-0 flex-wrap gap-2">
              <Button
                variant="primary"
                onClick={onStart}
                disabled={!canStart || busy}
                icon={<PlayCircle className="h-4 w-4" />}
              >
                安装并启动
              </Button>
              <Button
                variant="secondary"
                onClick={onStop}
                disabled={!canStop || busy}
                icon={<Square className="h-4 w-4" />}
              >
                停止
              </Button>
              {component.url ? (
                <a
                  href={component.url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center justify-center gap-2 rounded-md border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 shadow-sm transition-colors hover:bg-slate-50"
                >
                  <ExternalLink className="h-4 w-4" />
                  打开控制台
                </a>
              ) : null}
            </div>
          </div>

          <div>
            <div className="table-label mb-3">核心依赖</div>
            <div className="grid gap-3 sm:grid-cols-3">
              {component.dependencies.map((dependency) => (
                <DependencyPill key={dependency.name} dependency={dependency} />
              ))}
            </div>
          </div>

          <div>
            <div className="table-label mb-3">服务状态</div>
            <div className="overflow-hidden rounded-lg border border-slate-200">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="table-label border-b border-slate-200 bg-slate-50">
                    <th className="px-4 py-3 font-semibold">服务</th>
                    <th className="px-4 py-3 font-semibold">状态</th>
                    <th className="px-4 py-3 font-semibold">健康</th>
                    <th className="px-4 py-3 font-semibold">容器</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-200">
                  {component.services.map((service) => (
                    <ServiceRow key={service.name} service={service} />
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <div className="space-y-5 p-6">
          <div>
            <div className="text-sm font-semibold text-slate-900">解耦原则</div>
            <div className="mt-2 space-y-2 text-sm leading-6 text-slate-500">
              <p>默认只启动 DuckDock 核心服务。</p>
              <p>Langfuse 通过 Compose profile 独立启动和停止。</p>
              <p>停止 Langfuse 不影响 Reporter 上传、资产解析和交接流程。</p>
            </div>
          </div>

          <div>
            <div className="text-sm font-semibold text-slate-900">容器职责</div>
            <div className="mt-3 space-y-3">
              {["mysql", "redis", "minio", "postgres", "clickhouse", "langfuse-db-init", "langfuse-minio-init", "langfuse-worker", "langfuse-web"].map(
                (name) => (
                  <div key={name} className="rounded-lg border border-slate-200 bg-white px-3 py-2">
                    <MonoPill>{name}</MonoPill>
                    <div className="mt-1.5 text-xs leading-5 text-slate-500">{serviceDescriptions[name]}</div>
                  </div>
                )
              )}
            </div>
          </div>

          <CommandBlock title="启动命令" command={component.commands.start} onCopy={onCopy} />
          <CommandBlock title="停止命令" command={component.commands.stop} onCopy={onCopy} />
        </div>
      </div>
    </Card>
  );
}

function SummaryTile({
  label,
  value,
  detail,
  icon: Icon,
  tone,
  mono = false,
}: {
  label: string;
  value: string;
  detail: string;
  icon: typeof ServerCog;
  tone: "emerald" | "amber" | "indigo";
  mono?: boolean;
}) {
  const tileTone = tone === "indigo" ? "indigo" : "neutral";
  const iconColor =
    tone === "emerald" ? "text-emerald-600" : tone === "amber" ? "text-amber-600" : "text-indigo-600";

  return (
    <div className="metric-card">
      <div className="flex h-full items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="text-sm font-medium text-slate-500">{label}</div>
          {mono ? (
            <div className="mt-4 truncate font-mono text-2xl font-bold tracking-tight text-slate-900">{value}</div>
          ) : (
            <div className="mt-4 truncate text-2xl font-bold tracking-tight text-slate-900">{value}</div>
          )}
          <div className="mt-3 text-sm leading-5 text-slate-500">{detail}</div>
        </div>
        <IconTile size="lg" tone={tileTone}>
          <Icon className={`h-5 w-5 ${iconColor}`} />
        </IconTile>
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: ManagedComponentStatus }) {
  const tone: Tone =
    status === "running"
      ? "emerald"
      : status === "starting"
        ? "indigo"
        : status === "degraded" || status === "error" || status === "unavailable"
          ? "rose"
          : "neutral";

  return (
    <Badge tone={tone} icon={<CircleDot className="h-3.5 w-3.5" />}>
      {statusLabels[status]}
    </Badge>
  );
}

function DependencyPill({ dependency }: { dependency: ManagedComponentDependency }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white px-4 py-3">
      <div className="min-w-0">
        <MonoPill>{dependency.name}</MonoPill>
        <div className="mt-1.5 text-xs text-slate-500">{dependency.health ?? dependency.state}</div>
      </div>
      {dependency.ready ? (
        <CheckCircle2 className="h-5 w-5 shrink-0 text-emerald-600" />
      ) : (
        <AlertTriangle className="h-5 w-5 shrink-0 text-amber-600" />
      )}
    </div>
  );
}

function ServiceRow({ service }: { service: ManagedComponentService }) {
  const running = service.state === "running";
  return (
    <tr className="bg-white transition-colors hover:bg-slate-50/70">
      <td className="px-4 py-3">
        <MonoPill>{service.name}</MonoPill>
        <div className="mt-1.5 max-w-[360px] text-xs leading-5 text-slate-500">{serviceDescriptions[service.name] ?? "-"}</div>
      </td>
      <td className="px-4 py-3">
        <span className={`inline-flex items-center gap-2 ${running ? "text-emerald-700" : "text-slate-500"}`}>
          <CircleDot className="h-3.5 w-3.5" />
          {service.state}
        </span>
      </td>
      <td className="px-4 py-3 text-slate-500">{service.health ?? "-"}</td>
      <td className="max-w-[260px] truncate px-4 py-3 text-slate-500">
        {service.container_name ? <span className="font-mono text-xs">{service.container_name}</span> : "-"}
      </td>
    </tr>
  );
}

function CommandBlock({
  title,
  command,
  onCopy,
}: {
  title: string;
  command?: string;
  onCopy: (command: string) => void;
}) {
  if (!command) return null;
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-950 p-4 text-slate-100">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">{title}</div>
        <button type="button" onClick={() => onCopy(command)} className="rounded-md p-1.5 text-slate-300 hover:bg-white/10">
          <Copy className="h-4 w-4" />
        </button>
      </div>
      <code className="block whitespace-pre-wrap break-all font-mono text-xs leading-5">{command}</code>
    </div>
  );
}

function formatDate(value: string | null) {
  if (!value) return "-";
  return new Date(value).toLocaleString();
}

function getErrorDetail(error: unknown) {
  if (typeof error === "object" && error !== null && "response" in error) {
    const response = (error as { response?: { data?: { detail?: string } } }).response;
    return response?.data?.detail;
  }
  return null;
}
