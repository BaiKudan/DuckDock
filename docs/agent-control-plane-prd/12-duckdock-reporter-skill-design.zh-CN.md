# 12. DuckDock Reporter Skill 设计

## 1. 定位

`duckdock_reporter` 是安装在 OpenClaw、WorkBuddy 或类 OpenClaw 运行时里的标准自报 Skill。它不做实时监控，不做屏幕/键盘采集，不绕过平台权限。它只在运行时授权的定时任务里执行，生成 DuckDock 需要的 AI Agent 资产归档包。

一句话定位：

> DuckDock Reporter Skill 是企业 AI Agent 运行时的“周期性资产自报与交接归档说明书”。

## 2. 为什么适合 WorkBuddy

你本机已经安装 WorkBuddy，这类桌面 Agent 工作台通常有几个特点：

- 数据分散在本地工作区、会话、工具配置、项目上下文里。
- 不一定公开完整备份/恢复 API。
- 直接做外部扫描容易被员工理解成监控。
- 但由 WorkBuddy 自身定时执行一个“自报 Skill”，接受度更高。

因此 WorkBuddy 接入不应先做强爬虫，而应先做：

```text
WorkBuddy 定时任务
  -> 执行 duckdock_reporter
  -> 生成摘要/索引/hash/证据引用
  -> 打包 duckdock-pack-v1.zip
  -> 通过 DuckDock upload session 上传
  -> DuckDock 入库分析
```

## 3. Skill 包位置

当前草案在：

```text
docs/agent-control-plane-prd/duckdock-reporter-skill/
├── SKILL.md
├── references/
│   ├── pack-schema.md
│   └── workbuddy-windows.md
└── examples/
    ├── manifest.json
    └── runtime.json
```

`SKILL.md` 是可发布到 DuckDock Skill 仓库的核心文件。`references` 是给运行时或开发人员看的补充规范。

当前版本已经升级为双模式：

```text
deploy：通过对话完成安装、配置、试运行和定时任务部署。
run：按计划生成 duckdock-pack-v1.zip 并上传 DuckDock。
```

这意味着 WorkBuddy/OpenClaw 使用者不需要手工执行安装命令。管理员在 DuckDock 生成 Runtime ID 和 Reporter Token 后，使用者只要在当前 AI 工作台里说“帮我接入 DuckDock”，运行时就应进入 `deploy` 模式。

## 4. 执行策略

默认执行频率：

```text
每周五 16:00
```

增强执行场景：

- 员工离职。
- 项目交接。
- 高风险资产接管。
- 管理员手动触发补采。

默认采集模式：

```text
summary_index
```

这个模式只采集摘要、索引、路径、hash、时间、归属、资产关系，不上传原始聊天正文和原始记忆正文。

## 5. WorkBuddy 本机适配边界

WorkBuddy Windows 场景下，Skill 可以尝试定位：

```text
%WORKBUDDY_HOME%
%LOCALAPPDATA%\Programs\WorkBuddy
%LOCALAPPDATA%\Tencent\WorkBuddy
%APPDATA%\Tencent\WorkBuddy
%USERPROFILE%\.workbuddy
%USERPROFILE%\Documents\WorkBuddy
```

但这些只能作为候选路径。必须先确认存在 WorkBuddy 元数据或工作区标记，不能递归扫描整个用户目录。

优先级：

1. WorkBuddy 官方 API/导出能力。
2. WorkBuddy 自身任务执行上下文。
3. 用户或管理员配置的 workspace root。
4. 保守的本地目录发现。

## 6. 上传链路

Skill 只需要知道 DuckDock API 和上报 Token：

```text
DUCKDOCK_API_BASE=http://duckdock.example.com/api/v1
DUCKDOCK_RUNTIME_ID=12
DUCKDOCK_REPORT_TOKEN=dkr_report_xxxx_xxxxxxxxx
```

上传步骤：

1. 生成 `duckdock-pack-v1.zip`。
2. 计算 sha256 和 size。
3. `POST /reports/upload-sessions` 创建上传会话。
4. PUT 到返回的 `upload_url`。
5. `POST /reports/{report_id}/finalize`。

WorkBuddy 不接触 MinIO 密钥。

## 7. DuckDock 入库映射

| Skill 输出 | DuckDock 表/模型 |
|---|---|
| `runtime.json` | `runtime_instances` / `runtime_capability_snapshots` |
| `principals.ndjson` | `provider_principals` |
| `skills.ndjson` | `ai_assets(asset_type=skill)` |
| `agents.ndjson` | `ai_assets(asset_type=agent)` |
| `prompts.ndjson` | `ai_assets(asset_type=prompt)` |
| `workflows.ndjson` | `ai_assets(asset_type=workflow)` |
| `tools.ndjson` | `ai_assets(asset_type=tool)` |
| `memories.ndjson` | `ai_assets(asset_type=knowledge_base)` |
| `sessions.ndjson` | `work_traces` |
| `artifacts.ndjson` | `work_artifacts` / `evidence_items` |
| `evidence.ndjson` | `evidence_items` |
| 全量原始记录 | `raw_collection_records` |

## 8. 下一步开发建议

第一步：把 `duckdock_reporter` 发布成 DuckDock 内置 Skill 模板。

第二步：在前端运行时详情页增加“生成 Reporter Token”和“复制 WorkBuddy 定时任务配置”。

第三步：增加一个本地 dry-run 工具，只输出 `duckdock-pack-v1.zip`，不上传，用来验证 WorkBuddy 本机路径和脱敏效果。

第四步：在 WorkBuddy 上配置每周五 16:00 定时执行，并观察 DuckDock 控制平面的资产、工作历程、证据是否正确入库。
