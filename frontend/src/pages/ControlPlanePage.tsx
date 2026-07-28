import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import {
  Database,
  Eye,
  Handshake,
  Info,
  PlayCircle,
  Radar,
  RefreshCw,
  Route,
  ShieldAlert,
  Trash2,
  Wand2,
  X,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import {
  controlPlaneApi,
  namespacesApi,
  type AIAsset,
  type AiAssist,
  type AssetStatus,
  type AssetType,
  type CollectionJob,
  type Criticality,
  type HandoverCase,
  type HandoverCaseType,
  type HandoverStatus,
  type RuntimeDeployType,
  type RuntimeInstance,
  type RuntimeProvider,
} from "../api/client";
import { AiAssistBadge } from "../components/AiAssistBadge";
import { Badge, Button, Card, Input, MetricCard, MonoPill, PageHeader, type Tone } from "../components/ui";
import { useAuthStore } from "../store/auth";
import { parseAiAssistHeader } from "../utils/aiAssist";

const providerTone: Record<RuntimeProvider, Tone> = {
  openclaw: "indigo",
  arkclaw: "amber",
  workbuddy: "violet",
  jvs: "emerald",
  custom: "neutral",
};

const providerOptions: { label: string; value: RuntimeProvider }[] = [
  { label: "OpenClaw / 私有项目", value: "openclaw" },
  { label: "火山 ArkClaw", value: "arkclaw" },
  { label: "腾讯 WorkBuddy", value: "workbuddy" },
  { label: "阿里云 JVS", value: "jvs" },
  { label: "自定义运行时", value: "custom" },
];

const deployOptions: { label: string; value: RuntimeDeployType }[] = [
  { label: "私有化", value: "private" },
  { label: "SaaS", value: "saas" },
  { label: "本地部署", value: "on_prem" },
  { label: "离线环境", value: "offline" },
];

const assetTypeOptions: { label: string; value: AssetType }[] = [
  { label: "Skill", value: "skill" },
  { label: "Agent", value: "agent" },
  { label: "Prompt", value: "prompt" },
  { label: "Workflow", value: "workflow" },
  { label: "MCP", value: "mcp" },
  { label: "Tool", value: "tool" },
  { label: "Knowledge Base", value: "knowledge_base" },
  { label: "Scheduled Task", value: "scheduled_task" },
  { label: "Credential Ref", value: "credential_ref" },
  { label: "Workspace", value: "workspace" },
  { label: "Other", value: "other" },
];

const statusOptions: { label: string; value: AssetStatus }[] = [
  { label: "活跃", value: "active" },
  { label: "风险", value: "risky" },
  { label: "孤儿资产", value: "orphaned" },
  { label: "已归档", value: "archived" },
  { label: "已转移", value: "transferred" },
];

const criticalityOptions: { label: string; value: Criticality }[] = [
  { label: "低", value: "low" },
  { label: "中", value: "medium" },
  { label: "高", value: "high" },
  { label: "关键", value: "critical" },
];

const handoverTypeOptions: { label: string; value: HandoverCaseType }[] = [
  { label: "员工离职交接", value: "employee_offboarding" },
  { label: "项目交接", value: "project_handover" },
  { label: "供应商退出", value: "vendor_exit" },
  { label: "事故接管", value: "incident_takeover" },
];

const SELECT_CLASS =
  "w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm outline-none transition-colors focus:border-indigo-300 focus:ring-4 focus:ring-indigo-100";

const runtimeFormDefaults = {
  namespace_id: "" as number | "",
  provider: "openclaw" as RuntimeProvider,
  name: "",
  base_url: "",
  deploy_type: "private" as RuntimeDeployType,
  credential_ref: "",
};

const assetFormDefaults = {
  namespace_id: "" as number | "",
  asset_type: "skill" as AssetType,
  name: "",
  description: "",
  source_runtime_id: "" as number | "",
  source_provider: "custom" as RuntimeProvider,
  external_id: "",
  status: "active" as AssetStatus,
  criticality: "medium" as Criticality,
  bind_to_me: true,
};

const handoverFormDefaults = {
  namespace_id: "" as number | "",
  case_type: "employee_offboarding" as HandoverCaseType,
  title: "",
  subject_user_id: "",
  receiver_user_id: "",
};

function runtimeOverviewUserId(runtime: RuntimeInstance, fallbackUserId?: number): number | null {
  const reporter = runtime.metadata_json?.reporter;
  if (!reporter || typeof reporter !== "object") return fallbackUserId ?? null;
  const enrollment = (reporter as Record<string, unknown>).enrollment;
  if (!enrollment || typeof enrollment !== "object") return fallbackUserId ?? null;
  const rawUserId = (enrollment as Record<string, unknown>).user_id;
  return typeof rawUserId === "number" ? rawUserId : fallbackUserId ?? null;
}

export default function ControlPlanePage() {
  const { message, modal } = AntApp.useApp();
  const navigate = useNavigate();
  const currentUser = useAuthStore((state) => state.user);
  const [runtimeOpen, setRuntimeOpen] = useState(false);
  const [assetOpen, setAssetOpen] = useState(false);
  const [handoverOpen, setHandoverOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [runtimeForm, setRuntimeForm] = useState(runtimeFormDefaults);
  const [assetForm, setAssetForm] = useState(assetFormDefaults);
  const [handoverForm, setHandoverForm] = useState(handoverFormDefaults);
  const [handoverAiAssist, setHandoverAiAssist] = useState<Record<number, AiAssist>>({});

  const runtimes = useQuery({
    queryKey: ["control-plane", "runtimes"],
    queryFn: async () => (await controlPlaneApi.listRuntimes()).data,
  });
  const namespaces = useQuery({
    queryKey: ["namespaces"],
    queryFn: async () => (await namespacesApi.list()).data,
  });
  const assets = useQuery({
    queryKey: ["control-plane", "assets"],
    queryFn: async () => (await controlPlaneApi.listAssets()).data,
  });
  const handovers = useQuery({
    queryKey: ["control-plane", "handovers"],
    queryFn: async () => (await controlPlaneApi.listHandovers()).data,
  });
  const collectionJobs = useQuery({
    queryKey: ["control-plane", "collection-jobs"],
    queryFn: async () => (await controlPlaneApi.listCollectionJobs()).data,
  });

  const runtimeRows = runtimes.data ?? [];
  const namespaceRows = namespaces.data ?? [];
  const assetRows = assets.data ?? [];
  const handoverRows = handovers.data ?? [];
  const jobRows = collectionJobs.data ?? [];

  const metrics = useMemo(
    () => ({
      runtimes: runtimeRows.length,
      assets: assetRows.length,
      riskyAssets: assetRows.filter((item) => ["risky", "orphaned"].includes(item.status)).length,
      pendingHandovers: handoverRows.filter((item) =>
        ["collecting", "analyzing", "pending_approval", "approved", "executing", "verifying"].includes(item.status)
      ).length,
    }),
    [assetRows, handoverRows, runtimeRows]
  );

  async function refreshAll() {
    await Promise.all([
      namespaces.refetch(),
      runtimes.refetch(),
      assets.refetch(),
      handovers.refetch(),
      collectionJobs.refetch(),
    ]);
  }

  async function runAction(action: () => Promise<unknown>, success: string) {
    try {
      await action();
      await refreshAll();
      message.success(success);
    } catch (err: any) {
      message.error(err.response?.data?.detail ?? "操作失败");
    }
  }

  async function analyzeHandoverFromList(caseId: number) {
    await runAction(async () => {
      const response = await controlPlaneApi.analyzeHandover(caseId);
      const aiAssist = parseAiAssistHeader(response.headers["x-ai-assist"]);
      if (aiAssist) {
        setHandoverAiAssist((current) => ({ ...current, [caseId]: aiAssist }));
      }
    }, "交接分析已生成");
  }

  async function submitRuntime() {
    if (!runtimeForm.namespace_id || !runtimeForm.name.trim()) {
      message.error("请选择 Namespace 并输入运行时名称");
      return;
    }
    setSubmitting(true);
    try {
      await controlPlaneApi.createRuntime({
        namespace_id: runtimeForm.namespace_id,
        provider: runtimeForm.provider,
        name: runtimeForm.name,
        base_url: runtimeForm.base_url.trim() || null,
        deploy_type: runtimeForm.deploy_type,
        credential_ref: runtimeForm.credential_ref.trim() || null,
      });
      setRuntimeForm(runtimeFormDefaults);
      setRuntimeOpen(false);
      await refreshAll();
      message.success("运行时已创建");
    } catch (err: any) {
      message.error(err.response?.data?.detail ?? "创建运行时失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function submitAsset() {
    if (!assetForm.namespace_id || !assetForm.name.trim()) {
      message.error("请选择 Namespace 并输入资产名称");
      return;
    }
    setSubmitting(true);
    try {
      const runtime = runtimeRows.find((item) => item.id === assetForm.source_runtime_id);
      const asset = await controlPlaneApi.createAsset({
        namespace_id: assetForm.namespace_id,
        asset_type: assetForm.asset_type,
        name: assetForm.name,
        description: assetForm.description.trim() || null,
        source_provider: runtime?.provider ?? assetForm.source_provider,
        source_runtime_id: assetForm.source_runtime_id === "" ? null : assetForm.source_runtime_id,
        external_id: assetForm.external_id.trim() || null,
        status: assetForm.status,
        criticality: assetForm.criticality,
      });
      if (currentUser?.id && assetForm.bind_to_me) {
        await controlPlaneApi.createAssetOwnership(asset.data.id, {
          owner_type: "creator",
          user_id: currentUser.id,
          namespace_id: assetForm.namespace_id,
          confidence: 0.9,
          is_primary: true,
        });
      }
      setAssetForm(assetFormDefaults);
      setAssetOpen(false);
      await refreshAll();
      message.success("资产已登记");
    } catch (err: any) {
      message.error(err.response?.data?.detail ?? "登记资产失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function submitHandover() {
    if (!handoverForm.namespace_id || !handoverForm.title.trim()) {
      message.error("请选择 Namespace 并输入交接标题");
      return;
    }
    setSubmitting(true);
    try {
      await controlPlaneApi.createHandover({
        namespace_id: handoverForm.namespace_id,
        case_type: handoverForm.case_type,
        title: handoverForm.title,
        subject_user_id: handoverForm.subject_user_id ? Number(handoverForm.subject_user_id) : currentUser?.id ?? null,
        receiver_user_id: handoverForm.receiver_user_id ? Number(handoverForm.receiver_user_id) : currentUser?.id ?? null,
        runtime_ids: runtimeRows
          .filter((item) => item.namespace_id === handoverForm.namespace_id)
          .map((item) => item.id),
        collection_scope: {
          users: handoverForm.subject_user_id
            ? [Number(handoverForm.subject_user_id)]
            : currentUser?.id
              ? [currentUser.id]
              : null,
          include_work_traces: true,
          include_artifacts: true,
          lookback_days: 180,
        },
      });
      setHandoverForm(handoverFormDefaults);
      setHandoverOpen(false);
      await refreshAll();
      message.success("交接单已创建，可以点击“分析”生成待交接项");
    } catch (err: any) {
      message.error(err.response?.data?.detail ?? "创建交接单失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function seedDemoData() {
    if (!currentUser?.id) {
      message.warning("请先重新登录，确保当前用户信息已加载");
      return;
    }
    const namespace = namespaceRows[0];
    if (!namespace) {
      message.warning("请先创建一个可写 Namespace，再生成演示数据");
      return;
    }
    setSubmitting(true);
    try {
      const suffix = new Date().toLocaleTimeString("zh-CN", { hour12: false });
      const runtime = await controlPlaneApi.createRuntime({
        namespace_id: namespace.id,
        provider: "openclaw",
        name: `演示 OpenClaw 私有运行时 ${suffix}`,
        base_url: "https://openclaw.example.internal",
        deploy_type: "private",
        credential_ref: "vault://duckdock/demo-openclaw",
        metadata_json: { demo: true },
      });
      const asset = await controlPlaneApi.createAsset({
        namespace_id: namespace.id,
        asset_type: "skill",
        name: `客户工单总结 Skill ${suffix}`,
        description: "演示资产：员工创建并被运行时调用的私有化 Skill。",
        source_provider: runtime.data.provider,
        source_runtime_id: runtime.data.id,
        external_id: `demo-skill-${Date.now()}`,
        status: "risky",
        criticality: "high",
        metadata_json: { demo: true, used_by: "after-sales-agent" },
      });
      await controlPlaneApi.createAssetOwnership(asset.data.id, {
        owner_type: "creator",
        user_id: currentUser.id,
        namespace_id: namespace.id,
        confidence: 0.95,
        is_primary: true,
      });
      const handover = await controlPlaneApi.createHandover({
        namespace_id: namespace.id,
        case_type: "employee_offboarding",
        title: `演示：${currentUser.username} 的 AI Agent 资产交接 ${suffix}`,
        subject_user_id: currentUser.id,
        receiver_user_id: currentUser.id,
        runtime_ids: [runtime.data.id],
        collection_scope: {
          users: [currentUser.id],
          include_work_traces: true,
          include_artifacts: true,
          lookback_days: 180,
        },
        metadata_json: { demo: true, generated_by: "control-plane-demo" },
      });
      await controlPlaneApi.analyzeHandover(handover.data.id);
      await refreshAll();
      message.success("演示数据已生成，下面三个表会串起来显示一条业务闭环");
    } catch (err: any) {
      message.error(err.response?.data?.detail ?? "生成演示数据失败");
    } finally {
      setSubmitting(false);
    }
  }

  function cleanupDemoData() {
    modal.confirm({
      title: "清理演示数据",
      content: "只会删除明确带 demo 标记的运行时、资产、采集任务、原始记录和交接单；不会按名称模糊删除业务数据。",
      okText: "确认清理",
      okButtonProps: { danger: true },
      cancelText: "取消",
      async onOk() {
        setSubmitting(true);
        try {
          const { data } = await controlPlaneApi.cleanupDemoData();
          await refreshAll();
          const deletedTotal = Object.values(data.deleted).reduce((sum, value) => sum + value, 0);
          message.success(`已清理 ${deletedTotal} 条演示相关记录`);
        } catch (err: any) {
          message.error(err.response?.data?.detail ?? "清理演示数据失败");
        } finally {
          setSubmitting(false);
        }
      },
    });
  }

  const runtimeColumns: Column<RuntimeInstance>[] = [
    { key: "name", header: "名称", render: (row) => <span className="font-medium text-slate-900">{row.name}</span> },
    { key: "provider", header: "平台", render: (row) => <Badge tone={providerTone[row.provider]}>{row.provider}</Badge> },
    { key: "deploy", header: "部署", render: (row) => <MonoPill>{row.deploy_type}</MonoPill> },
    {
      key: "status",
      header: "状态",
      render: (row) => <Badge tone={row.status === "active" ? "emerald" : "amber"}>{row.status}</Badge>,
    },
    { key: "sync", header: "最近同步", render: (row) => formatDateTime(row.last_sync_at) },
    {
      key: "action",
      header: "操作",
      align: "right",
      render: (row) => {
        const overviewUserId = runtimeOverviewUserId(row, currentUser?.id);
        return (
          <div className="flex justify-end gap-2">
            <Button
              variant="secondary"
              size="sm"
              icon={<Eye className="h-3.5 w-3.5" />}
              onClick={() => {
                if (!overviewUserId) {
                  message.warning("缺少员工归属，无法打开概览");
                  return;
                }
                navigate(`/control-plane/runtimes/${row.id}/overview?user_id=${overviewUserId}`);
              }}
            >
              详情
            </Button>
            <Button
              variant="secondary"
              size="sm"
              icon={<PlayCircle className="h-3.5 w-3.5" />}
              onClick={() => runAction(() => controlPlaneApi.testRuntime(row.id), "上报链路自检完成")}
            >
              上报自检
            </Button>
          </div>
        );
      },
    },
  ];

  const assetColumns: Column<AIAsset>[] = [
    { key: "name", header: "资产", render: (row) => <span className="font-medium text-slate-900">{row.name}</span> },
    { key: "type", header: "类型", render: (row) => <MonoPill>{row.asset_type}</MonoPill> },
    {
      key: "source",
      header: "来源",
      render: (row) => <Badge tone={providerTone[row.source_provider]}>{row.source_provider}</Badge>,
    },
    {
      key: "status",
      header: "状态",
      render: (row) => (
        <Badge tone={["risky", "orphaned"].includes(row.status) ? "rose" : "emerald"}>{row.status}</Badge>
      ),
    },
    {
      key: "criticality",
      header: "关键级别",
      render: (row) => <Badge tone={criticalityTone(row.criticality)}>{row.criticality}</Badge>,
    },
    { key: "seen", header: "最近发现", render: (row) => formatDateTime(row.last_seen_at) },
  ];

  const handoverColumns: Column<HandoverCase>[] = [
    {
      key: "title",
      header: "交接单",
      render: (row) => (
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-slate-900">{row.title}</span>
          {handoverAiAssist[row.id] ? <AiAssistBadge aiAssist={handoverAiAssist[row.id]} /> : null}
        </div>
      ),
    },
    { key: "type", header: "类型", render: (row) => <MonoPill>{row.case_type}</MonoPill> },
    { key: "status", header: "状态", render: (row) => <Badge tone={handoverStatusTone(row.status)}>{row.status}</Badge> },
    { key: "risk", header: "风险", render: (row) => row.risk_level ?? "—" },
    { key: "created", header: "创建时间", render: (row) => formatDateTime(row.created_at) },
    {
      key: "action",
      header: "操作",
      align: "right",
      render: (row) => (
        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={() => navigate(`/handovers/${row.id}`)}>
            打开
          </Button>
          <Button variant="secondary" size="sm" onClick={() => runAction(() => controlPlaneApi.collectHandover(row.id), "采集任务已创建")}>
            采集
          </Button>
          <Button size="sm" onClick={() => void analyzeHandoverFromList(row.id)}>
            分析
          </Button>
        </div>
      ),
    },
  ];

  const jobColumns: Column<CollectionJob>[] = [
    { key: "id", header: "任务 ID", render: (row) => <MonoPill>#{row.id}</MonoPill> },
    { key: "runtime", header: "运行时", render: (row) => (row.runtime_id ? `runtime #${row.runtime_id}` : "交接范围") },
    { key: "trigger", header: "触发方式", render: (row) => <MonoPill>{row.trigger_type}</MonoPill> },
    { key: "status", header: "状态", render: (row) => <Badge tone={jobStatusTone(row.status)}>{row.status}</Badge> },
    { key: "created", header: "创建时间", render: (row) => formatDateTime(row.created_at) },
    {
      key: "result",
      header: "结果",
      render: (row) => {
        const counts = row.summary_json?.counts as Record<string, number> | undefined;
        return counts
          ? `assets ${counts.assets ?? 0}, traces ${counts.work_traces ?? 0}, raw ${counts.raw_records ?? 0}`
          : row.error_message ?? "—";
      },
    },
  ];

  return (
    <div className="app-page page-stack">
      <PageHeader
        eyebrow="Control Plane v2"
        title="企业 AI Agent 资产控制平面"
        description="统一管理运行时、AI 资产、工作历程、证据和离职/项目交接流程。"
        actions={
          <>
            <Button variant="secondary" disabled={submitting} onClick={seedDemoData} icon={<Wand2 className="h-4 w-4" />}>
              生成演示数据
            </Button>
            <Button variant="secondary" danger disabled={submitting} onClick={cleanupDemoData} icon={<Trash2 className="h-4 w-4" />}>
              清理演示数据
            </Button>
            <Button variant="secondary" onClick={() => setRuntimeOpen(true)} icon={<Database className="h-4 w-4" />}>
              新建运行时
            </Button>
            <Button variant="secondary" onClick={() => setAssetOpen(true)} icon={<Route className="h-4 w-4" />}>
              登记资产
            </Button>
            <Button onClick={() => setHandoverOpen(true)} icon={<Handshake className="h-4 w-4" />}>
              创建交接单
            </Button>
            <Button variant="secondary" onClick={() => runAction(refreshAll, "控制平面数据已刷新")} icon={<RefreshCw className="h-4 w-4" />}>
              刷新
            </Button>
          </>
        }
      />

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard label="运行时" value={metrics.runtimes} icon={Radar} />
        <MetricCard label="AI 资产" value={metrics.assets} icon={Route} />
        <MetricCard label="风险资产" value={metrics.riskyAssets} icon={ShieldAlert} tone="rose" />
        <MetricCard label="进行中交接" value={metrics.pendingHandovers} icon={RefreshCw} />
      </div>

      <div className="flex gap-3 rounded-lg border border-indigo-200 bg-indigo-50 px-4 py-3 text-sm leading-6 text-indigo-900">
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-indigo-600" />
        <div>
          <span className="font-semibold">推荐试用路径</span> — 先让 Reporter 自助 enroll 并上传 pack，Analysis Worker 会把标准结果物化成
          Skill、Agent、Prompt、Workflow、工作历程和证据线索；资产绑定到创建人后，创建交接单并点击分析，就能看到需要接管的资产。
          传统厂商 Pull Adapter 不是当前生产主线。
        </div>
      </div>

      <DataTable title="运行时实例" columns={runtimeColumns} rows={runtimeRows} loading={runtimes.isLoading} emptyText="还没有登记运行时。点击「新建运行时」接入 OpenClaw / WorkBuddy 实例。" />
      <DataTable title="AI 资产" columns={assetColumns} rows={assetRows} loading={assets.isLoading} emptyText="还没有登记 AI 资产。在运行时下登记 Skill / Agent / Prompt / Workflow。" />
      <DataTable title="交接单" columns={handoverColumns} rows={handoverRows} loading={handovers.isLoading} emptyText="还没有交接单。点击「创建交接单」并分析以生成待交接项。" />
      <DataTable title="采集任务" columns={jobColumns} rows={jobRows} loading={collectionJobs.isLoading} emptyText="还没有采集任务。对运行时自检或对交接单点击采集会创建任务。" />

      <Overlay open={runtimeOpen} title="新建运行时" onClose={() => setRuntimeOpen(false)}>
        <Field label="Namespace">
          <select
            className={SELECT_CLASS}
            value={runtimeForm.namespace_id}
            onChange={(e) =>
              setRuntimeForm({ ...runtimeForm, namespace_id: e.target.value === "" ? "" : Number(e.target.value) })
            }
          >
            <option value="">选择可写 Namespace</option>
            {namespaceRows.map((item) => (
              <option key={item.id} value={item.id}>{item.name}</option>
            ))}
          </select>
        </Field>
        <Field label="平台">
          <select
            className={SELECT_CLASS}
            value={runtimeForm.provider}
            onChange={(e) => setRuntimeForm({ ...runtimeForm, provider: e.target.value as RuntimeProvider })}
          >
            {providerOptions.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </Field>
        <Field label="名称">
          <Input
            value={runtimeForm.name}
            onChange={(e) => setRuntimeForm({ ...runtimeForm, name: e.target.value })}
            placeholder="例如：OpenClaw 私有化生产环境"
          />
        </Field>
        <Field label="访问地址">
          <Input
            value={runtimeForm.base_url}
            onChange={(e) => setRuntimeForm({ ...runtimeForm, base_url: e.target.value })}
            placeholder="https://openclaw.example.com"
          />
        </Field>
        <Field label="部署方式">
          <select
            className={SELECT_CLASS}
            value={runtimeForm.deploy_type}
            onChange={(e) => setRuntimeForm({ ...runtimeForm, deploy_type: e.target.value as RuntimeDeployType })}
          >
            {deployOptions.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </Field>
        <Field label="凭据引用">
          <Input
            value={runtimeForm.credential_ref}
            onChange={(e) => setRuntimeForm({ ...runtimeForm, credential_ref: e.target.value })}
            placeholder="vault://duckdock/openclaw/prod"
          />
        </Field>
        <ModalFooter onCancel={() => setRuntimeOpen(false)} onSubmit={submitRuntime} submitting={submitting} submitLabel="创建" />
      </Overlay>

      <Overlay open={assetOpen} title="登记 AI 资产" onClose={() => setAssetOpen(false)}>
        <Field label="Namespace">
          <select
            className={SELECT_CLASS}
            value={assetForm.namespace_id}
            onChange={(e) =>
              setAssetForm({
                ...assetForm,
                namespace_id: e.target.value === "" ? "" : Number(e.target.value),
                source_runtime_id: "",
              })
            }
          >
            <option value="">选择可写 Namespace</option>
            {namespaceRows.map((item) => (
              <option key={item.id} value={item.id}>{item.name}</option>
            ))}
          </select>
        </Field>
        <Field label="资产类型">
          <select
            className={SELECT_CLASS}
            value={assetForm.asset_type}
            onChange={(e) => setAssetForm({ ...assetForm, asset_type: e.target.value as AssetType })}
          >
            {assetTypeOptions.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </Field>
        <Field label="资产名称">
          <Input
            value={assetForm.name}
            onChange={(e) => setAssetForm({ ...assetForm, name: e.target.value })}
            placeholder="例如：客户工单总结 Skill"
          />
        </Field>
        <Field label="说明">
          <textarea
            rows={3}
            className={SELECT_CLASS}
            value={assetForm.description}
            onChange={(e) => setAssetForm({ ...assetForm, description: e.target.value })}
            placeholder="这个资产解决什么问题、由谁维护、在哪些 Agent 中使用"
          />
        </Field>
        <Field label="来源运行时">
          <select
            className={SELECT_CLASS}
            value={assetForm.source_runtime_id === "" ? "" : String(assetForm.source_runtime_id)}
            onChange={(e) =>
              setAssetForm({ ...assetForm, source_runtime_id: e.target.value === "" ? "" : Number(e.target.value) })
            }
          >
            <option value="">选择已接入的运行时</option>
            {runtimeRows.filter((item) => item.namespace_id === assetForm.namespace_id).map((item) => (
              <option key={item.id} value={item.id}>
                {item.name} ({item.provider})
              </option>
            ))}
          </select>
        </Field>
        <Field label="来源平台">
          <select
            className={SELECT_CLASS}
            value={assetForm.source_provider}
            onChange={(e) => setAssetForm({ ...assetForm, source_provider: e.target.value as RuntimeProvider })}
          >
            {providerOptions.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </Field>
        <Field label="外部 ID">
          <Input
            value={assetForm.external_id}
            onChange={(e) => setAssetForm({ ...assetForm, external_id: e.target.value })}
            placeholder="厂商侧 asset id / skill id / workflow id"
          />
        </Field>
        <Field label="状态">
          <select
            className={SELECT_CLASS}
            value={assetForm.status}
            onChange={(e) => setAssetForm({ ...assetForm, status: e.target.value as AssetStatus })}
          >
            {statusOptions.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </Field>
        <Field label="关键级别">
          <select
            className={SELECT_CLASS}
            value={assetForm.criticality}
            onChange={(e) => setAssetForm({ ...assetForm, criticality: e.target.value as Criticality })}
          >
            {criticalityOptions.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </Field>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-300"
            checked={assetForm.bind_to_me}
            onChange={(e) => setAssetForm({ ...assetForm, bind_to_me: e.target.checked })}
          />
          绑定为当前登录用户创建的资产
        </label>
        <ModalFooter onCancel={() => setAssetOpen(false)} onSubmit={submitAsset} submitting={submitting} submitLabel="登记" />
      </Overlay>

      <Overlay open={handoverOpen} title="创建交接单" onClose={() => setHandoverOpen(false)}>
        <Field label="Namespace">
          <select
            className={SELECT_CLASS}
            value={handoverForm.namespace_id}
            onChange={(e) =>
              setHandoverForm({
                ...handoverForm,
                namespace_id: e.target.value === "" ? "" : Number(e.target.value),
              })
            }
          >
            <option value="">选择可写 Namespace</option>
            {namespaceRows.map((item) => (
              <option key={item.id} value={item.id}>{item.name}</option>
            ))}
          </select>
        </Field>
        <Field label="交接类型">
          <select
            className={SELECT_CLASS}
            value={handoverForm.case_type}
            onChange={(e) => setHandoverForm({ ...handoverForm, case_type: e.target.value as HandoverCaseType })}
          >
            {handoverTypeOptions.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </Field>
        <Field label="标题">
          <Input
            value={handoverForm.title}
            onChange={(e) => setHandoverForm({ ...handoverForm, title: e.target.value })}
            placeholder="例如：张三离职 AI Agent 资产交接"
          />
        </Field>
        <Field label="交接对象用户 ID">
          <Input
            value={handoverForm.subject_user_id}
            onChange={(e) => setHandoverForm({ ...handoverForm, subject_user_id: e.target.value })}
            placeholder="默认当前登录用户"
          />
        </Field>
        <Field label="接收人用户 ID">
          <Input
            value={handoverForm.receiver_user_id}
            onChange={(e) => setHandoverForm({ ...handoverForm, receiver_user_id: e.target.value })}
            placeholder="默认当前登录用户"
          />
        </Field>
        <ModalFooter onCancel={() => setHandoverOpen(false)} onSubmit={submitHandover} submitting={submitting} submitLabel="创建" />
      </Overlay>
    </div>
  );
}

interface Column<T> {
  key: string;
  header: string;
  align?: "left" | "right";
  render: (row: T) => ReactNode;
}

function DataTable<T extends { id: number }>({
  title,
  columns,
  rows,
  loading,
  emptyText,
}: {
  title: string;
  columns: Column<T>[];
  rows: T[];
  loading: boolean;
  emptyText: string;
}) {
  return (
    <Card title={title}>
      {loading ? (
        <div className="empty-state">正在加载…</div>
      ) : rows.length === 0 ? (
        <div className="empty-state">{emptyText}</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200">
            <thead className="bg-slate-50">
              <tr>
                {columns.map((c) => (
                  <th key={c.key} className={`table-label px-5 py-3 ${c.align === "right" ? "text-right" : "text-left"}`}>
                    {c.header}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200">
              {rows.map((row) => (
                <tr key={row.id} className="transition hover:bg-slate-50/70">
                  {columns.map((c) => (
                    <td
                      key={c.key}
                      className={`px-5 py-4 text-sm text-slate-700 ${c.align === "right" ? "text-right" : "text-left"}`}
                    >
                      {c.render(row)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function Overlay({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/45 p-4 py-10"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md overflow-hidden rounded-lg border border-slate-200 bg-white shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
          <div className="text-lg font-semibold tracking-tight text-slate-900">{title}</div>
          <button onClick={onClose} className="text-slate-400 transition-colors hover:text-slate-600" aria-label="关闭">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="space-y-4 px-5 py-5">{children}</div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium uppercase tracking-[0.14em] text-slate-500">{label}</span>
      {children}
    </label>
  );
}

function ModalFooter({
  onCancel,
  onSubmit,
  submitting,
  submitLabel,
}: {
  onCancel: () => void;
  onSubmit: () => void;
  submitting: boolean;
  submitLabel: string;
}) {
  return (
    <div className="flex justify-end gap-2 pt-1">
      <Button variant="secondary" onClick={onCancel}>
        取消
      </Button>
      <Button onClick={onSubmit} disabled={submitting}>
        {submitLabel}
      </Button>
    </div>
  );
}

function formatDateTime(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function criticalityTone(criticality: Criticality): Tone {
  if (criticality === "critical") return "rose";
  if (criticality === "high") return "orange";
  return "indigo";
}

function handoverStatusTone(status: HandoverStatus): Tone {
  if (status === "completed") return "emerald";
  if (status === "rejected" || status === "cancelled") return "rose";
  if (status === "draft") return "neutral";
  return "indigo";
}

function jobStatusTone(status: CollectionJob["status"]): Tone {
  if (status === "succeeded") return "emerald";
  if (status === "failed" || status === "partial_failed") return "rose";
  if (status === "cancelled") return "neutral";
  if (status === "running") return "indigo";
  return "amber";
}
