# DuckDock 专属 OpenClaw Worker 领取与归档流程

## 目标

DuckDock 的核心服务不直接做重模型分析，只负责上传会话、对象存储、任务分配、状态机、审计和精简结果入库。专属 OpenClaw Worker 是异步分析执行方：它从 DuckDock 领取已经上传到 MinIO 的 report pack，按标准 SOP 处理后，把完整分析结果写回 MinIO，再通知 DuckDock 入库精简索引。

这个设计的关键边界是：

- 用户侧 OpenClaw / WorkBuddy / ArkClaw 负责周期性 Reporter 上报。
- DuckDock 负责授权、排队、一次性 URL、状态和归档索引。
- 专属 OpenClaw Worker 只处理 DuckDock 分配的包，不扫描员工电脑，也不持有 MinIO 密钥。

## 角色

| 角色 | 持有凭证 | 主要职责 |
| --- | --- | --- |
| Reporter runtime | `dkr_report_*` runtime token | 生成日报/周报包，创建上传会话，直传 MinIO，finalize |
| DuckDock API | MinIO / MySQL / Redis 服务端配置 | 校验上传包，创建分析任务，生成一次性下载和上传 URL，写入精简索引 |
| OpenClaw Worker | `dkr_worker_*` worker token | 领取分析任务，下载包，执行分析 SOP，上传结果文件，finalize 或 fail |
| 管理员 | DuckDock admin 账号 | 创建 / 禁用 worker token，取消 / 重试任务，审核 memory candidates |

## 主流程

1. Reporter finalize 成功后，DuckDock 创建一条 `report_analysis_jobs`，状态为 `pending`。
2. 一个或多个 OpenClaw Worker 调用 `POST /api/v1/analysis/jobs/lease`。
3. DuckDock 在事务里选择可执行任务：
   - `status = pending`；
   - 或者 `status in (leased, running)` 且 `lease_expires_at` 已过期；
   - `attempts < max_attempts`；
   - 按 `priority desc, created_at asc` 排序；
   - 使用 `FOR UPDATE SKIP LOCKED` 避免多个 worker 抢到同一条任务。
4. DuckDock 将任务标记为 `leased`，写入 `worker_id / lease_owner / lease_expires_at`，并把 `attempts + 1`。
5. DuckDock 返回：
   - 原始 report pack 的一次性 `download_url`；
   - 5 个标准结果文件的一次性 PUT URL；
   - `result_object_key` 和任务元数据。
6. Worker 下载 report pack，解包并执行分析 SOP。
7. 长任务期间 Worker 调用 `POST /api/v1/analysis/jobs/{job_id}/heartbeat`，任务状态进入 `running` 并续租。
8. Worker 产出并上传标准结果文件：
   - `analysis-result.json`
   - `asset-cards.json`
   - `worktrace-summary.md`
   - `memory-candidates.json`
   - `handover-signals.json`
9. Worker 调用 `POST /api/v1/analysis/jobs/{job_id}/finalize`，提交结果文件 object key、sha256、size 和摘要。
10. DuckDock 重新读取 MinIO 对象头和 sha256，校验通过后：
    - `report_analysis_jobs.status = succeeded`
    - `report_upload_sessions.status = succeeded`
    - `collection_jobs.status = succeeded`
    - `analysis_result_artifacts` 记录完整结果文件索引
    - `ai_assets / asset_ownerships / runtime_bindings / work_traces / memory_candidates` 写入精简结构化结果。

## Worker 分析产物

第一版基线 Worker 会解析 report pack 中的 `inventory/*.json|*.ndjson` 和 `summaries/*.md|*.txt`。当前识别的资产类型包括：

- `skill`
- `agent`
- `prompt`
- `workflow`
- `mcp`
- `tool`
- `knowledge_base`
- `scheduled_task`
- `credential_ref`
- `workspace`
- `other`

其中基线脚本默认从以下 inventory 组生成资产卡片：

- `skills`
- `agents`
- `prompts`
- `workflows`
- `tools`
- `mcp`
- `memories`
- `memory`
- `knowledge_bases`
- `scheduled_tasks`

后续如果 OpenClaw 使用更强的模型 SOP，只需要保持同一套结果文件协议，不需要改 DuckDock 的任务分配协议。

## 状态机

### `report_analysis_jobs`

```text
pending
  -> leased
  -> running
  -> succeeded

leased / running
  -> pending   retryable fail 且未超过 max_attempts
  -> failed    不可重试或超过 max_attempts
  -> cancelled 管理员取消
```

### `report_upload_sessions`

```text
uploaded
  -> ingesting
  -> succeeded

ingesting
  -> uploaded retryable fail
  -> failed   terminal fail / cancel
```

### `collection_jobs`

```text
pending
  -> running
  -> succeeded

running
  -> pending   retryable fail
  -> failed    terminal fail
  -> cancelled admin cancel
```

## 失败与重试

Worker 调用 `POST /api/v1/analysis/jobs/{job_id}/fail` 时：

- `retryable=true` 且 `attempts < max_attempts`：任务回到 `pending`，上传会话回到 `uploaded`，collection job 回到 `pending`，并释放 `worker_id / lease_owner / lease_expires_at`。
- `retryable=false` 或已达到最大尝试次数：任务、上传会话和 collection job 标记为失败。

管理员禁用 Worker 时：

- 该 worker 的 token 立即失效。
- 未耗尽尝试次数的 leased/running job 回队列。
- 已耗尽尝试次数的 job 标记失败。

## 并行策略

多 Worker 并行不需要额外队列组件。每个 Worker 使用自己的 `dkr_worker_*` token 调用同一个 lease API。DuckDock 依靠数据库事务、行锁、lease 超时和幂等 finalize 控制并发。

推荐部署方式：

```powershell
docker compose --profile analysis-worker up -d --scale analysis-worker=3 analysis-worker
```

如果使用多个专属 OpenClaw 实例，也应为每个实例创建独立 Worker token，便于禁用、审计和定位失败。

## OpenClaw 接入方式

第一阶段不强依赖 OpenClaw 内部私有 API。原因是原生 OpenClaw、ArkClaw、WorkBuddy、JVS 和线下定制版本的 API 面不一定一致。DuckDock 只要求 worker runtime 能做到：

- 安装 `duckdock-analysis-worker` skill 或执行等价 SOP。
- 持有 `DUCKDOCK_API_BASE` 和 `DUCKDOCK_WORKER_TOKEN`。
- 能访问 DuckDock API。
- 能访问 DuckDock 返回的一次性 MinIO URL。
- 能周期运行或常驻轮询。

当前仓库提供了基线脚本：

```powershell
python docs/agent-control-plane-prd/duckdock-analysis-worker/scripts/duckdock_analysis_worker.py `
  --api-base https://duckdock.example.com/api/v1 `
  --worker-token dkr_worker_xxx_xxx `
  --analysis-mode baseline `
  --once
```

## 轻量 LLM Worker 路线

如果不希望把专属 OpenClaw 作为分析执行器，可以直接使用当前脚本的 `llm` 模式。它仍然使用同一套 lease / heartbeat / result upload / finalize 协议，只把“分析步骤”替换为 OpenAI-compatible 模型调用。

推荐配置：

```env
DUCKDOCK_ANALYSIS_MODE=llm
DUCKDOCK_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DUCKDOCK_LLM_MODEL=qwen3.7-max
DUCKDOCK_LLM_ENABLE_THINKING=true
DUCKDOCK_LLM_API_KEY=由服务器密钥管理注入
```

该模式适合作为第一版生产实现：部署轻、协议稳定、可横向扩容，也不依赖 OpenClaw / WorkBuddy 的私有内部 API。后续如果需要更强的工具调用、浏览器执行或复杂证据链验证，再把同一个 worker 协议接到专属 OpenClaw runtime。

生产运行建议通过 compose profile：

```powershell
docker compose --profile analysis-worker up -d analysis-worker
```

## 已落地的代码位置

- API：`backend/app/api/v1/endpoints/analysis.py`
- 任务状态服务：`backend/app/services/analysis_service.py`
- 结果入库：`backend/app/services/analysis_materializer.py`
- 数据模型：`backend/app/models/control_plane.py`
- 基线 Worker：`docs/agent-control-plane-prd/duckdock-analysis-worker/scripts/duckdock_analysis_worker.py`
- 测试：`backend/tests/test_analysis_service.py`

## 当前实现结论

这条链路已经具备生产化雏形：Worker 不接触 MinIO 密钥，任务可并行领取，失败可回队列，结果文件保留在 MinIO，MySQL 只保存精简索引和长期记忆候选。后续真正需要增强的是 Worker 的模型分析 SOP，而不是 DuckDock 的任务分配协议本身。
