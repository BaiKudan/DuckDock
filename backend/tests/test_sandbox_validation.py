"""Unit tests for the sandbox-validation parse + check-building layer.

Covers the hermetic, parse-only surface of ``app.services.sandbox_validation_service``:
  * ``load_spec`` — parsing ``.duckdock/validation.yaml`` (object / missing / non-object)
  * ``package_checks`` — file_exists / file_contains assertions + unsupported / invalid entries
  * ``eval_smoke`` — expect_contains / expect_any_contains / reject_contains evaluation
  * ``parse_json`` / ``extract_backend`` / ``extract_text`` / ``collect_strings`` — CLI JSON parsing
  * ``sanitize`` — slug/tag sanitization
  * ``BaseRunner._runtime_checks`` — pass/fail check building from *mocked* CLI ``CmdResult``s
    (no real docker / ssh / subprocess — we feed synthetic stdout JSON straight in)

Nothing here shells out: the runner's ``run()`` / ``_run_commands`` are NOT invoked. We build
``CmdResult`` objects by hand to mimic the CLI's JSON output and assert the check tree the
parser derives from it.
"""
from __future__ import annotations

import pytest

import app.services.sandbox_validation_service as sandbox_module
from app.api.v1.endpoints.scans import get_sandbox_readiness
from app.core.config import settings
from app.core.deps import require_admin
from app.models.sandbox_validation import SandboxValidationStatus as VS
from app.models.user import SystemRole, User
from app.services.sandbox_validation_service import (
    BaseRunner,
    CmdResult,
    DockerRunner,
    SandboxValidationError,
    SandboxValidationService,
    collect_strings,
    eval_smoke,
    extract_backend,
    extract_text,
    load_spec,
    package_checks,
    parse_json,
    sanitize,
)


def _statuses(checks) -> dict[str, VS]:
    return {c.name: c.status for c in checks}


# ── load_spec ──────────────────────────────────────────────────────


def test_load_spec_missing_yaml_returns_empty_skeleton():
    spec = load_spec({"SKILL.md": "x"})
    assert spec == {"checks": [], "smoke_prompts": [], "load": {}}


def test_load_spec_parses_object():
    raw = "checks:\n  - type: file_exists\n    path: SKILL.md\nload:\n  skill_name: foo\n"
    spec = load_spec({".duckdock/validation.yaml": raw})
    assert spec["load"]["skill_name"] == "foo"
    assert spec["checks"][0]["type"] == "file_exists"


def test_load_spec_empty_string_returns_skeleton():
    # falsy raw (empty file) short-circuits to the skeleton
    spec = load_spec({".duckdock/validation.yaml": ""})
    assert spec == {"checks": [], "smoke_prompts": [], "load": {}}


def test_load_spec_non_object_raises():
    with pytest.raises(SandboxValidationError):
        load_spec({".duckdock/validation.yaml": "- just\n- a\n- list\n"})


# ── package_checks: file_exists / file_contains ────────────────────


def test_package_check_file_exists_pass_and_fail():
    files = {"SKILL.md": "hello"}
    spec = {
        "checks": [
            {"type": "file_exists", "path": "SKILL.md"},
            {"type": "file_exists", "path": "MISSING.md"},
        ]
    }
    checks = package_checks(files, spec)
    st = _statuses(checks)
    assert st["file_exists:SKILL.md"] == VS.PASSED
    assert st["file_exists:MISSING.md"] == VS.FAILED


def test_package_check_file_contains_pass():
    files = {"SKILL.md": "name: demo\nversion: 1.0.0"}
    spec = {"checks": [{"type": "file_contains", "path": "SKILL.md", "text": "name: demo"}]}
    checks = package_checks(files, spec)
    assert checks[0].status == VS.PASSED
    assert checks[0].details == {"text": "name: demo"}


def test_package_check_file_contains_missing_file_fails():
    spec = {"checks": [{"type": "file_contains", "path": "nope.md", "text": "x"}]}
    checks = package_checks({}, spec)
    assert checks[0].status == VS.FAILED
    assert "missing file" in checks[0].summary.lower()


def test_package_check_file_contains_text_absent_fails():
    files = {"SKILL.md": "hello world"}
    spec = {"checks": [{"type": "file_contains", "path": "SKILL.md", "text": "goodbye"}]}
    checks = package_checks(files, spec)
    assert checks[0].status == VS.FAILED


def test_package_check_unsupported_type_skipped():
    spec = {"checks": [{"type": "checksum", "path": "x"}]}
    checks = package_checks({}, spec)
    assert checks[0].status == VS.SKIPPED
    assert checks[0].name == "unsupported:checksum"


def test_package_check_non_object_entry_fails():
    spec = {"checks": ["not-a-dict"]}
    checks = package_checks({}, spec)
    assert checks[0].name == "invalid_check"
    assert checks[0].status == VS.FAILED


def test_package_checks_empty_spec_yields_no_checks():
    assert package_checks({"a": "b"}, {"checks": []}) == []
    assert package_checks({"a": "b"}, {}) == []


# ── eval_smoke ─────────────────────────────────────────────────────


def test_eval_smoke_all_expected_present_passes():
    prompt = {"expect_contains": ["Hello", "World"]}
    status, details = eval_smoke(prompt, "hello WORLD, friend")  # case-insensitive
    assert status == VS.PASSED
    assert details["missing_expected"] == []


def test_eval_smoke_missing_expected_fails():
    prompt = {"expect_contains": ["alpha", "beta"]}
    status, details = eval_smoke(prompt, "only alpha here")
    assert status == VS.FAILED
    assert details["missing_expected"] == ["beta"]


def test_eval_smoke_expect_any_one_match_passes():
    prompt = {"expect_any_contains": ["cat", "dog"]}
    status, details = eval_smoke(prompt, "I have a Dog.")
    assert status == VS.PASSED
    assert details["expected_any_matched"] is True


def test_eval_smoke_expect_any_none_match_fails():
    prompt = {"expect_any_contains": ["cat", "dog"]}
    status, details = eval_smoke(prompt, "I have a fish.")
    assert status == VS.FAILED
    assert details["expected_any_matched"] is False


def test_eval_smoke_reject_contains_hit_fails():
    prompt = {"expect_contains": ["ok"], "reject_contains": ["error"]}
    status, details = eval_smoke(prompt, "ok but ERROR happened")
    assert status == VS.FAILED
    assert details["forbidden_hits"] == ["error"]


def test_eval_smoke_empty_constraints_passes():
    status, details = eval_smoke({}, "any output at all")
    assert status == VS.PASSED
    assert details["missing_expected"] == []
    assert details["forbidden_hits"] == []


def test_eval_smoke_non_dict_prompt_fails():
    status, details = eval_smoke("oops", "out")
    assert status == VS.FAILED
    assert "error" in details


# ── parse_json / extract_backend / extract_text ────────────────────


def test_parse_json_object():
    assert parse_json('{"a": 1}') == {"a": 1}


def test_parse_json_non_object_wrapped():
    assert parse_json("[1, 2]") == {"value": [1, 2]}


def test_parse_json_invalid_and_empty_return_none():
    assert parse_json("not json") is None
    assert parse_json("") is None


def test_extract_backend_top_level():
    assert extract_backend({"backend": "docker"}) == "docker"


def test_extract_backend_nested_sandbox():
    assert extract_backend({"sandbox": {"backend": "ssh"}}) == "ssh"


def test_extract_backend_nested_effective_and_resolved():
    assert extract_backend({"effective": {"sandbox": {"backend": "docker"}}}) == "docker"
    assert extract_backend({"resolved": {"sandbox": {"backend": "ssh"}}}) == "ssh"


def test_extract_backend_none_when_absent():
    assert extract_backend({"unrelated": 1}) is None
    assert extract_backend(None) is None


def test_extract_text_from_reply_field():
    raw = '{"reply": "The answer is 42", "noise": 99}'
    assert "The answer is 42" in extract_text(raw)


def test_extract_text_falls_back_to_raw_on_non_json():
    assert extract_text("  plain text  ") == "plain text"


def test_collect_strings_walks_nested_structures():
    out: list[str] = []
    collect_strings({"message": ["a", {"text": "b"}], "x": "c"}, out)
    assert "a" in out and "b" in out and "c" in out


# ── sanitize ───────────────────────────────────────────────────────


def test_sanitize_replaces_unsafe_chars():
    assert sanitize("my/slug name!") == "my-slug-name"


def test_sanitize_strips_leading_trailing_separators():
    assert sanitize("--weird--") == "weird"


def test_sanitize_empty_falls_back_to_unnamed():
    assert sanitize("///") == "unnamed"
    assert sanitize("") == "unnamed"


# ── BaseRunner._runtime_checks: pass/fail building from mocked CLI ──


def _runner() -> DockerRunner:
    """A DockerRunner with no smoke prompts; only the parse layer is exercised."""
    return DockerRunner(
        slug="demo-skill",
        skill_key="demo",
        tag="v1.0.0",
        access_subject="user:1",
        spec={"smoke_prompts": []},
        network_mode="registry_only",
        workspace_mode="ephemeral",
        agent_smoke_enabled=False,
        agent_smoke_timeout_seconds=None,
    )


def test_runtime_checks_no_results_fails():
    checks = _runner()._runtime_checks([])
    assert len(checks) == 1
    assert checks[0].name == "openclaw_runtime"
    assert checks[0].status == VS.FAILED


def test_runtime_checks_login_failure_short_circuits():
    results = [CmdResult("clawhub_login", 1, "", "auth denied")]
    checks = _runner()._runtime_checks(results)
    st = _statuses(checks)
    assert st["clawhub_login"] == VS.FAILED
    # install missing -> openclaw_install fails and returns early
    assert st["openclaw_install"] == VS.FAILED
    assert "openclaw_sandbox_backend" not in st


def test_runtime_checks_happy_path_all_pass():
    results = [
        CmdResult("clawhub_login", 0, "", ""),
        CmdResult("clawhub_install", 0, "installed", ""),
        CmdResult("openclaw_sandbox_explain", 0, '{"backend": "docker"}', ""),
        CmdResult(
            "openclaw_skill_info",
            0,
            '{"eligible": true, "missing": {}, "source": "registry", "filePath": "/s/demo"}',
            "",
        ),
        CmdResult(
            "openclaw_skills_check",
            0,
            '{"skills": [{"name": "demo", "eligible": true}]}',
            "",
        ),
    ]
    checks = _runner()._runtime_checks(results)
    st = _statuses(checks)
    assert st["clawhub_login"] == VS.PASSED
    assert st["openclaw_install"] == VS.PASSED
    assert st["openclaw_sandbox_backend"] == VS.PASSED
    assert st["openclaw_skill_discovery"] == VS.PASSED
    assert st["openclaw_requirements"] == VS.PASSED
    assert st["openclaw_skills_check"] == VS.PASSED
    # no smoke prompts -> agent_smoke is SKIPPED, not failed
    assert st["agent_smoke"] == VS.SKIPPED


def test_runtime_checks_backend_mismatch_recorded_but_not_hard_fail():
    # explain reports a *different* backend; _runtime_checks marks the backend
    # check FAILED but continues evaluating subsequent checks.
    results = [
        CmdResult("clawhub_login", 0, "", ""),
        CmdResult("clawhub_install", 0, "installed", ""),
        CmdResult("openclaw_sandbox_explain", 0, '{"backend": "ssh"}', ""),
        CmdResult("openclaw_skill_info", 0, '{"eligible": true, "missing": {}}', ""),
        CmdResult("openclaw_skills_check", 0, '{"skills": []}', ""),
    ]
    checks = _runner()._runtime_checks(results)
    st = _statuses(checks)
    assert st["openclaw_sandbox_backend"] == VS.FAILED
    # skill discovery still evaluated after the mismatch
    assert st["openclaw_skill_discovery"] == VS.PASSED


def test_runtime_checks_unmet_requirements_fail():
    results = [
        CmdResult("clawhub_login", 0, "", ""),
        CmdResult("clawhub_install", 0, "installed", ""),
        CmdResult("openclaw_sandbox_explain", 0, '{"backend": "docker"}', ""),
        CmdResult(
            "openclaw_skill_info",
            0,
            '{"eligible": true, "missing": {"bins": ["jq"]}}',
            "",
        ),
        CmdResult("openclaw_skills_check", 0, '{"skills": []}', ""),
    ]
    checks = _runner()._runtime_checks(results)
    st = _statuses(checks)
    assert st["openclaw_requirements"] == VS.FAILED


def test_runtime_checks_skill_not_found_fails_and_stops():
    results = [
        CmdResult("clawhub_login", 0, "", ""),
        CmdResult("clawhub_install", 0, "installed", ""),
        CmdResult("openclaw_sandbox_explain", 0, '{"backend": "docker"}', ""),
        CmdResult("openclaw_skill_info", 0, '{"error": "not found"}', ""),
    ]
    checks = _runner()._runtime_checks(results)
    st = _statuses(checks)
    assert st["openclaw_skill_discovery"] == VS.FAILED
    # requirements check is not reached
    assert "openclaw_requirements" not in st


def test_runtime_checks_skills_check_no_record_skipped():
    results = [
        CmdResult("clawhub_login", 0, "", ""),
        CmdResult("clawhub_install", 0, "installed", ""),
        CmdResult("openclaw_sandbox_explain", 0, '{"backend": "docker"}', ""),
        CmdResult("openclaw_skill_info", 0, '{"eligible": true, "missing": {}}', ""),
        CmdResult("openclaw_skills_check", 0, '{"skills": [{"name": "other"}]}', ""),
    ]
    checks = _runner()._runtime_checks(results)
    st = _statuses(checks)
    # no per-skill record for "demo" -> SKIPPED (not FAILED)
    assert st["openclaw_skills_check"] == VS.SKIPPED


def test_runtime_checks_skills_check_eligible_list_form():
    # alternate CLI shape: {"eligible": ["demo", ...]}
    results = [
        CmdResult("clawhub_login", 0, "", ""),
        CmdResult("clawhub_install", 0, "installed", ""),
        CmdResult("openclaw_sandbox_explain", 0, '{"backend": "docker"}', ""),
        CmdResult("openclaw_skill_info", 0, '{"eligible": true, "missing": {}}', ""),
        CmdResult("openclaw_skills_check", 0, '{"eligible": ["demo"]}', ""),
    ]
    checks = _runner()._runtime_checks(results)
    assert _statuses(checks)["openclaw_skills_check"] == VS.PASSED


# ── _overall aggregation over a built check list ───────────────────


def test_overall_failed_when_any_failed():
    runner = _runner()
    results = [CmdResult("clawhub_login", 1, "", "denied")]
    checks = runner._runtime_checks(results)
    assert runner._overall(checks) == VS.FAILED


def test_overall_passed_on_happy_path():
    runner = _runner()
    results = [
        CmdResult("clawhub_login", 0, "", ""),
        CmdResult("clawhub_install", 0, "installed", ""),
        CmdResult("openclaw_sandbox_explain", 0, '{"backend": "docker"}', ""),
        CmdResult("openclaw_skill_info", 0, '{"eligible": true, "missing": {}}', ""),
        CmdResult("openclaw_skills_check", 0, '{"skills": [{"name": "demo"}]}', ""),
    ]
    checks = runner._runtime_checks(results)
    # happy-path runtime checks contain PASSED + a SKIPPED smoke, no FAILED
    assert runner._overall(checks) == VS.PASSED


def test_unsupported_backend_runner_raises():
    from app.services.sandbox_validation_service import SandboxValidationService

    svc = SandboxValidationService()

    original = settings.SKILL_SANDBOX_BACKEND
    settings.SKILL_SANDBOX_BACKEND = "podman"
    try:
        with pytest.raises(SandboxValidationError):
            svc._runner(
                slug="s",
                skill_key="k",
                tag="v1",
                access_subject="u",
                spec={},
                network_mode="offline",
                workspace_mode="ephemeral",
                agent_smoke_enabled=False,
                agent_smoke_timeout_seconds=None,
            )
    finally:
        settings.SKILL_SANDBOX_BACKEND = original


def test_isinstance_baserunner():
    # sanity: the concrete runner used in tests is a BaseRunner subclass
    assert isinstance(_runner(), BaseRunner)


def test_check_readiness_lists_missing_backend_prerequisites(monkeypatch):
    monkeypatch.setattr(settings, "SKILL_SANDBOX_ENABLED", True)
    monkeypatch.setattr(settings, "SKILL_SANDBOX_BACKEND", "docker")
    monkeypatch.setattr(settings, "SKILL_SANDBOX_DOCKER_IMAGE", "duckdock-openclaw-sandbox:local")
    monkeypatch.setattr(sandbox_module, "exists", lambda _command: False)

    result = SandboxValidationService().check_readiness()

    assert result["ready"] is False
    assert result["backend"] == "docker"
    assert "docker_available" in result["missing"]
    assert "clawhub_command" in result["missing"]
    assert "openclaw_command" in result["missing"]


async def test_sandbox_readiness_endpoint_is_admin_only():
    user = User(username="sandbox-reader", email="sandbox-reader@example.com", hashed_password="x")

    with pytest.raises(Exception) as exc:
        await require_admin(user)

    assert getattr(exc.value, "status_code", None) == 403


async def test_sandbox_readiness_endpoint_returns_ready_payload(monkeypatch):
    payload = {
        "ready": True,
        "enabled": True,
        "backend": "docker",
        "checks": [],
        "missing": [],
    }
    monkeypatch.setattr(sandbox_module.sandbox_validation_service, "check_readiness", lambda: payload)
    admin = User(
        username="sandbox-admin",
        email="sandbox-admin@example.com",
        hashed_password="x",
        system_role=SystemRole.ADMIN,
    )

    assert await get_sandbox_readiness(admin) == payload
