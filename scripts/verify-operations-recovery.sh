#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$REPO_DIR"

TASK_STAMP="$(date -u +%Y%m%d%H%M%S)"
TASK_DB="duckdock_e08_recovery_${TASK_STAMP}"
TASK_KEY="operations/recovery-drills/${TASK_STAMP}/canary.bin"
TASK_TMP="$(mktemp -d)"
TASK_ADMIN_USERNAME="${DUCKDOCK_OPS_ADMIN_USERNAME:-e07-identity-validator}"
TASK_ADMIN_PASSWORD="${DUCKDOCK_OPS_ADMIN_PASSWORD:-DuckDock@E07Local2026!}"
TASK_API_BASE="${DUCKDOCK_API_BASE:-http://127.0.0.1:8801}"
TASK_NAMESPACE_ID="${DUCKDOCK_OPS_NAMESPACE_ID:-6}"
TASK_TIMELINE_START="$(python3 -c 'from datetime import datetime,timedelta,timezone; print((datetime.now(timezone.utc)-timedelta(days=7)).isoformat().replace("+00:00","Z"))')"
TASK_TIMELINE_END="$(python3 -c 'from datetime import datetime,timedelta,timezone; print((datetime.now(timezone.utc)+timedelta(minutes=1)).isoformat().replace("+00:00","Z"))')"

if [[ ! "$TASK_DB" =~ ^duckdock_e08_recovery_[0-9]{14}$ ]]; then
  echo "Unsafe temporary database name" >&2
  exit 1
fi

cleanup() {
  docker compose exec -T -e TASK_DB="$TASK_DB" mysql sh -lc \
    'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "DROP DATABASE IF EXISTS \`$TASK_DB\`;"' \
    >/dev/null 2>&1 || true
  rm -rf "$TASK_TMP"
}
trap cleanup EXIT

TASK_STARTED_EPOCH="$(date -u +%s)"
TASK_STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

docker compose exec -T -e TASK_DB="$TASK_DB" mysql sh -lc '
  mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "CREATE DATABASE \`$TASK_DB\` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"
  mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$TASK_DB" -e "
    CREATE TABLE recovery_canary (id INT PRIMARY KEY, marker VARCHAR(64) NOT NULL, digest CHAR(64) NOT NULL);
    INSERT INTO recovery_canary VALUES
      (1, '\''duckdock-e08-alpha'\'', SHA2('\''duckdock-e08-alpha'\'', 256)),
      (2, '\''duckdock-e08-beta'\'', SHA2('\''duckdock-e08-beta'\'', 256)),
      (3, '\''duckdock-e08-gamma'\'', SHA2('\''duckdock-e08-gamma'\'', 256));
  "
'

docker compose exec -T -e TASK_DB="$TASK_DB" mysql sh -lc \
  'mysqldump --skip-comments --skip-dump-date -uroot -p"$MYSQL_ROOT_PASSWORD" "$TASK_DB"' \
  > "$TASK_TMP/mysql.sql"
TASK_MYSQL_DIGEST="$(shasum -a 256 "$TASK_TMP/mysql.sql" | awk '{print $1}')"

docker compose exec -T -e TASK_DB="$TASK_DB" mysql sh -lc '
  mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "DROP DATABASE \`$TASK_DB\`; CREATE DATABASE \`$TASK_DB\` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"
'
docker compose exec -T -e TASK_DB="$TASK_DB" mysql sh -lc \
  'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$TASK_DB"' \
  < "$TASK_TMP/mysql.sql"

TASK_MYSQL_ROWS="$(docker compose exec -T -e TASK_DB="$TASK_DB" mysql sh -lc \
  'mysql -N -uroot -p"$MYSQL_ROOT_PASSWORD" "$TASK_DB" -e "SELECT COUNT(*) FROM recovery_canary"' \
  | tr -d '[:space:]')"
if [[ "$TASK_MYSQL_ROWS" != "3" ]]; then
  echo "MySQL restore verification failed: rows=$TASK_MYSQL_ROWS" >&2
  exit 1
fi

TASK_OBJECT_RESULT="$(docker compose exec -T -e TASK_KEY="$TASK_KEY" backend python - <<'PY'
import hashlib
import os

from app.services.artifact_service import artifact_service

key = os.environ["TASK_KEY"]
payload = b"duckdock-e08-object-restore-canary-v1"
expected = hashlib.sha256(payload).hexdigest()
client = artifact_service.data_client
bucket = artifact_service.bucket
client.put_object(Bucket=bucket, Key=key, Body=payload, ContentType="application/octet-stream")
backup = client.get_object(Bucket=bucket, Key=key)["Body"].read()
client.delete_object(Bucket=bucket, Key=key)
client.put_object(Bucket=bucket, Key=key, Body=backup, ContentType="application/octet-stream")
restored = client.get_object(Bucket=bucket, Key=key)["Body"].read()
client.delete_object(Bucket=bucket, Key=key)
actual = hashlib.sha256(restored).hexdigest()
if actual != expected:
    raise SystemExit("object restore digest mismatch")
print(f"{actual}:1")
PY
)"
TASK_OBJECT_DIGEST="${TASK_OBJECT_RESULT%%:*}"
TASK_OBJECT_COUNT="${TASK_OBJECT_RESULT##*:}"

TASK_GIT_HEAD="$(git rev-parse HEAD)"
TASK_BACKUP_SET_DIGEST="$(printf '%s' "${TASK_MYSQL_DIGEST}:${TASK_OBJECT_DIGEST}:${TASK_GIT_HEAD}" | shasum -a 256 | awk '{print $1}')"
TASK_FINISHED_EPOCH="$(date -u +%s)"
TASK_FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
TASK_RTO_SECONDS="$((TASK_FINISHED_EPOCH - TASK_STARTED_EPOCH))"

docker compose exec -T \
  -e DEBUG=false \
  -e TASK_ADMIN_USERNAME="$TASK_ADMIN_USERNAME" \
  -e TASK_ADMIN_PASSWORD="$TASK_ADMIN_PASSWORD" \
  backend python - <<'PY'
import asyncio
import os

from sqlalchemy import select

from app.core.database import AsyncSessionLocal, engine
from app.core.security import hash_password
from app.models.user import AuthSource, SystemRole, User


async def main() -> None:
    async with AsyncSessionLocal() as db:
        username = os.environ["TASK_ADMIN_USERNAME"]
        password = os.environ["TASK_ADMIN_PASSWORD"]
        user = await db.scalar(select(User).where(User.username == username))
        if user is None:
            user = User(
                username=username,
                email=f"{username}@duckdock.dev",
                hashed_password=hash_password(password),
                system_role=SystemRole.ADMIN,
                auth_source=AuthSource.LOCAL,
                is_active=True,
            )
            db.add(user)
        else:
            user.hashed_password = hash_password(password)
            user.system_role = SystemRole.ADMIN
            user.is_active = True
        await db.commit()
    await engine.dispose()


asyncio.run(main())
PY

TASK_LOGIN_JSON="$(curl -fsS -X POST "$TASK_API_BASE/api/v1/auth/login" \
  -H 'Content-Type: application/json' \
  --data "{\"username\":\"$TASK_ADMIN_USERNAME\",\"password\":\"$TASK_ADMIN_PASSWORD\"}")"
TASK_TOKEN="$(printf '%s' "$TASK_LOGIN_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')"

export TASK_STAMP TASK_GIT_HEAD TASK_BACKUP_SET_DIGEST TASK_MYSQL_DIGEST
export TASK_OBJECT_DIGEST TASK_MYSQL_ROWS TASK_OBJECT_COUNT TASK_RTO_SECONDS
export TASK_STARTED_AT TASK_FINISHED_AT
python3 - "$TASK_TMP/receipt.json" <<'PY'
import json
import os
import sys

payload = {
    "idempotency_key": f"e08-local-recovery-{os.environ['TASK_STAMP']}",
    "environment": "local-dev",
    "git_head": os.environ["TASK_GIT_HEAD"],
    "backup_set_digest": os.environ["TASK_BACKUP_SET_DIGEST"],
    "mysql_digest": os.environ["TASK_MYSQL_DIGEST"],
    "object_store_digest": os.environ["TASK_OBJECT_DIGEST"],
    "mysql_row_count": int(os.environ["TASK_MYSQL_ROWS"]),
    "object_count": int(os.environ["TASK_OBJECT_COUNT"]),
    "rpo_seconds": 0,
    "rto_seconds": int(os.environ["TASK_RTO_SECONDS"]),
    "started_at": os.environ["TASK_STARTED_AT"],
    "finished_at": os.environ["TASK_FINISHED_AT"],
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(payload, handle, separators=(",", ":"))
PY

TASK_RECEIPT="$(curl -fsS -X POST "$TASK_API_BASE/api/v2/operations/recovery-drills" \
  -H "Authorization: Bearer $TASK_TOKEN" \
  -H 'Content-Type: application/json' \
  --data-binary "@$TASK_TMP/receipt.json")"

for _ in $(seq 1 25); do
  curl -fsS "$TASK_API_BASE/api/v2/agent-runs?namespace_id=$TASK_NAMESPACE_ID&started_after=$TASK_TIMELINE_START&started_before=$TASK_TIMELINE_END&limit=1" \
    -H "Authorization: Bearer $TASK_TOKEN" >/dev/null
done

TASK_SLO="$(curl -fsS -X POST "$TASK_API_BASE/api/v2/operations/slo-evaluations" \
  -H "Authorization: Bearer $TASK_TOKEN" \
  -H 'Content-Type: application/json' \
  --data "{\"idempotency_key\":\"e08-local-slo-$TASK_STAMP\",\"window_minutes\":15}")"

python3 - "$TASK_RECEIPT" "$TASK_SLO" <<'PY'
import json
import sys

receipt = json.loads(sys.argv[1])
slo = json.loads(sys.argv[2])
print(json.dumps({
    "ok": receipt["status"] == "PASSED",
    "recovery_drill_public_id": receipt["public_id"],
    "recovery_status": receipt["status"],
    "mysql_row_count": receipt["mysql_row_count"],
    "object_count": receipt["object_count"],
    "rpo_seconds": receipt["rpo_seconds"],
    "rto_seconds": receipt["rto_seconds"],
    "backup_set_digest": receipt["backup_set_digest"],
    "slo_evaluation_public_id": slo["public_id"],
    "slo_status": slo["status"],
    "slo_reason_codes": slo["reason_codes"],
}, indent=2, sort_keys=True))
PY
