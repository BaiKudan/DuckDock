#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

cleanup() {
  docker compose stop otel-collector-langfuse >/dev/null 2>&1 || true
  docker compose rm -sf otel-langfuse-queue-init >/dev/null 2>&1 || true
  docker compose rm -sf otel-langfuse-mock >/dev/null 2>&1 || true
}
trap cleanup EXIT

export DUCKDOCK_LANGFUSE_PUBLIC_KEY=pk-shadow-export
export DUCKDOCK_LANGFUSE_SECRET_KEY=sk-shadow-export
export DUCKDOCK_LANGFUSE_OTLP_ENDPOINT="http://otel-langfuse-mock:14319/api/public/otel"

docker compose --profile telemetry-test up \
  -d --force-recreate otel-langfuse-mock

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
  echo "Langfuse exporter Collector did not become ready" >&2
  exit 1
fi

unauthenticated_status="$(
  curl -sS -o /dev/null -w '%{http_code}' \
    -H 'Content-Type: application/json' \
    --data-binary @ops/otel-collector/fixtures/openclaw-shadow-trace.json \
    "http://127.0.0.1:${DUCKDOCK_LANGFUSE_OTEL_HTTP_HOST_PORT:-14328}/v1/traces"
)"
if [[ "$unauthenticated_status" != "401" ]]; then
  echo "Expected unauthenticated OTLP request to return 401, got ${unauthenticated_status}" >&2
  exit 1
fi

curl -fsS \
  -u "${DUCKDOCK_OTEL_SMOKE_USERNAME:-duckdock-runtime-dev}:${DUCKDOCK_OTEL_SMOKE_PASSWORD:-duckdock-otel-dev}" \
  -H 'Content-Type: application/json' \
  --data-binary @ops/otel-collector/fixtures/openclaw-shadow-trace.json \
  "http://127.0.0.1:${DUCKDOCK_LANGFUSE_OTEL_HTTP_HOST_PORT:-14328}/v1/traces" \
  >/dev/null

mock_container="$(docker compose ps -q otel-langfuse-mock)"
for _ in $(seq 1 30); do
  if [[ "$(docker inspect -f '{{.State.Status}}' "$mock_container")" == "exited" ]]; then
    break
  fi
  sleep 1
done
if [[ "$(docker inspect -f '{{.State.Status}}' "$mock_container")" != "exited" ]]; then
  echo "Langfuse mock did not receive an export" >&2
  exit 1
fi

mock_exit_code="$(docker inspect -f '{{.State.ExitCode}}' "$mock_container")"
docker compose logs --no-color otel-langfuse-mock
if [[ "$mock_exit_code" != "0" ]]; then
  echo "Langfuse mock rejected the Collector export" >&2
  exit 1
fi
