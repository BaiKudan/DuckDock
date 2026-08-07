from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "run_security_preaudit.py"
GITLEAKS_SCRIPT_PATH = REPO_ROOT / "scripts" / "run-gitleaks.sh"
GITLEAKS_IGNORE_PATH = REPO_ROOT / ".gitleaksignore"


def _module():
    spec = importlib.util.spec_from_file_location("run_security_preaudit", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_security_preaudit_covers_four_first_party_images_and_security_layers() -> None:
    module = _module()
    images = module.image_specs("2.0.0-rc.1")

    assert {image.key for image in images} == {
        "backend",
        "frontend",
        "tls_gateway",
        "alertmanager",
    }
    assert len(module.SECURITY_TESTS) >= 18
    assert "not an independent security assessment" in module.AUTHORIZATION_SCOPE
    assert "not authorization" in module.AUTHORIZATION_SCOPE


def test_security_preaudit_requires_a_new_external_output_directory(
    tmp_path: Path,
) -> None:
    module = _module()
    existing = tmp_path / "existing"
    existing.mkdir()

    with pytest.raises(ValueError, match="must not already exist"):
        module.validate_output_dir(existing)
    with pytest.raises(ValueError, match="outside the repository"):
        module.validate_output_dir(REPO_ROOT / "unsafe-output")


def test_security_preaudit_secret_patterns_do_not_match_the_scanner_source() -> None:
    _module()
    output = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "AKIA[0-9A-Z]" not in output
    assert "-----BEGIN PRIVATE KEY-----" not in output


def test_gitleaks_gate_is_digest_pinned_redacted_and_full_history() -> None:
    script = GITLEAKS_SCRIPT_PATH.read_text(encoding="utf-8")
    ignored_fingerprints = [line for line in GITLEAKS_IGNORE_PATH.read_text(encoding="utf-8").splitlines() if line]

    assert "v8.30.1@sha256:c00b6bd0" in script
    assert "--redact=100" in script
    assert "--network none" in script
    assert "--read-only" in script
    assert "--cap-drop ALL" in script
    assert "--gitleaks-ignore-path /repo/.gitleaksignore" in script
    assert "--log-opts" not in script
    assert len(ignored_fingerprints) == 33
    assert len(set(ignored_fingerprints)) == 33


def test_internal_preaudit_executes_full_history_gitleaks() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert 'key="gitleaks_full_history"' in script
    assert '"scripts/run-gitleaks.sh"' in script


def test_gitleaks_runner_refuses_report_inside_repository() -> None:
    report_path = REPO_ROOT / ".unsafe-gitleaks-report.json"
    assert not report_path.exists()

    completed = subprocess.run(
        ["bash", str(GITLEAKS_SCRIPT_PATH), str(report_path)],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "outside the repository" in completed.stderr
    assert not report_path.exists()
