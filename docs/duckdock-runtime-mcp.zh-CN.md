# DuckDock Runtime MCP 接入说明

本文说明 DuckDock 在 WorkBuddy 上的 MCP 化接入路线。

当前结论：

- 当前仓库已有第一版 `tools/duckdock-runtime-mcp` 本地 adapter 和自检测试。
- 短期继续保留 Reporter SOP，因为它是最容易现场兜底的接入方式。
- 中期把安装、配置、结构化上报、最小校验上报、定时、状态、暂停、卸载稳定为标准 MCP 工具。
- 长期由企业管理员在内网镜像或桌面环境中预置 MCP，员工不需要理解 MCP 配置，只需要在 WorkBuddy 中发起“帮我接入 DuckDock”。

## 1. 为什么需要 MCP

SOP 方式依赖用户复制自然语言说明，让 WorkBuddy 自行理解步骤。它适合兜底，但执行结果不够结构化。

MCP 方式把动作变成稳定工具：

| MCP 工具 | 用途 |
|---|---|
| `duckdock.runtime.detect` | 检测本机 WorkBuddy、Reporter skill、配置和可调用桥接能力。 |
| `duckdock.reporter.install` | 从 DuckDock 私有 Skills Registry 安装 `duckdock/duckdock-reporter`。 |
| `duckdock.reporter.configure` | 写入 DuckDock API、runtime_id、provider 和可选 Reporter Credential。 |
| `duckdock.reporter.run_structured` | 使用本机 Reporter Credential 提交一次 `duckdock-structured-report-v1` 日报/周报。 |
| `duckdock.execution.session_start` / `session_complete` | 为 OpenClaw 类 Runtime 建立/结束 metadata-only AgentSession，并保持稳定 external session ID。 |
| `duckdock.execution.run_start` / `run_complete` | 建立/结束 AgentRun，把 stable external run ID 与同一 OTel trace/root span 绑定。 |
| `duckdock.execution.buffer_status` | 查看持久 ack cursor、待发送年龄/数量/字节、重试、容量压力和 loss marker。 |
| `duckdock.execution.buffer_flush` | 使用当前 Runtime Credential 按稳定本地序号重放待发送生命周期信封。 |
| `duckdock.execution.buffer_discard` | 显式丢弃精确序号范围，并生成持久 `PARTIAL` / `DECLARED_LOSS` marker。 |
| `duckdock.reporter.dry_run` | 创建一次性 WorkBuddy 自动化，执行结构化 Reporter 验证。 |
| `duckdock.reporter.schedule` | 创建周期 WorkBuddy 自动化，默认每周五 16:00。 |
| `duckdock.reporter.status` | 查看本地安装、配置和自动化摘要。 |
| `duckdock.reporter.pause` | 暂停 DuckDock Reporter 自动化。 |
| `duckdock.reporter.uninstall` | 移除配置，并可选移除 skill 和自动化。 |

这让“对话式接入”从一段说明升级成可幂等、可检查、可回滚的工具调用。

## 2. 首次接入方式

第一版 MCP 位于：

```text
tools/duckdock-runtime-mcp/duckdock_runtime_mcp.py
```

WorkBuddy 中增加 MCP 配置时使用绝对路径。例如本机开发环境：

```json
{
  "mcpServers": {
    "duckdock-runtime": {
      "command": "python",
      "args": [
        "/absolute/path/to/DuckDock/tools/duckdock-runtime-mcp/duckdock_runtime_mcp.py",
        "--api-base",
        "https://duckdock.example.com/api/v1"
      ]
    }
  }
}
```

接入后，用户在 WorkBuddy 中输入：

```text
帮我把这台 WorkBuddy 接入 DuckDock。
DuckDock API: https://duckdock.example.com/api/v1
Runtime ID: <通过 /reporters/enroll 返回的 runtime_id>
Reporter Credential: <通过 /reporters/enroll 一次性返回的 dkr_report_*>
每周五 16:00 自动上报，并现在做一次最小校验上报。
```

WorkBuddy 应按顺序调用：

```text
duckdock.runtime.detect
duckdock.reporter.install
duckdock.reporter.configure
duckdock.reporter.run_structured
duckdock.reporter.dry_run
duckdock.reporter.schedule
duckdock.reporter.status
```

## 3. 本地自检

```powershell
python tools/duckdock-runtime-mcp/duckdock_runtime_mcp.py `
  --api-base https://duckdock.example.com/api/v1 `
  --self-test
```

自检只检测本机 WorkBuddy、Reporter skill 和 MCP 桥接状态，不上传数据。

`duckdock.runtime.detect` 会区分两个状态，避免把“桥能调用”和“WorkBuddy 已加载 DuckDock 工具”混为一谈：

- `workbuddy_mcp.callable`：DuckDock Runtime MCP 可以连上 WorkBuddy 本机 connector-proxy，并能创建/查询自动化。
- `workbuddy_mcp_config.duckdock_runtime_registered`：WorkBuddy 的 MCP 配置中已经注册了 `duckdock-runtime-mcp`；只有满足这个前置条件，WorkBuddy 自己执行的自动化才可以调用 `duckdock.reporter.run_structured`。

已验证 `run_structured` 可完成 heartbeat + 结构化上报，WorkBuddy 自动化 create/delete 桥可用。完整“周期任务到点自主执行”仍需在目标桌面环境注册并重载 `duckdock-runtime-mcp` 后复验。

## 4. 离线持久队列

Runtime MCP v0.3.0 会在第一次网络请求前，把 metadata-only
Session/Run 生命周期信封写入本机 `execution-buffer.sqlite3`。SQLite WAL、
`synchronous=FULL`、稳定队列序号和稳定幂等键保证 MCP 进程崩溃后可以继续
按序重放。

如果 Session/Run start 在离线时返回 `queued=true`，调用方应保存
`queue_sequence`，并在后续调用中使用 `session_queue_sequence` 或
`run_queue_sequence`。重放时 adapter 会先持久化上游 acknowledgement，
再把服务端 public ID 注入依赖请求，最后推进 ack cursor。

默认上限是 10,000 个待发送信封和 64 MiB，可通过
`DUCKDOCK_EXECUTION_BUFFER_MAX_ITEMS` /
`DUCKDOCK_EXECUTION_BUFFER_MAX_BYTES` 调整。到达 hard limit 后拒绝新入队，
不会自动淘汰旧记录。人工 discard 必须给出精确范围、reason code 和
`confirm=true`；payload 会被清空，但范围、数量、原因以及
`PARTIAL` / `DECLARED_LOSS` 降级标记会持久保留。

这里承诺的是 at-least-once replay，不是 exactly-once transport。服务端
幂等键负责避免重复领域事实。Reporter Credential 发送时才从当前配置加载，
不会写入队列记录；轮换后的 credential 只能重放同一个 Runtime 的记录。

## 5. 安全边界

- DuckDock MCP 不下发 MinIO 密钥。
- 日报/周报默认走 `/reports/structured` 轻量入库，不创建 MinIO 上传会话。
- 交接/审计/证据包场景才使用 DuckDock 返回的一次性上传 URL。
- MCP 返回结果会遮蔽 Reporter Credential。
- 如果 `duckdock.reporter.configure` 保存 credential，它只保存在本机配置文件中，必须是 runtime/user/device 级、可撤销 `dkr_report_*`。
- 第一版只把 WorkBuddy 的高层自动化能力作为产品化接口，不把 WorkBuddy remote-control 的文件系统、PTY 等高权限 API 作为默认依赖。
- 采集边界仍由 `duckdock-reporter` skill 控制，默认只收集资产索引、摘要、hash、路径和证据摘要，不上传完整私密会话原文。
- 持久队列仍包含 metadata ID 和时间戳，必须放在满足企业终端/磁盘加密策略
  的存储上；`duckdock.reporter.uninstall` 默认保留队列，避免静默删除未确认数据。

## 6. 长期代办

- 在企业内网安装包中预置 `duckdock-runtime-mcp`。
- 在 WorkBuddy / OpenClaw 桌面镜像中预置 MCP 配置。
- 增加管理员侧“复制 MCP 配置 / 下载本地接入包”入口。
- 后续增加 OpenClaw、ArkClaw、JVS 的 provider adapter，但保持同一组 MCP tool schema。
