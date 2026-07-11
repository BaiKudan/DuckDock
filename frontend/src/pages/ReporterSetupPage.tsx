import { App as AntApp } from "antd";
import {
  CheckCircle2,
  Clipboard,
  Copy,
  FileCheck2,
  RefreshCw,
  Settings2,
  TerminalSquare,
  UploadCloud,
} from "lucide-react";
import { useMemo, useState } from "react";

import { Button, Card, IconTile, MonoPill } from "../components/ui";

type RuntimeKey = "workbuddy" | "openclaw" | "arkclaw";

interface RuntimeGuide {
  key: RuntimeKey;
  name: string;
  description: string;
  primaryMode: string;
  registryInstruction: string;
  installInstruction: string;
  scheduleInstruction: string;
  uploadCheckInstruction: string;
  cliTemplate?: string;
}

function trimTrailingSlash(value: string) {
  return value.replace(/\/+$/, "");
}

function resolveDuckDockApiBase(origin: string) {
  const configured =
    import.meta.env.VITE_PUBLIC_DUCKDOCK_API_BASE || import.meta.env.VITE_DUCKDOCK_API_BASE || "";
  if (configured) {
    return trimTrailingSlash(configured);
  }

  try {
    const url = new URL(origin);
    if (url.port === "5175") {
      url.port = "8990";
    } else if (url.port === "5174") {
      url.port = "8801";
    }
    return `${url.origin}/api/v1`;
  } catch {
    return `${trimTrailingSlash(origin)}/api/v1`;
  }
}

function runtimeGuides(apiBase: string): RuntimeGuide[] {
  const registryUrl = `${apiBase}/registry/skill.md`;
  const reporterSkill = "duckdock/duckdock-reporter";
  const commonScope =
    "只采集 DuckDock 需要的资产目录、Skills、Agents、Prompts、Workflow、记忆索引、会话摘要、任务产物索引和证据摘要；默认不要上传完整私密会话原文。";
  const commonSchedule =
    "请创建 DuckDock Reporter 定时任务：每周五 16:00 执行一次。首次运行时先登录 DuckDock 并调用 /reporters/enroll 自助登记 runtime endpoint，保存一次性返回的 dkr_report_* Reporter Credential；之后生成 duckdock-pack-v1.zip，使用 DuckDock 上传会话直传对象存储，并在上传后 finalize。";
  const commonCheck =
    "请立刻执行一次 DuckDock Reporter dry-run 上传检查：生成最小测试包，创建上传会话，上传到 DuckDock，finalize 后返回 report_id、pack_sha256、status 和错误信息。";

  return [
    {
      key: "workbuddy",
      name: "腾讯云 WorkBuddy",
      description: "适合通过对话让 WorkBuddy 安装探针 skill，并以工作区为边界做周期归档。",
      primaryMode: "复制到 WorkBuddy 对话框",
      registryInstruction: `请先把 DuckDock 私有 Skills Registry 配置为当前 WorkBuddy 的私有技能库地址：${registryUrl}。配置完成后同步/刷新技能库，再继续安装。`,
      installInstruction: `请从刚配置的 DuckDock 私有技能库安装 ${reporterSkill}。安装后按最小权限授权：${commonScope}`,
      scheduleInstruction: `${commonSchedule} 运行时类型请标记为 workbuddy，DUCKDOCK_API_BASE=${apiBase}；如本机尚无 runtime_id 或 credential，请先走自助 enroll，不要让管理员逐次下发上传 token。`,
      uploadCheckInstruction: commonCheck,
    },
    {
      key: "openclaw",
      name: "原生 OpenClaw",
      description: "适合 OpenClaw 原生实例，优先使用自身 skill registry、定时任务和 backup/export 能力。",
      primaryMode: "复制到 OpenClaw 对话框",
      registryInstruction: `请先把 DuckDock 私有 Skills Registry 配置为当前 OpenClaw 的私有技能库地址：${registryUrl}。如果支持 SkillHub/ClawHub registry，请把它登记为 duckdock-private 并同步索引。`,
      installInstruction: `请安装 ${reporterSkill}，并允许它定位本实例的 skills、agents、prompts、workflows、memory index、session summary 和 artifact index。${commonScope}`,
      scheduleInstruction: `${commonSchedule} 如果本实例支持 backup create/export，请优先利用原生导出结果生成 DuckDock pack。DUCKDOCK_API_BASE=${apiBase}，DUCKDOCK_RUNTIME_ID=<runtime_id>，DUCKDOCK_REPORTER_CREDENTIAL=<dkr_report_*>。`,
      uploadCheckInstruction: `${commonCheck} 如果 dry-run 成功，请在 DuckDock 个人工作台刷新 Reporter 自报状态。`,
      cliTemplate: `openclaw registry add duckdock-private ${registryUrl}
openclaw skill install duckdock/duckdock-reporter --registry duckdock-private
openclaw task schedule duckdock-reporter-weekly "0 16 * * 5" \\
  --skill duckdock/duckdock-reporter \\
  --env DUCKDOCK_API_BASE=${apiBase} \\
  --env DUCKDOCK_RUNTIME_ID=<runtime_id> \\
  --env DUCKDOCK_REPORTER_CREDENTIAL=<dkr_report_*>
openclaw skill run duckdock/duckdock-reporter --dry-run --upload-check`,
    },
    {
      key: "arkclaw",
      name: "火山云 ArkClaw",
      description: "适合兼容 OpenClaw 工作流但 API 能力需要厂商侧确认的运行时，先走对话式探针部署。",
      primaryMode: "复制到 ArkClaw 对话框",
      registryInstruction: `请先把 DuckDock 私有 Skills Registry 配置为当前 ArkClaw 的私有技能库地址：${registryUrl}。如果 ArkClaw 当前只支持自定义 skill 源，请使用这个地址作为 DuckDock 镜像源并同步索引。`,
      installInstruction: `请安装 ${reporterSkill}，并检查 ArkClaw 是否允许读取技能、智能体、提示词、工作流、会话摘要和任务产物索引。${commonScope}`,
      scheduleInstruction: `${commonSchedule} 运行时类型请标记为 arkclaw，DUCKDOCK_API_BASE=${apiBase}；如 ArkClaw 暂不支持直传，请先生成本地 pack 并返回上传阻塞原因。`,
      uploadCheckInstruction: `${commonCheck} 请明确返回 ArkClaw 当前可用的导出/API 能力，以及不能采集的字段清单。`,
    },
  ];
}

export default function ReporterSetupPage() {
  const { message } = AntApp.useApp();
  const [runtimeKey, setRuntimeKey] = useState<RuntimeKey>("workbuddy");
  const origin = typeof window === "undefined" ? "https://duckdock.example.com" : window.location.origin;
  const apiBase = useMemo(() => resolveDuckDockApiBase(origin), [origin]);
  const guides = useMemo(() => runtimeGuides(apiBase), [apiBase]);
  const selected = guides.find((guide) => guide.key === runtimeKey) ?? guides[0];
  const registryUrl = `${apiBase}/registry/skill.md`;
  const registryIndexUrl = `${apiBase}/registry/index?include_urls=true`;
  const reporterSkill = "duckdock/duckdock-reporter";
  const fullPrompt = [
    selected.registryInstruction,
    selected.installInstruction,
    selected.scheduleInstruction,
    selected.uploadCheckInstruction,
  ].join("\n\n");
  const mcpScriptPath = "<DuckDock 项目根目录>/tools/duckdock-runtime-mcp/duckdock_runtime_mcp.py";
  const mcpConfig = `{
  "mcpServers": {
    "duckdock-runtime": {
      "command": "python",
      "args": [
        "${mcpScriptPath}",
        "--api-base",
        "${apiBase}"
      ]
    }
  }
}`;
  const mcpBootPrompt = `帮我把这台 WorkBuddy 接入 DuckDock。
DuckDock API: ${apiBase}
如果本机还没有 DuckDock runtime，请先登录并调用 /reporters/enroll 自助登记。
Reporter Credential: <enroll 后一次性返回的 dkr_report_*>
请安装 DuckDock Reporter，做一次 dry-run 上传检查，并创建每周五 16:00 的周期上报任务。`;

  async function copy(value: string, label: string) {
    try {
      await navigator.clipboard.writeText(value);
      message.success(`${label} 已复制`);
    } catch {
      message.error(`${label} 复制失败`);
    }
  }

  return (
    <div className="app-page max-w-6xl space-y-6">
      <Card>
        <div className="grid lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="px-7 py-7 lg:px-8">
            <div className="section-kicker">REPORTER SETUP SOP</div>
            <h1 className="mt-3 text-3xl font-bold tracking-tight text-slate-950">DuckDock 探针接入 SOP</h1>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-500">
              面向 WorkBuddy、OpenClaw、ArkClaw 使用者。复制下面的一句话到对应客户端，让它完成私有 Skills Registry 配置、安装 DuckDock Reporter、创建周期任务并做一次 dry-run 上传检查。
            </p>
            <div className="mt-6 flex flex-wrap gap-2">
              {guides.map((guide) => (
                <Button
                  key={guide.key}
                  variant={guide.key === runtimeKey ? "primary" : "secondary"}
                  onClick={() => setRuntimeKey(guide.key)}
                >
                  {guide.name}
                </Button>
              ))}
            </div>
          </div>
          <div className="border-t border-slate-200 bg-slate-50 px-7 py-7 lg:border-l lg:border-t-0 lg:px-8">
            <div className="text-sm font-medium text-slate-500">私有 Skills Registry 地址</div>
            <div className="mt-3 break-all rounded-lg border border-slate-200 bg-white px-4 py-3 font-mono text-xs leading-5 text-slate-700">
              {registryUrl}
            </div>
            <Button
              variant="secondary"
              className="mt-4"
              icon={<Copy className="h-4 w-4 text-slate-400" />}
              onClick={() => copy(registryUrl, "私有镜像地址")}
            >
              复制地址
            </Button>
            <div className="mt-5 space-y-3 border-t border-slate-200 pt-5 text-xs leading-5 text-slate-500">
              <div>
                <span className="font-semibold text-slate-700">机器索引：</span>
                <span className="break-all font-mono">{registryIndexUrl}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="font-semibold text-slate-700">探针 Skill：</span>
                <MonoPill>{reporterSkill}</MonoPill>
              </div>
              <div>本地真实验证请使用后端直连地址；不要把 WorkBuddy 指到 5175 前端代理地址。</div>
            </div>
          </div>
        </div>
      </Card>

      <Card
        title="标准 MCP 接入（推荐产品化路线）"
        description="SOP 仍然保留作兜底；如果 WorkBuddy 支持配置 MCP，优先接入 duckdock-runtime-mcp，让安装、配置、dry-run、定时和状态检查都走结构化工具。"
        action={
          <Button
            variant="secondary"
            icon={<Copy className="h-4 w-4 text-slate-400" />}
            onClick={() => copy(mcpConfig, "MCP 配置")}
          >
            复制 MCP 配置
          </Button>
        }
      >
        <div className="grid gap-0 lg:grid-cols-2">
          <div className="border-b border-slate-200 p-6 lg:border-b-0 lg:border-r">
            <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
              <IconTile size="sm" tone="indigo">
                <TerminalSquare className="h-4 w-4" />
              </IconTile>
              01. 先把 DuckDock MCP 加到 WorkBuddy
            </div>
            <div className="mt-2 text-xs leading-5 text-slate-500">
              把下面配置中的脚本路径替换为 DuckDock 项目在本机的绝对路径。企业部署时这一步应由管理员预置。
            </div>
            <pre className="mt-4 overflow-x-auto rounded-lg bg-slate-950 px-4 py-4 font-mono text-xs leading-6 text-slate-100">
              <code>{mcpConfig}</code>
            </pre>
          </div>
          <div className="p-6">
            <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
              <IconTile size="sm" tone="indigo">
                <Clipboard className="h-4 w-4" />
              </IconTile>
              02. 然后在 WorkBuddy 中用一句话接入
            </div>
            <div className="mt-2 text-xs leading-5 text-slate-500">
              WorkBuddy 会调用 detect、install、configure、dry_run、schedule、status 这组 MCP 工具，不再只依赖自然语言 SOP。
            </div>
            <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 font-mono text-xs leading-6 text-slate-700">
              {mcpBootPrompt}
            </div>
            <Button
              variant="secondary"
              className="mt-4"
              icon={<Copy className="h-4 w-4 text-slate-400" />}
              onClick={() => copy(mcpBootPrompt, "MCP 接入对话")}
            >
              复制接入对话
            </Button>
          </div>
        </div>
      </Card>

      <div className="grid gap-6 lg:grid-cols-[260px,minmax(0,1fr)]">
        <Card padded className="self-start">
          <div className="text-sm font-semibold text-slate-900">执行顺序</div>
          <div className="mt-4 space-y-3">
            <StepMarker index="01" icon={Settings2} title="配置私有 Registry" />
            <StepMarker index="02" icon={Clipboard} title="安装 Reporter Skill" />
            <StepMarker index="03" icon={RefreshCw} title="创建周期任务" />
            <StepMarker index="04" icon={UploadCloud} title="dry-run 上传检查" />
          </div>
          <div className="mt-5 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-xs leading-5 text-amber-700">
            注意：员工或 Agent 可以自助 enroll 获取长期 Reporter Credential；管理员手工 token 只作为运维兜底。
          </div>
        </Card>

        <main className="space-y-5">
          <Card
            title={selected.name}
            description={selected.description}
            action={
              <Button
                variant="primary"
                icon={<Copy className="h-4 w-4" />}
                onClick={() => copy(fullPrompt, "完整对话指令")}
              >
                复制完整 SOP
              </Button>
            }
          >
            <div className="divide-y divide-slate-200">
              <CopyStep
                index="01"
                title="一句话指定 DuckDock 私有 Skills Registry"
                description={selected.primaryMode}
                value={selected.registryInstruction}
                onCopy={copy}
              />
              <CopyStep
                index="02"
                title="安装 DuckDock Reporter 探针 Skill"
                description="安装后只授予 DuckDock 需要的最小采集范围。"
                value={selected.installInstruction}
                onCopy={copy}
              />
              <CopyStep
                index="03"
                title="创建周期归档任务"
                description="建议每周五 16:00，也可以由管理员按岗位风险改成每日。"
                value={selected.scheduleInstruction}
                onCopy={copy}
              />
              <CopyStep
                index="04"
                title="主动上传检查"
                description="用 dry-run 验证 credential、上传会话、对象存储直传和 finalize 是否正常。"
                value={selected.uploadCheckInstruction}
                onCopy={copy}
              />
            </div>
          </Card>

          {selected.cliTemplate ? (
            <Card
              title="OpenClaw CLI 模板"
              description="如果你的 OpenClaw 实例支持 CLI，可以复制后替换 runtime_id 和 credential。"
              action={
                <Button
                  variant="secondary"
                  icon={<Copy className="h-4 w-4 text-slate-400" />}
                  onClick={() => copy(selected.cliTemplate ?? "", "CLI 模板")}
                >
                  复制 CLI
                </Button>
              }
            >
              <pre className="overflow-x-auto bg-slate-950 px-6 py-5 font-mono text-xs leading-6 text-slate-100">
                <code>{selected.cliTemplate}</code>
              </pre>
            </Card>
          ) : null}

          <Card padded>
            <div className="flex items-start gap-3">
              <IconTile size="md" tone="neutral">
                <FileCheck2 className="h-5 w-5" />
              </IconTile>
              <div>
                <div className="text-lg font-semibold tracking-tight text-slate-900">完成后如何确认</div>
                <div className="mt-2 grid gap-3 text-sm leading-6 text-slate-500 md:grid-cols-3">
                  <CheckItem text="客户端返回 report_id 与 pack_sha256。" />
                  <CheckItem text="个人工作台的 Reporter 自报出现 healthy 或 waiting 状态。" />
                  <CheckItem text="管理员在控制平面能看到上传会话和采集任务记录。" />
                </div>
              </div>
            </div>
          </Card>
        </main>
      </div>
    </div>
  );
}

function StepMarker({ index, icon: Icon, title }: { index: string; icon: typeof Settings2; title: string }) {
  return (
    <div className="flex items-center gap-3">
      <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-900 text-white">
        <Icon className="h-4 w-4" />
      </div>
      <div>
        <div className="font-mono text-xs font-semibold text-slate-400">{index}</div>
        <div className="text-sm font-medium text-slate-900">{title}</div>
      </div>
    </div>
  );
}

function CopyStep({
  index,
  title,
  description,
  value,
  onCopy,
}: {
  index: string;
  title: string;
  description: string;
  value: string;
  onCopy: (value: string, label: string) => void;
}) {
  return (
    <div className="grid gap-4 px-6 py-5 lg:grid-cols-[150px,minmax(0,1fr)_120px] lg:items-start">
      <div className="flex items-start gap-3">
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-slate-900 font-mono text-xs font-semibold text-white">
          {index}
        </div>
        <div className="mt-0.5">
          <div className="text-sm font-semibold text-slate-900">{title}</div>
          <div className="mt-1 text-xs leading-5 text-slate-500">{description}</div>
        </div>
      </div>
      <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 font-mono text-xs leading-6 text-slate-700">
        {value}
      </div>
      <Button
        variant="secondary"
        size="sm"
        icon={<Copy className="h-4 w-4 text-slate-400" />}
        onClick={() => onCopy(value, title)}
      >
        复制
      </Button>
    </div>
  );
}

function CheckItem({ text }: { text: string }) {
  return (
    <div className="flex gap-2">
      <CheckCircle2 className="mt-1 h-4 w-4 shrink-0 text-emerald-500" />
      <span>{text}</span>
    </div>
  );
}
