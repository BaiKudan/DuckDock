#!/usr/bin/env python3
"""Run all locally executable GA preflight gates and emit a non-authorizing receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
FRONTEND_ROOT = REPO_ROOT / "frontend"
SCHEMA_VERSION = "duckdock-ga-local-preflight-v1"
AUTHORIZATION_SCOPE = (
    "non-authorizing local preflight; target evidence, independent assessment, "
    "organizational approvals and GA publication remain mandatory"
)
MYPY_RESULT_RE = re.compile(r"Found (?P<count>[0-9]+) errors?\b")
EXTERNAL_REQUIREMENTS = (
    {
        "key": "final_release_supply_chain",
        "status": "PENDING_EXTERNAL",
        "required_evidence": "signed v2.0.0 tag, final CI run, immutable image digests, SLSA, SBOM and scan",
    },
    {
        "key": "target_deployment_controls",
        "status": "PENDING_EXTERNAL",
        "required_evidence": "target TLS, Secrets, network isolation, alert delivery/on-call and immutable backup receipts",
    },
    {
        "key": "target_capacity_and_growth",
        "status": "PENDING_EXTERNAL",
        "required_evidence": "sustained target HTTPS load, datastore growth and independent cleanup receipts",
    },
    {
        "key": "target_ha_and_fault_domains",
        "status": "PENDING_EXTERNAL",
        "required_evidence": "target Kubernetes zone failure and managed state-service failover verification",
    },
    {
        "key": "independent_security_assessment",
        "status": "PENDING_EXTERNAL",
        "required_evidence": "Security-authorized third-party assessment, remediation retest and data deletion proof",
    },
    {
        "key": "four_party_authorization",
        "status": "PENDING_EXTERNAL",
        "required_evidence": "distinct Product, Architecture, Security and Operations signatures on one frozen campaign",
    },
)


@dataclass(frozen=True)
class CommandCheck:
    key: str
    command: tuple[str, ...]
    cwd: Path
    timeout_seconds: int = 900
    env: dict[str, str] = field(default_factory=dict)
    evaluator: str = "exit_zero"
    required_services: tuple[str, ...] = ()
    forbidden_services: tuple[str, ...] = ()


@dataclass(frozen=True)
class CheckResult:
    key: str
    status: str
    command: list[str]
    cwd: str
    duration_ms: int
    exit_code: int
    detail: str
    log_path: str
    log_sha256: str
    log_size: int
    environment_keys: list[str]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def output_artifact_manifest(output_dir: Path) -> list[dict[str, object]]:
    artifacts: list[dict[str, object]] = []
    for current_root, directory_names, file_names in os.walk(output_dir, followlinks=False):
        current = Path(current_root)
        for name in directory_names:
            directory = current / name
            if directory.is_symlink():
                raise ValueError(f"symbolic-link output artifact is forbidden: {directory}")
        for name in file_names:
            path = current / name
            if path.is_symlink():
                raise ValueError(f"symbolic-link output artifact is forbidden: {path}")
            payload = path.read_bytes()
            artifacts.append(
                {
                    "path": str(path.relative_to(output_dir)),
                    "sha256": sha256_bytes(payload),
                    "size": len(payload),
                }
            )
    return sorted(artifacts, key=lambda item: str(item["path"]))


def harden_output_tree(output_dir: Path) -> None:
    for current_root, directory_names, file_names in os.walk(output_dir, followlinks=False):
        current = Path(current_root)
        if current.is_symlink():
            raise ValueError(f"symbolic-link output directory is forbidden: {current}")
        current.chmod(0o700)
        for name in directory_names:
            directory = current / name
            if directory.is_symlink():
                raise ValueError(f"symbolic-link output directory is forbidden: {directory}")
            directory.chmod(0o700)
        for name in file_names:
            path = current / name
            if path.is_symlink():
                raise ValueError(f"symbolic-link output artifact is forbidden: {path}")
            path.chmod(0o600)


def run_git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def source_state() -> dict[str, str]:
    dirty = run_git("status", "--porcelain=v1", "--untracked-files=all")
    if dirty:
        raise ValueError("working tree must be clean before a GA local preflight")
    return {
        "commit": run_git("rev-parse", "HEAD"),
        "tree": run_git("rev-parse", "HEAD^{tree}"),
        "branch": run_git("branch", "--show-current") or "DETACHED",
        "version": json.loads((FRONTEND_ROOT / "package.json").read_text(encoding="utf-8"))["version"],
    }


def validate_output_dir(path: Path) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    if resolved.exists():
        raise ValueError("output directory must not already exist")
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise ValueError("output directory must be outside the repository")
    return resolved


def python_bin() -> str:
    preferred = BACKEND_ROOT / ".venv" / "bin" / "python"
    return str(preferred if preferred.is_file() else Path(sys.executable))


def repository_checks() -> list[CommandCheck]:
    py = python_bin()
    checks = [
        CommandCheck(
            "backend_dependency_audit",
            (py, "-m", "pip_audit", "-r", "requirements.lock"),
            BACKEND_ROOT,
        ),
        CommandCheck(
            "backend_ruff",
            (py, "-m", "ruff", "check", "app", "alembic", "scripts"),
            BACKEND_ROOT,
        ),
        CommandCheck(
            "backend_mypy_ratchet",
            (py, "-m", "mypy", "app"),
            BACKEND_ROOT,
            evaluator="mypy_ratchet",
        ),
        CommandCheck(
            "backend_compile",
            (py, "-m", "compileall", "-q", "app", "alembic", "scripts"),
            BACKEND_ROOT,
        ),
        CommandCheck(
            "backend_tests_with_coverage",
            (
                py,
                "-m",
                "pytest",
                "--cov=app.core.deps",
                "--cov=app.services.iam_service",
                "--cov=app.services.release_gate_service",
                "--cov=app.services.credential_service",
                "--cov-report=term-missing",
                "--cov-fail-under=65",
            ),
            BACKEND_ROOT,
            timeout_seconds=1200,
        ),
        CommandCheck(
            "openapi_v2_contract",
            (py, "scripts/export_openapi_v2.py", "--check"),
            BACKEND_ROOT,
        ),
        CommandCheck(
            "frontend_dependency_audit",
            ("npm", "audit", "--audit-level=high"),
            FRONTEND_ROOT,
        ),
        CommandCheck(
            "frontend_lint",
            ("npm", "run", "lint", "--", "--max-warnings=0"),
            FRONTEND_ROOT,
        ),
        CommandCheck("frontend_tests", ("npm", "test", "--", "--run"), FRONTEND_ROOT),
        CommandCheck("frontend_build", ("npm", "run", "build"), FRONTEND_ROOT),
        CommandCheck(
            "compose_default",
            ("docker", "compose", "config", "--services"),
            REPO_ROOT,
            evaluator="services",
            required_services=("backend", "frontend", "minio", "mysql", "redis", "worker", "beat"),
            forbidden_services=("analysis-worker", "clickhouse", "langfuse-web", "langfuse-worker", "postgres"),
        ),
        CommandCheck(
            "compose_analysis_worker",
            ("docker", "compose", "--profile", "analysis-worker", "config", "--services"),
            REPO_ROOT,
            evaluator="services",
            required_services=("analysis-worker",),
        ),
        CommandCheck(
            "compose_observability",
            ("docker", "compose", "--profile", "observability", "config", "--services"),
            REPO_ROOT,
            evaluator="services",
            required_services=("clickhouse", "langfuse-web", "langfuse-worker", "postgres"),
        ),
        CommandCheck(
            "compose_production",
            (
                "docker",
                "compose",
                "--env-file",
                ".env.prod.example",
                "-f",
                "docker-compose.prod.yml",
                "-f",
                "docker-compose.prod-tls.yml",
                "config",
                "--quiet",
            ),
            REPO_ROOT,
            env={"DUCKDOCK_PROD_ENV_FILE": ".env.prod.example"},
        ),
        CommandCheck(
            "production_repository_baseline",
            (py, "scripts/verify-production-baseline.py"),
            REPO_ROOT,
        ),
        CommandCheck(
            "ga_authorization_contract_lint",
            (
                py,
                "backend/scripts/verify_ga_production_authorization.py",
                "ops/ga/production-authorization.example.json",
                "--lint",
            ),
            REPO_ROOT,
        ),
    ]
    for template in sorted((REPO_ROOT / "ops" / "ga").glob("*.example.json")):
        checks.append(
            CommandCheck(
                f"ga_template_{template.stem}",
                (py, "-m", "json.tool", str(template)),
                REPO_ROOT,
            )
        )
    for script in (
        "scripts/prod.sh",
        "scripts/restore.sh",
        "scripts/seal-backup.sh",
        "scripts/sign-ga-approval.sh",
        "scripts/verify-ga-infrastructure-dev.sh",
        "scripts/rehearse-kubernetes-ha.sh",
    ):
        checks.append(CommandCheck(f"syntax_{Path(script).stem}", ("bash", "-n", script), REPO_ROOT))
    checks.append(
        CommandCheck(
            "syntax_tls_gateway_entrypoint",
            ("sh", "-n", "ops/tls-gateway/entrypoint.sh"),
            REPO_ROOT,
        )
    )
    return checks


def integrated_checks(output_dir: Path | None = None) -> list[CommandCheck]:
    py = python_bin()
    playwright_output = (output_dir or Path("/tmp/duckdock-ga-local-preflight")) / "playwright"
    e2e_env = {
        "DEBUG": "true",
        "E2E_AUTO_SEED": "1",
        "E2E_BASE_URL": "http://127.0.0.1:5174",
        "E2E_OUTPUT_DIR": str(playwright_output),
        "E2E_SEED_COMMAND": (
            "cd .. && docker compose exec -T backend python scripts/seed_handover_e2e.py --json"
        ),
    }
    return [
        CommandCheck("live_core_stack", ("bash", "scripts/dev.sh", "up"), REPO_ROOT),
        CommandCheck("live_observability_stack", ("bash", "scripts/dev.sh", "observability"), REPO_ROOT),
        CommandCheck(
            "live_otel_collector",
            ("docker", "compose", "--profile", "telemetry-langfuse", "up", "-d", "otel-collector-langfuse"),
            REPO_ROOT,
        ),
        CommandCheck(
            "live_prometheus",
            ("docker", "compose", "--profile", "operations", "up", "-d", "prometheus"),
            REPO_ROOT,
        ),
        CommandCheck("live_analysis_worker", ("bash", "scripts/dev.sh", "worker"), REPO_ROOT),
        CommandCheck("live_backend_health", ("curl", "-fsS", "http://127.0.0.1:8801/health"), REPO_ROOT),
        CommandCheck("live_backend_readiness", ("curl", "-fsS", "http://127.0.0.1:8801/readyz"), REPO_ROOT),
        CommandCheck(
            "live_langfuse_health",
            ("curl", "-fsS", "http://127.0.0.1:3200/api/public/health"),
            REPO_ROOT,
        ),
        CommandCheck("live_prometheus_health", ("curl", "-fsS", "http://127.0.0.1:9090/-/ready"), REPO_ROOT),
        CommandCheck("live_otel_health", ("curl", "-fsS", "http://127.0.0.1:14133/"), REPO_ROOT),
        CommandCheck(
            "live_alembic_check",
            ("docker", "compose", "exec", "-T", "backend", "alembic", "check"),
            REPO_ROOT,
        ),
        CommandCheck(
            "live_frontend_e2e",
            ("npx", "playwright", "test"),
            FRONTEND_ROOT,
            env=e2e_env,
            timeout_seconds=1200,
        ),
        CommandCheck(
            "live_langfuse_compatibility",
            ("bash", "scripts/verify-langfuse-upgrade.sh"),
            REPO_ROOT,
            env={"DUCKDOCK_TEST_PYTHON": py},
            timeout_seconds=1200,
        ),
    ]


def evaluate_result(check: CommandCheck, exit_code: int, output: str) -> tuple[bool, str]:
    if check.evaluator == "mypy_ratchet":
        ceiling = int((BACKEND_ROOT / ".mypy-baseline").read_text(encoding="utf-8").strip())
        if exit_code == 0:
            count = 0
        else:
            matches = list(MYPY_RESULT_RE.finditer(output))
            if not matches:
                return False, "mypy failed without a parseable error count"
            count = int(matches[-1].group("count"))
        return count <= ceiling, f"mypy errors={count}, ceiling={ceiling}"
    if check.evaluator == "services":
        services = {line.strip() for line in output.splitlines() if line.strip()}
        missing = sorted(set(check.required_services) - services)
        forbidden = sorted(set(check.forbidden_services).intersection(services))
        passed = exit_code == 0 and not missing and not forbidden
        return passed, f"missing={missing}, forbidden={forbidden}, services={sorted(services)}"
    return exit_code == 0, f"exit_code={exit_code}"


def run_check(check: CommandCheck, logs_dir: Path) -> CheckResult:
    print(f"[{check.key}] running", flush=True)
    started = time.monotonic()
    environment = os.environ.copy()
    environment.update(check.env)
    exit_code = 127
    try:
        completed = subprocess.run(
            list(check.command),
            cwd=check.cwd,
            env=environment,
            capture_output=True,
            check=False,
            timeout=check.timeout_seconds,
        )
        exit_code = completed.returncode
        output = (completed.stdout or b"") + (completed.stderr or b"")
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        output = (exc.stdout or b"") + (exc.stderr or b"") + b"\ncommand timed out\n"
    except OSError as exc:
        output = f"command could not start: {exc}\n".encode()
    duration_ms = int((time.monotonic() - started) * 1000)
    log_path = logs_dir / f"{check.key}.log"
    log_path.write_bytes(output)
    log_path.chmod(0o600)
    decoded = output.decode("utf-8", errors="replace")
    passed, detail = evaluate_result(check, exit_code, decoded)
    status = "PASS" if passed else "BLOCK"
    print(f"[{check.key}] {status} ({duration_ms} ms)", flush=True)
    return CheckResult(
        key=check.key,
        status=status,
        command=list(check.command),
        cwd=str(check.cwd.relative_to(REPO_ROOT)),
        duration_ms=duration_ms,
        exit_code=exit_code,
        detail=detail,
        log_path=str(log_path.relative_to(logs_dir.parent)),
        log_sha256=sha256_bytes(output),
        log_size=len(output),
        environment_keys=sorted(check.env),
    )


def write_receipt(output_dir: Path, receipt: dict[str, object]) -> tuple[Path, str]:
    payload = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode()
    receipt_path = output_dir / "receipt.json"
    sidecar = output_dir / "receipt.json.sha256"
    if receipt_path.exists() or sidecar.exists():
        raise ValueError("preflight receipt outputs must not already exist")
    receipt_path.write_bytes(payload)
    receipt_path.chmod(0o600)
    digest = sha256_bytes(payload)
    sidecar.write_text(f"{digest}  receipt.json\n", encoding="utf-8")
    sidecar.chmod(0o600)
    if sha256_bytes(receipt_path.read_bytes()) != digest:
        raise RuntimeError("persisted preflight receipt digest verification failed")
    return receipt_path, digest


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--profile", choices=("repository", "integrated"), default="repository")
    parser.add_argument("--fail-fast", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        source = source_state()
        output_dir = validate_output_dir(args.output_dir)
        output_dir.mkdir(mode=0o700, parents=True)
        logs_dir = output_dir / "logs"
        logs_dir.mkdir(mode=0o700)
        started_at = utc_now()
        checks = repository_checks()
        if args.profile == "integrated":
            checks.extend(integrated_checks(output_dir))
        results: list[CheckResult] = []
        for check in checks:
            result = run_check(check, logs_dir)
            results.append(result)
            if result.status == "BLOCK" and args.fail_fast:
                break
        status = "PASS" if len(results) == len(checks) and all(item.status == "PASS" for item in results) else "BLOCK"
        artifacts = output_artifact_manifest(output_dir)
        receipt = {
            "schema_version": SCHEMA_VERSION,
            "status": status,
            "authorization_scope": AUTHORIZATION_SCOPE,
            "profile": args.profile,
            "source": source,
            "started_at": started_at,
            "finished_at": utc_now(),
            "pass_count": sum(item.status == "PASS" for item in results),
            "block_count": sum(item.status == "BLOCK" for item in results),
            "planned_check_count": len(checks),
            "executed_check_count": len(results),
            "checks": [asdict(item) for item in results],
            "artifact_count": len(artifacts),
            "artifacts": artifacts,
            "external_pending_count": len(EXTERNAL_REQUIREMENTS),
            "external_requirements": list(EXTERNAL_REQUIREMENTS),
            "next_action": (
                "fix_local_preflight_blocks"
                if status == "BLOCK"
                else "collect_real_release_target_security_and_approval_evidence"
            ),
        }
        receipt_path, digest = write_receipt(output_dir, receipt)
        harden_output_tree(output_dir)
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError, json.JSONDecodeError) as exc:
        print(f"GA local preflight failed: {exc}", file=sys.stderr)
        return 3
    print(json.dumps({"status": status, "receipt": str(receipt_path), "sha256": digest}, sort_keys=True))
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
