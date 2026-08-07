from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "run_ga_local_preflight.py"


def load_preflight_module():
    spec = importlib.util.spec_from_file_location("run_ga_local_preflight", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_local_ga_preflight_covers_repository_ci_and_stays_non_authorizing():
    preflight = load_preflight_module()
    checks = {check.key: check for check in preflight.repository_checks()}

    assert {
        "backend_dependency_audit",
        "backend_ruff",
        "backend_mypy_ratchet",
        "backend_compile",
        "backend_tests_with_coverage",
        "openapi_v2_contract",
        "frontend_dependency_audit",
        "frontend_lint",
        "frontend_tests",
        "frontend_build",
        "compose_default",
        "compose_analysis_worker",
        "compose_observability",
        "compose_production",
        "production_repository_baseline",
        "ga_authorization_contract_lint",
    }.issubset(checks)
    assert "non-authorizing" in preflight.AUTHORIZATION_SCOPE
    assert {item["status"] for item in preflight.EXTERNAL_REQUIREMENTS} == {"PENDING_EXTERNAL"}
    assert len(preflight.EXTERNAL_REQUIREMENTS) == 6


def test_local_ga_integrated_profile_covers_live_stack_e2e_and_langfuse(tmp_path):
    preflight = load_preflight_module()
    check_map = {check.key: check for check in preflight.integrated_checks(tmp_path)}

    assert {
        "live_core_stack",
        "live_observability_stack",
        "live_analysis_worker",
        "live_backend_health",
        "live_backend_readiness",
        "live_alembic_check",
        "live_frontend_e2e",
        "live_langfuse_compatibility",
    }.issubset(check_map)
    assert check_map["live_frontend_e2e"].env["E2E_OUTPUT_DIR"] == str(tmp_path / "playwright")


@pytest.mark.parametrize(
    ("exit_code", "output", "expected", "detail"),
    [
        (0, "Success: no issues found", True, "mypy errors=0, ceiling=82"),
        (1, "Found 81 errors in 22 files", True, "mypy errors=81, ceiling=82"),
        (1, "Found 83 errors in 22 files", False, "mypy errors=83, ceiling=82"),
        (2, "mypy crashed", False, "mypy failed without a parseable error count"),
    ],
)
def test_mypy_ratchet_is_fail_closed(exit_code, output, expected, detail):
    preflight = load_preflight_module()
    check = preflight.CommandCheck(
        "backend_mypy_ratchet",
        ("python", "-m", "mypy", "app"),
        preflight.BACKEND_ROOT,
        evaluator="mypy_ratchet",
    )

    assert preflight.evaluate_result(check, exit_code, output) == (expected, detail)


def test_output_directory_must_be_new_and_outside_repository(tmp_path):
    preflight = load_preflight_module()

    outside = tmp_path / "new-receipt"
    assert preflight.validate_output_dir(outside) == outside.resolve()

    outside.mkdir()
    with pytest.raises(ValueError, match="must not already exist"):
        preflight.validate_output_dir(outside)
    with pytest.raises(ValueError, match="outside the repository"):
        preflight.validate_output_dir(REPO_ROOT / "local-preflight-output")


def test_receipt_is_content_addressed_and_not_overwritten(tmp_path):
    preflight = load_preflight_module()
    output = tmp_path / "receipt"
    output.mkdir(mode=0o700)
    receipt = {
        "schema_version": preflight.SCHEMA_VERSION,
        "status": "PASS",
        "authorization_scope": preflight.AUTHORIZATION_SCOPE,
    }

    path, digest = preflight.write_receipt(output, receipt)

    assert preflight.sha256_bytes(path.read_bytes()) == digest
    assert (output / "receipt.json.sha256").read_text().startswith(digest)
