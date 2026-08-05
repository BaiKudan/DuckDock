#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

container_name="duckdock-generic-otlp-smoke"
duckdock_mock_log="$(mktemp)"
provider_mock_log="$(mktemp)"
queue_dir="$(mktemp -d)"
duckdock_mock_pid=""
provider_mock_pid=""

cleanup() {
  docker rm -f "$container_name" >/dev/null 2>&1 || true
  for pid in "$duckdock_mock_pid" "$provider_mock_pid"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
      wait "$pid" >/dev/null 2>&1 || true
    fi
  done
  rm -f "$duckdock_mock_log" "$provider_mock_log"
  rm -rf "$queue_dir"
}
trap cleanup EXIT

chmod 0777 "$queue_dir"
DUCKDOCK_GENERIC_MOCK_PORT=14329 \
DUCKDOCK_GENERIC_MOCK_EXPECT=duckdock \
  python3 ops/otel-collector/mock_generic_otlp.py \
  >"$duckdock_mock_log" 2>&1 &
duckdock_mock_pid="$!"

docker rm -f "$container_name" >/dev/null 2>&1 || true
docker run -d --name "$container_name" \
  --add-host host.docker.internal:host-gateway \
  -e DUCKDOCK_OTEL_HTPASSWD='duckdock-runtime-dev:duckdock-otel-dev' \
  -e DUCKDOCK_OTEL_NAMESPACE_PUBLIC_ID='ns_generic_smoke' \
  -e DUCKDOCK_OTEL_RUNTIME_PUBLIC_ID='rt_generic_smoke' \
  -e DUCKDOCK_OTEL_QUEUE_MAX_BYTES='134217728' \
  -e DUCKDOCK_OTEL_QUEUE_SIZE='128' \
  -e DUCKDOCK_GENERIC_PROVIDER_OTLP_ENDPOINT='http://host.docker.internal:14330/provider' \
  -e DUCKDOCK_GENERIC_PROVIDER_TOKEN='provider-dev-token' \
  -e DUCKDOCK_API_BASE='http://host.docker.internal:14329' \
  -e DUCKDOCK_TELEMETRY_SINK_PUBLIC_ID='tsk_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' \
  -e DUCKDOCK_GENERIC_REPORTER_TOKEN='reporter-dev-token' \
  -v "$repo_root/ops/otel-collector/generic-otlp-bridge.yaml:/etc/otelcol/config.yaml:ro" \
  -v "$queue_dir:/var/lib/otelcol" \
  -p 127.0.0.1:14417:4317 \
  -p 127.0.0.1:14418:4318 \
  -p 127.0.0.1:14134:13133 \
  -p 127.0.0.1:14889:8888 \
  otel/opentelemetry-collector-contrib:0.157.0 \
  --config=/etc/otelcol/config.yaml >/dev/null

ready=false
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:14134/ >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done
if [[ "$ready" != true ]]; then
  docker logs "$container_name" >&2
  echo "Generic OTLP Collector did not become ready" >&2
  exit 1
fi

unauthenticated_status="$(
  curl -sS -o /dev/null -w '%{http_code}' \
    -H 'Content-Type: application/json' \
    --data-binary @ops/otel-collector/fixtures/generic-otlp-trace.json \
    http://127.0.0.1:14418/v1/traces
)"
if [[ "$unauthenticated_status" != "401" ]]; then
  echo "Expected unauthenticated OTLP request to return 401, got ${unauthenticated_status}" >&2
  exit 1
fi

curl -fsS \
  -u duckdock-runtime-dev:duckdock-otel-dev \
  -H 'Content-Type: application/json' \
  --data-binary @ops/otel-collector/fixtures/generic-otlp-trace.json \
  http://127.0.0.1:14418/v1/traces >/dev/null

duckdock_mock_finished=false
for _ in $(seq 1 30); do
  if ! kill -0 "$duckdock_mock_pid" >/dev/null 2>&1; then
    duckdock_mock_finished=true
    break
  fi
  sleep 1
done
if [[ "$duckdock_mock_finished" != true ]]; then
  echo "DuckDock projection mock did not receive its export" >&2
  exit 1
fi
if ! wait "$duckdock_mock_pid"; then
  cat "$duckdock_mock_log" >&2
  exit 1
fi
duckdock_mock_pid=""
cat "$duckdock_mock_log"

# The trace provider is deliberately still unavailable. DuckDock has already
# accepted its independent projection; wait until the provider queue proves
# the sanitized OTLP batch is durable, then recover the provider without a
# second producer request.
provider_queue_observed=false
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:14889/metrics |
    awk '
      /^otelcol_exporter_queue_size/ &&
      index($0, "otlp_http/provider") > 0 &&
      ($NF + 0) > 0 { found = 1 }
      END { exit !found }
    '
  then
    provider_queue_observed=true
    break
  fi
  sleep 1
done
if [[ "$provider_queue_observed" != true ]]; then
  echo "Provider outage did not create a pending durable queue item" >&2
  exit 1
fi

DUCKDOCK_GENERIC_MOCK_PORT=14330 \
DUCKDOCK_GENERIC_MOCK_EXPECT=provider \
  python3 ops/otel-collector/mock_generic_otlp.py \
  >"$provider_mock_log" 2>&1 &
provider_mock_pid="$!"

provider_mock_finished=false
for _ in $(seq 1 30); do
  if ! kill -0 "$provider_mock_pid" >/dev/null 2>&1; then
    provider_mock_finished=true
    break
  fi
  sleep 1
done
if [[ "$provider_mock_finished" != true ]]; then
  echo "Recovered provider did not receive the queued export" >&2
  exit 1
fi
if ! wait "$provider_mock_pid"; then
  cat "$provider_mock_log" >&2
  exit 1
fi
provider_mock_pid=""
cat "$provider_mock_log"

collector_logs="$(docker logs "$container_name" 2>&1)"
for forbidden in \
  GENERIC_SECRET_CANARY_DO_NOT_EXPORT \
  forged-namespace \
  forged-runtime \
  PRODUCER_ATTESTED; do
  if [[ "$collector_logs" == *"$forbidden"* ]]; then
    echo "Collector logs exposed forbidden marker: ${forbidden}" >&2
    exit 1
  fi
done

echo '{"generic_otlp_bridge":"passed","receiver_auth":"passed","dual_export":"passed","metadata_only":"passed","provider_outage_replay":"passed","second_producer_request":false}'
