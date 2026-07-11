# DuckDock CI 说明

本文档说明 GitHub Actions CI 的触发方式、检查内容、本地复现命令和维护原则。

## 1. 触发条件

CI 配置位于 `.github/workflows/ci.yml`。

当前触发条件：

- push 到 `main`
- 针对 `main` 的 pull request
- GitHub 页面手动执行 `workflow_dispatch`

## 2. 检查矩阵

CI 分为四个阻塞 job。

| Job | 目的 | 关键命令 |
|---|---|---|
| `backend` | 校验 FastAPI 后端 lint/type/test/import、MySQL Alembic 迁移可在空库执行 | `ruff check`、`mypy`、`pytest`、`python -m compileall app alembic`、`python -m alembic -c alembic.ini upgrade head` |
| `frontend` | 校验 React / TypeScript 前端 lint/unit/build | `npm ci`、`npm run lint`、`npm test`、`npm run build` |
| `e2e` | 启动完整 DuckDock dev 栈，运行 Playwright auth smoke 与 seeded P3-11 交接闭环 | `bash scripts/dev.sh up`、`npx playwright test` |
| `compose` | 校验默认 Docker Compose 只包含 DuckDock 核心服务，`analysis-worker` / Langfuse 只在对应 profile 中出现 | `docker compose config --services`、`docker compose --profile analysis-worker config --services`、`docker compose --profile observability config --services` |

## 3. 后端 CI 环境

后端 job 使用 GitHub Actions service container 启动 MySQL 8.4。

CI 使用的核心变量：

```bash
DATABASE_URL=mysql+aiomysql://duckdock:duckdock@127.0.0.1:3306/duckdock?charset=utf8mb4
SECRET_KEY=duckdock-ci-secret
DUCKDOCK_CREDENTIAL_KEY=<test-only Fernet key>
COMPONENT_MANAGER_ENABLED=false
```

说明：

- CI 不启动 Langfuse、ClickHouse、MinIO 实例。
- MinIO 相关环境变量只用于保证配置对象可初始化。
- 组件管理器在 CI 中关闭，避免 CI Runner 误执行宿主机 Docker Compose 操作。
- Alembic 必须能在空 MySQL 数据库上从 baseline 一路升级到 `head`。

## 4. 前端 CI 环境

前端 job 使用 Node.js 20，并基于 `frontend/package-lock.json` 执行确定性安装。

本地等价命令：

```powershell
cd frontend
npm ci
npm run build
```

E2E 本地等价命令：

```powershell
cd frontend
npm run test:e2e
```

如果要单独跑 seeded 交接闭环，需要先启动完整本地 DuckDock 栈，再执行：

```powershell
cd frontend
$env:DEBUG="true"
$env:E2E_AUTO_SEED="1"
$env:E2E_BASE_URL="http://localhost:5174"
npm run test:e2e:handover
```

如果只是开发机快速验证，也可以使用：

```powershell
cd frontend
npm install
npm run build
```

## 5. Compose 约束

DuckDock 默认启动组必须保持轻量，只包含核心服务：

- `mysql`
- `redis`
- `minio`
- `backend`
- `worker`
- `beat`
- `frontend`

`analysis-worker` 必须只出现在 `analysis-worker` profile。

以下观测组件必须只出现在 `observability` profile：

- `postgres`
- `clickhouse`
- `langfuse-db-init`
- `langfuse-minio-init`
- `langfuse-worker`
- `langfuse-web`

管理员需要 Langfuse 时，应从 DuckDock 管理员控制台的“组件管理”页面启动，或者手动执行：

```powershell
docker compose --profile observability up -d postgres clickhouse langfuse-db-init langfuse-minio-init langfuse-worker langfuse-web
```

停止观测组件：

```powershell
docker compose --profile observability stop langfuse-web langfuse-worker clickhouse postgres
```

## 6. 本地完整复现

在 Windows 开发机上，推荐按以下顺序复现 CI 的核心检查：

```powershell
# 后端编译
.\.venv\Scripts\python.exe -m compileall backend\app backend\alembic

# 前端构建
cd frontend
npm run build
cd ..

# 默认 compose 不应包含 analysis-worker 或 Langfuse
docker compose config --services

# analysis-worker profile 应包含 analysis-worker
docker compose --profile analysis-worker config --services

# 可选观测 profile 应包含 Langfuse
docker compose --profile observability config --services
```

如需验证迁移：

```powershell
.\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
```

## 7. 维护规则

- 新增后端数据表时，必须提交 Alembic migration，并保证空 MySQL 可升级到 `head`。
- 新增前端依赖时，必须同步提交 `frontend/package-lock.json`。
- 新增 Docker Compose 服务时，必须明确它属于核心启动组还是可选 profile。
- `analysis-worker`、Langfuse、ClickHouse 等可选组件默认不得进入核心启动组。
- `e2e` 是阻塞门禁；auth smoke 或 seeded closed-loop 失败时必须修复，不能用 skip 掩盖主流程退化。
