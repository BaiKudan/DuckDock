# DuckDock 本地开发运维说明

本文档说明如何在本地统一启动、停止、查看状态，以及如何使用 Alembic 管理数据库迁移。当前推荐入口是 `scripts/dev.sh`（macOS / Linux / WSL，全栈 Docker Compose）；Windows 原生进程脚本仍保留为备选。

## 1. 前置要求

- Docker Desktop 已启动
- Docker Compose v2 可用
- Node/Python 本机工具仅在原生进程模式或本地测试时需要

## 2. 本地启动脚本

项目提供两套基础脚本：

- `scripts/dev.sh`：macOS / Linux / WSL 推荐，全栈 Docker Compose。
- `scripts/dev-start.ps1` / `scripts/dev-stop.ps1` / `scripts/dev-status.ps1`：Windows 混合模式，中间件用 Compose，backend/worker/beat/frontend 跑本机进程。

### 2.1 推荐启动(macOS / Linux / WSL)

在仓库根目录执行：

```bash
bash scripts/dev.sh up
```

这会：

- 从 `.env.example` 复制 `.env`（若不存在）
- 启动 Docker 中间件：`mysql`、`redis`、`minio`
- 等 MySQL 健康
- 跑 `alembic upgrade head`
- 启动 `backend`、`worker`、`beat`、`frontend`

常用命令：

```bash
bash scripts/dev.sh status
bash scripts/dev.sh logs backend
bash scripts/dev.sh stop
bash scripts/dev.sh observability   # 可选 Langfuse
bash scripts/dev.sh worker          # 可选 analysis-worker，需先写 DUCKDOCK_ANALYSIS_WORKER_TOKEN
```

Langfuse 和 analysis-worker 都是可选 profile，默认 `dev.sh up` 不启动。

### 2.2 Windows 混合模式

- `scripts/dev-start.ps1`
- `scripts/dev-stop.ps1`
- `scripts/dev-status.ps1`

#### 启动

在仓库根目录执行：

```powershell
.\scripts\dev-start.ps1
```

这会：

- 停掉已知的本地 DuckDock 进程
- 启动 Docker 中间件（全部来自根 `docker-compose.yml`）：
  - `mysql`
  - `redis`
  - `minio`
- 跑 `alembic upgrade head`
- 启动本机进程：
  - FastAPI 后端（`:8801`）
  - Celery worker
  - Celery beat
  - Vite 前端（`:5174`）

需要 Langfuse 时单独启动 `docker compose --profile observability up -d`（`http://127.0.0.1:3200`）。

如果只想启动后端和 worker，不启动前端：

```powershell
.\scripts\dev-start.ps1 -NoFrontend
```

#### 停止

只停止本地应用进程：

```powershell
.\scripts\dev-stop.ps1
```

连 DuckDock Docker 中间件（以及已启动的可选组件）一起停掉：

```powershell
.\scripts\dev-stop.ps1 -WithDockerDown
```

#### 查看状态

```powershell
.\scripts\dev-status.ps1
```

它会显示：

- `backend` 健康状态
- `frontend` 健康状态
- 本地 pid 文件状态
- 相关 Docker 容器状态

## 3. 日志位置

日志统一写到仓库根目录 `.logs`：

- `.logs/backend.log`
- `.logs/backend.err.log`
- `.logs/celery.log`
- `.logs/celery.err.log`
- `.logs/beat.log`
- `.logs/beat.err.log`
- `.logs/frontend.log`
- `.logs/frontend.err.log`

pid 文件也写在 `.logs` 下：

- `.logs/backend.pid`
- `.logs/celery.pid`
- `.logs/beat.pid`
- `.logs/frontend.pid`

## 4. Alembic 迁移

### 4.1 当前策略

当前仓库已加入 baseline migration：

- `backend/alembic/versions/20260409_0001_baseline_schema.py`

这个 baseline 用于：

- 新数据库初始化
- 现有数据库转入 Alembic 管理

应用本身不再在 `startup` 时自动建表。

### 4.2 新库初始化

对于全新数据库，推荐从仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
```

说明：

- `alembic/env.py` 会自动从项目配置读取 `DATABASE_URL`
- 不需要手工改 `alembic.ini`

### 4.3 现有数据库接入 Alembic

如果当前数据库已经由旧版本应用运行过，表已经存在，此时不要直接执行 `upgrade head`。

应先把现有数据库标记为 baseline：

```powershell
.\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini stamp 20260409_0001
```

之后再执行后续 migration。

### 4.4 新建迁移

模型变更后，执行：

```powershell
.\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini revision --autogenerate -m "describe change"
```

然后检查生成脚本，再执行：

```powershell
.\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
```

## 5. 推荐日常流程

本地开发建议按下面顺序：

1. `.\scripts\dev-start.ps1`
2. 开发代码
3. 如有模型改动，执行 Alembic migration
4. 运行前端构建或后端编译检查
5. `.\scripts\dev-status.ps1`
6. 完成后 `.\scripts\dev-stop.ps1`

## 6. 注意事项

- 当前 Windows 下 `.venv\Scripts\python.exe` 仍会派生底层 CPython 子进程，这属于正常行为
- 只要底层解释器来自 `uv` 安装的 CPython，而不是 `anaconda3\python.exe`，就说明环境已经收敛
- 若切换 Python 版本，建议整体重建 `.venv`
