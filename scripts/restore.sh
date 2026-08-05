#!/usr/bin/env bash
# Restore a DuckDock production backup set created by scripts/prod.sh backup.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

ENV_ENC_FILE="${DUCKDOCK_PROD_ENV_ENC:-.env.prod.enc}"
ENV_FILE="${DUCKDOCK_PROD_ENV_FILE:-.env.prod}"
ENV_FROM_SOPS=0
RESTORE_TMP=""

cleanup_env() {
  if [ "$ENV_FROM_SOPS" = "1" ] && [ -f "$ENV_FILE" ]; then
    rm -f "$ENV_FILE"
  fi
  if [ -n "$RESTORE_TMP" ] && [ -d "$RESTORE_TMP" ]; then
    rm -rf "$RESTORE_TMP"
  fi
}
trap cleanup_env EXIT

usage() {
  cat <<'EOF'
Usage:
  bash scripts/restore.sh --bundle duckdock-backup-YYYYmmdd-HHMMSS \
    --allowed-signers /etc/duckdock/backup-allowed-signers \
    --signer-identity backup-operator@example.com \
    --age-key /etc/duckdock/backup-age-key.txt

Legacy plaintext form (staging only):
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
BUNDLE=""
ALLOWED_SIGNERS=""
SIGNER_IDENTITY=""
BACKUP_AGE_KEY="${DUCKDOCK_BACKUP_AGE_KEY_FILE:-}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --bundle) BUNDLE="${2:-}"; shift 2 ;;
    --allowed-signers) ALLOWED_SIGNERS="${2:-}"; shift 2 ;;
    --signer-identity) SIGNER_IDENTITY="${2:-}"; shift 2 ;;
    --age-key) BACKUP_AGE_KEY="${2:-}"; shift 2 ;;
    --db) DB_BACKUP="${2:-}"; shift 2 ;;
    --repos) REPOS_BACKUP="${2:-}"; shift 2 ;;
    --minio) MINIO_BACKUP="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

if [ -n "$BUNDLE" ]; then
  for command in age ssh-keygen python3; do
    command -v "$command" >/dev/null 2>&1 || {
      echo "$command is required for secure bundle restore" >&2
      exit 1
    }
  done
  for file in \
    "$BUNDLE/manifest.json" \
    "$BUNDLE/manifest.json.sig" \
    "$BUNDLE/mysql.sql.gz.age" \
    "$BUNDLE/repos.tar.gz.age" \
    "$BUNDLE/minio.tar.gz.age" \
    "$ALLOWED_SIGNERS" \
    "$BACKUP_AGE_KEY"; do
    if [ -z "$file" ] || [ ! -r "$file" ]; then
      echo "Missing secure restore artifact/key: ${file:-<empty>}" >&2
      exit 1
    fi
  done
  if [ -z "$SIGNER_IDENTITY" ]; then
    echo "--signer-identity is required for secure bundle restore" >&2
    exit 1
  fi
  ssh-keygen -Y verify \
    -f "$ALLOWED_SIGNERS" \
    -I "$SIGNER_IDENTITY" \
    -n duckdock-backup \
    -s "$BUNDLE/manifest.json.sig" \
    < "$BUNDLE/manifest.json"
  RESTORE_TMP="$(mktemp -d)"
  export BUNDLE RESTORE_TMP
  python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

bundle = Path(os.environ["BUNDLE"])
manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
if manifest.get("schema_version") != "duckdock-secure-backup-v1":
    raise SystemExit("unsupported backup manifest schema")
expected_names = {"mysql.sql.gz.age", "repos.tar.gz.age", "minio.tar.gz.age"}
artifacts = manifest.get("artifacts")
if not isinstance(artifacts, dict) or set(artifacts) != expected_names:
    raise SystemExit("backup manifest artifact set mismatch")
def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()
for name in expected_names:
    path = bundle / name
    actual = digest(path)
    if actual != artifacts[name].get("encrypted_sha256"):
        raise SystemExit(f"encrypted backup digest mismatch: {name}")
PY
  age -d -i "$BACKUP_AGE_KEY" -o "$RESTORE_TMP/mysql.sql.gz" "$BUNDLE/mysql.sql.gz.age"
  age -d -i "$BACKUP_AGE_KEY" -o "$RESTORE_TMP/repos.tar.gz" "$BUNDLE/repos.tar.gz.age"
  age -d -i "$BACKUP_AGE_KEY" -o "$RESTORE_TMP/minio.tar.gz" "$BUNDLE/minio.tar.gz.age"
  python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

bundle = Path(os.environ["BUNDLE"])
restored = Path(os.environ["RESTORE_TMP"])
manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
mapping = {
    "mysql.sql.gz.age": "mysql.sql.gz",
    "repos.tar.gz.age": "repos.tar.gz",
    "minio.tar.gz.age": "minio.tar.gz",
}
def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()
for encrypted_name, plain_name in mapping.items():
    actual = digest(restored / plain_name)
    if actual != manifest["artifacts"][encrypted_name].get("plaintext_sha256"):
        raise SystemExit(f"decrypted backup digest mismatch: {plain_name}")
PY
  DB_BACKUP="$RESTORE_TMP/mysql.sql.gz"
  REPOS_BACKUP="$RESTORE_TMP/repos.tar.gz"
  MINIO_BACKUP="$RESTORE_TMP/minio.tar.gz"
else
  for file in "$DB_BACKUP" "$REPOS_BACKUP" "$MINIO_BACKUP"; do
    if [ -z "$file" ] || [ ! -f "$file" ]; then
      echo "Missing restore artifact: ${file:-<empty>}" >&2
      usage
      exit 1
    fi
  done
fi

if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  echo "Docker Compose is required." >&2
  exit 1
fi
COMPOSE=(
  "${DC[@]}"
  -f docker-compose.prod.yml
  -f docker-compose.prod-tls.yml
  --env-file "$ENV_FILE"
)

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
"${COMPOSE[@]}" stop tls-gateway frontend analysis-worker backend worker beat || true

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
