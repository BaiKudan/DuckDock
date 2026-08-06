from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def test_prod_minio_template_uses_separate_app_credentials():
    env_example = _read(".env.prod.example")

    assert "MINIO_ROOT_USER=duckdock-root" in env_example
    assert "MINIO_ACCESS_KEY=__CHANGE_ME_APP_ACCESS_KEY__" in env_example
    assert "MINIO_SECRET_KEY=__CHANGE_ME_APP_SECRET_KEY__" in env_example
    assert "Do not reuse MINIO_ROOT_*" in env_example


def test_prod_compose_provisions_minio_app_user_before_backend_starts():
    compose = _read("docker-compose.prod.yml")

    assert "minio-init:" in compose
    assert "mc admin user add duckdock" in compose
    assert "mc admin policy attach duckdock duckdock-app --user" in compose
    assert compose.count("minio-init:") >= 3
    assert compose.count("condition: service_completed_successfully") >= 3


def test_prod_preflight_rejects_minio_root_reuse():
    prod_script = _read("scripts/prod.sh")

    assert 'require MINIO_ACCESS_KEY "duckdock minioadmin"' in prod_script
    assert 'MINIO_ACCESS_KEY:-}" = "${MINIO_ROOT_USER:-}' in prod_script
    assert 'MINIO_SECRET_KEY:-}" = "${MINIO_ROOT_PASSWORD:-}' in prod_script


def test_prod_preflight_requires_sops_age_encrypted_env():
    prod_script = _read("scripts/prod.sh")

    assert ".env.prod.enc" in prod_script
    assert "preflight|up|worker|down|build|migrate|status|logs|shell|backup" in prod_script
    assert "preflight)" in prod_script
    assert "SOPS_AGE_KEY_FILE" in prod_script
    assert "SOPS_AGE_KEY" in prod_script
    assert "不得回退裸 .env.prod" in prod_script
    assert "sops -d" in prod_script


def test_prod_compose_has_backend_and_frontend_healthchecks():
    compose = _read("docker-compose.prod.yml")

    assert "http://127.0.0.1:8801/readyz" in compose
    assert "start_period: 30s" in compose
    assert "wget -qO- http://127.0.0.1:8080/health" in compose
    assert "backend:\n        condition: service_healthy" in compose


def test_prod_compose_deploys_the_prometheus_readiness_dependency():
    compose = _read("docker-compose.prod.yml")
    env_example = _read(".env.prod.example")
    prometheus_config = _read("ops/prometheus/prometheus.yml")

    assert "  prometheus:" in compose
    assert "prom/prometheus:v3.13.2" in compose
    assert "./ops/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro" in compose
    assert "prometheus_data:/prometheus" in compose
    assert "127.0.0.1:${DUCKDOCK_PROMETHEUS_HOST_PORT:-9090}:9090" in compose
    assert "PROMETHEUS_BASE_URL=http://prometheus:9090" in env_example
    assert 'targets: ["backend:8801"]' in prometheus_config
    assert "environment: local-dev" not in prometheus_config


def test_production_exposes_only_the_tls_gateway_publicly():
    compose = _read("docker-compose.prod.yml")
    tls_compose = _read("docker-compose.prod-tls.yml")

    assert "127.0.0.1:${HTTP_PORT:-8080}:8080" in compose
    assert "127.0.0.1:${MINIO_HOST_PORT:-9000}:9000" in compose
    assert '"${HTTPS_BIND_ADDRESS:-0.0.0.0}:${HTTPS_PORT:-443}:8443"' in tls_compose
    assert "ssl_protocols TLSv1.2 TLSv1.3" in _read("ops/tls-gateway/duckdock-tls.conf.template")
    assert "DUCKDOCK_TLS_CERT_FILE" in _read("scripts/prod.sh")
    assert "openssl x509" in _read("scripts/prod.sh")


def test_production_networks_separate_data_app_and_observability():
    compose = _read("docker-compose.prod.yml")

    for name in ("app_internal", "data_internal", "observability_internal"):
        assert f"  {name}:\n    internal: true" in compose
    assert "networks: [data_internal]" in compose
    assert "networks: [app_internal]" in compose
    assert "networks: [observability_internal]" in compose


def test_prometheus_routes_alerts_to_alertmanager_and_watches_delivery():
    compose = _read("docker-compose.prod.yml")
    prometheus = _read("ops/prometheus/prometheus.yml")
    alerts = _read("ops/prometheus/duckdock-alerts.yml")

    assert "  alertmanager:" in compose
    assert "alertmanager:9093" in prometheus
    assert "DuckDockAlertmanagerUnavailable" in alerts
    assert "DuckDockAlertDeliveryFailing" in alerts
    assert "DuckDockRunTimelineLatencyHigh" in alerts
    assert '"/bin/amtool"' in compose
    assert '"--alertmanager.url=http://127.0.0.1:9093"' in compose
    assert '"alert"' in compose and '"query"' in compose
    assert "__CHANGE_ME_ONCALL_WEBHOOK__" in _read("ops/alertmanager/alertmanager.example.yml")


def test_third_party_production_images_are_digest_pinned():
    compose = _read("docker-compose.prod.yml")
    image_lines = [line.strip() for line in compose.splitlines() if line.strip().startswith("image:")]

    assert image_lines
    assert all("@sha256:" in line for line in image_lines)


def test_alertmanager_is_rebuilt_from_exact_source_with_security_fixes():
    dockerfile = _read("ops/alertmanager/Dockerfile")

    assert dockerfile.count("2c8da51e03f3dbbed24f9711ca2d76aab4eef9c5") >= 3
    assert "ARG ALERTMANAGER_SOURCE_COMMIT" not in dockerfile
    assert "golang.org/x/crypto@v0.52.0" in dockerfile
    assert "google.golang.org/grpc@v1.82.1" in dockerfile
    assert "FROM scratch" in dockerfile
    assert "USER 65534:65534" in dockerfile


def test_prod_frontend_does_not_hardcode_a_loopback_prometheus_link():
    page = _read("frontend/src/pages/OperationsPage.tsx")
    dockerfile = _read("frontend/Dockerfile.prod")
    compose = _read("docker-compose.prod.yml")

    assert "import.meta.env.VITE_PROMETHEUS_URL" in page
    assert 'href="http://127.0.0.1:9090"' not in page
    assert 'ARG VITE_PROMETHEUS_URL=""' in dockerfile
    assert "VITE_PROMETHEUS_URL: ${VITE_PROMETHEUS_URL:-}" in compose


def test_prod_compose_allows_graceful_shutdown_window_for_app_services():
    compose = _read("docker-compose.prod.yml")

    assert compose.count("stop_grace_period: 90s") >= 3
    assert "--timeout-graceful-shutdown 90" in compose


def test_prod_down_uses_soft_shutdown_timeout():
    prod_script = _read("scripts/prod.sh")

    assert '"${COMPOSE[@]}" down -t 90' in prod_script


def test_backend_requires_celery_soft_shutdown_version():
    requirements = _read("backend/requirements.txt")

    assert "celery>=5.5.0" in requirements


def test_production_image_context_excludes_test_and_local_validation_artifacts():
    backend_ignore = _read("backend/.dockerignore")
    frontend_ignore = _read("frontend/.dockerignore")

    for entry in (
        "**/__pycache__/",
        "**/*.py[cod]",
        "tests/",
        ".pytest_cache/",
        ".mypy_cache/",
        ".venv/",
        "scripts/collect_ga_target_readiness.py",
        "scripts/collect_ga_target_ha.py",
        "scripts/collect_ga_state_services_ha.py",
        "scripts/ga_state_services_evidence.py",
        "scripts/collect_ga_target_alerting.py",
        "scripts/collect_ga_target_network.py",
        "scripts/collect_ga_target_network_evidence.py",
        "scripts/ga_network_evidence.py",
        "scripts/collect_ga_target_secrets.py",
        "scripts/collect_ga_target_recovery.py",
        "scripts/collect_ga_independent_security.py",
        "scripts/ga_security_assessment.py",
        "scripts/seed_handover_e2e.py",
        "scripts/g2_target_capacity_gate.py",
        "scripts/collect_ga_target_capacity.py",
        "scripts/ga_capacity_evidence.py",
        "scripts/collect_ga_target_tls.py",
        "scripts/ga_tls_evidence.py",
        "scripts/ga_release_identity.py",
        "scripts/collect_ga_release_provenance.py",
        "scripts/ga_release_provenance.py",
        "scripts/probe_ga_target_tls.py",
        "scripts/verify_ga_production_authorization.py",
        "scripts/verify_*_dev.py",
        "app/clinic_assets/fixtures/",
    ):
        assert entry in backend_ignore
    for entry in ("node_modules/", "dist/", "e2e/", "src/test/"):
        assert entry in frontend_ignore


def test_final_tag_reruns_full_gate_and_publishes_attested_images():
    workflow = _read(".github/workflows/ci.yml")

    assert "tags:\n      - v2.0.0" in workflow
    assert "needs: [backend, frontend, e2e, compose]" in workflow
    assert "Verify final signed tag" in workflow
    assert "'.verification.verified'" in workflow
    assert workflow.count("github.ref == 'refs/tags/v2.0.0'") >= 10
    assert workflow.count("provenance: mode=max") == 4
    assert workflow.count("provenance: mode=max,version=v1") == 4
    assert workflow.count("sbom: true") == 4
    assert workflow.count("sarif-file:") == 4
    assert workflow.count(
        "docker/scout-action@7c6b6c3f7844478ace1ffd4e7aef649053d1f87d"
    ) == 4
    assert "Retain final vulnerability scan reports" in workflow
    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in workflow
    assert "if-no-files-found: error" in workflow
    assert "retention-days: 90" in workflow
    assert "scan_artifact_digest=${{ steps.upload_scan_reports.outputs.artifact-digest }}" in workflow
    assert workflow.count('context: "{{defaultContext}}:') == 4
    assert workflow.count("tags: ${{ github.ref == 'refs/tags/v2.0.0'") == 4
    assert workflow.count("build-{0}', github.sha") == 4
    assert "Promote scanned image indexes to previously unused final tags" in workflow
    assert "final tag already exists; refusing overwrite" in workflow
    assert "could not prove final tag is absent" in workflow
    assert workflow.index("Scan patched Alertmanager") < workflow.index(
        "Retain final vulnerability scan reports"
    )
    assert workflow.index("Retain final vulnerability scan reports") < workflow.index(
        "Promote scanned image indexes to previously unused final tags"
    )
    assert "Resolve deployable platform image digests" in workflow
    for image in (
        "duckdock-backend:2.0.0",
        "duckdock-frontend:2.0.0",
        "duckdock-tls-gateway:2.0.0",
        "duckdock-alertmanager:0.33.1-duckdock.1",
    ):
        assert f"ghcr.io/baikudan/{image}" in workflow


def test_production_frontend_runtime_image_contains_only_built_assets():
    dockerfile = _read("frontend/Dockerfile.prod")
    runtime_stage = dockerfile.split("FROM nginx:", maxsplit=1)[1]

    assert "COPY --from=build /app/dist /usr/share/nginx/html" in runtime_stage
    assert "COPY . ." not in runtime_stage
    assert "npm ci" not in runtime_stage


def test_production_images_use_numeric_non_root_users_for_kubernetes():
    backend = _read("backend/Dockerfile")
    frontend = _read("frontend/Dockerfile.prod")

    assert "addgroup -S -g 10001 appuser" in backend
    assert "adduser -S -D -H -u 10001 -G appuser appuser" in backend
    assert "USER 10001:10001" in backend
    assert "USER 101:101" in frontend
    assert "USER appuser" not in backend
    assert "USER nginx" not in frontend


def test_e2e_seed_has_no_production_override():
    seed_script = _read("backend/scripts/seed_handover_e2e.py")

    assert "if not settings.DEBUG:" in seed_script
    assert "--allow-non-debug" not in seed_script
    assert "E2E_ALLOW_SEED" not in seed_script
    assert "await engine.dispose()" in seed_script


def test_backend_exposes_readyz_route():
    from app.main import app

    assert any(getattr(route, "path", None) == "/readyz" for route in app.routes)


@pytest.mark.asyncio
async def test_readyz_returns_200_when_dependencies_are_ready(monkeypatch):
    from app import main

    async def ok():
        return None

    monkeypatch.setattr(main, "_check_mysql", ok)
    monkeypatch.setattr(main, "_check_redis", ok)
    monkeypatch.setattr(main, "_check_minio", ok)

    response = await main.readyz()

    assert response["ready"] is True
    assert {item["name"] for item in response["checks"]} == {"mysql", "redis", "minio"}


@pytest.mark.asyncio
async def test_readyz_returns_503_when_dependency_is_down(monkeypatch):
    from app import main

    async def ok():
        return None

    async def fail():
        raise RuntimeError("redis refused connection")

    monkeypatch.setattr(main, "_check_mysql", ok)
    monkeypatch.setattr(main, "_check_redis", fail)
    monkeypatch.setattr(main, "_check_minio", ok)

    response = await main.readyz()

    assert response.status_code == 503
    payload = json.loads(response.body)
    assert payload["ready"] is False
    assert any(item["name"] == "redis" and item["ok"] is False for item in payload["checks"])


def test_frontend_nginx_exposes_container_health_endpoint():
    nginx_conf = _read("frontend/nginx.conf")

    assert "location = /health" in nginx_conf
    assert 'return 200 "ok\\n";' in nginx_conf


def test_prod_template_requires_public_cors_origins():
    env_example = _read(".env.prod.example")

    assert 'CORS_ORIGINS=["https://your-domain.example.com"]' in env_example
    assert 'CORS_ORIGINS=["http://localhost' not in env_example


def test_prod_preflight_rejects_default_or_wildcard_cors():
    prod_script = _read("scripts/prod.sh")

    assert 'require CORS_ORIGINS ""' in prod_script
    assert "localhost|127\\.0\\.0\\.1|\\*" in prod_script


def test_compose_deploys_celery_beat_scheduler_for_reaper():
    # PUSH-01: a `celery worker` does NOT execute beat_schedule entries, so without a
    # dedicated beat runner the analysis-job lease reaper never fires. Both the dev and
    # prod stacks MUST deploy a scheduler — guard against silently dropping it.
    for compose_file in ("docker-compose.yml", "docker-compose.prod.yml"):
        compose = _read(compose_file)
        has_beat_service = "celery -A app.workers.celery_app beat" in compose
        has_embedded_beat = "celery -A app.workers.celery_app worker" in compose and " -B " in compose
        assert has_beat_service or has_embedded_beat, (
            f"{compose_file} deploys no celery beat scheduler; the lease reaper would never run"
        )


def test_prod_compose_deploys_analysis_worker():
    compose = _read("docker-compose.prod.yml")

    assert "analysis-worker:" in compose
    match = re.search(r"  analysis-worker:\n(?P<body>.*?)(?:\n  [a-zA-Z0-9_-]+:|\nvolumes:)", compose, re.S)
    assert match is not None
    body = match.group("body")
    assert 'profiles: ["analysis-worker"]' in body
    assert "docs/agent-control-plane-prd/duckdock-analysis-worker" in compose
    assert "DUCKDOCK_ANALYSIS_WORKER_TOKEN" in compose
    assert "--analysis-mode" in compose


def test_prod_script_starts_analysis_worker_profile_only_on_demand():
    prod_script = _read("scripts/prod.sh")

    assert "worker)" in prod_script
    assert "--profile analysis-worker up -d --build analysis-worker" in prod_script
    assert "require_analysis_worker_token" in prod_script


def test_dev_analysis_worker_waits_for_backend_health():
    compose = _read("docker-compose.yml")
    match = re.search(r"  analysis-worker:\n(?P<body>.*?)(?:\n  [a-zA-Z0-9_-]+:|\n  # ──)", compose, re.S)
    assert match is not None
    body = match.group("body")

    assert "healthcheck:" in compose
    assert "http://127.0.0.1:8801/health" in compose
    assert "backend:" in body
    assert "condition: service_healthy" in body


def test_dev_script_starts_beat_service():
    dev_script = _read("scripts/dev.sh")

    assert "up -d backend worker beat frontend" in dev_script


def test_compose_pins_memory_limits_on_stateful_and_worker_services():
    # OPS-04: without deploy.resources.limits.memory a single runaway service (a fat
    # mysql query, a leaking worker, minio caching) can OOM the whole host. Pin sane
    # ceilings on mysql / minio / worker in BOTH dev and prod stacks.
    for compose_file in ("docker-compose.yml", "docker-compose.prod.yml"):
        compose = _read(compose_file)
        assert "deploy:" in compose, f"{compose_file} declares no deploy block"
        # mysql (1g), minio (512m), worker (1g) — env-overridable where supported.
        assert "MYSQL_MEM_LIMIT:-1g" in compose, f"{compose_file} mysql lacks a memory limit"
        assert "MINIO_MEM_LIMIT:-512m" in compose, f"{compose_file} minio lacks a memory limit"
        assert "WORKER_MEM_LIMIT:-1g" in compose, f"{compose_file} worker lacks a memory limit"
        # three services pinned ⇒ at least three limits.memory lines.
        assert compose.count("memory:") >= 3, f"{compose_file} pins fewer than 3 memory limits"


def test_prod_backup_archives_repos_and_minio_volumes():
    # OPS-06: a DB-only dump silently loses the Git bare repos and MinIO objects. The
    # backup must capture repos_data and minio_data under the SAME timestamp.
    prod_script = _read("scripts/prod.sh")

    assert "repos_data" in prod_script
    assert "minio_data" in prod_script
    assert "duckdock-repos-${ts}.tar.gz" in prod_script
    assert "duckdock-minio-${ts}.tar.gz" in prod_script


def test_prod_backup_is_encrypted_signed_and_sent_offsite():
    prod_script = _read("scripts/prod.sh")
    seal_script = _read("scripts/seal-backup.sh")

    assert "DUCKDOCK_BACKUP_AGE_RECIPIENT" in prod_script
    assert "DUCKDOCK_BACKUP_SIGNING_KEY" in prod_script
    assert "DUCKDOCK_BACKUP_S3_URI" in prod_script
    assert "ssh-keygen -Y sign" in seal_script
    assert 'age -r "$TASK_AGE_RECIPIENT"' in seal_script
    assert 'aws s3 cp "$TASK_BUNDLE"' in seal_script
    assert "duckdock-secure-backup-v1" in seal_script


def test_prod_restore_covers_mysql_repos_and_minio():
    restore_script = _read("scripts/restore.sh")

    assert "--db" in restore_script
    assert "--repos" in restore_script
    assert "--minio" in restore_script
    assert "mysql" in restore_script
    assert "repos_data" in restore_script
    assert "minio_data" in restore_script
    assert "SOPS_AGE_KEY_FILE" in restore_script
    assert "ssh-keygen -Y verify" in restore_script
    assert "encrypted_sha256" in restore_script
    assert "plaintext_sha256" in restore_script


def test_kubernetes_ha_reference_has_cross_zone_safety_controls():
    workloads = _read("ops/kubernetes/ha/workloads.yaml")
    availability = _read("ops/kubernetes/ha/availability.yaml")
    policies = _read("ops/kubernetes/ha/network-policies.yaml")

    assert workloads.count("replicas: 3") >= 3
    assert workloads.count("topology.kubernetes.io/zone") >= 3
    assert workloads.count("whenUnsatisfiable: DoNotSchedule") >= 3
    assert workloads.count("nodeTaintsPolicy: Honor") >= 3
    assert workloads.count("matchLabelKeys: [pod-template-hash]") >= 3
    assert workloads.count("automountServiceAccountToken: false") >= 5
    assert availability.count("kind: PodDisruptionBudget") == 3
    assert availability.count("kind: HorizontalPodAutoscaler") == 3
    assert "name: default-deny" in policies
    assert "controlled-external-egress" in policies
    assert "0.0.0.0/0" in policies
    assert "production gate rejects an unmodified target report" in policies


def test_kubernetes_ha_rehearsal_is_non_authorizing_and_kubernetes_native():
    script = _read("scripts/rehearse-kubernetes-ha.sh")
    nginx = _read("ops/kubernetes/ha-rehearsal/frontend-kubernetes.conf")
    kustomization = _read("ops/kubernetes/ha-rehearsal/kustomization.yaml")

    assert '"schema_version": "duckdock-kubernetes-ha-failover-v1"' in script
    assert '"scope": "local-rehearsal"' in script
    assert '"enforcement_exercised": False' in script
    assert '"managed_mysql_ha": False' in script
    assert "backend.duckdock.svc.cluster.local" in nginx
    assert "resolver 127.0.0.11" not in nginx
    assert "emptyDir" in kustomization


def test_frontend_nginx_resolves_backend_dynamically():
    # OPS-07: a literal proxy_pass host is resolved once at startup; after a backend
    # restart the cached IP goes stale and nginx returns permanent 502s. Use the docker
    # embedded DNS resolver + a variable upstream so nginx re-resolves per request.
    nginx_conf = _read("frontend/nginx.conf")

    assert "resolver 127.0.0.11 valid=10s;" in nginx_conf
    # variable upstream forces runtime re-resolution (literal proxy_pass would not).
    assert "set $backend_upstream" in nginx_conf
    assert "proxy_pass http://$backend_upstream" in nginx_conf
    # keep existing behaviour intact.
    assert "location = /health" in nginx_conf
    assert "location /api/" in nginx_conf


def test_frontend_nginx_sets_security_headers():
    nginx_conf = _read("frontend/nginx.conf")

    assert "Strict-Transport-Security" in nginx_conf
    assert "X-Frame-Options" in nginx_conf
    assert "X-Content-Type-Options" in nginx_conf
    assert "Content-Security-Policy" in nginx_conf
    assert "frame-ancestors 'none'" in nginx_conf
