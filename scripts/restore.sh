#!/usr/bin/env bash
# Restore a DuckDock production backup set created by scripts/prod.sh backup.
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

usage() {
  cat <<'EOF'
Usage:
  bash scripts/restore.sh --db duckdock-mysql-YYYYmmdd-HHMMSS.sql.gz \
    --repos duckdock-repos-YYYYmmdd-HHMMSS.tar.gz \
    --minio duckdock-minio-YYYYmmdd-HHMMSS.tar.gz

Requires .env.prod.enc plus SOPS_AGE_KEY_FILE or SOPS_AGE_KEY. The restore
stops application services, restores MySQL/repos_data/minio_data, then starts
the stack again.
EOF
}

DB_BACKUP=""
REPOS_BACKUP=""
MINIO_BACKUP=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --db) DB_BACKUP="${2:-}"; shift 2 ;;
    --repos) REPOS_BACKUP="${2:-}"; shift 2 ;;
    --minio) MINIO_BACKUP="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

for file in "$DB_BACKUP" "$REPOS_BACKUP" "$MINIO_BACKUP"; do
  if [ -z "$file" ] || [ ! -f "$file" ]; then
    echo "Missing restore artifact: ${file:-<empty>}" >&2
    usage
    exit 1
  fi
done

if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  echo "Docker Compose is required." >&2
  exit 1
fi
COMPOSE=("${DC[@]}" -f docker-compose.prod.yml --env-file "$ENV_FILE")

if [ ! -f "$ENV_ENC_FILE" ]; then
  echo "Missing $ENV_ENC_FILE. Restore requires the encrypted production env." >&2
  exit 1
fi
if ! command -v sops >/dev/null 2>&1; then
  echo "sops is required for production restore." >&2
  exit 1
fi
if [ -z "${SOPS_AGE_KEY_FILE:-}" ] && [ -z "${SOPS_AGE_KEY:-}" ]; then
  echo "Set SOPS_AGE_KEY_FILE or SOPS_AGE_KEY before restore." >&2
  exit 1
fi
umask 077
sops -d "$ENV_ENC_FILE" > "$ENV_FILE"
ENV_FROM_SOPS=1
set -a; . "$ENV_FILE"; set +a

echo "Stopping application services..."
"${COMPOSE[@]}" stop frontend analysis-worker backend worker beat || true

echo "Ensuring stateful services are running..."
"${COMPOSE[@]}" up -d mysql redis minio

echo "Restoring MySQL from $DB_BACKUP ..."
gzip -dc "$DB_BACKUP" | "${COMPOSE[@]}" exec -T mysql sh -c 'exec mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE"'

echo "Restoring repos_data from $REPOS_BACKUP ..."
cat "$REPOS_BACKUP" | "${COMPOSE[@]}" run --rm --no-deps -T -u 0 -v repos_data:/volume --entrypoint sh backend -c 'rm -rf /volume/* /volume/.[!.]* /volume/..?* 2>/dev/null || true; tar -C /volume -xzf -; chown -R appuser:appuser /volume'

echo "Restoring minio_data from $MINIO_BACKUP ..."
cat "$MINIO_BACKUP" | "${COMPOSE[@]}" run --rm --no-deps -T -u 0 -v minio_data:/volume --entrypoint sh backend -c 'rm -rf /volume/* /volume/.[!.]* /volume/..?* 2>/dev/null || true; tar -C /volume -xzf -'

echo "Starting production stack..."
"${COMPOSE[@]}" up -d
"${COMPOSE[@]}" ps
echo "Restore complete."
