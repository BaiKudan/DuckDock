#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# DuckDock 生产启动脚本(baseline)。对照 scripts/dev.sh 的生产版。
# 用 docker-compose.prod.yml:不可变镜像、无热重载、前端 nginx、迁移先行。
#
# 用法: bash scripts/prod.sh [preflight|up|worker|down|build|migrate|status|logs|shell|backup|help]
#   不带参数 = up。启动前会用 SOPS/age 解密 .env.prod.enc 并预检强密钥(弱口即拒绝)。
#
# ⚠ 这是 Compose 生产部署入口。上线前仍需补 TLS 反代、监控和恢复演练。
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

ENV_ENC_FILE="${DUCKDOCK_PROD_ENV_ENC:-.env.prod.enc}"
ENV_FILE="${DUCKDOCK_PROD_ENV_FILE:-.env.prod}"
ENV_FROM_SOPS=0

cleanup_env() {
  if [ "$ENV_FROM_SOPS" = "1" ] && [ -f "$ENV_FILE" ]; then
    rm -f "$ENV_FILE"
  fi
}
trap cleanup_env EXIT

if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  echo "✗ 未找到 docker compose。请确认 Docker 已安装并运行。" >&2
  exit 1
fi
COMPOSE=("${DC[@]}" -f docker-compose.prod.yml --env-file "$ENV_FILE")

decrypt_env() {
  if [ ! -f "$ENV_ENC_FILE" ]; then
    echo "✗ 缺少 $ENV_ENC_FILE —— 生产必须使用 SOPS/age 密文配置,不得回退裸 .env.prod。" >&2
    echo "  生成方式见 docs/production-deployment.md；可用 DUCKDOCK_PROD_ENV_ENC 指向密文文件。" >&2
    exit 1
  fi
  if ! command -v sops >/dev/null 2>&1; then
    echo "✗ 未找到 sops。请安装 sops 并配置 age key 后再启动生产。" >&2
    exit 1
  fi
  if [ -z "${SOPS_AGE_KEY_FILE:-}" ] && [ -z "${SOPS_AGE_KEY:-}" ]; then
    echo "✗ 缺少 age 解密密钥。请设置 SOPS_AGE_KEY_FILE 或 SOPS_AGE_KEY。" >&2
    exit 1
  fi
  umask 077
  if ! sops -d "$ENV_ENC_FILE" > "$ENV_FILE"; then
    rm -f "$ENV_FILE"
    echo "✗ SOPS/age 解密失败,拒绝启动生产。" >&2
    exit 1
  fi
  ENV_FROM_SOPS=1
}

preflight() {
  decrypt_env
  set -a; . "$ENV_FILE"; set +a
  local bad=0
  require() {  # require VARNAME "禁用值1 禁用值2 ..."
    local name="$1" forbid="$2" val="${!1:-}"
    if [ -z "$val" ] || [[ "$val" == __CHANGE_ME* ]]; then
      echo "  ✗ $name 未设置"; bad=1
    elif [ -n "$forbid" ] && printf '%s\n' $forbid | grep -qxF "$val"; then
      echo "  ✗ $name 仍是默认/弱口"; bad=1
    fi
  }
  echo "→ 生产配置预检…"
  require SECRET_KEY "change-me-in-production-use-openssl-rand-hex-32 duckdock-ci-secret"
  require DUCKDOCK_CREDENTIAL_KEY ""
  require MYSQL_PASSWORD "duckdock"
  require MYSQL_ROOT_PASSWORD "duckdock-root duckdock"
  require MINIO_ROOT_PASSWORD "minioadmin duckdock_secret"
  require MINIO_ACCESS_KEY "duckdock minioadmin"
  require MINIO_SECRET_KEY "duckdock_secret"
  require CORS_ORIGINS ""
  if [ "${DUCKDOCK_ANALYSIS_WORKER_ENABLED:-false}" = "true" ]; then
    require DUCKDOCK_ANALYSIS_WORKER_TOKEN ""
  fi
  if [ "${MINIO_ACCESS_KEY:-}" = "${MINIO_ROOT_USER:-}" ]; then
    echo "  ✗ MINIO_ACCESS_KEY must not reuse MINIO_ROOT_USER"; bad=1
  fi
  if [ "${MINIO_SECRET_KEY:-}" = "${MINIO_ROOT_PASSWORD:-}" ]; then
    echo "  ✗ MINIO_SECRET_KEY must not reuse MINIO_ROOT_PASSWORD"; bad=1
  fi
  if printf '%s' "${CORS_ORIGINS:-}" | grep -Eq 'localhost|127\.0\.0\.1|\*'; then
    echo "  ✗ CORS_ORIGINS must use production origins, not localhost, 127.0.0.1, or *"; bad=1
  fi
  if [ "$bad" -ne 0 ]; then
    echo "✗ 预检未通过,拒绝以不安全配置启动生产。" >&2
    exit 1
  fi
  echo "  预检通过 ✓"
}

require_analysis_worker_token() {
  if [ -z "${DUCKDOCK_ANALYSIS_WORKER_TOKEN:-}" ] || [[ "${DUCKDOCK_ANALYSIS_WORKER_TOKEN:-}" == __CHANGE_ME* ]]; then
    echo "✗ 启动 analysis-worker 前必须在 /analysis 创建 Worker token,并写入 DUCKDOCK_ANALYSIS_WORKER_TOKEN。" >&2
    exit 1
  fi
}

case "${1:-up}" in
  preflight)
    preflight
    ;;
  up)
    preflight
    echo "→ 构建镜像 → 迁移(一次性)→ 启动 backend/worker/beat/frontend(生产)…"
    "${COMPOSE[@]}" up -d --build
    echo ""
    "${COMPOSE[@]}" ps
    echo ""
    echo "✓ 入口: http://localhost:${HTTP_PORT:-80}  —— 上线请置于 TLS 反向代理之后。"
    ;;
  worker)
    preflight
    require_analysis_worker_token
    echo "→ 启动 analysis-worker profile…"
    "${DC[@]}" -f docker-compose.prod.yml --env-file "$ENV_FILE" --profile analysis-worker up -d --build analysis-worker
    ;;
  build)   preflight; "${COMPOSE[@]}" build "${@:2}" ;;
  migrate) preflight; "${COMPOSE[@]}" run --rm migrate ;;
  down)    decrypt_env; "${COMPOSE[@]}" down -t 90 ;;
  status|ps) decrypt_env; "${COMPOSE[@]}" ps ;;
  logs)    decrypt_env; "${COMPOSE[@]}" logs -f --tail=100 "${@:2}" ;;
  shell)   decrypt_env; "${COMPOSE[@]}" exec "${2:-backend}" bash ;;
  backup)
    preflight
    ts="$(date +%Y%m%d-%H%M%S)"
    db_out="duckdock-mysql-${ts}.sql.gz"
    repos_out="duckdock-repos-${ts}.tar.gz"
    minio_out="duckdock-minio-${ts}.tar.gz"
    echo "→ 导出 MySQL 到 ${db_out} …"
    "${COMPOSE[@]}" exec -T mysql sh -c 'exec mysqldump -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE"' | gzip > "$db_out"
    # Git 裸仓库卷(repos_data)与 MinIO 对象卷(minio_data)用同一时间戳归档,
    # 借一次性 alpine 容器挂载命名卷并 tar 到 stdout(无需停服)。
    echo "→ 归档 Git 仓库卷(repos_data)到 ${repos_out} …"
    "${COMPOSE[@]}" run --rm --no-deps -T -v repos_data:/volume:ro --entrypoint sh backend -c 'exec tar -C /volume -czf - .' > "$repos_out"
    echo "→ 归档 MinIO 对象卷(minio_data)到 ${minio_out} …"
    "${COMPOSE[@]}" run --rm --no-deps -T -v minio_data:/volume:ro --entrypoint sh backend -c 'exec tar -C /volume -czf - .' > "$minio_out"
    echo "✓ 备份完成: ${db_out} + ${repos_out} + ${minio_out}(DB + Git 仓库 + MinIO 对象,同一时间戳 ${ts})"
    ;;
  help|-h|--help)
    echo "用法: bash scripts/prod.sh [preflight|up|worker|down|build|migrate|status|logs|shell|backup]"
    ;;
  *)
    echo "未知子命令: $1  (用 help 查看)" >&2; exit 1 ;;
esac
