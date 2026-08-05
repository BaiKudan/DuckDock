"""Future Release Gate evidence must be pinned to an exact candidate revision.

Foundation only introduces the provider-neutral lookup seam.  The current
Clinic-backed Release Gate must not consume these results until the later
candidate/evaluation specification is implemented.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.governance import NamespaceGovernancePolicy
from app.models.namespace import Namespace
from app.models.skill import Skill, SkillVersion, SkillVersionReviewStatus, SkillVersionStatus
from app.services.release_evidence_ports import (
    CandidateEvidenceReference,
    ReleaseEvidenceSelector,
)
from app.services.release_gate_service import ReleaseGateService


class InMemoryReleaseEvidenceLookupPort:
    def __init__(self, evidence: list[CandidateEvidenceReference]) -> None:
        self._evidence = {item.selector: item for item in evidence}
        self.requests: list[ReleaseEvidenceSelector] = []

    async def lookup(
        self,
        selector: ReleaseEvidenceSelector,
    ) -> tuple[CandidateEvidenceReference, ...]:
        self.requests.append(selector)
        item = self._evidence.get(selector)
        return (item,) if item is not None else ()


def _selector(
    *,
    candidate: str = "candidate:skill-a:17",
    deployment: str = "deployment-public-a",
    revision: str = "4",
) -> ReleaseEvidenceSelector:
    return ReleaseEvidenceSelector(
        namespace_id=7,
        release_candidate_ref=candidate,
        deployment_public_id=deployment,
        deployment_revision=revision,
    )


def _evidence(selector: ReleaseEvidenceSelector) -> CandidateEvidenceReference:
    return CandidateEvidenceReference(
        selector=selector,
        evidence_id="evidence-alpha-001",
        evidence_kind="runtime_evaluation",
        verdict="pass",
        artifact_sha256="a" * 64,
        observed_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("namespace_id", 0),
        ("release_candidate_ref", ""),
        ("release_candidate_ref", "latest"),
        ("deployment_public_id", " "),
        ("deployment_revision", ""),
    ],
)
def test_selector_requires_exact_candidate_and_deployment_revision(
    field: str,
    value: object,
) -> None:
    values: dict[str, object] = {
        "namespace_id": 7,
        "release_candidate_ref": "candidate:skill-a:17",
        "deployment_public_id": "deployment-public-a",
        "deployment_revision": "4",
    }
    values[field] = value

    with pytest.raises(ValueError):
        ReleaseEvidenceSelector(**values)  # type: ignore[arg-type]


async def test_lookup_has_no_namespace_latest_fallback() -> None:
    exact = _selector()
    lookup = InMemoryReleaseEvidenceLookupPort([_evidence(exact)])
    service = ReleaseGateService(evidence_lookup=lookup)

    assert await service.lookup_candidate_evidence(exact) == (
        _evidence(exact),
    )
    assert (
        await service.lookup_candidate_evidence(
            _selector(candidate="candidate:skill-a:18")
        )
        == ()
    )
    assert await service.lookup_candidate_evidence(_selector(revision="5")) == ()
    assert lookup.requests == [
        exact,
        _selector(candidate="candidate:skill-a:18"),
        _selector(revision="5"),
    ]


async def test_current_release_gate_does_not_call_future_evidence_lookup(
    async_session,
) -> None:
    class FailIfCalledEvidenceLookup:
        called = False

        async def lookup(
            self,
            selector: ReleaseEvidenceSelector,
        ) -> tuple[CandidateEvidenceReference, ...]:
            self.called = True
            raise AssertionError(f"current gate must not query future evidence: {selector}")

    lookup = FailIfCalledEvidenceLookup()
    service = ReleaseGateService(evidence_lookup=lookup)
    policy = NamespaceGovernancePolicy(
        namespace_id=7,
        manual_review_required=False,
        require_examples=True,
        require_validation_spec=True,
        require_sandbox_success=True,
        clinic_gate_enabled=False,
        min_clinic_score=75.0,
        clinic_max_age_hours=24,
    )

    decision = await service.evaluate(
        async_session,
        namespace=Namespace(id=7, name="release-seam", owner_id=1),
        skill=Skill(),
        version=SkillVersion(review_status=SkillVersionReviewStatus.NOT_REQUIRED),
        files={
            "SKILL.md": "# Release seam",
            "examples/demo.md": "example",
            ".duckdock/validation.yaml": "steps: []",
        },
        policy=policy,
    )

    assert decision.status == SkillVersionStatus.PRODUCTION
    assert decision.gate_result["issue_count"] == 0
    assert lookup.called is False
