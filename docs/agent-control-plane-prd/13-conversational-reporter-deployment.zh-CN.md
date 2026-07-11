# 13. 对话式 DuckDock Reporter 部署方案

## 1. 目标

OpenClaw、WorkBuddy 的使用者不需要手工理解 Skill 安装、Token、MinIO、定时任务、zip 包和 finalize。用户只要用自然语言和当前 AI 工作台对话，就能完成“伪探针”的部署。

目标体验：

```text
用户：帮我把这个 WorkBuddy 接入 DuckDock，每周五下午四点自动上报。
WorkBuddy：我会部署 DuckDock Reporter。默认只上报企业 AI 资产索引、会话摘要、产物 hash、路径和交接风险，不上传原始私聊全文、密钥、cookie 或个人系统目录。是否继续？
```

这不是静默安装，也不是实时监控。它是用户确认后的“自报归档任务”。

## 2. 角色分工

| 角色 | 职责 |
|---|---|
| DuckDock 管理员 | 创建 Runtime、生成 Reporter Token、查看上报结果、撤销 Token |
| OpenClaw/WorkBuddy 使用者 | 通过对话确认部署、提供 DuckDock 参数或扫码/粘贴配置、确认定时任务 |
| `duckdock_reporter` Skill | 完成安装引导、配置保存、dry-run、定时任务创建、上报执行 |
| DuckDock 后端 | 签发 upload session、校验包、解析入库、生成资产与交接视图 |

## 3. 管理员预置流程

管理员在 DuckDock 控制平面先完成：

1. 创建运行时：

```text
provider = workbuddy 或 openclaw
name = 员工/团队/实例名称
deploy_type = desktop/private/on_prem
```

2. 生成 Reporter Token：

```text
POST /api/v1/runtimes/{runtime_id}/report-tokens
```

3. 把以下配置给使用者：

```text
DUCKDOCK_API_BASE
DUCKDOCK_RUNTIME_ID
DUCKDOCK_REPORT_TOKEN
默认计划：每周五 16:00
默认模式：summary_index
```

后续建议前端做成一个“复制部署口令”：

```text
请在你的 WorkBuddy/OpenClaw 中输入：

帮我接入 DuckDock。API 地址是 https://duckdock.example.com/api/v1，
Runtime ID 是 12，Reporter Token 是 dkr_report_xxxx_xxxxxxxxx。
默认每周五下午四点上报，先试运行，不上传原文。
```

## 4. 使用者对话流程

### 4.1 首次接入

用户：

```text
帮我把这个 WorkBuddy 接入 DuckDock，每周五下午四点自动上报。
```

WorkBuddy/OpenClaw：

```text
我会部署 DuckDock Reporter。默认只上报企业 AI 资产索引、会话摘要、产物 hash、路径和交接风险，不上传原始私聊全文、密钥、cookie 或个人系统目录。是否继续？
```

用户确认后：

```text
请提供 DuckDock API 地址、Runtime ID 和 Reporter Token。Token 只会写入本机安全配置，之后不会在对话里回显。
```

用户提供配置后：

```text
配置已保存。现在我会先做一次试运行，只生成本地 DuckDock Pack，不上传。是否执行？
```

试运行结果：

```text
试运行完成：
- assets: 18
- sessions: 42
- memories: 6
- artifacts: 27
- redactions: 5
未上传。是否创建每周五 16:00 的定时上报任务？
```

用户确认后：

```text
定时任务已创建：DuckDock Weekly Reporter。是否现在执行一次测试上传？
```

### 4.2 修改计划

用户：

```text
改成每天晚上八点上报。
```

响应：

```text
我将把 DuckDock Reporter 的计划从每周五 16:00 改为每天 20:00。采集范围不变，仍为摘要和索引模式。是否确认修改？
```

### 4.3 暂停

用户：

```text
暂停 DuckDock 上报。
```

响应：

```text
我会禁用 DuckDock Reporter 定时任务，但保留本地配置和 Token，方便恢复。是否确认？
```

### 4.4 完全移除

用户：

```text
把 DuckDock Reporter 从这台 WorkBuddy 移除。
```

响应：

```text
我会删除 DuckDock 定时任务和本地 Reporter 配置。已上传到 DuckDock 的历史记录不会被删除。是否确认？完成后还需要 DuckDock 管理员撤销 Runtime Report Token。
```

## 5. Skill 包调整

当前 Skill 包已经升级为双模式：

```text
duckdock_reporter deploy
duckdock_reporter run
```

实际对用户不暴露命令，而是通过对话触发：

| 用户意图 | Skill 模式 |
|---|---|
| 帮我接入 DuckDock | `deploy` |
| 安装 DuckDock Reporter | `deploy` |
| 先试运行，不上传 | `deploy --dry-run` |
| 每周五 16:00 自动上报 | `deploy schedule` |
| 立刻上传一次 | `run --manual` |
| 暂停上报 | `deploy disable` |
| 移除 Reporter | `deploy uninstall` |

Skill 包路径：

```text
docs/agent-control-plane-prd/duckdock-reporter-skill/
```

新增内容：

```text
references/conversational-deployment.md
examples/deployment-conversation.md
examples/scheduled-task-config.json
```

## 6. 安装流程设计

### 6.1 OpenClaw

优先使用 OpenClaw 原生 Skill 安装能力：

```text
用户通过对话发起
  -> OpenClaw 检查 duckdock_reporter 是否已安装
  -> 未安装则从 DuckDock Skill Registry 拉取
  -> 写入 DuckDock 配置和 Token
  -> dry-run
  -> 创建 OpenClaw 原生 scheduled task
  -> 可选测试上传
```

逻辑命令形态：

```text
openclaw skill install duckdock_reporter
openclaw config secret set DUCKDOCK_REPORT_TOKEN
openclaw task create "DuckDock Weekly Reporter" --skill duckdock_reporter --action run --cron "0 16 * * 5"
```

真实命令以 OpenClaw 实际版本为准，Skill 只要求执行这些逻辑动作。

### 6.2 WorkBuddy

优先使用 WorkBuddy 自身自动化/任务能力：

```text
用户通过对话发起
  -> WorkBuddy 检查是否支持 Skill/插件/自动化任务
  -> 安装或启用 duckdock_reporter
  -> 把 Token 写入 WorkBuddy 安全配置
  -> dry-run
  -> 创建 WorkBuddy 自动化任务
  -> 可选测试上传
```

如果 WorkBuddy 暂时不能创建原生定时任务，再退回 Windows Task Scheduler：

```text
名称：DuckDock Weekly Reporter
触发器：每周五 16:00
动作：调用 WorkBuddy/OpenClaw 运行 duckdock_reporter run
重试：3 次，每次间隔 10 分钟
超时：30 分钟
```

## 7. 权限与合规提示

部署时必须展示一次范围说明：

```text
默认只采集企业 AI 工作资产索引、摘要、hash、路径、时间和归属关系。
不会采集键盘输入、屏幕截图、浏览器 cookie、个人密码、密钥、私聊原文或非工作目录。
```

必须用户确认的动作：

- 保存 DuckDock Token。
- 创建或修改定时任务。
- 执行第一次测试上传。
- 开启原文/原始证据上传。
- 卸载或删除配置。

## 8. DuckDock 侧后续开发

前端需要增加三个入口：

1. 运行时详情页：生成 Reporter Token。
2. 运行时详情页：复制“对话式部署口令”。
3. 上报会话页：查看 report upload session、状态、错误和 collection job。

后端已经具备：

- Runtime Report Token。
- Report Upload Session。
- Presigned PUT URL。
- Finalize 校验。
- Pack 解析入库。

下一步不是再做上传链路，而是补“部署口令”和“对话式安装指引”的前端体验。
