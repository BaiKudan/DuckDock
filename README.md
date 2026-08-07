# DuckDock

> 企业 AI Agent 资产、Skills 注册与交接治理平台

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

DuckDock 把 **私有 Skills 注册表** · **Scanner + Sandbox 发布门禁** · **Clinic 质量诊所** · **Agent 控制平面（资产盘点 + 离职/项目交接）** 整合在同一个控制台里：基于 Git bare repo 做版本管理、MinIO 做 artifact 分发、OpenAI-compatible LLM 做质量评测与交接建议，并可选自托管 Langfuse 提供评测可观测性。

适合需要**自托管** AI Skill / Agent 资产管理与交接治理平台的团队：单租户部署（一企业一实例），强调审计可追溯、凭证安全、最小暴露、LLM 仅建议。

> **发布状态：`2.0.0-rc.1` 候选版。** 当前工程就绪结论见 [`docs/release-2.0-rc1-readiness.zh-CN.md`](docs/release-2.0-rc1-readiness.zh-CN.md)。仓库技术门禁不等于生产授权；正式上线仍需完成目标环境的安全审计、容量验证、备份恢复演练、监控接入和变更审批，并执行第 12 节清单与 [`SECURITY.md`](SECURITY.md)。

## 目录

- [1. 定位](#1-定位)
- [2. 默认端口](#2-默认端口)
- [3. 一键启动](#3-一键启动)
- [4. 架构总览](#4-架构总览)
- [5. 核心能力](#5-核心能力)
- [6. API 入口](#6-api-入口)
- [7. 数据库与迁移](#7-数据库与迁移)
- [8. 质量门禁与测试](#8-质量门禁与测试)
- [9. 环境变量](#9-环境变量)
- [10. 常见故障](#10-常见故障)
- [11. 文档索引](#11-文档索引)
- [12. 生产部署清单](#12-生产部署清单)
- [13. 开发与贡献](#13-开发与贡献)
- [14. 许可证](#14-许可证)

---

## 1. 定位

- **Namespace** 隔离 + RBAC（`admin / developer / readonly`）+ **Robot Account** 给 CI/CD 用；系统级权限键（`runtime.* / asset.* / handover.* / worktrace.* / evidence.*`）收口控制平面读写。
- **Git bare repo** 做 skill 版本存储（tag = version），`GET_LOCK()` 串行化同一 skill 的并发发布。
- **Scanner**（静态 + AI）+ **SandboxValidation**（Docker / SSH）+ **ReleaseGate**（governance policy）三道发布门禁。
- **Clinic** 多维度 LLM-judge 评测 · 可选自托管 **Langfuse** 做 trace observability。
- **Agent 控制平面**：运行时连接、资产盘点（Push 上报采集）、工作历程与证据、离职/项目**交接闭环**（建议 → 审批 → 执行 → 验收，全程审计 + 证据可追溯）。
- **企业协同**：审计日志、Webhook（HMAC-SHA256）、配额 & 保留策略、跨 namespace replication、ClawHub 公开分发。

> **设计红线**（见 [`.specify/memory/constitution.md`](.specify/memory/constitution.md)）：业务主库仅 MySQL 8.x；写入串行 + 幂等；测试先行；审计与证据不可绕过；敏感内容最小暴露 + 凭证加密 + LLM 仅出建议。

---

## 2. 默认端口

DuckDock 为本地开发提供一组稳定的默认 host 端口，统一记录在 [`docker-compose.yml`](docker-compose.yml) 和端口说明中：

| 服务 | Host 端口 | 容器端口 | profile | 对外 |
|---|---|---|---|---|
| Frontend (Vite) | **5174** | 5174 | default | 浏览器 |
| Backend (FastAPI) | **8801** | 8801 | default | 浏览器 / CLI |
| MySQL 8.x | **3307** | 3306 | default | 本机 · 主业务库 |
| Redis | **6379** | 6379 | default | celery db=0, langfuse db=1 |
| MinIO S3 API | **9000** | 9000 | default | 本机 + 签名 URL |
| MinIO Console | **9001** | 9001 | default | 本机 admin |
| PostgreSQL 16 | 5432 | 5432 | `observability` | 仅自托管 Langfuse |
| Langfuse Web | **3200** | 3000 | `observability` | 浏览器 |
| ClickHouse | — | 8123 | `observability` | 仅内网 |
| OTLP gRPC / HTTP | **4317 / 4318** | 4317 / 4318 | `telemetry` | 仅 127.0.0.1 |
| OTel health | **13133** | 13133 | `telemetry` | 仅 127.0.0.1 |

> 如遇端口冲突，只调整 host 侧映射；container 端口与服务内部 URL 保持不变。详见 [`docs/port-plan.zh-CN.md`](docs/port-plan.zh-CN.md)。`analysis-worker` 与 `observability` 是**可选 profile**，默认 `docker compose up` 不启用。

---

## 3. 一键启动

### 3.1 前置依赖

Python 3.12+ · Node.js 22.12–24.x · Docker / Docker Compose。

### 3.2 推荐：全栈 Compose（macOS / Linux / WSL）

```bash
bash scripts/dev.sh up
```

[`scripts/dev.sh`](scripts/dev.sh) 是维护中的一键脚本：自动从 `.env.example` 复制 `.env` → 起核心中间件（mysql / redis / minio）→ 等 MySQL 健康 → 跑 `alembic upgrade head` → 起 backend / worker / beat / frontend（热重载）。等价的手动方式：

```bash
cp .env.example .env          # 至少改 SECRET_KEY / MINIO_ROOT_PASSWORD / MYSQL_PASSWORD;
                              # 凭证功能需填 DUCKDOCK_CREDENTIAL_KEY（Fernet key）
docker compose up -d          # 起默认 profile：mysql · redis · minio · backend · worker · beat · frontend
```

> 首启需拉镜像 + 构建（≈3 分钟）。后端使用 SQLAlchemy async + `aiomysql` 连接 MySQL。

常用子命令：`bash scripts/dev.sh {up|status|logs [服务]|migrate|build|shell|stop|down|observability|reset}`。

### 3.3 可选：Langfuse 可观测性

```bash
docker compose --profile observability up -d
# 或 bash scripts/dev.sh observability
```

会幂等执行 `langfuse-db-init`（建 `langfuse` 库）与 `langfuse-minio-init`（建 `langfuse` bucket，否则 trace 静默丢失）后自动退出。

### 3.4 备选：Windows 混合模式

中间件用 Compose、backend / worker / frontend 跑原生进程（日志与热更更直观）。详见 [`docs/dev-operations.zh-CN.md`](docs/dev-operations.zh-CN.md) 与 `scripts/dev-start.ps1`：

```powershell
docker compose up -d mysql redis minio
cd backend; ..\.venv\Scripts\alembic.exe upgrade head
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8801 --reload
..\.venv\Scripts\celery.exe -A app.workers.celery_app worker --loglevel=info --concurrency=2 --pool=solo
cd ..\frontend; npm install; npm run dev   # → http://127.0.0.1:5174
```

### 3.5 健康检查

```bash
curl -s http://127.0.0.1:8801/health             # backend → {"status":"ok"}
curl -sI http://127.0.0.1:5174                    # frontend → 200
curl -s http://127.0.0.1:9000/minio/health/live   # MinIO
curl -s http://127.0.0.1:3200/api/public/health   # Langfuse（observability profile）
```

### 3.6 首次登录

**DuckDock 不预置管理员**。第一个通过 `/api/v1/auth/register` 注册的用户会**自动获得系统 ADMIN**，后续注册为普通 USER。打开 `http://127.0.0.1:5174` → 注册 → 登录。Langfuse UI（`:3200`）由 `.env` 的 `LANGFUSE_INIT_*` 自动 bootstrap。

---

## 4. 架构总览

完整 Mermaid 图（容器拓扑 / 分层 / 发布时序 / ERD / RBAC / 交接生命周期）见 **[`docs/architecture.md`](docs/architecture.md)**。简化版：

```
                  ┌──────── Browser ────────┐
                  v                          v
           Frontend :5174          Langfuse Web :3200 (observability)
                  │ /api proxy                ▲ traces
                  v                           │
           Backend :8801 ◄──── FastAPI ───────┘
                  │
   ┌──────────────┼───────────────┬───────────────┐
   v              v               v               v
 MySQL :3307   Redis :6379   MinIO :9000/9001   Git bare /repos
                  ▲
        Celery Worker（scan · clinic · webhook · lifecycle · replication · report-ingest · agent-insight）
```

采集为 **Push-only**，但分两条入口：日常日报/周报由现场 Reporter 用长期可撤销 `dkr_report_*` credential 直接提交 `duckdock-structured-report-v1` JSON → `CollectionJob/WorkTrace/AIAsset/MemoryCandidate` 轻量入库；交接、审计或证据型材料再上传 `duckdock-pack-v1.zip` → MinIO → `report_upload` 状态机 → `analysis-worker` 标准化分析入库。支持 v2 的在线 Reporter 还会先协商 Adapter profile/能力，并为每次执行写入 metadata-only Session/Run；Hermes 使用原生 `hermes-reporter` profile。后台运行时详情把两条链统一成员工/agent 时间线，并可用 `DUCKDOCK_LLM_*` 生成一键 AI 概览；模型不可用时显示 baseline 降级。服务端不外联厂商，`analysis-worker` 在开发 compose 中是可选 profile，可按积压队列水平扩容。

---

## 5. 核心能力

### 注册表 / 平台

- 用户注册 / 登录 / JWT 刷新 / OIDC SSO / LDAP / 企业 UID / identity link
- Namespace 创建 + 成员管理 + IAM 权限键 + Robot token
- Skill 创建 / 发布 / diff / 包下载 / 公开分发（ClawHub `/.well-known/clawhub.json`）
- Scanner 结果查询 + 手动重扫 + namespace 级规则豁免
- Clinic 多维度评测 + 推荐 + 历史趋势（可选 Langfuse trace）
- 中 / 英双语 UI（React 18 + Ant Design 5，indigo / slate 主题）

### Agent 控制平面

| 模块 | 内容 |
|---|---|
| 运行时连接 | provider（openclaw / jvs / arkclaw / workbuddy / custom）· 凭证 Fernet 加密落库（绝不回明文）· 上报链路自检 |
| 资产盘点 | Push 上报采集 · `ai_asset` upsert + 归属推断 · `runtime_binding` |
| 工作历程 / 证据 | 敏感级默认遮蔽 + reveal（权限 + 原因 + 审计）· 证据**限时签名下载 URL** · 越权尝试留痕 |
| 交接闭环 | 建议（规则版 / 可选 LLM 顾问 + 置信度）→ 审批闸门 → 人工执行回执 → 验收归档，全程审计；公开请求不提供尚未落地的自动执行模式 |
| 敏感隔离 | 敏感工作历程 / 证据按 **ownership 过滤**（持提权键 / admin 不受限） |

### 企业特性

审计日志 · Webhook（HMAC-SHA256 + 重试）· Robot 账号（`dkr_robot_*`）· 配额 · 保留策略（GC）· Replication · ClawHub 公开分发 · Sandbox 验证（OpenClaw Docker/SSH 两级 gate）· Governance（license 白名单 / sandbox 策略）。

---

## 6. API 入口

兼容 API 使用 `/api/v1`，新的运行证据、评测、发布和运维接口使用 `/api/v2`。实际端点与请求模型以运行实例的 `/docs` 和冻结的 v2 OpenAPI 契约为准，README 不维护容易失真的端点计数。

```
auth  iam  namespaces  skills  clawhub  registry  scans  clinic  components
control_plane  analysis  people  audit  webhooks  robots  lifecycle  replication
```

诊断入口：

| URL | 用途 |
|---|---|
| `/health` | 健康检查 |
| `/docs` · `/redoc` | Swagger UI · ReDoc |
| `/api/v2/reporter/sessions` · `/api/v2/reporter/runs` | `execution.write` ReporterCredential 驱动的 metadata-only Session/Run 生命周期 |
| `/api/v2/reporter/telemetry-sinks/{sink_public_id}/v1/traces` | Generic OTLP Collector 的 credential-derived Run/Trace mapping 与 quarantine 投影 |
| `/api/v2/reporter/pack-imports` | Pack/ATIF 严格 manifest 预检、签名上传、幂等导入初始化 |
| `/api/v2/reporter/pack-imports/{public_id}` | Runtime-scoped 导入状态与 metadata-only Artifact 索引 |
| `/api/v2/reporter/pack-imports/{public_id}/finalize` | 全包/逐 payload 摘要、归档安全、Run mapping 与 MinIO 物化 |
| `/api/v2/reporter/handshakes` · `/api/v2/reporter/heartbeats` | credential-bound 动态能力协商、过期/重放与 heartbeat/config/Collector drift |
| `/api/v2/fleet/runtimes` · 前端 `/fleet` | Namespace RBAC 的 Runtime 版本、能力、心跳历史、Collector 与漂移视图 |
| `/api/v2/agent-runs` | Namespace RBAC、31 天有界时间窗与签名游标的 Run 管理查询 |
| `/api/v2/reconciliation/*` | 系统管理员 v1/v2 对账健康、单 WorkTrace 查询与幂等重算 |
| `/.well-known/clawhub.json` | ClawHub 私有注册表 discovery |

---

## 7. 数据库与迁移

业务主库 **MySQL 8.x**（`mysql+aiomysql://…@mysql:3306/duckdock?charset=utf8mb4`，host 3307）。PostgreSQL 16 + ClickHouse 仅 `observability` profile 供自托管 Langfuse,**不承载业务数据**。

迁移文件位于 `backend/alembic/versions/`。不要从文档中的固定数量或历史编号判断数据库状态；部署时必须以 Alembic 自身输出为准：

```bash
cd backend
alembic heads
alembic current
alembic upgrade head
alembic check
```

空 MySQL 必须能够直接升级到唯一 head，已有数据库必须先备份再迁移。应用的 `/readyz` 和 Operations 页面会显示实际数据库 revision，但不能替代部署前检查。

---

## 8. 质量门禁与测试

CI（[`.github/workflows/ci.yml`](.github/workflows/ci.yml)）四个阻塞 job 必须全绿：

**backend**：`ruff`（阻塞,select=F）· `mypy` baseline ratchet（错误数只降不升,见 `backend/.mypy-baseline`）· `pytest` 在真实 MySQL 8.x 上跑（核心安全模块 `deps / iam / release_gate / credential` **覆盖率 ≥ 65%**）· `compileall` · `from app.main import app` · `alembic upgrade head`（空库）。

**frontend**：`npm ci` → `eslint`（flat config,卡 unused / undefined / Hooks 误用）→ `vitest` → `npm run build`（含 `tsc`）。

**e2e**：在隔离的开发/CI 数据库启动完整栈，Playwright 运行认证与交接闭环；造数工具在 `DEBUG=false` 时无条件拒绝执行。

**compose**：校验默认 profile 只含 mysql/redis/minio/backend/worker/beat/frontend；`analysis-worker` / `observability` / `telemetry*` 不得默认开启。

OpenClaw Telemetry 使用可选 `telemetry` profile；
Langfuse v4 导出使用独立 `telemetry-langfuse` profile，默认栈均不启动。
参考配置、双格式 GenAI conformance、Secret Canary/exporter smoke 与生产边界见
[`ops/otel-collector/README.md`](ops/otel-collector/README.md)。

测试数量和通过数由 CI 产生，不在说明文档中硬编码。单元测试中的替身只能证明代码分支；涉及 MySQL、MinIO、Reporter、Langfuse、Prometheus 和恢复目标的生产判断必须使用对应环境的集成证据。

---

## 9. 环境变量

`.env.example` 是权威模板。关键分组：

| 组 | 关键变量 |
|---|---|
| App | `SECRET_KEY` · `DEBUG` · `BACKEND_BASE_URL` · `FRONTEND_BASE_URL` · `CORS_ORIGINS` |
| MySQL（主库）| `MYSQL_DATABASE/USER/PASSWORD` · `DATABASE_URL`（`mysql+aiomysql://…`）|
| Redis / MinIO | `REDIS_URL` · `MINIO_ROOT_*` · `MINIO_ACCESS/SECRET_KEY` · `MINIO_BUCKET` · `MINIO_ENDPOINT` · `MINIO_PUBLIC_ENDPOINT` |
| Agent execution rollout | `AGENT_EXECUTION_INGESTION_ENABLED` · `AGENT_EXECUTION_RUNTIME_ALLOWLIST` |
| 凭证加密 | `DUCKDOCK_CREDENTIAL_KEY`（Fernet,独立于 SECRET_KEY；`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`）|
| 签名 URL | `REGISTRY_SIGNED_URL_EXPIRE_SECONDS` · `REGISTRY_SIGNED_URL_MAX_SECONDS` · `REPORT_UPLOAD_URL_EXPIRE_SECONDS` |
| Pack/ATIF | `PACK_IMPORT_MAX_COMPRESSED_BYTES` · `PACK_IMPORT_MAX_UNCOMPRESSED_BYTES` · `PACK_IMPORT_MAX_ENTRY_BYTES` · `PACK_IMPORT_MAX_PAYLOADS` · `PACK_IMPORT_MAX_COMPRESSION_RATIO` · `PACK_IMPORT_PENDING_TTL_SECONDS` |
| Adapter/Fleet | `ADAPTER_HANDSHAKE_TTL_SECONDS` · `ADAPTER_HANDSHAKE_REPLAY_WINDOW_SECONDS` · `ADAPTER_MAX_CLOCK_SKEW_SECONDS` · `FLEET_HEARTBEAT_STALE_SECONDS` |
| LLM（Clinic / Skill 生成 / 交接顾问 / analysis-worker / agent overview）| `CLINIC_LLM_*` · `SKILL_GEN_*` · `HANDOVER_LLM_ENABLED`（默认关）+ `HANDOVER_LLM_*` · `DUCKDOCK_LLM_*` · `ANTHROPIC_API_KEY` |
| LLM 安全 | `LLM_ALLOW_PRIVATE_BASE_URL`（默认 false:拒私网/环回 base_url,SSRF 纵深防御）|
| Sandbox | `SKILL_SANDBOX_*`（docker / ssh backend, 两级 gate）|
| Langfuse（observability）| `LANGFUSE_COMPATIBILITY_PROFILE` · `LANGFUSE_IMAGE_TAG` · `LANGFUSE_CLICKHOUSE_TAG` · `LANGFUSE_SALT` · `LANGFUSE_ENCRYPTION_KEY`（必须 64 hex）· `LANGFUSE_NEXTAUTH_SECRET` · `LANGFUSE_INIT_*` · `CLINIC_LANGFUSE_ENABLED` |

> `MINIO_PUBLIC_ENDPOINT` 给签名 URL 用：本机开发与 `MINIO_ENDPOINT` 相同；生产里为对外可达域名。

---

## 10. 常见故障

- **后端镜像依赖变更后未生效**：执行 `bash scripts/dev.sh build backend`，然后重启 backend / worker / beat。
- **`alembic upgrade head` 在空库 Duplicate column**：给 `0009` 控制平面表加列的迁移必须幂等（inspector 守卫）。
- **Langfuse v4 读接口 404 / 升级验收**：不要再调用 legacy trace/session/observation API；使用 Observations API v2，并显式请求所需字段组。确认 `langfuse-minio-init` 成功、Collector 发送 `x-langfuse-ingestion-version: 4`；完整升级门禁执行 `bash scripts/verify-langfuse-upgrade.sh`，流程见 [`docs/langfuse-upgrade-runbook.zh-CN.md`](docs/langfuse-upgrade-runbook.zh-CN.md)。
- **端口冲突 / 前端连不上后端**：后端端口是 **8801**（不是 8001）；`VITE_API_PROXY_TARGET` host 模式 `http://127.0.0.1:8801`、compose 模式 `http://backend:8801`。
- **Worker 任务不跑**：`docker compose ps worker redis`,确认 Redis healthy + worker `celery@<host> ready`。

---

## 11. 文档索引

| 文档 | 内容 |
|---|---|
| [`documentation-index.zh-CN.md`](docs/documentation-index.zh-CN.md) | 文档入口（当前 / 历史分层）|
| [`architecture.md`](docs/architecture.md) | **架构总览**（Mermaid · 端口 · RBAC · ERD · 时序 · 生命周期）|
| [`control-plane-admin-guide.zh-CN.md`](docs/control-plane-admin-guide.zh-CN.md) | 控制平面管理员手册（权限收口 · 凭证 · Push 接入 · 交接生命周期 · reveal · 上线清单）|
| [`duckdock-handbook.zh-CN.md`](docs/duckdock-handbook.zh-CN.md) | 使用手册：管理员 / 员工 / Reporter 接入 |
| [`operations-runbook.zh-CN.md`](docs/operations-runbook.zh-CN.md) | 运维手册：单机 Compose · 组件 · 备份 · 排障 |
| [`ga-production-authorization.zh-CN.md`](docs/ga-production-authorization.zh-CN.md) | 2.0 GA：目标环境证据、独立审计、四方签名与生产授权门禁 |
| [`dev-operations.zh-CN.md`](docs/dev-operations.zh-CN.md) · [`ci.zh-CN.md`](docs/ci.zh-CN.md) | 本地开发运维 · CI 说明 |
| [`port-plan.zh-CN.md`](docs/port-plan.zh-CN.md) · [`permission-matrix.zh-CN.md`](docs/permission-matrix.zh-CN.md) | 端口方案 · 权限矩阵 |
| [`duckdock-runtime-mcp.zh-CN.md`](docs/duckdock-runtime-mcp.zh-CN.md) · [`workbuddy-reporter-validation.zh-CN.md`](docs/workbuddy-reporter-validation.zh-CN.md) | Runtime MCP 接入 · Reporter 集成验收指南 |
| [`registry-sync-contract.md`](docs/registry-sync-contract.md) · [`openclaw-registry-contract.md`](docs/openclaw-registry-contract.md) | Registry 同步契约 · OpenClaw 集成契约 |
| `clinic-*.zh-CN.md` · `enterprise-iam-roadmap` · `next-development-roadmap` · `production-strengthening-plan` | Clinic / IAM / 路线 / 生产加固 |
| `agent-control-plane-prd/` | 历史 PRD 与设计决策溯源（非当前操作规范）|

> 需求走 `specs/<NNN>-<slug>/`（spec → plan → tasks）；代码从规格派生。当前主线规格包括 `008-duckdock-2-foundation` 与 `009-foundation-tenant-contract`；`001`–`007` 保留为既有底座与历史增强。

---

## 12. 生产部署清单

单主机基线使用 `scripts/prod.sh`、[`docker-compose.prod.yml`](docker-compose.prod.yml)
和强制 TLS overlay [`docker-compose.prod-tls.yml`](docker-compose.prod-tls.yml)；跨故障域
基线在 [`ops/kubernetes/ha`](ops/kubernetes/ha)。上线前至少：

- [ ] 替换 `.env.prod` 全部默认 secret（`SECRET_KEY` · `MYSQL_*` · `MINIO_ROOT_PASSWORD` · `DUCKDOCK_CREDENTIAL_KEY` · Langfuse 三件套）
- [ ] MySQL / Redis / S3 / RWX 仓库存储使用目标故障域内的 HA 服务，并收紧默认拒绝 NetworkPolicy 的 egress
- [ ] 用 `scripts/prepare-kubernetes-ha-target.py` 生成并复验三阶段目标包；server dry-run 后等待 commit-bound migration 成功再滚应用
- [ ] TLS 证书覆盖 application/object 两个域名；公网扫描确认只有 443，关 `DEBUG`
- [ ] Alertmanager firing/resolved 均送达真实 on-call；异地 age 加密、签名、object-lock 备份完成破坏性恢复
- [ ] 至少 900 秒、声明 RPS 以上且 50,000+ Run 的容量/数据增长门禁通过
- [ ] 九份 GA trust policy/公钥通过全局职责分离预检；发布机构审阅绑定 release/target、独立恢复目标、新 evidence root 和 12-phase DAG 的不可覆盖执行 campaign
- [ ] 八个风险 phase 均由原 Operations 授权 identity 在活动窗口、依赖完成后签署独立 start interlock；动作/回执时间不早于许可
- [ ] 至少一个 Celery worker 常驻;`alembic upgrade head` 对齐 schema
- [ ] 第一个注册账号用企业管理员邮箱（自动成 admin）
- [ ] 核心栈启动后在 `/analysis` 创建 Worker token，填入 `.env.prod.enc`，再 `bash scripts/prod.sh worker`
- [ ] 独立安全机构完成 exact commit/image 评估且 Critical/High 为零
- [ ] Product、Architecture、Security、Operations 四个不同身份签署相同 release digest；最终门禁输出 `GA_AUTHORIZED`

清单勾选不是机器授权。最终必须执行
[`docs/ga-production-authorization.zh-CN.md`](docs/ga-production-authorization.zh-CN.md)
中的内容寻址证据与 OpenSSH 四方签名流程。

---

## 13. 开发与贡献

完整流程见 [`CONTRIBUTING.md`](CONTRIBUTING.md)；安全问题请按 [`SECURITY.md`](SECURITY.md) 私密报告，不要公开披露漏洞细节或真实凭证。

- **规格驱动**：先读 [`.specify/memory/constitution.md`](.specify/memory/constitution.md)（最高约束）。新需求走 `specs/<NNN>-<slug>/`,里程碑前跑 `/speckit-analyze` 防漂移。
- **测试先行**：核心写路径（发布门禁 / 状态机 / 越权）先写测试;PR 必须过 CI 四个阻塞 job。
- **提交前**：`bash scripts/dev.sh` 起栈;后端 `ruff check` + `mypy` + `pytest`;前端 `npm run lint` + `npm test` + `npm run build`。
- **约束**：MySQL-only（禁 PG 专属 SQL 进业务路径）;写入串行 + 幂等;敏感内容最小暴露;凭证加密;LLM 仅出建议、处置需人工审批。

---

## 14. 许可证

DuckDock 原创代码、文档与仓库内原创设计资源以 [MIT License](LICENSE) 开源，允许使用、复制、修改、合并、发布、分发、再许可和销售，但必须保留版权与许可声明，且软件按“原样”提供、不附带担保。完整且具有约束力的条款以仓库根目录 [`LICENSE`](LICENSE) 为准。

第三方依赖、字体、图标和外部服务仍适用其各自许可证与条款，MIT License 不会对它们重新授权。贡献本项目即表示贡献者确认有权提交相关内容，并同意按 MIT License 授权；详见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

```
Copyright (c) 2026 DuckDock contributors
Permission is hereby granted, free of charge, to any person obtaining a copy...
```
