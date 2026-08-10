#!/usr/bin/env bash
# Execute a disposable, three-zone Kubernetes rehearsal of DuckDock's stateless
# HA topology. This produces local-reference evidence and intentionally cannot
# satisfy the managed-state or target-production GA requirements.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

TASK_CLUSTER="duckdock-ga-ha"
TASK_OUTPUT="specs/016-ga-production-authorization/evidence/local-kubernetes-ha-rehearsal-20260805.json"
TASK_KEEP_CLUSTER=0
TASK_TMP="$(mktemp -d)"
TASK_KUBECONFIG="$TASK_TMP/kubeconfig"
TASK_CREATED=0
TASK_PROBE_PID=""
TASK_STOP_PROBE="$TASK_TMP/stop-probe"
TASK_PROBE_LOG="$TASK_TMP/probe.jsonl"

usage() {
  cat <<'EOF'
Usage: bash scripts/rehearse-kubernetes-ha.sh [options]

Options:
  --output PATH       Evidence JSON output path.
  --keep-cluster      Leave the disposable kind cluster running for inspection.
  -h, --help          Show this help.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --output)
      [ "$#" -ge 2 ] || { echo "--output requires a path" >&2; exit 2; }
      TASK_OUTPUT="$2"
      shift 2
      ;;
    --keep-cluster)
      TASK_KEEP_CLUSTER=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

cleanup() {
  touch "$TASK_STOP_PROBE" 2>/dev/null || true
  if [ -n "$TASK_PROBE_PID" ]; then
    wait "$TASK_PROBE_PID" >/dev/null 2>&1 || true
  fi
  if [ "$TASK_CREATED" -eq 1 ] && [ "$TASK_KEEP_CLUSTER" -eq 0 ]; then
    kind delete cluster --name "$TASK_CLUSTER" >/dev/null 2>&1 || true
  fi
  rm -rf "$TASK_TMP"
}
trap cleanup EXIT

for command_name in docker kind kubectl openssl python3; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Missing required command: $command_name" >&2
    exit 2
  }
done

if kind get clusters | grep -Fxq "$TASK_CLUSTER"; then
  echo "Refusing to reuse existing kind cluster $TASK_CLUSTER; remove or rename it first." >&2
  exit 2
fi

export KUBECONFIG="$TASK_KUBECONFIG"

echo "[ha-rehearsal] building exact local application images"
docker build -t duckdock-backend:2.0.0-rc.1 backend
docker build -t duckdock-frontend:2.0.0-rc.1 -f frontend/Dockerfile.prod frontend

echo "[ha-rehearsal] creating one control-plane and three fault-domain workers"
kind create cluster \
  --name "$TASK_CLUSTER" \
  --config ops/kubernetes/ha-rehearsal/kind.yaml \
  --wait 5m
TASK_CREATED=1

echo "[ha-rehearsal] loading immutable local inputs into every node"
kind load docker-image --name "$TASK_CLUSTER" \
  duckdock-backend:2.0.0-rc.1 \
  duckdock-frontend:2.0.0-rc.1

TASK_MYSQL_PASSWORD="$(openssl rand -hex 18)"
TASK_MINIO_PASSWORD="$(openssl rand -hex 18)"
TASK_SECRET_KEY="$(openssl rand -hex 32)"
TASK_CREDENTIAL_KEY="$(python3 -c 'import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"

kubectl apply -f ops/kubernetes/ha/namespace.yaml >/dev/null
kubectl create namespace duckdock-ha-deps >/dev/null
kubectl create secret generic duckdock-ha-deps-secrets \
  --namespace duckdock-ha-deps \
  --from-literal=MYSQL_DATABASE=duckdock \
  --from-literal=MYSQL_USER=duckdock \
  --from-literal=MYSQL_PASSWORD="$TASK_MYSQL_PASSWORD" \
  --from-literal=MYSQL_ROOT_PASSWORD="$TASK_MYSQL_PASSWORD" \
  --from-literal=MINIO_ROOT_USER=duckdock \
  --from-literal=MINIO_ROOT_PASSWORD="$TASK_MINIO_PASSWORD" >/dev/null

kubectl create secret generic duckdock-runtime-secrets \
  --namespace duckdock \
  --from-literal=APP_NAME=DuckDock \
  --from-literal=DEBUG=false \
  --from-literal=DATABASE_URL="mysql+aiomysql://duckdock:${TASK_MYSQL_PASSWORD}@mysql.duckdock-ha-deps.svc.cluster.local:3306/duckdock?charset=utf8mb4" \
  --from-literal=REDIS_URL=redis://redis.duckdock-ha-deps.svc.cluster.local:6379/0 \
  --from-literal=SECRET_KEY="$TASK_SECRET_KEY" \
  --from-literal=DUCKDOCK_CREDENTIAL_KEY="$TASK_CREDENTIAL_KEY" \
  --from-literal=MINIO_ENDPOINT=minio.duckdock-ha-deps.svc.cluster.local:9000 \
  --from-literal=MINIO_PUBLIC_ENDPOINT=minio.duckdock-ha-deps.svc.cluster.local:9000 \
  --from-literal=MINIO_ACCESS_KEY=duckdock \
  --from-literal=MINIO_SECRET_KEY="$TASK_MINIO_PASSWORD" \
  --from-literal=MINIO_BUCKET=duckdock \
  --from-literal=MINIO_SECURE=false \
  --from-literal=COMPONENT_MANAGER_ENABLED=false \
  --from-literal='CORS_ORIGINS=["https://duckdock.rehearsal.invalid"]' >/dev/null

echo "[ha-rehearsal] starting disposable single-node state dependencies"
kubectl apply -f ops/kubernetes/ha-rehearsal/dependencies.yaml >/dev/null
for dependency in mysql redis minio; do
  kubectl -n duckdock-ha-deps rollout status "deployment/$dependency" --timeout=5m
done
kubectl -n duckdock-ha-deps wait --for=condition=complete job/minio-init --timeout=5m
kubectl -n duckdock-ha-probe rollout status deployment/availability-probe --timeout=5m

echo "[ha-rehearsal] applying restricted HA application overlay"
kubectl apply --server-side -k ops/kubernetes/ha-rehearsal >/dev/null
kubectl -n duckdock wait --for=condition=complete job/duckdock-migrate --timeout=10m
for deployment in backend frontend worker beat; do
  kubectl -n duckdock rollout status "deployment/$deployment" --timeout=10m
done

probe_once() {
  kubectl -n duckdock-ha-probe exec deployment/availability-probe -- python -c '
import urllib.error
import urllib.request
assert urllib.request.urlopen("http://frontend.duckdock.svc.cluster.local:8080/health", timeout=3).status == 200
try:
    urllib.request.urlopen("http://frontend.duckdock.svc.cluster.local:8080/api/v1/auth/me", timeout=3)
except urllib.error.HTTPError as exc:
    assert exc.code == 401
else:
    raise AssertionError("unauthenticated API request unexpectedly succeeded")
' >/dev/null
}

probe_once
kubectl -n duckdock get pods -o json > "$TASK_TMP/before.json"
kubectl get nodes -o json > "$TASK_TMP/nodes.json"
kubectl -n duckdock get poddisruptionbudgets -o json > "$TASK_TMP/pdb.json"
kubectl -n duckdock get networkpolicies -o json > "$TASK_TMP/network-policies.json"
kubectl version -o json > "$TASK_TMP/kubernetes-version.json"

TASK_BEAT_NODE="$(kubectl -n duckdock get pods -l app.kubernetes.io/component=beat -o jsonpath='{.items[0].spec.nodeName}')"
TASK_DRAIN_ZONE="$(kubectl get node "$TASK_BEAT_NODE" -o jsonpath='{.metadata.labels.topology\.kubernetes\.io/zone}')"
TASK_DRAIN_STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
TASK_DRAIN_STARTED_EPOCH="$(date +%s)"

echo "[ha-rehearsal] continuously probing frontend and API while draining $TASK_BEAT_NODE ($TASK_DRAIN_ZONE)"
(
  while [ ! -e "$TASK_STOP_PROBE" ]; do
    TASK_SAMPLE_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    if probe_once; then
      printf '{"observed_at":"%s","passed":true}\n' "$TASK_SAMPLE_TIME" >> "$TASK_PROBE_LOG"
    else
      printf '{"observed_at":"%s","passed":false}\n' "$TASK_SAMPLE_TIME" >> "$TASK_PROBE_LOG"
    fi
    sleep 1
  done
) &
TASK_PROBE_PID="$!"

kubectl taint node "$TASK_BEAT_NODE" \
  duckdock.io/fault-domain-unavailable=true:NoSchedule \
  --overwrite >/dev/null
kubectl drain "$TASK_BEAT_NODE" \
  --ignore-daemonsets \
  --delete-emptydir-data \
  --force \
  --grace-period=10 \
  --timeout=8m

for deployment in backend frontend worker beat; do
  kubectl -n duckdock rollout status "deployment/$deployment" --timeout=8m
done
probe_once
TASK_DRAIN_RECOVERED_EPOCH="$(date +%s)"
TASK_DRAIN_RECOVERED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
touch "$TASK_STOP_PROBE"
wait "$TASK_PROBE_PID"
TASK_PROBE_PID=""
kubectl -n duckdock get pods -o json > "$TASK_TMP/after-drain.json"

TASK_BEAT_RECOVERY_NODE="$(kubectl -n duckdock get pods -l app.kubernetes.io/component=beat -o jsonpath='{.items[0].spec.nodeName}')"
if [ "$TASK_BEAT_RECOVERY_NODE" = "$TASK_BEAT_NODE" ]; then
  echo "Beat did not move away from the drained node." >&2
  exit 1
fi

echo "[ha-rehearsal] returning the zone and exercising rolling rebalance"
kubectl taint node "$TASK_BEAT_NODE" \
  duckdock.io/fault-domain-unavailable:NoSchedule- >/dev/null
kubectl uncordon "$TASK_BEAT_NODE"
for deployment in backend frontend worker; do
  kubectl -n duckdock rollout restart "deployment/$deployment" >/dev/null
done
for deployment in backend frontend worker; do
  kubectl -n duckdock rollout status "deployment/$deployment" --timeout=10m
done
probe_once
kubectl -n duckdock get pods -o json > "$TASK_TMP/restored.json"

export TASK_OUTPUT TASK_CLUSTER TASK_TMP TASK_PROBE_LOG TASK_BEAT_NODE
export TASK_BEAT_RECOVERY_NODE TASK_DRAIN_ZONE TASK_DRAIN_STARTED TASK_DRAIN_RECOVERED
export TASK_DRAIN_STARTED_EPOCH TASK_DRAIN_RECOVERED_EPOCH
export TASK_SOURCE_COMMIT="$(git rev-parse HEAD)"
export TASK_KIND_VERSION="$(kind version)"
export TASK_BACKEND_IMAGE_ID="$(docker image inspect duckdock-backend:2.0.0-rc.1 --format '{{.Id}}')"
export TASK_FRONTEND_IMAGE_ID="$(docker image inspect duckdock-frontend:2.0.0-rc.1 --format '{{.Id}}')"

python3 - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

tmp = Path(os.environ["TASK_TMP"])

def load(name: str):
    return json.loads((tmp / name).read_text(encoding="utf-8"))

def component_state(document: dict, component: str) -> dict:
    pods = [
        item
        for item in document["items"]
        if item.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/component") == component
        and item.get("metadata", {}).get("deletionTimestamp") is None
    ]
    rows = []
    for pod in pods:
        ready = any(
            condition.get("type") == "Ready" and condition.get("status") == "True"
            for condition in pod.get("status", {}).get("conditions", [])
        )
        rows.append(
            {
                "pod": pod["metadata"]["name"],
                "node": pod.get("spec", {}).get("nodeName"),
                "ready": ready,
            }
        )
    return {"ready_replicas": sum(row["ready"] for row in rows), "pods": rows}

nodes = load("nodes.json")
node_zones = {
    item["metadata"]["name"]: item["metadata"].get("labels", {}).get("topology.kubernetes.io/zone")
    for item in nodes["items"]
}

def snapshot(name: str) -> dict:
    document = load(name)
    components = {
        component: component_state(document, component)
        for component in ("backend", "frontend", "worker", "beat")
    }
    for value in components.values():
        for pod in value["pods"]:
            pod["zone"] = node_zones.get(pod["node"])
    return components

before = snapshot("before.json")
after = snapshot("after-drain.json")
restored = snapshot("restored.json")
probe_samples = [
    json.loads(line)
    for line in Path(os.environ["TASK_PROBE_LOG"]).read_text(encoding="utf-8").splitlines()
    if line.strip()
]
probe_failures = sum(not item.get("passed", False) for item in probe_samples)
replicas_ok = all(after[name]["ready_replicas"] >= 3 for name in ("backend", "frontend", "worker"))
restored_zones_ok = all(
    len({pod["zone"] for pod in restored[name]["pods"] if pod["ready"] and pod["zone"]}) >= 3
    for name in ("backend", "frontend", "worker")
)
beat_ok = (
    after["beat"]["ready_replicas"] == 1
    and os.environ["TASK_BEAT_NODE"] != os.environ["TASK_BEAT_RECOVERY_NODE"]
)
pdb = load("pdb.json")
pdb_minimums = {
    item["metadata"]["name"]: item["spec"].get("minAvailable")
    for item in pdb["items"]
}
network_policies = load("network-policies.json")
passed = replicas_ok and restored_zones_ok and beat_ok and probe_failures == 0 and len(probe_samples) > 0
result = {
    "schema_version": "duckdock-kubernetes-ha-failover-v1",
    "scope": "local-rehearsal",
    "status": "PASS_LOCAL_REFERENCE" if passed else "BLOCK",
    "passed": passed,
    "observed_at": datetime.now(timezone.utc).isoformat(),
    "target_environment": "local-kind-duckdock-ga-ha",
    "cluster_context": f"kind-{os.environ['TASK_CLUSTER']}",
    "source_commit": os.environ["TASK_SOURCE_COMMIT"],
    "kind_version": os.environ["TASK_KIND_VERSION"],
    "kubernetes_version": load("kubernetes-version.json")["serverVersion"]["gitVersion"],
    "images": {
        "backend": {"name": "duckdock-backend:2.0.0-rc.1", "local_image_id": os.environ["TASK_BACKEND_IMAGE_ID"]},
        "frontend": {"name": "duckdock-frontend:2.0.0-rc.1", "local_image_id": os.environ["TASK_FRONTEND_IMAGE_ID"]},
    },
    "fault_domains": sorted({zone for zone in node_zones.values() if zone}),
    "fault_domains_exercised": len({zone for zone in node_zones.values() if zone}),
    "replica_counts": {"backend": 3, "frontend": 3, "worker": 3, "beat": 1},
    "before": before,
    "after_zone_drain": after,
    "after_zone_return_and_rolling_rebalance": restored,
    "fault_injection": {
        "drained_node": os.environ["TASK_BEAT_NODE"],
        "drained_zone": os.environ["TASK_DRAIN_ZONE"],
        "started_at": os.environ["TASK_DRAIN_STARTED"],
        "recovered_at": os.environ["TASK_DRAIN_RECOVERED"],
        "recovery_seconds": int(os.environ["TASK_DRAIN_RECOVERED_EPOCH"]) - int(os.environ["TASK_DRAIN_STARTED_EPOCH"]),
        "node_failover_passed": replicas_ok and probe_failures == 0,
        "zone_failover_passed": replicas_ok and probe_failures == 0,
        "beat_recovery_passed": beat_ok,
        "beat_original_node": os.environ["TASK_BEAT_NODE"],
        "beat_recovery_node": os.environ["TASK_BEAT_RECOVERY_NODE"],
    },
    "availability_probe": {
        "transport": "in-cluster frontend Service health and unauthenticated API proxy probes",
        "sample_count": len(probe_samples),
        "failure_count": probe_failures,
        "passed": probe_failures == 0 and len(probe_samples) > 0,
    },
    "pdb_min_available": pdb_minimums,
    "network_policy": {
        "objects_admitted": len(network_policies["items"]),
        "enforcement_exercised": False,
        "detail": "kindnet admission is not proof of target CNI enforcement",
    },
    "state_services": {
        "managed_mysql_ha": False,
        "managed_redis_ha": False,
        "object_store_ha": False,
        "rwx_repository_storage_ha": False,
        "detail": "disposable single-node dependencies and emptyDir repository mounts are rehearsal-only",
    },
    "authorization_scope": "stateless Kubernetes scheduling/failover evidence only; cannot authorize production GA",
    "cleanup": "kind cluster deleted by trap unless --keep-cluster was explicitly supplied",
}
output = Path(os.environ["TASK_OUTPUT"])
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps({"status": result["status"], "output": str(output)}, sort_keys=True))
if not passed:
    raise SystemExit(1)
PY
