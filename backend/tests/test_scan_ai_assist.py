"""SCAN degrade-surfacing — the shared ``ai_assist`` indicator on a scan result.

The deep AI scan (``scanner_service.scan_with_llm``) only runs when a unified Qwen
key resolves; otherwise the scan is static-regex only. That fallback used to be
silent. These tests pin the shared degradation contract so reviewers can tell
whether AI scanning actually happened:

    ai_assist = {"mode": "llm" | "baseline", "degraded": <bool>, "reason": <str|null>}

  * deep scan ran      → mode="llm",      degraded=False, reason=None
  * static-only (no key
    / call skipped)    → mode="baseline", degraded=True,  reason=<why>

Unit layer exercises ``build_ai_assist`` / ``aggregate_issues`` passthrough; the
worker layer drives ``_async_scan`` against the SQLite test DB (collaborators
stubbed) to prove the indicator is persisted/surfaced on the version's
``gate_result.technical_checks.ai_assist`` for both the failed and gated paths.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.core.config import settings
from app.models.namespace import Namespace
from app.models.scan import ScanResult, ScanStatus
from app.models.scanner_suppression import ScannerRuleSuppression  # noqa: F401 - ensure table
from app.models.skill import (
    Skill,
    SkillVersion,
    SkillVersionReviewStatus,
    SkillVersionStatus,
)
from app.models.user import User
from app.services.release_gate_service import ReleaseGateDecision
from app.services.scanner_service import aggregate_issues, build_ai_assist, Issue
from app.models.scan import IssueSeverity
from app.workers import scan_tasks


# ── Unit: build_ai_assist + aggregate_issues passthrough ────────────


def test_build_ai_assist_llm_when_deep_scan_ran():
    assert build_ai_assist(deep_scan_ran=True) == {
        "mode": "llm",
        "degraded": False,
        "reason": None,
    }


def test_build_ai_assist_baseline_when_static_only():
    assert build_ai_assist(deep_scan_ran=False, reason="no_llm_key_resolved") == {
        "mode": "baseline",
        "degraded": True,
        "reason": "no_llm_key_resolved",
    }


def test_build_ai_assist_baseline_default_reason():
    indicator = build_ai_assist(deep_scan_ran=False)
    assert indicator["mode"] == "baseline"
    assert indicator["degraded"] is True
    assert indicator["reason"]  # non-empty default reason


def test_aggregate_issues_attaches_ai_assist_when_provided():
    indicator = build_ai_assist(deep_scan_ran=True)
    out = aggregate_issues(
        [Issue(rule="X", severity=IssueSeverity.LOW, message="m", file="a")],
        ai_assist=indicator,
    )
    assert out["ai_assist"] == indicator


def test_aggregate_issues_omits_ai_assist_when_absent():
    out = aggregate_issues([])
    assert "ai_assist" not in out


# ── Worker integration helpers ──────────────────────────────────────


async def _seed_version(async_session) -> SkillVersion:
    user = User(
        username="scan",
        email="scan@example.com",
        hashed_password="x",
        full_name="Scan",
    )
    async_session.add(user)
    await async_session.flush()

    ns = Namespace(name="acme", owner_id=user.id)
    async_session.add(ns)
    await async_session.flush()

    skill = Skill(namespace_id=ns.id, name="widget", git_repo_path="/tmp/widget.git")
    async_session.add(skill)
    await async_session.flush()

    version = SkillVersion(
        skill_id=skill.id,
        tag="1.0.0",
        commit_sha="deadbeef",
        status=SkillVersionStatus.QUARANTINE,
        review_status=SkillVersionReviewStatus.NOT_REQUIRED,
        published_by=user.id,
    )
    async_session.add(version)
    await async_session.flush()

    async_session.add(ScanResult(version_id=version.id, status=ScanStatus.PENDING))
    await async_session.commit()
    return version


def _stub_worker(monkeypatch, async_session, *, files: dict[str, str]):
    """Wire scan_tasks collaborators to the test session + harmless stubs."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_session():
        yield async_session

    monkeypatch.setattr(scan_tasks, "worker_db_session", _fake_session)
    monkeypatch.setattr(
        scan_tasks.git_service,
        "get_version_files",
        lambda ns_name, skill_name, tag: files,
    )

    policy = SimpleNamespace(
        sandbox_network_mode="none",
        sandbox_workspace_mode="ephemeral",
        sandbox_agent_smoke_enabled=False,
        sandbox_agent_smoke_timeout_seconds=30,
        require_sandbox_success=False,
        manual_review_required=False,
        require_examples=False,
        require_validation_spec=False,
        clinic_gate_enabled=False,
        min_clinic_score=0,
        clinic_max_age_hours=0,
    )

    async def _fake_policy(db, *, namespace_id):
        return policy

    async def _fake_public_release(db, *, version_id):
        return None

    def _fake_derive(version, public_release):
        return "draft"

    async def _fake_record_sync(*args, **kwargs):
        return None

    async def _fake_dispatch(*args, **kwargs):
        return None

    monkeypatch.setattr(scan_tasks, "get_or_create_namespace_governance", _fake_policy)
    monkeypatch.setattr(scan_tasks, "get_public_release", _fake_public_release)
    monkeypatch.setattr(scan_tasks, "derive_sync_state", _fake_derive)
    monkeypatch.setattr(scan_tasks, "record_version_sync_event", _fake_record_sync)
    monkeypatch.setattr(scan_tasks, "dispatch_event", _fake_dispatch)
    return policy


def _read_ai_assist(version: SkillVersion) -> dict:
    assert version.gate_result is not None
    return version.gate_result["technical_checks"]["ai_assist"]


# ── Worker: no key → static-only → ai_assist baseline/degraded ──────


async def test_scan_without_key_surfaces_baseline_ai_assist(async_session, monkeypatch):
    # No LLM key resolves → deep scan skipped → only static scanner runs.
    monkeypatch.setattr(settings, "DUCKDOCK_LLM_API_KEY", "")
    # README only (no SKILL.md) trips SKILLMD_MISSING (HIGH) → FAILED path,
    # which builds gate_result inline (no sandbox/release-gate needed).
    _stub_worker(monkeypatch, async_session, files={"README.md": "hello"})

    version = await _seed_version(async_session)
    await scan_tasks._async_scan(version.id)
    await async_session.refresh(version)

    ai_assist = _read_ai_assist(version)
    assert ai_assist["mode"] == "baseline"
    assert ai_assist["degraded"] is True
    assert ai_assist["reason"]  # explains why AI scanning did not happen

    scan = (
        await async_session.execute(
            ScanResult.__table__.select().where(ScanResult.version_id == version.id)
        )
    ).first()
    assert scan is not None  # scan result persisted
    assert version.status == SkillVersionStatus.REJECTED  # SKILLMD_MISSING is HIGH


# ── Worker: key + deep scan ran → ai_assist mode == "llm" ───────────


async def test_scan_with_deep_scan_surfaces_llm_ai_assist(async_session, monkeypatch):
    monkeypatch.setattr(settings, "DUCKDOCK_LLM_API_KEY", "canon-key")
    monkeypatch.setattr(settings, "DUCKDOCK_LLM_BASE_URL", "https://llm.local/v1")
    monkeypatch.setattr(settings, "DUCKDOCK_LLM_MODEL", "canon-model")

    called = {}

    async def _fake_scan_with_llm(files, *, api_key, base_url, model):
        called["ran"] = True
        called["model"] = model
        return []  # deep scan ran, found nothing new

    monkeypatch.setattr(scan_tasks.scanner_service, "scan_with_llm", _fake_scan_with_llm)
    # Still a static HIGH (no SKILL.md) → FAILED path, deterministic gate_result.
    _stub_worker(monkeypatch, async_session, files={"README.md": "hello"})

    version = await _seed_version(async_session)
    await scan_tasks._async_scan(version.id)
    await async_session.refresh(version)

    assert called.get("ran") is True
    assert called.get("model") == "canon-model"
    ai_assist = _read_ai_assist(version)
    assert ai_assist["mode"] == "llm"
    assert ai_assist["degraded"] is False
    assert ai_assist["reason"] is None


# ── Worker: key set but deep scan FAILS → must NOT mislabel as llm ──


async def test_scan_with_failed_deep_scan_surfaces_baseline_ai_assist(async_session, monkeypatch):
    # A key resolves so the deep scan is attempted, but scan_with_llm raises
    # ScanLLMError (bad config / network / malformed response). The indicator MUST
    # report baseline/degraded, not mode="llm" — a failed scan must not masquerade
    # as a successful AI scan.
    monkeypatch.setattr(settings, "DUCKDOCK_LLM_API_KEY", "canon-key")
    monkeypatch.setattr(settings, "DUCKDOCK_LLM_BASE_URL", "https://llm.local/v1")
    monkeypatch.setattr(settings, "DUCKDOCK_LLM_MODEL", "canon-model")

    async def _boom(files, *, api_key, base_url, model):
        raise scan_tasks.ScanLLMError("deep scan request/response failed: boom")

    monkeypatch.setattr(scan_tasks.scanner_service, "scan_with_llm", _boom)
    _stub_worker(monkeypatch, async_session, files={"README.md": "hello"})

    version = await _seed_version(async_session)
    await scan_tasks._async_scan(version.id)
    await async_session.refresh(version)

    ai_assist = _read_ai_assist(version)
    assert ai_assist["mode"] == "baseline"
    assert ai_assist["degraded"] is True
    assert "deep_scan_failed" in (ai_assist["reason"] or "")


# ── Worker: gated (non-failed) path also surfaces ai_assist ─────────


async def test_scan_passed_path_attaches_ai_assist_into_gate_result(async_session, monkeypatch):
    monkeypatch.setattr(settings, "DUCKDOCK_LLM_API_KEY", "")
    # Clean SKILL.md with valid front-matter metadata → no static HIGH/critical →
    # non-failed path runs sandbox + release-gate (both stubbed).
    files = {"SKILL.md": "# clean skill\n\nThis skill summarizes notes."}
    _stub_worker(monkeypatch, async_session, files=files)

    sandbox_result = SimpleNamespace(
        status=scan_tasks.SandboxValidationStatus.PASSED,
        engine="stub",
        summary="ok",
        checks=[],
        logs=[],
    )
    monkeypatch.setattr(
        scan_tasks.sandbox_validation_service,
        "validate_version",
        lambda **kwargs: sandbox_result,
    )

    async def _fake_evaluate(db, **kwargs):
        return ReleaseGateDecision(
            status=SkillVersionStatus.PRODUCTION,
            review_status=SkillVersionReviewStatus.NOT_REQUIRED,
            gate_result={
                "final_status": "production",
                "technical_checks": {"scan_status": "passed", "sandbox_status": "passed"},
            },
        )

    monkeypatch.setattr(scan_tasks.release_gate_service, "evaluate", _fake_evaluate)

    version = await _seed_version(async_session)
    # metadata drives SKILL.md compliance (avoid SKILLMD_NO_FRONTMATTER medium → warned is fine,
    # but keep it clean so status is passed and the gate path is exercised).
    version.skill_metadata = {"name": "widget", "version": "1.0.0", "description": "d"}
    await async_session.commit()

    await scan_tasks._async_scan(version.id)
    await async_session.refresh(version)

    ai_assist = _read_ai_assist(version)
    # No key resolved → baseline even on the happy path; the existing technical_checks
    # from the gate decision are preserved alongside the injected indicator.
    assert ai_assist["mode"] == "baseline"
    assert ai_assist["degraded"] is True
    assert version.gate_result["technical_checks"]["scan_status"] == "passed"
