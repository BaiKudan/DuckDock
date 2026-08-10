# Reporter 集成验收指南

本文用于验证 Hermes、WorkBuddy 或其他员工侧 Agent 通过 DuckDock Reporter 接口完成结构化日常上报与交接材料归档。它是一份可复现的验收流程，不记录任何部署环境专属的用户、runtime、credential、对象键或任务 ID。

## 1. 验收范围

需要分别验证两条数据路径：

1. **日常报告**：Reporter 直接提交 `duckdock-structured-report-v1` JSON，DuckDock 轻量校验并物化为工作历程、资产和候选信号。
2. **交接或审计材料**：Reporter 生成最小 `duckdock-pack-v1.zip`，通过一次性签名 URL 上传 MinIO，再由 analysis-worker 标准化处理并入库。

服务端不主动扫描员工设备。Reporter 只上传企业允许的结构化摘要、资产索引和必要证据；完整私密会话、密钥文件和浏览器数据不在默认采集范围内。

## 2. 前置条件

- DuckDock 核心栈健康：backend、worker、beat、MySQL、Redis、MinIO 和 frontend。
- 数据库已执行 `alembic upgrade head`。
- 测试员工账号可登录，并有权调用 `/api/v1/reporters/enroll`。
- analysis-worker 已启用；验证真实模型时还需配置 `DUCKDOCK_LLM_*`。
- 所有示例均使用占位地址 `https://duckdock.example.com`，执行时替换为目标环境地址。

建议在 disposable dev/staging 环境执行，不要用生产用户或生产凭证做首次验收。

## 3. Reporter 登记与凭证

员工登录后调用：

```http
POST /api/v1/reporters/enroll
Content-Type: application/json
Authorization: Bearer <employee-access-token>
```

请求示例：

```json
{
  "runtime_name": "Employee WorkBuddy",
  "provider": "workbuddy",
  "agent_kind": "workbuddy",
  "device_id": "workstation-01"
}
```

验收要求：

- 返回 runtime 信息和一次性显示的 `dkr_report_*` credential。
- credential 只写入 Reporter 本地 `0600` 配置文件，不进入 prompt、日志、截图或仓库。
- 后端只保存 credential hash；管理端只能看到前缀和状态。
- rotate 后旧 credential 失效，revoke 后所有后续请求返回未授权。

## 4. 心跳验收

使用 Reporter Credential 调用：

```http
POST /api/v1/reporters/heartbeat
Authorization: Bearer <reporter-credential>
Content-Type: application/json
```

至少提交 runtime 版本、设备标识、Reporter 版本和健康摘要。验收要求：

- runtime 的 `last_seen_at` 更新。
- credential 的最近使用时间更新。
- 管理后台显示 Reporter 为 healthy。
- 使用其他 runtime 的 credential 不得更新当前 runtime。

## 5. 结构化日报/周报

日常报告应调用 `/api/v1/reports/structured`，不经过 MinIO：

```json
{
  "runtime_id": 42,
  "schema_version": "duckdock-structured-report-v1",
  "report_type": "weekly",
  "title": "Weekly delivery report",
  "summary": "Completed planned delivery and documented follow-up risks.",
  "period_start": "2026-01-05T00:00:00Z",
  "period_end": "2026-01-09T23:59:59Z",
  "idempotency_key": "weekly-2026-01-05",
  "highlights": ["Delivered the scheduled milestone"],
  "blockers": ["External dependency requires confirmation"],
  "next_actions": ["Confirm the dependency owner and due date"],
  "project_refs": ["example-project"],
  "asset_refs": [
    {
      "external_id": "example-project/skill/example-skill",
      "name": "example-skill",
      "asset_type": "skill",
      "description": "Reusable workflow for the example project",
      "criticality": "medium",
      "metadata_json": {"version": "1.0.0"}
    }
  ]
}
```

`runtime_id` 使用 enroll 返回的实际 runtime ID；示例中的 `42` 只是占位值。

验收要求：

- API 返回成功且包含 report/collection 标识。
- `CollectionJob` 到达终态。
- `WorkTrace`、`AIAsset` 和可选 `MemoryCandidate` 按 schema 物化。
- 重放同一幂等键不会生成重复记录。
- schema、日期区间或字段类型错误返回明确的 4xx，不进入重型 LLM 修复路径。
- 管理员运行时详情和员工工作台均可看到报告时间线。

## 6. 交接与审计包

仅在需要完整归档材料时使用 pack 路径：

1. Reporter 生成 `duckdock-pack-v1.zip`。
2. 调用上传会话接口获取一次性签名 PUT URL。
3. 直接上传到 MinIO。
4. 调用 finalize，提交 size 与 SHA-256。
5. analysis-worker lease 任务、下载包、校验 manifest、生成标准结果并 finalize。
6. DuckDock 将结果物化到 MySQL，完整制品继续保留在 MinIO。

包内至少包含：

```text
manifest.json
summary.json
assets.json
work_traces.json
```

验收要求：

- 签名 URL 有短有效期，且不暴露 MinIO root credential。
- size/hash 不匹配时 finalize 返回 422，并将失败状态显式记录。
- 非归属 runtime 不得 finalize 或读取会话。
- worker lease 可重试；过期 lease 能被 reaper 回收。
- 模型不可用时 `ai_assist.mode=baseline` 且 `degraded=true`，不得伪装成模型结果。
- MySQL 只保存结构化索引和摘要，不复制完整原始包内容。

## 7. WorkBuddy Runtime MCP

仓库提供 `tools/duckdock-runtime-mcp/duckdock_runtime_mcp.py`。接入顺序：

```text
duckdock.runtime.detect
duckdock.reporter.install
duckdock.reporter.configure
duckdock.reporter.run_structured
duckdock.reporter.dry_run
duckdock.reporter.schedule
duckdock.reporter.status
```

验收要求：

- `detect` 能区分桥接接口可调用与 MCP 已注册两个状态。
- `configure` 写入的 credential 被遮蔽，配置文件权限为 `0600`。
- `run_structured` 完成 heartbeat 与结构化报告提交。
- `dry_run` 创建一次性自动化并可清理。
- 周期任务必须在 WorkBuddy 注册并重载 MCP 后验证至少一次真实触发。

## 8. Hermes Reporter

Hermes 可使用 `scripts/hermes_reporter_pilot.py` 作为参考实现。推荐默认提交结构化日报/周报，只有 handover/audit 场景显式启用 pack 模式。

从 `hermes-pilot-0.2.0` 起，每次真实 cron 会先以原生
`hermes-reporter` profile 完成 v2 handshake/heartbeat，再创建一个
metadata-only Session/Run；报告提交成功后关闭为 `SUCCEEDED/ENDED`，异常时关闭为
`FAILED/ABANDONED`。Run duration 由 DuckDock 服务端按持久化时间计算，pilot 不上传
客户端推导的 `duration_ms`。

验收时确认：

- runtime external ID 和 device ID 不包含用户名、主机名原文或硬编码设备型号。
- cron/调度器只引用受限权限配置文件。
- Reporter 日志不打印 credential、签名 URL query 或原始私密内容。
- Fleet 显示 `hermes-reporter`、`hermes-reporter-pilot`、DD-C1 与 healthy heartbeat。
- 每次真实触发都能关联一个终态 Session 和 Run，且 trust 为
  `CHANNEL_AUTHENTICATED/REPORTER`、capture mode 为 `metadata_only`。
- rotate/revoke 后调度任务能明确报错并停止重试风暴。

## 9. 失败与越权场景

至少覆盖：

- 空、过期、撤销和跨 runtime credential。
- 重复 heartbeat 与重复结构化报告。
- 非法 schema、超大 payload、错误 hash 和过期上传 URL。
- worker 中途退出、lease 超时、重复 finalize。
- LLM 超时、非 JSON 响应和 baseline 降级。
- 普通员工访问其他员工 runtime、资产、工作历程和分析结果。

所有失败都应产生明确状态和必要审计事件，不得静默丢弃。

## 10. 验收记录模板

环境专属结果应保存在部署方自己的受控记录系统，不提交到公共仓库：

| 字段 | 内容 |
|---|---|
| 日期 | YYYY-MM-DD |
| DuckDock commit/version | `<version>` |
| Reporter/MCP version | `<version>` |
| 测试环境 | dev / staging |
| 路径 | structured / pack / both |
| 结果 | pass / fail |
| 失败与修复 | `<summary>` |
| 证据位置 | `<private audit reference>` |

公共仓库只维护可复现步骤、自动化测试和不含环境身份信息的能力状态。
