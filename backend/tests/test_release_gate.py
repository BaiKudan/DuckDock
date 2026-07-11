"""发布门禁（governance release gate）决策逻辑测试。

覆盖 ReleaseGateService.evaluate 的关键判定：clean→PRODUCTION、缺包→REVIEW、
sandbox 失败 / scan 失败 / 人工复核拒绝→REJECTED、人工复核待审→REVIEW（review_status→PENDING）。
对应 constitution 原则 I/IV · specs/001 的发布门禁。纯逻辑 + 内存 SQLite（clinic 查询返回 None）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.clinic import ClinicEvaluation, EvalStatus
from app.models.governance import NamespaceGovernancePolicy
from app.models.namespace import Namespace
from app.models.sandbox_validation import SandboxValidationRun, SandboxValidationStatus
from app.models.scan import ScanResult, ScanStatus
from app.models.skill import (
    Skill,
    SkillVersion,
    SkillVersionReviewStatus,
    SkillVersionStatus,
)
from app.services.release_gate_service import release_gate_service

COMPLETE_FILES = {
    "SKILL.md": "x",
    "examples/demo.md": "x",
    ".duckdock/validation.yaml": "spec",
}


def _policy(**overrides) -> NamespaceGovernancePolicy:
    base = dict(
        namespace_id=1,
        manual_review_required=False,
        require_examples=True,
        require_validation_spec=True,
        require_sandbox_success=True,
        clinic_gate_enabled=False,
        min_clinic_score=75.0,
        clinic_max_age_hours=168,
    )
    base.update(overrides)
    return NamespaceGovernancePolicy(**base)


async def _evaluate(session, *, files, policy, version=None, scan=None, sandbox_run=None):
    return await release_gate_service.evaluate(
        session,
        namespace=Namespace(name="team-a", owner_id=1),
        skill=Skill(),
        version=version or SkillVersion(review_status=SkillVersionReviewStatus.NOT_REQUIRED),
        files=files,
        policy=policy,
        scan=scan,
        sandbox_run=sandbox_run,
    )


async def test_clean_package_reaches_production(async_session):
    decision = await _evaluate(async_session, files=COMPLETE_FILES, policy=_policy())
    assert decision.status == SkillVersionStatus.PRODUCTION
    assert decision.gate_result["issue_count"] == 0


async def test_missing_examples_and_validation_goes_to_review(async_session):
    decision = await _evaluate(async_session, files={"SKILL.md": "x"}, policy=_policy())
    assert decision.status == SkillVersionStatus.REVIEW
    codes = {issue["code"] for issue in decision.gate_result["issues"]}
    assert "package_examples_missing" in codes
    assert "validation_spec_missing" in codes


async def test_sandbox_failure_rejects(async_session):
    decision = await _evaluate(
        async_session,
        files=COMPLETE_FILES,
        policy=_policy(require_sandbox_success=True),
        sandbox_run=SandboxValidationRun(status=SandboxValidationStatus.FAILED),
    )
    assert decision.status == SkillVersionStatus.REJECTED
    codes = {issue["code"] for issue in decision.gate_result["issues"]}
    assert "sandbox_not_passed" in codes


async def test_failed_scan_rejects(async_session):
    decision = await _evaluate(
        async_session,
        files=COMPLETE_FILES,
        policy=_policy(),
        scan=ScanResult(status=ScanStatus.FAILED),
    )
    assert decision.status == SkillVersionStatus.REJECTED


async def test_explicit_review_rejection_rejects(async_session):
    decision = await _evaluate(
        async_session,
        files=COMPLETE_FILES,
        policy=_policy(manual_review_required=True),
        version=SkillVersion(review_status=SkillVersionReviewStatus.REJECTED),
    )
    assert decision.status == SkillVersionStatus.REJECTED
    codes = {issue["code"] for issue in decision.gate_result["issues"]}
    assert "manual_review_rejected" in codes


async def test_manual_review_required_holds_in_review_and_marks_pending(async_session):
    decision = await _evaluate(
        async_session,
        files=COMPLETE_FILES,
        policy=_policy(manual_review_required=True),
        version=SkillVersion(review_status=SkillVersionReviewStatus.NOT_REQUIRED),
    )
    assert decision.status == SkillVersionStatus.REVIEW
    assert decision.review_status == SkillVersionReviewStatus.PENDING
    codes = {issue["code"] for issue in decision.gate_result["issues"]}
    assert "manual_review_pending" in codes


def test_clinic_gate_payload_normalizes_naive_completed_at():
    ai_assist = {"mode": "baseline", "degraded": True, "reason": "DUCKDOCK_LLM_API_KEY is not configured"}
    evaluation = ClinicEvaluation(
        namespace_id=1,
        status=EvalStatus.COMPLETED,
        overall_score=88.0,
        grade="B",
        ai_assist=ai_assist,
        completed_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2, minutes=5),
    )

    payload = release_gate_service._clinic_gate_payload(
        _policy(clinic_gate_enabled=True, clinic_max_age_hours=24),
        evaluation,
    )

    assert payload["age_hours"] == 2
    assert payload["ai_assist"] == ai_assist
    assert payload["blocking_issue"] is None
