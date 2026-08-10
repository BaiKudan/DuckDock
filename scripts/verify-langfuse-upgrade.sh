#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${DUCKDOCK_TEST_PYTHON:-$repo_root/.venv/bin/python}"
expected_server_version="${LANGFUSE_CANDIDATE_SERVER_VERSION:-4.1.0}"

if [[ ! -x "$python_bin" ]]; then
  echo "DuckDock test Python was not found: $python_bin" >&2
  echo "Create .venv and install backend/requirements-dev.txt first." >&2
  exit 1
fi

(
  cd "$repo_root/backend"
  "$python_bin" -m pytest \
    tests/test_langfuse_compatibility_contract.py \
    tests/test_langfuse_service.py \
    tests/test_langfuse_telemetry_sink.py \
    tests/test_otel_collector_reference.py \
    tests/test_evaluation_hub.py \
    tests/test_evaluation_annotation_queue.py \
    tests/test_evaluation_promotion.py \
    tests/test_evaluation_case_routing.py \
    tests/test_failure_taxonomy_experience.py \
    tests/test_semantic_failure_clustering.py \
    tests/test_semantic_clustering_regression.py \
    tests/test_evaluation_comparison.py \
    tests/test_release_candidate_evaluation_gate.py \
    -q
)

cd "$repo_root"
docker compose --profile observability up -d \
  postgres \
  clickhouse \
  langfuse-db-init \
  langfuse-minio-init \
  langfuse-worker \
  langfuse-web

for _ in $(seq 1 90); do
  all_healthy=true
  for service in langfuse-web langfuse-worker; do
    container_id="$(docker compose ps -q "$service")"
    health="$(
      docker inspect \
        --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
        "$container_id"
    )"
    if [[ "$health" != "healthy" ]]; then
      all_healthy=false
    fi
  done
  if [[ "$all_healthy" == true ]]; then
    break
  fi
  sleep 1
done

for service in langfuse-web langfuse-worker; do
  container_id="$(docker compose ps -q "$service")"
  if [[ -z "$container_id" ]]; then
    echo "Langfuse service did not start: $service" >&2
    exit 1
  fi
  health="$(
    docker inspect \
      --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
      "$container_id"
  )"
  if [[ "$health" != "healthy" ]]; then
    echo "Langfuse service is not healthy: $service status=$health" >&2
    exit 1
  fi
  actual_version="$(
    docker inspect \
      --format '{{index .Config.Labels "org.opencontainers.image.version"}}' \
      "$container_id"
  )"
  if [[ "$actual_version" != "$expected_server_version" ]]; then
    echo "$service version mismatch: expected=$expected_server_version actual=$actual_version" >&2
    exit 1
  fi
done

docker compose exec -T backend \
  python scripts/verify_langfuse_compatibility.py \
  --expected-server-version "$expected_server_version" \
  "$@"
