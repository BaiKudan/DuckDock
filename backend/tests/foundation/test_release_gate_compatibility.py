"""Characterize the existing Clinic-backed Release Gate decisions.

Foundation work must preserve these outcomes until a future candidate-pinned
evaluation contract intentionally replaces the Namespace-latest lookup.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clinic import ClinicEvaluation, EvalStatus
from app.models.governance import NamespaceGovernancePolicy
from app.models.namespace import Namespace
from app.models.skill import (
    Skill,
    SkillVersion,
    SkillVersionReviewStatus,
    SkillVersionStatus,
)
from app.models.user import User
from app.services.release_gate_service import ReleaseGateDecision, release_gate_service


COMPLETE_FILES = {
    "SKILL.md": "# Characterized skill",
    "examples/demo.md": "example",
    ".duckdock/validation.yaml": "steps: []",
}


def _clinic_policy(namespace_id: int) -> NamespaceGovernancePolicy:
    return NamespaceGovernancePolicy(
        namespace_id=namespace_id,
        manual_review_required=False,
        require_examples=True,
        require_validation_spec=True,
        require_sandbox_success=True,
        clinic_gate_enabled=True,
        min_clinic_score=75.0,
        clinic_max_age_hours=24,
    )


async def _seed_namespace(session: AsyncSession, *, suffix: str) -> Namespace:
    owner = User(
        username=f"release-gate-owner-{suffix}",
        email=f"release-gate-owner-{suffix}@duckdock.test",
        hashed_password="not-used",
    )
    session.add(owner)
    await session.flush()

    namespace = Namespace(name=f"release-gate-{suffix}", owner_id=owner.id)
    session.add(namespace)
    await session.flush()
    return namespace


async def _record_completed_evaluation(
    session: AsyncSession,
    *,
    namespace_id: int,
    score: float,
    grade: str,
) -> ClinicEvaluation:
    evaluation = ClinicEvaluation(
        namespace_id=namespace_id,
        status=EvalStatus.COMPLETED,
        overall_score=score,
        grade=grade,
        completed_at=datetime.now(timezone.utc),
    )
    session.add(evaluation)
    await session.flush()
    return evaluation


async def _evaluate(session: AsyncSession, namespace: Namespace) -> ReleaseGateDecision:
    return await release_gate_service.evaluate(
        session,
        namespace=namespace,
        skill=Skill(),
        version=SkillVersion(review_status=SkillVersionReviewStatus.NOT_REQUIRED),
        files=COMPLETE_FILES,
        policy=_clinic_policy(namespace.id),
    )


async def test_current_release_gate_passes_a_fresh_above_threshold_evaluation(
    async_session: AsyncSession,
) -> None:
    namespace = await _seed_namespace(async_session, suffix="pass")
    evaluation = await _record_completed_evaluation(
        async_session,
        namespace_id=namespace.id,
        score=88.0,
        grade="B",
    )

    decision = await _evaluate(async_session, namespace)

    assert decision.status == SkillVersionStatus.PRODUCTION
    assert decision.review_status == SkillVersionReviewStatus.NOT_REQUIRED
    assert decision.gate_result["final_status"] == SkillVersionStatus.PRODUCTION.value
    assert decision.gate_result["issue_count"] == 0
    assert decision.gate_result["issues"] == []
    assert decision.gate_result["clinic"]["evaluation_id"] == evaluation.id
    assert decision.gate_result["clinic"]["score"] == 88.0
    assert decision.gate_result["clinic"]["blocking_issue"] is None


async def test_current_release_gate_holds_a_below_threshold_evaluation_for_review(
    async_session: AsyncSession,
) -> None:
    namespace = await _seed_namespace(async_session, suffix="below-threshold")
    evaluation = await _record_completed_evaluation(
        async_session,
        namespace_id=namespace.id,
        score=74.0,
        grade="C",
    )

    decision = await _evaluate(async_session, namespace)

    assert decision.status == SkillVersionStatus.REVIEW
    assert decision.review_status == SkillVersionReviewStatus.NOT_REQUIRED
    assert decision.gate_result["final_status"] == SkillVersionStatus.REVIEW.value
    assert decision.gate_result["issue_count"] == 1
    assert decision.gate_result["issues"] == [
        {
            "code": "clinic_below_threshold",
            "severity": "high",
            "message": "Latest Clinic score 74.0 is below the policy threshold 75.0.",
        }
    ]
    assert decision.gate_result["clinic"]["evaluation_id"] == evaluation.id
    assert decision.gate_result["clinic"]["blocking_issue"] == decision.gate_result["issues"][0]


async def test_current_release_gate_holds_when_no_completed_evaluation_exists(
    async_session: AsyncSession,
) -> None:
    namespace = await _seed_namespace(async_session, suffix="no-evaluation")

    decision = await _evaluate(async_session, namespace)

    assert decision.status == SkillVersionStatus.REVIEW
    assert decision.review_status == SkillVersionReviewStatus.NOT_REQUIRED
    assert decision.gate_result["final_status"] == SkillVersionStatus.REVIEW.value
    assert decision.gate_result["issue_count"] == 1
    assert decision.gate_result["issues"] == [
        {
            "code": "clinic_missing",
            "severity": "high",
            "message": "Clinic gate is enabled but there is no completed evaluation for this namespace.",
        }
    ]
    assert decision.gate_result["clinic"]["evaluation_id"] is None
    assert decision.gate_result["clinic"]["score"] is None
    assert decision.gate_result["clinic"]["blocking_issue"] == decision.gate_result["issues"][0]
