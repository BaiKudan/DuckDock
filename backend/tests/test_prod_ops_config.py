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
    assert "prom/prometheus:v3.5.0" in compose
    assert "./ops/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro" in compose
    assert "prometheus_data:/prometheus" in compose
    assert '127.0.0.1:${DUCKDOCK_PROMETHEUS_HOST_PORT:-9090}:9090' in compose
    assert "PROMETHEUS_BASE_URL=http://prometheus:9090" in env_example
    assert 'targets: ["backend:8801"]' in prometheus_config
    assert "environment: local-dev" not in prometheus_config


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
        "scripts/seed_handover_e2e.py",
        "scripts/verify_*_dev.py",
        "app/clinic_assets/fixtures/",
    ):
        assert entry in backend_ignore
    for entry in ("node_modules/", "dist/", "e2e/", "src/test/"):
        assert entry in frontend_ignore


def test_production_frontend_runtime_image_contains_only_built_assets():
    dockerfile = _read("frontend/Dockerfile.prod")
    runtime_stage = dockerfile.split("FROM nginx:", maxsplit=1)[1]

    assert "COPY --from=build /app/dist /usr/share/nginx/html" in runtime_stage
    assert "COPY . ." not in runtime_stage
    assert "npm ci" not in runtime_stage


def test_e2e_seed_has_no_production_override():
    seed_script = _read("backend/scripts/seed_handover_e2e.py")

    assert "if not settings.DEBUG:" in seed_script
    assert "--allow-non-debug" not in seed_script
    assert "E2E_ALLOW_SEED" not in seed_script


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
    assert "CORS_ORIGINS=[\"http://localhost" not in env_example


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
        has_embedded_beat = (
            "celery -A app.workers.celery_app worker" in compose and " -B " in compose
        )
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


def test_prod_restore_covers_mysql_repos_and_minio():
    restore_script = _read("scripts/restore.sh")

    assert "--db" in restore_script
    assert "--repos" in restore_script
    assert "--minio" in restore_script
    assert "mysql" in restore_script
    assert "repos_data" in restore_script
    assert "minio_data" in restore_script
    assert "SOPS_AGE_KEY_FILE" in restore_script


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
