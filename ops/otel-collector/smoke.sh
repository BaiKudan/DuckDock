#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

docker compose --profile telemetry up -d --force-recreate otel-collector

ready=false
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:${DUCKDOCK_OTEL_HEALTH_HOST_PORT:-13133}/ >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done
if [[ "$ready" != true ]]; then
  echo "OpenClaw shadow Collector did not become ready" >&2
  exit 1
fi

unauthenticated_status="$(
  curl -sS -o /dev/null -w '%{http_code}' \
    -H 'Content-Type: application/json' \
    --data-binary @ops/otel-collector/fixtures/openclaw-shadow-trace.json \
    "http://127.0.0.1:${DUCKDOCK_OTEL_HTTP_HOST_PORT:-4318}/v1/traces"
)"
if [[ "$unauthenticated_status" != "401" ]]; then
  echo "Expected unauthenticated OTLP request to return 401, got ${unauthenticated_status}" >&2
  exit 1
fi

curl -fsS \
  -u "${DUCKDOCK_OTEL_SMOKE_USERNAME:-duckdock-runtime-dev}:${DUCKDOCK_OTEL_SMOKE_PASSWORD:-duckdock-otel-dev}" \
  -H 'Content-Type: application/json' \
  --data-binary @ops/otel-collector/fixtures/openclaw-shadow-trace.json \
  "http://127.0.0.1:${DUCKDOCK_OTEL_HTTP_HOST_PORT:-4318}/v1/traces" \
  >/dev/null

logs=""
for _ in $(seq 1 10); do
  logs="$(docker compose logs --no-color --since 2m otel-collector)"
  if [[ "$logs" == *"run-shadow-001"* ]]; then
    break
  fi
  sleep 1
done

for expected in \
  "run-shadow-001" \
  "session-shadow-001" \
  "ns_openclaw_shadow_dev" \
  "rt_openclaw_shadow_dev" \
  "CHANNEL_AUTHENTICATED" \
  "COLLECTOR" \
  "duckdock-genai-shadow-v1+otel-semconv-1.40.0" \
  "session-openinference-001" \
  "qwen-openinference-shadow" \
  "openinference-shadow-provider" \
  "gen_ai.usage.input_tokens" \
  "gen_ai.usage.output_tokens" \
  "gen_ai.conversation.id"; do
  if [[ "$logs" != *"$expected"* ]]; then
    echo "Sanitized Collector output is missing expected marker: ${expected}" >&2
    exit 1
  fi
done

for forbidden in \
  "SHADOW_SECRET_CANARY_DO_NOT_EXPORT" \
  "forged-namespace" \
  "forged-runtime" \
  "forged-client-model" \
  "private user request" \
  "llm.input_messages" \
  "llm.output_messages" \
  "tool_call.function.arguments"; do
  if [[ "$logs" == *"$forbidden"* ]]; then
    echo "Sanitized Collector output leaked forbidden marker: ${forbidden}" >&2
    exit 1
  fi
done

printf '%s\n' "$logs" | python3 ops/otel-collector/verify_conformance.py

printf '%s\n' \
  '{"authenticated_status":200,"unauthenticated_status":401,"secret_canary_absent":true,"trusted_identity_injected":true,"openinference_normalized":true,"otel_genai_equivalent":true,"content_surfaces_removed":true}'
