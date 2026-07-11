# DuckDock 运维手册

## 1. 部署目标

当前部署模型按“单台/少量服务器 + Docker Compose”设计。生产入口以 [`docs/production-deployment.md`](./production-deployment.md) 和 `scripts/prod.sh` 为准；本文覆盖本地/内网运维、组件管理、排障和备份恢复原则。

核心服务：

- MySQL 8：业务数据库。
- Redis：Celery 和短期队列状态。
- MinIO：Registry 包、Reporter 上传包、分析结果制品。
- FastAPI backend：业务 API。
- Celery worker：扫描、发布、后台任务。
- Celery beat：周期任务、lease/reaper 和 GC 调度；生产/开发都应只有一个 beat。
- Frontend：React/Vite 前端。

可选组件：

- Analysis Worker：专属 OpenClaw / Worker，用于异步分析 Reporter 上传包。
- Langfuse：Clinic / LLM trace 观测组件，默认不作为核心能力强制开放。

## 2. 环境变量

本地开发/验证复制模板：

```powershell
Copy-Item .env.example .env
```

生产不要使用裸 `.env` 作为事实来源。生产按下面流程生成 `.env.prod.enc`：

```bash
cp .env.prod.example .env.prod
$EDITOR .env.prod
SOPS_AGE_KEY_FILE=/etc/duckdock/duckdock-prod-age.txt sops --encrypt .env.prod > .env.prod.enc
rm -f .env.prod
bash scripts/prod.sh preflight
```

本地或生产都必须修改的关键项：

```env
SECRET_KEY=<openssl rand -hex 32>
MYSQL_PASSWORD=<strong password>
MYSQL_ROOT_PASSWORD=<strong password>
MINIO_ROOT_PASSWORD=<strong password>
MINIO_SECRET_KEY=<strong password>
BACKEND_BASE_URL=https://duckdock.example.com
FRONTEND_BASE_URL=https://duckdock.example.com
MINIO_PUBLIC_ENDPOINT=minio.duckdock.example.com
CORS_ORIGINS=["https://duckdock.example.com"]
```

关键说明：

- `DATABASE_URL` 是后端实际使用的 MySQL 连接串。
- `MINIO_ENDPOINT` 是后端访问对象存储的地址。
- `MINIO_PUBLIC_ENDPOINT` 是签名 URL 中暴露给 Reporter 和 Analysis Worker 的地址，必须被它们访问到。
- `BACKEND_BASE_URL` 用于生成 registry/SOP 中的 API 地址。
- `FRONTEND_BASE_URL` 用于前端跳转和 SSO 回调。

本地自定义域名示例：

```text
127.0.0.1 duckdock.test
BACKEND_BASE_URL=http://duckdock.test:8990
FRONTEND_BASE_URL=http://duckdock.test:5175
MINIO_PUBLIC_ENDPOINT=duckdock.test:9000
```

如果本机开启系统代理，需要把 `duckdock.test`、`127.0.0.1`、`localhost` 加入代理绕过列表。

## 3. 启动核心服务

本地/dev 核心服务启动：

```powershell
docker compose up -d mysql redis minio backend worker beat frontend
```

生产核心栈启动：

```bash
export SOPS_AGE_KEY_FILE=/etc/duckdock/duckdock-prod-age.txt
bash scripts/prod.sh up
```

查看状态：

```powershell
docker compose ps
```

健康检查：

```powershell
curl http://127.0.0.1:8801/health
curl http://127.0.0.1:5174
curl http://127.0.0.1:9000/minio/health/live
```

本地开发也可以使用脚本：

```powershell
.\scripts\dev-start.ps1
.\scripts\dev-status.ps1
.\scripts\dev-stop.ps1
```

## 4. 组件管理

### 4.1 Langfuse

Langfuse 是可选观测组件。管理员可以在 DuckDock 控制台“组件管理”中启动或停止。

命令等价于：

```powershell
docker compose --profile observability up -d postgres clickhouse langfuse-db-init langfuse-minio-init langfuse-worker langfuse-web
docker compose --profile observability stop langfuse-web langfuse-worker clickhouse postgres
```

组件说明：

- `postgres`：Langfuse 自己的数据存储，不是 DuckDock 业务库。
- `clickhouse`：Langfuse 事件和 trace 分析库。
- `langfuse-db-init`：一次性初始化 Langfuse database。
- `langfuse-minio-init`：一次性初始化 Langfuse bucket。
- `langfuse-worker`：Langfuse 后台处理。
- `langfuse-web`：Langfuse Web 控制台。

这组服务与 DuckDock 核心业务解耦。核心功能不依赖 Langfuse。

### 4.2 Analysis Worker

Analysis Worker 是异步分析层，可以单个或多个并行部署。

启动前先在管理员控制台创建 Worker token，然后写入：

```env
DUCKDOCK_ANALYSIS_API_BASE=http://backend:8801/api/v1
DUCKDOCK_ANALYSIS_WORKER_TOKEN=dkr_worker_xxx_xxx
DUCKDOCK_ANALYSIS_WORKER_NAME=DuckDock Compose Analysis Worker
```

本地/dev 启动：

```powershell
docker compose --profile analysis-worker up -d analysis-worker
```

生产启动：

```bash
export SOPS_AGE_KEY_FILE=/etc/duckdock/duckdock-prod-age.txt
bash scripts/prod.sh worker
```

扩容：

```powershell
docker compose --profile analysis-worker up -d --scale analysis-worker=3 analysis-worker
```

生产直接扩容时必须显式带 `--profile analysis-worker` 和解密后的临时 env；详见 `production-deployment.md` 的 Analysis Worker 章节。

`--scale` 复用同一个 token 时,吞吐会扩容,但控制台会显示为一个逻辑 Worker。若需要逐副本禁用、审计或区分模型配置,请在 `/analysis` 为每个副本创建独立 token,并用不同 env/service name 启动。

队列饱和观测：

```powershell
curl -H "Authorization: Bearer <admin-jwt>" http://localhost:8990/api/v1/analysis/queue/metrics
```

关键字段：

- `backlog_count`: `pending` + 可重试的过期租约。
- `online_worker_count`: 最近心跳仍在线的逻辑 Worker token 数。
- `expired_lease_count`: 租约已过期但尚未被下一次 lease/reaper 回收的任务。
- `queued_per_online_worker`: 每个在线逻辑 Worker 的积压量。
- `saturation_level`: `healthy` / `watch` / `saturated`。

Worker 不持有 MinIO 密钥，只通过 DuckDock API 领取任务和一次性下载/上传 URL。当前默认继续使用 DuckDock 自有 lease 协议和 Celery beat reaper；只有当分析流程演进成多步骤、长事务、需要步骤级补偿恢复时,才评估 Temporal/Prefect。

## 5. Reporter 上传链路运维

标准链路：

```text
WorkBuddy / OpenClaw / ArkClaw
  -> POST /reports/upload-sessions
  -> PUT presigned MinIO URL
  -> POST /reports/{report_id}/finalize
  -> ReportAnalysisJob pending
  -> Analysis Worker lease/download/analyze/upload/finalize
  -> MySQL materialized indexes
```

排查顺序：

1. `report_upload_sessions`
   - 是否有记录。
   - `status` 是否从 `pending` 到 `uploaded` 或 `succeeded`。
   - `actual_sha256` 和 `actual_size_bytes` 是否存在。

2. MinIO
   - `object_key` 是否存在。
   - 对象大小是否和 DB 一致。

3. `report_analysis_jobs`
   - 是否创建 pending job。
   - 是否被 Worker lease。
   - 是否 succeeded / failed / cancelled。

4. `analysis_result_artifacts`
   - 是否有五类结果文件。

5. 结构化表
   - `ai_assets`
   - `asset_ownerships`
   - `runtime_bindings`
   - `work_traces`
   - `memory_candidates`

## 6. 备份与恢复

### 6.1 必备备份

必须备份：

- MySQL 数据卷：`mysql_data`
- MinIO 数据卷：`minio_data`
- Git repo volume：`repos_data`
- `.env`，但必须放在安全密钥库或加密备份中

可选备份：

- Redis：通常可以重建，但若队列中有关键任务，应在维护窗口停写后备份。
- Langfuse 相关卷：仅当启用观测组件并需要保留 trace 时备份。

### 6.2 恢复顺序

1. 恢复 `.env`。
2. 恢复 MySQL。
3. 恢复 MinIO。
4. 恢复 Git repo volume。
5. 启动核心服务。
6. 检查 `/health`。
7. 抽查 Registry、Reporter 上传包和分析结果下载链接。

## 7. 安全运维

- Reporter Credential 按 runtime/user/device 发放，测试完成后撤销；legacy report token 仅作运维兜底。
- Analysis Worker token 按 worker 发放，泄露后禁用 Worker。
- 不在文档、截图、Git、自动化 prompt 中保留长期 token。
- MinIO root 密钥不要下发给 Reporter 或 Analysis Worker。
- Reporter 上传包默认不包含完整会话原文。
- Analysis Worker 只处理 DuckDock 分配的对象，不直接扫描员工机器。

## 8. 常见故障

### Reporter 上传 URL 访问失败

检查：

- `MINIO_PUBLIC_ENDPOINT` 是否能被 Reporter 所在机器访问。
- 域名是否被代理劫持。
- 本地验证时是否需要 `NO_PROXY` 或代理绕过。

### finalize 失败

检查：

- 上传对象是否真的存在。
- `size_bytes` 是否一致。
- `sha256` 是否一致。
- Reporter Credential 或 legacy report token 是否属于该 runtime，且未被撤销/过期。

### 上传成功但员工看不到资产

检查：

- 分析任务是否 succeeded。
- `asset_ownerships` 是否生成。
- 分析结果里是否包含 `owner`、`owner_username`、`owner_email`、`created_by`、`actor_username` 或 `actor_email`。
- 员工账号邮箱和上报包中的 email 是否一致。

### Analysis Worker 不处理任务

检查：

- Worker token 是否有效。
- Worker 是否能访问 DuckDock API。
- Worker 是否能访问 `MINIO_PUBLIC_ENDPOINT` 生成的一次性下载 URL。
- `report_analysis_jobs` 是否处于 `pending`。

## 9. 发布前检查

```powershell
cd backend
python -m pytest

cd ..\frontend
npm run build
```

Compose 配置检查：

```powershell
docker compose config --services
docker compose --profile analysis-worker config --services
docker compose --profile observability config --services
```
