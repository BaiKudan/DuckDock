# DuckDock Analysis Worker 生产运行说明

## 定位

Analysis Worker 是 DuckDock 异步分析层的执行节点。它不保存 MinIO 密钥，只通过 DuckDock API 领取任务、获取一次性下载 URL、上传标准结果文件，并提交完成状态。

核心服务默认不自动启动 Analysis Worker。管理员应先在 `/analysis` 页面创建 Worker token，再按需启动一个或多个 Worker。

## Docker Compose 启动

1. 启动核心服务。开发/本地 compose 可直接用：

```bash
docker compose up -d mysql redis minio backend frontend worker beat
```

生产用 SOPS/age 入口：

```bash
export SOPS_AGE_KEY_FILE=/etc/duckdock/duckdock-prod-age.txt
bash scripts/prod.sh up
```

2. 登录 DuckDock 管理端，打开 `/analysis`，点击“新建 Worker”，复制 token。

3. 写入 `.env`。

```env
DUCKDOCK_ANALYSIS_API_BASE=http://backend:8801/api/v1
DUCKDOCK_ANALYSIS_WORKER_TOKEN=dkr_worker_xxx_xxx
DUCKDOCK_ANALYSIS_WORKER_NAME=DuckDock Compose Analysis Worker
DUCKDOCK_ANALYSIS_WORKER_POLL_SECONDS=60
DUCKDOCK_ANALYSIS_WORKER_LEASE_SECONDS=1800
DUCKDOCK_ANALYSIS_WORKER_WORK_DIR=/tmp/duckdock-analysis-worker
DUCKDOCK_ANALYSIS_MODE=baseline
```

## LLM Worker 模式

默认 `DUCKDOCK_ANALYSIS_MODE=baseline`，只使用确定性解析器，不调用外部模型。

如果希望使用轻量 LLM Worker 做总结、归类和交接判断，设置：

```env
DUCKDOCK_ANALYSIS_MODE=llm
DUCKDOCK_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DUCKDOCK_LLM_MODEL=qwen3.7-max
DUCKDOCK_LLM_ENABLE_THINKING=true
DUCKDOCK_LLM_TIMEOUT_SECONDS=90
DUCKDOCK_LLM_MAX_INPUT_CHARS=24000
DUCKDOCK_LLM_API_KEY=通过服务器密钥管理注入，不写入仓库
```

也可以使用 `DUCKDOCK_ANALYSIS_MODE=auto`：配置了 `DUCKDOCK_LLM_API_KEY`、`DASHSCOPE_API_KEY` 或 `SKILL_GEN_API_KEY` 时走 LLM，否则自动回退到 baseline。

Worker 只把压缩后的 pack 摘要、baseline 抽取结果和已脱敏字段发给模型。标准输出仍然是 `analysis-result.json`、`asset-cards.json`、`worktrace-summary.md`、`memory-candidates.json`、`handover-signals.json` 这 5 个文件；原始包和完整结果仍保存在 MinIO，MySQL 只保存精简索引。

4. 启动可选 profile。开发/本地 compose：

```bash
docker compose --profile analysis-worker up -d analysis-worker
```

生产：

```bash
export SOPS_AGE_KEY_FILE=/etc/duckdock/duckdock-prod-age.txt
bash scripts/prod.sh worker
```

5. 扩容 Worker。

```bash
docker compose --profile analysis-worker up -d --scale analysis-worker=3 analysis-worker
```

`--scale` 会复用同一个 worker token,适合临时扩吞吐；控制台会把它们视为一个逻辑 Worker。需要逐副本禁用、逐副本审计或混用不同模型配置时,请为每个副本创建独立 token 并用不同 service/env 启动。

## 队列指标与饱和判断

管理端 `/analysis` 顶部指标来自：

```http
GET /api/v1/analysis/queue/metrics
```

返回字段包括：

- `backlog_count`: 当前待处理量,等于 `pending` 加上可重试的过期租约。
- `online_worker_count`: 最近心跳仍在线的逻辑 Worker token 数。
- `expired_lease_count`: 租约已过期但尚未被下一次 lease/reaper 回收的任务数。
- `oldest_pending_seconds`: 最老 pending 任务等待时间。
- `queued_per_online_worker`: 积压量除以在线逻辑 Worker 数。
- `saturation_level`: `healthy`、`watch` 或 `saturated`。

处理建议：

| 信号 | 处理 |
|---|---|
| `backlog_count > 0` 且 `online_worker_count = 0` | 启动 Worker 或检查 token/网络。 |
| `expired_lease_count > 0` | 查看 Worker 日志；下一次 lease 或 beat reaper 会回收。 |
| `queued_per_online_worker >= 5` | 临时增加 Worker 副本或降低 LLM 耗时。 |
| `oldest_pending_seconds >= 600` | 检查 MinIO URL、LLM key、worker 日志和 `/analysis` 失败详情。 |

当前默认仍使用 DuckDock 自有 MySQL lease 队列和 Celery beat reaper。Temporal/Prefect 暂不引入；只有当后续分析流程变成多步骤长事务、需要步骤级恢复/补偿/可视化 DAG 时再评估。

## MinIO URL 要求

`MINIO_PUBLIC_ENDPOINT` 必须同时被用户侧 Reporter 和 Analysis Worker 访问到。不要把它理解成“公网”二字，而是“签名 URL 的访问端点”。

常见配置：

| 场景 | 建议值 |
|---|---|
| 真实生产 | `minio.company.example.com` 或网关域名 |
| 单机局域网 | 服务器 LAN IP 或内网 DNS |
| Docker Desktop 本机调试 | `host.docker.internal:9000` |
| 只在宿主机脚本运行 Worker | `localhost:9000` |

如果 `MINIO_PUBLIC_ENDPOINT=localhost:9000`，容器内 Worker 访问到的是 Worker 容器自己，不是宿主机 MinIO。这种配置只适合 Worker 也跑在宿主机进程里。

## 管理运维

管理员可以在 `/analysis` 执行：

- 禁用 Worker：吊销该 Worker token，正在处理的任务会释放回队列；如果任务已耗尽最大尝试次数，则标记失败。
- 取消任务：终止未完成任务，上传会话标记为失败，collection job 标记为 cancelled。
- 重试任务：失败或取消任务重新入队，并默认清零 attempts。
- 打开结果文件：DuckDock 生成一次性下载 URL，不暴露对象存储密钥。

## 测试

后端状态机测试位于 `backend/tests/test_analysis_service.py`，覆盖：

- failed job retry
- running job cancel
- worker disable 后任务回队列 / 失败
- disabled worker token 失效
- terminal job 不允许取消
- worker lease 后返回 5 个标准结果文件上传 URL
- heartbeat 后 retryable fail 释放租约并回队列
- finalize 保留 worker summary 计数并触发 materialize

本地执行：

```bash
cd backend
python -m pytest
```

CI 已安装 `backend/requirements-dev.txt` 并运行 `python -m pytest`。
