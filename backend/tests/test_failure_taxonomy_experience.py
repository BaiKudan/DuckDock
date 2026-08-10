from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.evaluation import (
    EvaluationCaseRoutingLane,
    EvaluationExperienceCandidateStatus,
    EvaluationExperienceExtractionOutcome,
)
from app.models.outbox import OutboxEvent
from app.schemas.evaluation import (
    EvaluationCaseRoutingPolicyCreate,
    EvaluationCaseRoutingPolicyVersionCreate,
    EvaluationCaseRoutingRunCreate,
    EvaluationExperienceExtractionRunCreate,
    EvaluationFailureTaxonomyPolicyCreate,
    EvaluationFailureTaxonomyPolicyVersionCreate,
)
from app.services.evaluation_case_routing_service import (
    create_evaluation_case_routing_policy,
    create_evaluation_case_routing_policy_version,
    run_evaluation_case_routing_policy,
)
from app.services.evaluation_experience_service import (
    create_evaluation_failure_taxonomy_policy,
    create_evaluation_failure_taxonomy_policy_version,
    run_evaluation_experience_extraction,
)
from test_evaluation_case_routing import _client, _seed


async def _route(async_session, suffix: str, *, recurring: bool):
    (
        user,
        namespace,
        golden_dataset,
        bad_case_dataset,
        _,
        promotion_version,
        promotion_runs,
    ) = await _seed(async_session, suffix)
    if recurring:
        for run in promotion_runs:
            for item in run.items:
                if item.score_present and not item.quality_passed:
                    item.diversity_bucket_digest = "f" * 64
    policy = await create_evaluation_case_routing_policy(
        async_session,
        request=EvaluationCaseRoutingPolicyCreate(
            namespace_id=namespace.id,
            name=f"experience-routing-{suffix}",
        ),
        actor=user,
    )
    version = await create_evaluation_case_routing_policy_version(
        async_session,
        policy=policy,
        request=EvaluationCaseRoutingPolicyVersionCreate(
            source_promotion_policy_version_public_id=(
                promotion_version.public_id
            ),
            golden_target_size=2,
            golden_min_items=1,
            bad_case_target_size=2,
            bad_case_min_items=1,
        ),
        actor=user,
    )
    run = await run_evaluation_case_routing_policy(
        async_session,
        version_public_id=version.public_id,
        request=EvaluationCaseRoutingRunCreate(
            promotion_run_public_ids=[
                value.public_id for value in promotion_runs
            ],
            golden_dataset_public_id=golden_dataset.public_id,
            bad_case_dataset_public_id=bad_case_dataset.public_id,
        ),
        idempotency_key=f"experience-routing-run-{suffix}",
        actor=user,
    )
    assert sum(
        item.selected and item.lane == EvaluationCaseRoutingLane.BAD_CASE
        for item in run.items
    ) == 2
    return user, namespace, version, run


@pytest.mark.asyncio
async def test_http_extracts_and_reviews_cross_run_experience_candidate(
    async_session,
) -> None:
    user, namespace, routing_version, routing_run = await _route(
        async_session, "http", recurring=True
    )
    async with _client(async_session, user) as client:
        policy_response = await client.post(
            "/api/v2/evaluation-failure-taxonomy-policies",
            json={
                "namespace_id": namespace.id,
                "name": "failure-taxonomy-http",
            },
        )
        assert policy_response.status_code == 201
        policy = policy_response.json()
        version_response = await client.post(
            f"/api/v2/evaluation-failure-taxonomy-policies/{policy['public_id']}/versions",
            json={
                "source_case_routing_policy_version_public_id": (
                    routing_version.public_id
                ),
                "min_cluster_occurrences": 2,
                "min_source_runs": 2,
                "include_isolated": False,
                "max_candidates": 5,
            },
        )
        assert version_response.status_code == 201
        version = version_response.json()
        run_response = await client.post(
            f"/api/v2/evaluation-failure-taxonomy-policy-versions/{version['public_id']}/runs",
            headers={"Idempotency-Key": "experience-extraction-http"},
            json={
                "source_case_routing_run_public_id": routing_run.public_id
            },
        )
        assert run_response.status_code == 201
        payload = run_response.json()
        replay_response = await client.post(
            f"/api/v2/evaluation-failure-taxonomy-policy-versions/{version['public_id']}/runs",
            headers={"Idempotency-Key": "experience-extraction-http"},
            json={
                "source_case_routing_run_public_id": routing_run.public_id
            },
        )
        assert replay_response.status_code == 201
        assert replay_response.json()["public_id"] == payload["public_id"]
        candidate = payload["candidates"][0]
        review_response = await client.post(
            f"/api/v2/evaluation-experience-candidates/{candidate['public_id']}/reviews",
            json={"decision": "APPROVED"},
        )
        assert review_response.status_code == 201
        reviewed = review_response.json()
        duplicate_review = await client.post(
            f"/api/v2/evaluation-experience-candidates/{candidate['public_id']}/reviews",
            json={"decision": "REJECTED", "comment": "duplicate review"},
        )
        assert duplicate_review.status_code == 409

    assert payload["outcome"] == "EXTRACTED"
    assert payload["source_bad_case_count"] == 2
    assert payload["cluster_count"] == 1
    assert payload["eligible_cluster_count"] == 1
    assert payload["candidate_count"] == 1
    assert candidate["category"] == "CROSS_RUN_RECURRING"
    assert candidate["status"] == "PENDING_REVIEW"
    assert candidate["source_item_count"] == 2
    assert candidate["source_run_count"] == 2
    assert len(candidate["evidence_items"]) == 2
    assert reviewed["status"] == "APPROVED"
    assert reviewed["review"]["decision"] == "APPROVED"

    events = list(
        (
            await async_session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.event_type.in_(
                        (
                            "EvaluationExperienceExtractionRunCreated",
                            "EvaluationExperienceCandidateReviewed",
                        )
                    )
                )
            )
        ).all()
    )
    assert {event.event_type for event in events} == {
        "EvaluationExperienceExtractionRunCreated",
        "EvaluationExperienceCandidateReviewed",
    }
    serialized = json.dumps([event.payload_json for event in events])
    assert "duplicate review" not in serialized
    assert "input" not in serialized
    assert "output" not in serialized
    audits = list(
        (
            await async_session.scalars(
                select(AuditLog).where(
                    AuditLog.action.in_(
                        (
                            "evaluation_experience_extraction_run.created",
                            "evaluation_experience_candidate.reviewed",
                        )
                    )
                )
            )
        ).all()
    )
    assert len(audits) == 2


@pytest.mark.asyncio
async def test_isolated_failures_block_when_policy_excludes_them(
    async_session,
) -> None:
    user, namespace, routing_version, routing_run = await _route(
        async_session, "blocked", recurring=False
    )
    policy = await create_evaluation_failure_taxonomy_policy(
        async_session,
        request=EvaluationFailureTaxonomyPolicyCreate(
            namespace_id=namespace.id,
            name="failure-taxonomy-blocked",
        ),
        actor=user,
    )
    version = await create_evaluation_failure_taxonomy_policy_version(
        async_session,
        policy=policy,
        request=EvaluationFailureTaxonomyPolicyVersionCreate(
            source_case_routing_policy_version_public_id=(
                routing_version.public_id
            ),
            min_cluster_occurrences=2,
            min_source_runs=2,
            include_isolated=False,
            max_candidates=20,
        ),
        actor=user,
    )
    run = await run_evaluation_experience_extraction(
        async_session,
        version_public_id=version.public_id,
        request=EvaluationExperienceExtractionRunCreate(
            source_case_routing_run_public_id=routing_run.public_id
        ),
        idempotency_key="experience-extraction-blocked",
        actor=user,
    )

    assert run.outcome == EvaluationExperienceExtractionOutcome.BLOCKED
    assert run.reason_codes_json == ["no_eligible_failure_clusters"]
    assert run.source_bad_case_count == 2
    assert run.cluster_count == 2
    assert run.eligible_cluster_count == 0
    assert run.candidate_count == 0
    assert run.candidates == []


@pytest.mark.asyncio
async def test_isolated_candidates_can_be_explicitly_enabled(
    async_session,
) -> None:
    user, namespace, routing_version, routing_run = await _route(
        async_session, "isolated", recurring=False
    )
    policy = await create_evaluation_failure_taxonomy_policy(
        async_session,
        request=EvaluationFailureTaxonomyPolicyCreate(
            namespace_id=namespace.id,
            name="failure-taxonomy-isolated",
        ),
        actor=user,
    )
    version = await create_evaluation_failure_taxonomy_policy_version(
        async_session,
        policy=policy,
        request=EvaluationFailureTaxonomyPolicyVersionCreate(
            source_case_routing_policy_version_public_id=(
                routing_version.public_id
            ),
            include_isolated=True,
            max_candidates=1,
        ),
        actor=user,
    )
    run = await run_evaluation_experience_extraction(
        async_session,
        version_public_id=version.public_id,
        request=EvaluationExperienceExtractionRunCreate(
            source_case_routing_run_public_id=routing_run.public_id
        ),
        idempotency_key="experience-extraction-isolated",
        actor=user,
    )

    assert run.outcome == EvaluationExperienceExtractionOutcome.EXTRACTED
    assert run.candidate_count == 1
    assert run.candidates[0].category.value == "ISOLATED"
    assert (
        run.candidates[0].status
        == EvaluationExperienceCandidateStatus.PENDING_REVIEW
    )
