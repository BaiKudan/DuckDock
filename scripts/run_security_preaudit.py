#!/usr/bin/env python3
"""Run DuckDock's reproducible, non-independent security pre-audit."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
FRONTEND_ROOT = REPO_ROOT / "frontend"
SCHEMA_VERSION = "duckdock-security-preaudit-v1"
AUTHORIZATION_SCOPE = (
    "internal engineering pre-audit only; not an independent security assessment "
    "and not authorization to publish or deploy GA"
)
SECURITY_TESTS = (
    "tests/test_app_security.py",
    "tests/test_authz.py",
    "tests/test_analysis_authz.py",
    "tests/test_control_plane_authz.py",
    "tests/test_credentials.py",
    "tests/test_git_service_path_security.py",
    "tests/test_iam_binding_scope.py",
    "tests/test_identity_security_v2.py",
    "tests/test_logging_and_cors.py",
    "tests/test_robot_auth.py",
    "tests/test_sensitive_filtering.py",
    "tests/test_skill_publish_authz.py",
    "tests/test_sso_enterprise_identity.py",
    "tests/test_sso_security.py",
    "tests/test_ssrf.py",
    "tests/test_webhook_ssrf.py",
    "tests/test_collect_ga_target_network.py",
    "tests/foundation/test_transactional_outbox.py",
)
SECRET_SCAN_EXCLUDED_PREFIXES = (
    "backend/tests/",
    "frontend/src/__tests__/",
    "specs/016-ga-production-authorization/evidence/",
)
SECRET_SCAN_EXCLUDED_PATHS = {
    "backend/scripts/archive_ga_authorized_bundle.py",
}


@dataclass(frozen=True)
class ImageSpec:
    key: str
    image: str
    context: str
    dockerfile: str


@dataclass(frozen=True)
class CheckResult:
    key: str
    status: str
    command: list[str]
    duration_ms: int
    exit_code: int | None
    log_path: str
    log_sha256: str
    log_size: int
    detail: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


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
        raise ValueError("working tree must be clean before the security pre-audit")
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


def image_specs(version: str) -> tuple[ImageSpec, ...]:
    return (
        ImageSpec("backend", f"duckdock-backend:{version}", "backend", "backend/Dockerfile"),
        ImageSpec(
            "frontend",
            f"duckdock-frontend:{version}",
            "frontend",
            "frontend/Dockerfile.prod",
        ),
        ImageSpec(
            "tls_gateway",
            f"duckdock-tls-gateway:{version}",
            "ops/tls-gateway",
            "ops/tls-gateway/Dockerfile",
        ),
        ImageSpec(
            "alertmanager",
            "duckdock-alertmanager:0.33.1-duckdock.1",
            "ops/alertmanager",
            "ops/alertmanager/Dockerfile",
        ),
    )


def _result(
    *,
    key: str,
    command: list[str],
    exit_code: int | None,
    duration_ms: int,
    log_path: Path,
    output_dir: Path,
    detail: str,
) -> CheckResult:
    payload = log_path.read_bytes()
    return CheckResult(
        key=key,
        status="PASS" if exit_code == 0 else "BLOCK",
        command=command,
        duration_ms=duration_ms,
        exit_code=exit_code,
        log_path=str(log_path.relative_to(output_dir)),
        log_sha256=sha256_bytes(payload),
        log_size=len(payload),
        detail=detail,
    )


def run_command(
    *,
    key: str,
    command: list[str],
    cwd: Path,
    output_dir: Path,
    timeout_seconds: int = 1200,
) -> CheckResult:
    log_path = output_dir / "logs" / f"{key}.log"
    started = time.monotonic()
    print(f"[security-preaudit] RUN {key}", flush=True)
    try:
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                command,
                cwd=cwd,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        exit_code: int | None = completed.returncode
        detail = "command exited successfully" if exit_code == 0 else "command failed"
    except (OSError, subprocess.TimeoutExpired) as exc:
        log_path.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        exit_code = None
        detail = f"command could not complete: {type(exc).__name__}"
    result = _result(
        key=key,
        command=command,
        exit_code=exit_code,
        duration_ms=round((time.monotonic() - started) * 1000),
        log_path=log_path,
        output_dir=output_dir,
        detail=detail,
    )
    print(f"[security-preaudit] {result.status} {key}", flush=True)
    return result


def blocked_result(*, key: str, detail: str, output_dir: Path) -> CheckResult:
    log_path = output_dir / "logs" / f"{key}.log"
    log_path.write_text(f"BLOCKED: {detail}\n", encoding="utf-8")
    return _result(
        key=key,
        command=[],
        exit_code=None,
        duration_ms=0,
        log_path=log_path,
        output_dir=output_dir,
        detail=detail,
    )


def tracked_secret_scan(output_dir: Path) -> CheckResult:
    # Construct markers in pieces so this scanner does not match itself.
    patterns = (
        re.compile("-----BEGIN " + r"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        re.compile("AK" + r"IA[0-9A-Z]{16}"),
        re.compile("gh" + r"[pousr]_[A-Za-z0-9_]{36,255}"),
        re.compile("sk" + r"-[A-Za-z0-9]{32,}"),
        re.compile("dkr_" + r"(?:report|worker)_[A-Za-z0-9_-]{20,}"),
    )
    started = time.monotonic()
    paths = run_git("ls-files", "-z").split("\0")
    findings: list[str] = []
    scanned = 0
    for relative in paths:
        if not relative:
            continue
        if relative in SECRET_SCAN_EXCLUDED_PATHS or relative.startswith(SECRET_SCAN_EXCLUDED_PREFIXES):
            continue
        path = REPO_ROOT / relative
        try:
            payload = path.read_bytes()
        except OSError as exc:
            findings.append(f"{relative}: unreadable ({type(exc).__name__})")
            continue
        if b"\0" in payload:
            continue
        scanned += 1
        text = payload.decode("utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if any(pattern.search(line) for pattern in patterns):
                findings.append(f"{relative}:{line_number}: secret-like token")
    log_path = output_dir / "logs" / "tracked_secret_heuristic.log"
    log_path.write_text(
        json.dumps(
            {
                "scope": "current tracked non-test source; heuristic, not git history",
                "excluded_prefixes": list(SECRET_SCAN_EXCLUDED_PREFIXES),
                "excluded_paths": sorted(SECRET_SCAN_EXCLUDED_PATHS),
                "files_scanned": scanned,
                "findings": findings,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return _result(
        key="tracked_secret_heuristic",
        command=["internal:tracked-secret-heuristic"],
        exit_code=0 if not findings else 2,
        duration_ms=round((time.monotonic() - started) * 1000),
        log_path=log_path,
        output_dir=output_dir,
        detail=f"scanned {scanned} tracked non-test text files; {len(findings)} finding(s)",
    )


def inspect_image(spec: ImageSpec) -> dict[str, object]:
    completed = subprocess.run(
        ["docker", "image", "inspect", spec.image],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    document = json.loads(completed.stdout)[0]
    return {
        "key": spec.key,
        "image": spec.image,
        "image_id": document["Id"],
        "repo_digests": document.get("RepoDigests") or [],
        "labels": (document.get("Config") or {}).get("Labels") or {},
    }


def artifact_manifest(output_dir: Path) -> list[dict[str, object]]:
    artifacts: list[dict[str, object]] = []
    for current_root, directory_names, file_names in os.walk(output_dir, followlinks=False):
        current = Path(current_root)
        for name in directory_names:
            if (current / name).is_symlink():
                raise ValueError("symbolic-link output artifact is forbidden")
        for name in file_names:
            path = current / name
            if path.name in {"receipt.json", "receipt.sha256"}:
                continue
            if path.is_symlink():
                raise ValueError("symbolic-link output artifact is forbidden")
            artifacts.append(
                {
                    "path": str(path.relative_to(output_dir)),
                    "sha256": sha256_file(path),
                    "size": path.stat().st_size,
                }
            )
    return sorted(artifacts, key=lambda item: str(item["path"]))


def harden_output_tree(output_dir: Path) -> None:
    for current_root, directory_names, file_names in os.walk(output_dir, followlinks=False):
        current = Path(current_root)
        current.chmod(0o700)
        for name in directory_names:
            directory = current / name
            if directory.is_symlink():
                raise ValueError("symbolic-link output directory is forbidden")
            directory.chmod(0o700)
        for name in file_names:
            path = current / name
            if path.is_symlink():
                raise ValueError("symbolic-link output artifact is forbidden")
            path.chmod(0o600)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="New evidence directory outside the repository",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        output_dir = validate_output_dir(args.output_dir)
        source = source_state()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"security pre-audit refused: {exc}", file=sys.stderr)
        return 2
    output_dir.mkdir(mode=0o700, parents=True)
    (output_dir / "logs").mkdir(mode=0o700)
    (output_dir / "sarif").mkdir(mode=0o700)

    py = python_bin()
    checks = [
        run_command(
            key="backend_dependency_audit",
            command=[py, "-m", "pip_audit", "-r", "requirements.lock"],
            cwd=BACKEND_ROOT,
            output_dir=output_dir,
        ),
        run_command(
            key="frontend_dependency_audit",
            command=["npm", "audit", "--audit-level=high"],
            cwd=FRONTEND_ROOT,
            output_dir=output_dir,
        ),
        run_command(
            key="high_confidence_sast",
            command=[py, "-m", "ruff", "check", "--select", "S101,S110,S314", "app", "scripts"],
            cwd=BACKEND_ROOT,
            output_dir=output_dir,
        ),
        run_command(
            key="security_negative_tests",
            command=[py, "-m", "pytest", "-q", *SECURITY_TESTS],
            cwd=BACKEND_ROOT,
            output_dir=output_dir,
        ),
        tracked_secret_scan(output_dir),
        run_command(
            key="gitleaks_full_history",
            command=[
                "bash",
                "scripts/run-gitleaks.sh",
                str(output_dir / "gitleaks-report.json"),
            ],
            cwd=REPO_ROOT,
            output_dir=output_dir,
        ),
    ]

    images: list[dict[str, object]] = []
    for spec in image_specs(source["version"]):
        build = run_command(
            key=f"build_{spec.key}",
            command=[
                "docker",
                "build",
                "--file",
                spec.dockerfile,
                "--tag",
                spec.image,
                spec.context,
            ],
            cwd=REPO_ROOT,
            output_dir=output_dir,
        )
        checks.append(build)
        if build.status != "PASS":
            checks.append(
                blocked_result(
                    key=f"scan_{spec.key}",
                    detail="image build did not pass; refusing to scan a possibly stale tag",
                    output_dir=output_dir,
                )
            )
            continue
        sarif_path = output_dir / "sarif" / f"{spec.key}.sarif.json"
        scan = run_command(
            key=f"scan_{spec.key}",
            command=[
                "docker",
                "scout",
                "cves",
                "--only-severity",
                "critical,high",
                "--exit-code",
                "--format",
                "sarif",
                "--output",
                str(sarif_path),
                f"local://{spec.image}",
            ],
            cwd=REPO_ROOT,
            output_dir=output_dir,
        )
        checks.append(scan)
        try:
            image = inspect_image(spec)
        except (OSError, KeyError, ValueError, subprocess.CalledProcessError) as exc:
            checks.append(
                blocked_result(
                    key=f"inspect_{spec.key}",
                    detail=f"built image could not be inspected: {type(exc).__name__}",
                    output_dir=output_dir,
                )
            )
            continue
        image["scan_status"] = scan.status
        image["sarif_path"] = str(sarif_path.relative_to(output_dir)) if sarif_path.is_file() else None
        image["sarif_sha256"] = sha256_file(sarif_path) if sarif_path.is_file() else None
        images.append(image)

    pass_count = sum(check.status == "PASS" for check in checks)
    block_count = sum(check.status == "BLOCK" for check in checks)
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "observed_at": utc_now(),
        "authorization_scope": AUTHORIZATION_SCOPE,
        "source": source,
        "status": "PASS_INTERNAL_PREAUDIT" if block_count == 0 else "BLOCK_INTERNAL_PREAUDIT",
        "summary": {
            "pass_count": pass_count,
            "block_count": block_count,
            "independent_security_assessment": "PENDING_EXTERNAL",
        },
        "checks": [asdict(check) for check in checks],
        "images": images,
        "artifacts": artifact_manifest(output_dir),
        "decision": (
            "Internal pre-audit passed; third-party scope authorization, independent testing, "
            "remediation retest and evidence deletion receipt remain mandatory before GA."
            if block_count == 0
            else "Internal pre-audit blocked; resolve every BLOCK before commissioning the independent assessment."
        ),
    }
    receipt_path = output_dir / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    digest = sha256_file(receipt_path)
    (output_dir / "receipt.sha256").write_text(f"{digest}  receipt.json\n", encoding="utf-8")
    harden_output_tree(output_dir)
    print(f"[security-preaudit] {receipt['status']} pass={pass_count} block={block_count}")
    print(f"[security-preaudit] receipt={receipt_path}")
    print(f"[security-preaudit] receipt_sha256={digest}")
    return 0 if block_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
