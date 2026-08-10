#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

cleanup() {
  docker compose stop otel-collector-langfuse >/dev/null 2>&1 || true
  docker compose rm -sf otel-collector-langfuse >/dev/null 2>&1 || true
  docker compose rm -sf otel-langfuse-queue-init >/dev/null 2>&1 || true
  docker compose rm -sf otel-langfuse-mock >/dev/null 2>&1 || true
}
trap cleanup EXIT

export DUCKDOCK_LANGFUSE_PUBLIC_KEY=pk-shadow-export
export DUCKDOCK_LANGFUSE_SECRET_KEY=sk-shadow-export
export DUCKDOCK_LANGFUSE_OTLP_ENDPOINT="http://otel-langfuse-mock:14319/api/public/otel"
export DUCKDOCK_OTEL_QUEUE_SIZE=32
export DUCKDOCK_OTEL_QUEUE_MAX_BYTES=16777216

cleanup
compose_project="$(
  docker compose config --format json |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])'
)"
queue_volume="$(
  docker volume ls -q \
    --filter "label=com.docker.compose.project=${compose_project}" \
    --filter label=com.docker.compose.volume=otel_langfuse_queue_data
)"
if [[ -n "$queue_volume" ]]; then
  docker volume rm "$queue_volume" >/dev/null
fi

# Start without a provider endpoint. The OTLP response acknowledges local WAL
# admission, not downstream Langfuse acceptance.
docker compose --profile telemetry-langfuse up \
  -d --force-recreate otel-collector-langfuse

ready=false
for _ in $(seq 1 30); do
  if curl -fsS \
    "http://127.0.0.1:${DUCKDOCK_LANGFUSE_OTEL_HEALTH_HOST_PORT:-14133}/" \
    >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done
if [[ "$ready" != true ]]; then
  echo "Persistent Collector did not become ready" >&2
  exit 1
fi

curl -fsS \
  -u "${DUCKDOCK_OTEL_SMOKE_USERNAME:-duckdock-runtime-dev}:${DUCKDOCK_OTEL_SMOKE_PASSWORD:-duckdock-otel-dev}" \
  -H 'Content-Type: application/json' \
  --data-binary @ops/otel-collector/fixtures/openclaw-shadow-trace.json \
  "http://127.0.0.1:${DUCKDOCK_LANGFUSE_OTEL_HTTP_HOST_PORT:-14328}/v1/traces" \
  >/dev/null

# Do not mistake an initialized empty bbolt file for a persisted batch. Wait
# until Collector's own queue metric proves the request is still pending.
queue_observed=false
for _ in $(seq 1 30); do
  if curl -fsS \
    "http://127.0.0.1:${DUCKDOCK_LANGFUSE_OTEL_METRICS_HOST_PORT:-18888}/metrics" |
    awk '
      /^otelcol_exporter_queue_size/ &&
      index($0, "otlp_http/langfuse") > 0 &&
      ($NF + 0) > 0 { found = 1 }
      END { exit !found }
    '
  then
    queue_observed=true
    break
  fi
  sleep 1
done
if [[ "$queue_observed" != true ]]; then
  echo "Collector did not report a pending persistent queue item" >&2
  exit 1
fi

queue_volume="$(
  docker volume ls -q \
    --filter "label=com.docker.compose.project=${compose_project}" \
    --filter label=com.docker.compose.volume=otel_langfuse_queue_data
)"
if [[ -z "$queue_volume" ]]; then
  echo "Collector persistent queue volume was not created" >&2
  exit 1
fi

wal_persisted=false
for _ in $(seq 1 30); do
  if docker run --rm \
    -v "${queue_volume}:/queue:ro" \
    python:3.12-alpine \
    sh -c 'find /queue -type f -size +0c -print -quit | grep -q .' \
    >/dev/null 2>&1; then
    wal_persisted=true
    break
  fi
  sleep 1
done
if [[ "$wal_persisted" != true ]]; then
  echo "No non-empty Collector WAL file was observed" >&2
  exit 1
fi

if docker run --rm \
  -v "${queue_volume}:/queue:ro" \
  python:3.12-alpine \
  sh -c \
  'grep -R -a -E "sk-shadow-export|duckdock-otel-dev" /queue >/dev/null 2>&1'
then
  echo "A transport credential was persisted in the Collector WAL" >&2
  exit 1
fi

# Simulate an ungraceful process crash, then prove the existing WAL is replayed
# after restart without a second OTLP producer request.
collector_container="$(docker compose ps -q otel-collector-langfuse)"
docker kill --signal KILL "$collector_container" >/dev/null
docker compose rm -sf otel-collector-langfuse >/dev/null

docker compose --profile telemetry-test up \
  -d --force-recreate otel-langfuse-mock
docker compose --profile telemetry-langfuse up \
  -d --force-recreate otel-collector-langfuse

mock_container="$(docker compose ps -q otel-langfuse-mock)"
for _ in $(seq 1 30); do
  if [[ "$(docker inspect -f '{{.State.Status}}' "$mock_container")" == "exited" ]]; then
    break
  fi
  sleep 1
done
if [[ "$(docker inspect -f '{{.State.Status}}' "$mock_container")" != "exited" ]]; then
  echo "Restarted Collector did not replay the WAL" >&2
  exit 1
fi

mock_exit_code="$(docker inspect -f '{{.State.ExitCode}}' "$mock_container")"
docker compose logs --no-color otel-langfuse-mock
if [[ "$mock_exit_code" != "0" ]]; then
  echo "Replayed export failed the Langfuse protocol checks" >&2
  exit 1
fi

printf '%s\n' \
  '{"credential_absent_from_wal":true,"queue_metric_observed":true,"restart_replayed":true,"second_producer_request":false,"wal_persisted":true}'
