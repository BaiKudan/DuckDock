# DuckDock Analysis Worker Protocol

## 目标

DuckDock 核心服务只负责接收 Reporter 上传、校验 MinIO 对象、生成分析任务和保存精简结果。原始日报、周报、会话、记忆、证据包长期保留在 MinIO；MySQL 只保存必要元数据、任务状态和长期记忆候选。

专属 OpenClaw worker 是异步分析执行方。它不持有 MinIO 密钥，只持有 DuckDock 发放的 `dkr_worker_*` token，通过 DuckDock 获取一次性下载 URL 和一次性结果上传 URL。

## 基础流程

1. OpenClaw / WorkBuddy / ArkClaw 侧安装 `duckdock_reporter` skill。
2. Reporter 周期性打包日报或周报，向 DuckDock 创建上传会话。
3. Reporter 使用 DuckDock 返回的 presigned PUT URL 直传 MinIO。
4. Reporter 调用 finalize，DuckDock 校验大小、sha256 和对象存在性。
5. DuckDock 创建 `report_analysis_jobs`，状态为 `pending`。
6. 一个或多个 OpenClaw worker 调用 `/api/v1/analysis/jobs/lease` 抢占任务。
7. Worker 下载原始包，按标准 SOP 分析资产、工作历程、上下文、风险和交接信号。
8. Worker 上传标准结果文件到 DuckDock 给出的结果 URL。
9. Worker 调用 finalize，DuckDock 校验结果文件 sha256/size，保存结果文件索引与 `memory_candidates` 等精简结果。

## Worker API

### 管理员创建 worker

`POST /api/v1/analysis/workers`

返回值里只出现一次 `token`，后续无法找回，只能重建 worker token。

### Worker 领取任务

`POST /api/v1/analysis/jobs/lease`

鉴权：`Authorization: Bearer dkr_worker_<prefix>_<secret>`

请求体：

```json
{
  "lease_seconds": 1800,
  "worker_name": "openclaw-worker-01",
  "capabilities_json": {
    "runtime": "openclaw",
    "skill": "duckdock_analysis_worker",
    "model": "local-or-cloud-model"
  }
}
```

响应包含：

- `download_url`：原始 report pack 的一次性 GET URL
- `result_upload_url`：兼容字段，指向主结果 `analysis-result.json` 的一次性 PUT URL
- `result_object_key`：兼容字段，主结果对象地址
- `result_uploads`：标准结果文件集的一次性 PUT URL
- `job`：任务、运行时、输入对象和状态元数据

第一阶段标准结果文件：

| 文件 | 类型 | 用途 |
| --- | --- | --- |
| `analysis-result.json` | `analysis_result` | 本次分析的总摘要、版本、统计、处理日志索引 |
| `asset-cards.json` | `asset_cards` | 资产卡片候选，后续用于归并到控制平面资产 |
| `worktrace-summary.md` | `worktrace_summary` | 工作历程摘要，适合人审和交接阅读 |
| `memory-candidates.json` | `memory_candidates` | 长期记忆候选的完整来源说明 |
| `handover-signals.json` | `handover_signals` | 离职、岗位交接、项目交接相关信号 |

字段约定见 [`analysis-result-schema.md`](./analysis-result-schema.md)。

### Worker 心跳

`POST /api/v1/analysis/jobs/{job_id}/heartbeat`

用于长任务续租。第一阶段只刷新 `lease_expires_at` 和 worker 活跃时间。

### Worker 完成任务

`POST /api/v1/analysis/jobs/{job_id}/finalize`

请求体：

```json
{
  "result_sha256": "64-char-sha256",
  "result_size_bytes": 1024,
  "summary_json": {
    "asset_count": 12,
    "worktrace_count": 48,
    "risk_count": 2
  },
  "result_artifacts": [
    {
      "kind": "analysis_result",
      "filename": "analysis-result.json",
      "object_key": "analysis/2026/05/21/rpt_xxx/job-1/analysis-result.json",
      "content_type": "application/json",
      "sha256": "64-char-sha256",
      "size_bytes": 1024
    },
    {
      "kind": "worktrace_summary",
      "filename": "worktrace-summary.md",
      "object_key": "analysis/2026/05/21/rpt_xxx/job-1/worktrace-summary.md",
      "content_type": "text/markdown",
      "sha256": "64-char-sha256",
      "size_bytes": 2048
    }
  ],
  "memory_candidates": [
    {
      "candidate_type": "asset_summary",
      "subject_type": "skill",
      "subject_key": "runtime-local:skill/foo",
      "title": "Foo skill",
      "summary": "用于处理项目 A 的周报生成。",
      "confidence": 0.86,
      "sensitivity": "internal",
      "payload_json": {
        "owner_hint": "employee",
        "project": "project-a"
      }
    }
  ]
}
```

### Worker 标记失败

`POST /api/v1/analysis/jobs/{job_id}/fail`

`retryable=true` 且未超过 `max_attempts` 时任务回到 `pending`；否则任务和上传会话标记为 failed。

### 队列指标

`GET /api/v1/analysis/queue/metrics`

鉴权：管理员或具备资产读取权限的用户。

该接口返回全量队列指标,不受 `/analysis/jobs` 明细列表 200 条限制。核心字段：

| 字段 | 含义 |
|---|---|
| `status_counts` | 各 analysis job 状态计数。 |
| `worker_status_counts` | 各 worker token 状态计数。 |
| `backlog_count` | `pending` + 可重试的过期租约。 |
| `online_worker_count` | 最近心跳仍在线的逻辑 worker token 数。 |
| `expired_lease_count` | 已超过 `lease_expires_at` 的 leased/running 任务。 |
| `oldest_pending_seconds` | 最老 pending 任务等待秒数。 |
| `queued_per_online_worker` | 积压量除以在线逻辑 worker 数。 |
| `saturation_level` | `healthy` / `watch` / `saturated`。 |

## OpenClaw API 接入评估

OpenClaw 官方文档已经暴露 Gateway HTTP API、OpenAI-compatible API、OpenResponses API、单次工具调用 API、cron、skills 和 `openclaw backup create`。这些能力足以支撑“把 OpenClaw 作为 DuckDock worker 客户端”。

第一阶段不把 DuckDock 绑定到 OpenClaw 的内部执行 API。原因是 ArkClaw、WorkBuddy、JVS 或线下定制 OpenClaw 的 API 面不一定完全一致。DuckDock 先稳定自己的 worker 协议，OpenClaw worker 只需要能执行一个定时/常驻 skill 并调用 HTTP API 即可。

第二阶段再做 `openclaw-worker-adapter`：

- 如果 OpenClaw Gateway HTTP API 可用，由 DuckDock 或运维脚本向 Gateway 下发“执行 duckdock_analysis_worker SOP”的任务。
- 如果衍生版只支持对话/技能安装，则用 SOP 方式让使用者或数字员工完成部署。
- 如果需要多 worker 并行，则每个 OpenClaw 实例持有独立 `dkr_worker_*` token，靠 DuckDock lease 机制自然并发。

## 长期记忆路线

第一阶段不引入独立向量数据库。先用 MySQL 保存结构化 `memory_candidates`，并用 `analysis_result_artifacts` 保存完整分析结果文件索引。管理员或员工可以确认、排除、补充。等候选记忆稳定后，再把“已确认记忆” materialize 到正式资产、工作历程或长期记忆表。

如果后续需要语义检索，再把 pgvector、Qdrant、Milvus 作为可选组件接入，而不是作为 DuckDock 单机部署的默认依赖。
