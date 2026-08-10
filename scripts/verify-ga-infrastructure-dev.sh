#!/usr/bin/env bash
# Build and exercise the production TLS/network/Alertmanager baseline in an
# isolated local Compose project. The normal DuckDock development data is never
# mounted or modified.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

TASK_OUTPUT="${1:-specs/016-ga-production-authorization/evidence/local-infrastructure-20260805.json}"
TASK_PROJECT="duckdock_ga_infrastructure"
TASK_TMP="$(mktemp -d)"
TASK_ENV="$TASK_TMP/ga-infrastructure.env"
TASK_CERT="$TASK_TMP/tls.crt"
TASK_KEY="$TASK_TMP/tls.key"
TASK_ALERT_CONFIG="$TASK_TMP/alertmanager.yml"
TASK_ALERT_RECEIPTS="$TASK_TMP/alert-receipts.jsonl"
TASK_TLS_REPORT="$TASK_TMP/tls-report.json"
TASK_SINK_PID=""

cleanup() {
  if [ -n "$TASK_SINK_PID" ]; then
    kill "$TASK_SINK_PID" >/dev/null 2>&1 || true
  fi
  DUCKDOCK_PROD_ENV_FILE="$TASK_ENV" docker compose \
    -p "$TASK_PROJECT" \
    --env-file "$TASK_ENV" \
    -f docker-compose.prod.yml \
    -f docker-compose.prod-tls.yml \
    down -v --remove-orphans -t 30 >/dev/null 2>&1 || true
  rm -rf "$TASK_TMP"
}
trap cleanup EXIT

openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 90 \
  -subj '/CN=duckdock.localtest.me' \
  -addext 'subjectAltName=DNS:duckdock.localtest.me,DNS:objects.duckdock.localtest.me' \
  -keyout "$TASK_KEY" \
  -out "$TASK_CERT" >/dev/null 2>&1

export TASK_ENV TASK_CERT TASK_KEY TASK_ALERT_CONFIG
python3 - <<'PY'
import base64
import os
import secrets
from pathlib import Path

source = Path(".env.prod.example").read_text(encoding="utf-8").splitlines()
values = {
    "SECRET_KEY": secrets.token_hex(32),
    "DUCKDOCK_CREDENTIAL_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
    "MYSQL_PASSWORD": secrets.token_urlsafe(24),
    "MYSQL_ROOT_PASSWORD": secrets.token_urlsafe(24),
    "MINIO_ROOT_PASSWORD": secrets.token_urlsafe(24),
    "MINIO_ACCESS_KEY": f"ga-infra-{secrets.token_hex(8)}",
    "MINIO_SECRET_KEY": secrets.token_urlsafe(32),
    # TLS terminates at the gateway; service-to-service MinIO traffic remains
    # on the isolated app/data networks over HTTP.
    "MINIO_SECURE": "false",
    "MINIO_PUBLIC_ENDPOINT": "objects.duckdock.localtest.me:18443",
    "MINIO_HOST_PORT": "19000",
    "MINIO_CONSOLE_HOST_PORT": "19001",
    "DUCKDOCK_PUBLIC_HOST": "duckdock.localtest.me",
    "DUCKDOCK_MINIO_PUBLIC_HOST": "objects.duckdock.localtest.me",
    "BACKEND_BASE_URL": "https://duckdock.localtest.me:18443",
    "CORS_ORIGINS": '["https://duckdock.localtest.me:18443"]',
    "HTTPS_BIND_ADDRESS": "127.0.0.1",
    "HTTPS_PORT": "18443",
    "HTTP_PORT": "18080",
    "DUCKDOCK_TLS_CERT_FILE": os.environ["TASK_CERT"],
    "DUCKDOCK_TLS_KEY_FILE": os.environ["TASK_KEY"],
    "DUCKDOCK_PROMETHEUS_HOST_PORT": "19090",
    "ALERTMANAGER_CONFIG_FILE": os.environ["TASK_ALERT_CONFIG"],
    "ALERTMANAGER_EXTERNAL_URL": "https://alerts.duckdock.localtest.me",
    "ALERTMANAGER_HOST_PORT": "19093",
    "DUCKDOCK_RELEASE_COMMIT": "0" * 40,
    "DUCKDOCK_BACKUP_DIR": os.environ.get("TMPDIR", "/tmp"),
    "DUCKDOCK_BACKUP_AGE_RECIPIENT": "age1localvalidationonly",
    "DUCKDOCK_BACKUP_SIGNING_KEY": os.environ["TASK_KEY"],
    "DUCKDOCK_BACKUP_S3_URI": "s3://local-validation-only/duckdock",
}
rendered = []
seen = set()
for line in source:
    if "=" in line and not line.lstrip().startswith("#"):
        key = line.split("=", 1)[0]
        if key in values:
            rendered.append(f"{key}={values[key]}")
            seen.add(key)
            continue
    rendered.append(line)
for key, value in values.items():
    if key not in seen:
        rendered.append(f"{key}={value}")
Path(os.environ["TASK_ENV"]).write_text("\n".join(rendered) + "\n", encoding="utf-8")
Path(os.environ["TASK_ALERT_CONFIG"]).write_text(
    """global:
  resolve_timeout: 1s
route:
  receiver: local-validation
  group_by: [alertname]
  group_wait: 1s
  group_interval: 1s
  repeat_interval: 1h
receivers:
  - name: local-validation
    webhook_configs:
      - url: http://host.docker.internal:18081/alerts
        send_resolved: true
""",
    encoding="utf-8",
)
PY
chmod 0600 "$TASK_ENV" "$TASK_KEY" "$TASK_ALERT_CONFIG"

python3 backend/scripts/verify_alert_delivery_dev.py \
  --host 0.0.0.0 \
  --port 18081 \
  --output "$TASK_ALERT_RECEIPTS" \
  > "$TASK_TMP/alert-sink.log" 2>&1 &
TASK_SINK_PID="$!"

for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:18081/health >/dev/null; then
    break
  fi
  sleep 1
done
curl -fsS http://127.0.0.1:18081/health >/dev/null

DUCKDOCK_PROD_ENV_FILE="$TASK_ENV" docker compose \
  -p "$TASK_PROJECT" \
  --env-file "$TASK_ENV" \
  -f docker-compose.prod.yml \
  -f docker-compose.prod-tls.yml \
  up -d --build

for _ in $(seq 1 120); do
  if curl --cacert "$TASK_CERT" -fsS \
      https://duckdock.localtest.me:18443/health >/dev/null \
    && curl --cacert "$TASK_CERT" -fsS \
      https://objects.duckdock.localtest.me:18443/minio/health/live >/dev/null \
    && docker exec "${TASK_PROJECT}-prometheus-1" \
      wget -qO- http://127.0.0.1:9090/-/ready >/dev/null \
    && curl -fsS http://127.0.0.1:19093/-/ready >/dev/null; then
    break
  fi
  sleep 2
done

TASK_SOURCE_COMMIT="$(git rev-parse HEAD)"
TASK_BACKEND_IMAGE_ID="$(docker inspect "${TASK_PROJECT}-backend-1" --format '{{.Image}}')"
TASK_FRONTEND_IMAGE_ID="$(docker inspect "${TASK_PROJECT}-frontend-1" --format '{{.Image}}')"
if ! python3 backend/scripts/probe_ga_target_tls.py \
    --app-url https://duckdock.localtest.me:18443/health \
    --object-store-url https://objects.duckdock.localtest.me:18443/minio/health/live \
    --scope local-validation \
    --target-environment "$TASK_PROJECT" \
    --source-commit "$TASK_SOURCE_COMMIT" \
    --backend-image "local/duckdock/backend@${TASK_BACKEND_IMAGE_ID}" \
    --frontend-image "local/duckdock/frontend@${TASK_FRONTEND_IMAGE_ID}" \
    --ca-file "$TASK_CERT" \
    --output "$TASK_TLS_REPORT" >/dev/null; then
  cat "$TASK_TLS_REPORT" >&2
  exit 2
fi

TASK_NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
TASK_LATER="$(python3 -c 'from datetime import datetime,timedelta,timezone; print((datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat().replace("+00:00","Z"))')"
TASK_ALERT_NAME="DuckDockGALocalDelivery$(date -u +%Y%m%d%H%M%S)"
curl -fsS -X POST http://127.0.0.1:19093/api/v2/alerts \
  -H 'Content-Type: application/json' \
  --data "[{\"labels\":{\"alertname\":\"$TASK_ALERT_NAME\",\"severity\":\"critical\"},\"annotations\":{\"summary\":\"GA local delivery validation\"},\"startsAt\":\"$TASK_NOW\",\"endsAt\":\"$TASK_LATER\"}]" >/dev/null

for _ in $(seq 1 30); do
  [ "$(wc -l < "$TASK_ALERT_RECEIPTS" 2>/dev/null || echo 0)" -ge 1 ] && break
  sleep 1
done
curl -fsS -X POST http://127.0.0.1:19093/api/v2/alerts \
  -H 'Content-Type: application/json' \
  --data "[{\"labels\":{\"alertname\":\"$TASK_ALERT_NAME\",\"severity\":\"critical\"},\"annotations\":{\"summary\":\"GA local delivery validation\"},\"startsAt\":\"$TASK_NOW\",\"endsAt\":\"$TASK_NOW\"}]" >/dev/null
for _ in $(seq 1 30); do
  [ "$(wc -l < "$TASK_ALERT_RECEIPTS" 2>/dev/null || echo 0)" -ge 2 ] && break
  sleep 1
done

export TASK_OUTPUT TASK_TLS_REPORT TASK_ALERT_RECEIPTS TASK_PROJECT
python3 - <<'PY'
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

tls = json.loads(Path(os.environ["TASK_TLS_REPORT"]).read_text(encoding="utf-8"))
receipts = [
    json.loads(line)
    for line in Path(os.environ["TASK_ALERT_RECEIPTS"]).read_text(encoding="utf-8").splitlines()
    if line.strip()
]
statuses = [receipt.get("group_status") for receipt in receipts]
compose = subprocess.run(
    [
        "docker", "compose", "-p", os.environ["TASK_PROJECT"],
        "--env-file", os.environ["TASK_ENV"],
        "-f", "docker-compose.prod.yml", "-f", "docker-compose.prod-tls.yml",
        "ps", "--format", "json",
    ],
    stdout=subprocess.PIPE,
    check=True,
    text=True,
).stdout.splitlines()
services = [json.loads(line) for line in compose if line.strip()]
unhealthy = [item.get("Service") for item in services if item.get("State") != "running"]
passed = tls.get("status") == "PASS" and "firing" in statuses and "resolved" in statuses and not unhealthy
result = {
    "schema_version": "duckdock-ga-local-infrastructure-v1",
    "status": "PASS" if passed else "BLOCK",
    "observed_at": datetime.now(timezone.utc).isoformat(),
    "isolated_compose_project": os.environ["TASK_PROJECT"],
    "tls": tls,
    "alert_delivery": {
        "passed": "firing" in statuses and "resolved" in statuses,
        "receipt_statuses": statuses,
        "receipt_count": len(receipts),
    },
    "services": [
        {"service": item.get("Service"), "state": item.get("State"), "health": item.get("Health")}
        for item in services
    ],
    "cleanup": "isolated project and all dedicated volumes removed by trap",
}
output = Path(os.environ["TASK_OUTPUT"])
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps({"status": result["status"], "output": str(output)}, sort_keys=True))
if not passed:
    raise SystemExit(2)
PY
