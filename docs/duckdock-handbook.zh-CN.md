# DuckDock 使用手册

## 1. 产品定位

DuckDock 是企业 AI Agent 资产与交接控制平面。它不只是一个 Skills 仓库，而是用来回答下面几个管理问题：

- 谁创建或维护了哪些 AI 能力。
- 这些能力在哪些运行时中被使用，例如 WorkBuddy、OpenClaw、ArkClaw、JVS 或定制运行时。
- 这些能力产生了哪些工作历程、项目上下文和交接证据。
- 员工离职、岗位调整或项目交接时，哪些资产需要确认、补充、排除或移交。

DuckDock 的核心原则是“周期性自报”，不是实时监控。用户侧运行时通过 `duckdock-reporter` 上报两类数据：日报/周报默认提交隐私保护的结构化 JSON，交接/审计场景才生成 `duckdock-pack-v1.zip` 并直传对象存储，由 DuckDock 异步分析形成结构化索引。

## 2. 角色与入口

### 管理员入口

管理员登录后进入企业控制台，主要使用以下页面：

- 概览：查看全局资产、运行时、上传和存储状态。
- Agent 控制平面：登记运行时、管理 Reporter Credential / 兜底 report token、查看结构化报告、上报会话、分析任务和资产。
- 身份与权限：轻量维护用户、岗位、用户维度交接状态。
- 组件管理：按需启动可选组件，例如 Langfuse。
- 诊断中心 / 审计日志：定位任务、扫描、上报和权限事件。

管理员不应该替员工确认个人资产，只负责运行时登记、接入状态、风险和交接流程。

### 员工入口

普通员工登录后进入个人工作台，主要看到：

- 我的 AI 资产：已归属到自己的 Skills、Agents、Prompts、Workflows 等。
- 最近工作历程：Reporter 上报后经分析生成的摘要，不展示完整私聊原文。
- 项目上下文：从资产和工作历程 metadata 中提取的项目、仓库、客户或空间线索。
- Reporter 状态：关联运行时最近一次上报是否健康。
- 需要确认的资产：员工可以确认、补充或排除。

“离职交接”不应该默认展示给在职员工。只有管理员把用户标记为待离职或已离职状态后，个人工作台才显示相关交接卡片。

## 3. 管理员日常流程

### 3.1 登记运行时

进入“Agent 控制平面”，创建运行时实例：

- provider：`workbuddy`、`openclaw`、`arkclaw`、`jvs` 或 `custom`
- name：能识别业务或用户来源的名称
- deploy_type：`private`、`on_prem`、`saas` 等
- base_url：如果没有 API，可填 `local://workbuddy-desktop` 这类说明性地址

运行时是 Reporter Credential 的边界。一个 credential 只能为自己的 runtime 上传报告；legacy report token 只用于运维兜底。

### 3.2 Reporter Credential 接入

推荐路径是员工/Agent 侧 Reporter 登录 DuckDock 后调用 `POST /api/v1/reporters/enroll` 自助登记运行时 endpoint，并拿到一次性显示的长期 **Reporter Credential**（`dkr_report_*`）。使用规则：

- credential 只展示一次。
- credential 绑定 runtime/user/device，不能跨 runtime 上传。
- credential 可轮换、可撤销；设备丢失或 agent 退役后应立即 revoke。
- Reporter 日常日报/周报直接提交结构化 JSON；交接/审计包上传时只拿短期 presigned PUT URL，不接触 MinIO/S3 access key。
- 不要把 credential 写进 Git、公开文档、截图或长期自动化 prompt。

管理员仍可在运行时详情中创建 legacy report token，用于运维兜底、设备迁移或批量预配；它不是新员工/新 agent 接入的推荐路径。

### 3.3 引导用户接入 Reporter

员工或运行时使用者打开“接入 SOP”页面，按平台选择：

- 腾讯云 WorkBuddy
- 原生 OpenClaw
- ArkClaw

每个 SOP 都包含可复制命令或对话提示，核心参数是：

```text
DuckDock API Base: https://duckdock.example.com/api/v1
DuckDock Private Skills Registry: https://duckdock.example.com/api/v1/registry/skill.md
Reporter Skill: duckdock/duckdock-reporter
Runtime ID: <runtime_id>
Reporter Credential: <dkr_report_*，由 /reporters/enroll 一次性返回>
```

生产环境应使用企业内网域名或 VPN 可达域名，不建议让用户侧运行时依赖 `localhost`。

### 3.4 查看上报与分析状态

Reporter 完成上报后，DuckDock 中会出现两类记录。

日常结构化日报/周报：

1. `collection_job`
   - 记录 `report_id`、`runtime_id`、report type、周期和 schema 版本，状态直接为 `succeeded`。

2. 结构化索引
   - AI 资产引用
   - Runtime binding
   - 工作历程摘要
   - 记忆候选
   - 交接/风险信号

交接/审计包上传：

1. `report_upload_session`
   - 记录 `report_id`、`runtime_id`、上传人身份、MinIO object key、sha256、大小和状态。

2. `report_analysis_job`
   - 记录异步分析任务状态、Worker、租约、结果 object key 和 materialized counts。

3. 分析结果制品
   - `analysis-result.json`
   - `asset-cards.json`
   - `worktrace-summary.md`
   - `memory-candidates.json`
   - `handover-signals.json`

4. 结构化索引
   - AI 资产
   - Runtime binding
   - 工作历程摘要
   - 记忆候选
   - 交接信号

管理员在控制平面运行时列表点击“详情”后，会看到这个员工/agent 的统一时间线：结构化日报/周报、交接包、资产、风险信号与交接信号会聚合在同一视图。页面上的“生成 AI 概览”会创建 `agent_insight_jobs` 异步任务，使用 `DUCKDOCK_LLM_*` 读取已入库的结构化快照生成 JSON 摘要；LLM 不可用时自动回退 baseline，并在 UI 显示降级徽章。

## 4. 员工使用流程

### 4.1 查看个人资产

员工打开“我的 AI 资产”后，先看“当前最重要的事”：

- 待确认资产：需要判断是否属于自己。
- 需要补充：资产信息不足，员工可补充项目、用途或维护人。
- 需要排除：误归属或不应进入个人资产范围。

### 4.2 理解 Reporter 状态

Reporter 状态为 `healthy` 表示最近 8 天内有成功结构化上报，或有报告包成功上传并完成分析。

常见状态：

- healthy：最近上报成功。
- waiting：还没有上报，或最新报告还在 pending / uploaded / ingesting。
- failed：最近报告失败、过期或分析失败。

### 4.3 资产确认边界

员工确认的是“资产是否和自己有关”，不是审批企业资产权限。确认后系统会保留用户反馈，帮助后续交接和审计。

### 4.4 离职交接视图

只有管理员在身份与权限中把用户标记为待离职或已离职时，个人工作台才显示交接准备相关卡片。

交接目前保持轻量：

- 按用户维度。
- 按岗位/职责维度。
- 不做复杂组织治理，不替代 HR 或 IAM 主系统。

## 5. Reporter 上报说明

### 5.1 日常结构化报告

日报/周报默认不打包、不进 MinIO、不排 Analysis Worker。Reporter 直接提交 `duckdock-structured-report-v1` JSON，字段包括：

```text
schema_version
runtime_id
report_type: daily / weekly / status
period_start / period_end
title / summary
highlights / blockers / next_actions
project_refs
asset_refs
memory_candidates
handover_signals
idempotency_key
```

DuckDock 会把它轻量物化为 `CollectionJob`、`WorkTrace`、`AIAsset`、`RuntimeBinding` 和 `MemoryCandidate`。格式不规范时由 Reporter 侧 skill/prompt 先整理成标准 JSON；平台侧只做 schema 校验、幂等和最小补全。

### 5.2 交接/审计包

需要本地文件、证据索引、hash、redaction report 或完整归档时，才使用包上传。默认包格式是 `duckdock-pack-v1.zip`，至少包含：

```text
manifest.json
runtime.json
inventory/assets.ndjson
inventory/work_traces.ndjson
summaries/worktrace-summary.md
```

可选内容：

```text
inventory/memories.ndjson
inventory/artifacts.ndjson
inventory/evidence.ndjson
evidence/redaction-report.json
evidence/sha256sums.txt
```

默认不上传：

- 完整聊天原文
- 明文密钥、token、cookie、私钥
- 个人密码或本地系统凭据
- 与企业 AI 工作无关的个人文件

默认上传：

- 资产名称、类型、版本、hash、路径引用和摘要
- 会话或任务摘要
- 项目、仓库、客户、工单等上下文线索
- 交接相关的决策、产物和证据索引

## 6. WorkBuddy 通用接入原则

当前建议的 WorkBuddy 路径是：

1. WorkBuddy 自动化任务访问 DuckDock 私有 Registry。
2. 从 Registry 下载 `duckdock/duckdock-reporter` bundle。
3. 安装到本机 `~/.workbuddy/skills/duckdock-reporter`。
4. 周期性日报/周报调用 `/reports/structured` 提交标准 JSON。
5. 交接/审计时生成最小 `duckdock-pack-v1.zip`。
6. 使用 Reporter Credential 创建上传会话。
7. 直传 MinIO。
8. finalize。
9. DuckDock Analysis Worker 异步入库。

注意：本次验证没有发现 WorkBuddy 明确暴露的原生“私有 SkillHub 配置 UI”。因此 WorkBuddy SOP 应描述为“通过 WorkBuddy 自动化从 DuckDock Registry 安装 Reporter skill”，而不是假设用户一定能在 UI 中配置私有技能库。

## 7. Hermes 接入说明

Hermes Reporter 的推荐接入路径是：

1. 员工登录 DuckDock 并调用 `/api/v1/reporters/enroll` 自助登记 runtime endpoint。
2. DuckDock 返回一次性显示的长期 `dkr_report_*` Reporter Credential。
3. Reporter 把 credential 写入 `~/.duckdock/reporter/hermes.json`，权限收为 `0600`。
4. Hermes cron 创建 `DuckDock Hermes Reporter Pilot` 定时任务，周五 16:00 执行。
5. cron 以 no-agent script 模式执行 `scripts/hermes_reporter_pilot.py`。
6. 每次任务先以 `hermes-reporter` profile 完成 v2 handshake/heartbeat，创建
   metadata-only Session/Run；成功或失败都写入明确终态，duration 由服务端计算。
7. 默认任务提交 `duckdock-structured-report-v1` JSON，只采集 Hermes 公开元数据、插件/模型摘要、cron 状态和交接/风险信号。
8. 交接/审计时显式运行 pack 模式，生成摘要/索引型 `duckdock-pack-v1.zip` 后再走 MinIO + Analysis Worker。
9. 管理员 Fleet 可见 Hermes 原生 profile、DD-C1 和 heartbeat；资产列表可见
   runtime / scheduled task / gateway / plugins，员工工作台可见资产和工作历程。

当前 Hermes 的 Runtime 仍按 `provider=custom + agent_kind=hermes` 接入，但 Adapter
已使用一等 `hermes-reporter` profile；是否新增一等 `hermes` Runtime provider 枚举
属于后续产品决策。

## 8. WorkBuddy 接入说明

WorkBuddy Reporter 的推荐轻量路径是：

1. 员工登录 DuckDock 并调用 `/api/v1/reporters/enroll` 自助登记 `provider=workbuddy` runtime endpoint。
2. DuckDock 返回一次性显示的长期 `dkr_report_*` Reporter Credential。
3. Reporter 把 credential 写入 `~/.duckdock/reporter/workbuddy.json`，权限收为 `0600`。
4. `duckdock-runtime-mcp` 把 credential 写入 `~/.duckdock/runtime-mcp/config.json`，权限收为 `0600`。
5. `duckdock.reporter.run_structured` 基于 WorkBuddy 安装、进程、端口和 connector/plugin 摘要生成 `duckdock-structured-report-v1` JSON。
6. Reporter 调用 `/reporters/heartbeat` 和 `/reports/structured`，DuckDock 直接物化到 `CollectionJob`、`WorkTrace`、`AIAsset`、`MemoryCandidate`。
7. 管理后台运行时详情可见 WorkBuddy 时间线，`agent_overview` 一键 AI 概览可用。

早期试点也验证过 WorkBuddy `codebuddy --print` 生成结构化 JSON + 本地脚本提交；现在推荐把同一能力收敛到 `duckdock-runtime-mcp`，减少 prompt 接触 credential 的机会。

当前还需要在目标桌面环境复验“WorkBuddy 到点自主执行”：注册并重载 `duckdock-runtime` MCP 后，创建 `duckdock.reporter.schedule`，观察至少一次真实触发。交接/审计包仍走 pack 模式。

## 9. DuckDock Runtime MCP 接入

SOP 是当前兜底方案；如果 WorkBuddy 支持配置 MCP，推荐使用 `duckdock-runtime-mcp` 作为标准化接入方式。当前仓库已有本地 MCP adapter，已验证结构化上报和自动化桥 create/delete；企业镜像预置、后台下载接入包和版本升级策略仍属于后续产品化事项。

MCP 第一版位于：

```text
tools/duckdock-runtime-mcp/duckdock_runtime_mcp.py
```

它提供以下工具：

- `duckdock.runtime.detect`
- `duckdock.reporter.install`
- `duckdock.reporter.configure`
- `duckdock.reporter.run_structured`
- `duckdock.reporter.dry_run`
- `duckdock.reporter.schedule`
- `duckdock.reporter.status`
- `duckdock.reporter.pause`
- `duckdock.reporter.uninstall`

推荐顺序是：先在 WorkBuddy 中配置 DuckDock MCP，再让用户通过一句话完成 Reporter 安装、最小校验上报和周期任务创建。详细说明见 [DuckDock Runtime MCP 接入说明](./duckdock-runtime-mcp.zh-CN.md)。

## 10. 常见问题

### 为什么不用 Git 上传日报/周报？

日报/周报不需要包上传，默认直接走结构化 JSON；交接/审计包也不推荐走 Git。原因：

- Reporter 不需要接触对象存储密钥。
- 大包直传 MinIO/S3 更稳。
- DuckDock 仍控制上传会话、权限、状态、校验、解析和审计。
- `report_id`、`collection_job_id`、`pack_sha256` 可以保证幂等。

### 为什么 MySQL 不保存原始包内容？

MySQL 只保存必要索引和结论。原始上传包和完整分析结果保留在 MinIO，便于回溯、审计和降低数据库膨胀。

### Reporter 是否会监控员工？

设计上不是。Reporter 是运行时侧授权的周期性自报任务，只收集企业 AI 资产和交接所需的摘要、索引、hash 与上下文。默认不上传完整私聊原文。

### 如何判断接入成功？

管理员侧：

- 运行时最近报告状态为 `succeeded`。
- 对应分析任务为 `succeeded`。
- 分析控制台显示结果文件、schema、worker model/trace,且 `materialized_counts.inserted_counts`
  中资产、工作历程或记忆候选大于 0；重复上报时可查看 `updated_counts` / `deduped_counts`。

员工侧：

- Reporter 状态为 `healthy`。
- “我的 AI 资产”出现归属资产。
- “最近工作历程”出现摘要。
