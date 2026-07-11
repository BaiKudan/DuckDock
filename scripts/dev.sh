#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# DuckDock 本地开发启动脚本(WSL / Linux + Docker Desktop)
#
# 为什么用 Docker compose 全栈而不是原生进程:
#   仓库里的 .venv 是 Windows venv(Scripts/python.exe),node_modules 也含
#   Windows 原生二进制 —— 两者在 WSL 里都跑不了。compose 的 backend/worker/
#   frontend 已挂源码(./backend:/app、./frontend:/app)且带 --reload / Vite HMR,
#   改代码即时热更新,且用的是镜像内的 Linux 依赖,完全绕开跨平台环境冲突。
#
# 默认 profile 只起核心:mysql / redis / minio / backend / worker / beat / frontend。
# Langfuse 可观测性、analysis-worker 属可选 profile(见 observability / worker 子命令)。
#
# 用法: bash scripts/dev.sh [up|down|stop|restart|status|logs|migrate|build|shell|observability|worker|reset]
#   不带参数 = up。 chmod +x 后可直接 ./scripts/dev.sh
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# 兼容 docker compose(v2 插件)与旧 docker-compose
if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  echo "✗ 未找到 docker compose。" >&2
  echo "  请确认 Docker Desktop 正在运行,且 Settings → Resources → WSL integration 已为当前发行版打开。" >&2
  exit 1
fi

ensure_env() {
  if [ ! -f .env ]; then
    echo "→ 未找到 .env,从 .env.example 复制(凭证功能需另填 DUCKDOCK_CREDENTIAL_KEY)"
    cp .env.example .env
  fi
}

wait_mysql_healthy() {
  echo "→ 等待 MySQL 健康检查通过…"
  local cid health
  for _ in $(seq 1 60); do
    cid="$("${DC[@]}" ps -q mysql 2>/dev/null || true)"
    if [ -n "$cid" ]; then
      health="$(docker inspect --format '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo starting)"
      [ "$health" = "healthy" ] && { echo "  MySQL healthy ✓"; return 0; }
    fi
    sleep 2
  done
  echo "✗ MySQL 60s 内未就绪,请查看: bash scripts/dev.sh logs mysql" >&2
  exit 1
}

migrate() {
  echo "→ 执行数据库迁移(alembic upgrade head)…"
  "${DC[@]}" run --rm backend python -m alembic -c alembic.ini upgrade head
}

print_endpoints() {
  cat <<'EOF'

✓ DuckDock 开发栈已就绪:
   前端          http://localhost:5174
   后端 API      http://localhost:8801      (Swagger: http://localhost:8801/docs)
   MinIO 控制台  http://localhost:9001      (账号/密码见 .env 的 MINIO_ACCESS_KEY / MINIO_SECRET_KEY)
   MySQL         localhost:3307             (库 duckdock)

   实时日志: bash scripts/dev.sh logs          停止: bash scripts/dev.sh stop
   首次登录: 第一个注册的账号 = 系统管理员(见 README §3.6 / 控制平面手册)
EOF
}

cmd_up() {
  ensure_env
  echo "→ 启动核心中间件(mysql / redis / minio)…"
  "${DC[@]}" up -d mysql redis minio
  wait_mysql_healthy
  migrate
  echo "→ 启动 backend / worker / beat / frontend(热重载)…"
  "${DC[@]}" up -d backend worker beat frontend
  echo ""
  "${DC[@]}" ps
  print_endpoints
}

case "${1:-up}" in
  up)            cmd_up ;;
  build)         ensure_env; "${DC[@]}" build "${@:2}" ;;
  migrate)       migrate ;;
  stop)          "${DC[@]}" stop ;;
  restart)       "${DC[@]}" restart "${@:2}" ;;
  down)          "${DC[@]}" down ;;    # 移除容器,保留数据卷
  status|ps)     "${DC[@]}" ps ;;
  logs)          "${DC[@]}" logs -f --tail=100 "${@:2}" ;;
  shell)         "${DC[@]}" exec "${2:-backend}" bash ;;
  observability) "${DC[@]}" --profile observability up -d; echo "Langfuse: http://localhost:3200" ;;
  worker)        "${DC[@]}" --profile analysis-worker up -d analysis-worker ;;
  reset)
    read -r -p "⚠ 将删除全部容器 + 数据卷(MySQL/MinIO 数据清空),确认?(yes/N) " ans
    [ "$ans" = "yes" ] && "${DC[@]}" down -v || echo "已取消"
    ;;
  -h|--help|help)
    cat <<'EOF'
DuckDock 本地开发(WSL/Linux + Docker Desktop)· 全栈 Docker compose

  bash scripts/dev.sh up             起核心栈 + 迁移 + backend/worker/beat/frontend(默认)
  bash scripts/dev.sh status         查看容器状态
  bash scripts/dev.sh logs [服务]    跟随日志(可指定 backend/frontend/worker/mysql)
  bash scripts/dev.sh migrate        只跑 alembic upgrade head
  bash scripts/dev.sh build          重建镜像(依赖变更后)
  bash scripts/dev.sh shell [服务]   进容器 bash(默认 backend)
  bash scripts/dev.sh stop | down    停止 | 移除容器(保留数据卷)
  bash scripts/dev.sh observability  额外起 Langfuse 栈(:3200)
  bash scripts/dev.sh worker         启动可选 analysis-worker profile
  bash scripts/dev.sh reset          ⚠ 清空数据卷重来
EOF
    ;;
  *)
    echo "未知子命令: $1  (用 help 查看)" >&2; exit 1 ;;
esac
