from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.namespace import NamespaceMember, NamespaceRole
from app.models.outbox import OutboxEvent
from app.models.user import User
from app.schemas.evaluation import (
    EvaluationExperienceCandidateReviewCreate,
    EvaluationExperienceExtractionRunCreate,
    EvaluationFailureTaxonomyPolicyCreate,
    EvaluationFailureTaxonomyPolicyVersionCreate,
)
from app.models.evaluation import EvaluationExperienceReviewDecision
from app.services.evaluation_experience_service import (
    create_evaluation_failure_taxonomy_policy,
    create_evaluation_failure_taxonomy_policy_version,
    review_evaluation_experience_candidate,
    run_evaluation_experience_extraction,
)
from test_evaluation_case_routing import _client
from test_failure_taxonomy_experience import _route


async def _approved_candidate(db, suffix: str):
    user, namespace, routing_version, routing_run = await _route(
        db, suffix, recurring=True
    )
    policy = await create_evaluation_failure_taxonomy_policy(
        db,
        request=EvaluationFailureTaxonomyPolicyCreate(
            namespace_id=namespace.id,
            name=f"activation-taxonomy-{suffix}",
        ),
        actor=user,
    )
    version = await create_evaluation_failure_taxonomy_policy_version(
        db,
        policy=policy,
        request=EvaluationFailureTaxonomyPolicyVersionCreate(
            source_case_routing_policy_version_public_id=(
                routing_version.public_id
            ),
            min_cluster_occurrences=2,
            min_source_runs=2,
            include_isolated=False,
            max_candidates=5,
        ),
        actor=user,
    )
    run = await run_evaluation_experience_extraction(
        db,
        version_public_id=version.public_id,
        request=EvaluationExperienceExtractionRunCreate(
            source_case_routing_run_public_id=routing_run.public_id
        ),
        idempotency_key=f"activation-extraction-{suffix}",
        actor=user,
    )
    candidate = run.candidates[0]
    await review_evaluation_experience_candidate(
        db,
        public_id=candidate.public_id,
        request=EvaluationExperienceCandidateReviewCreate(
            decision=EvaluationExperienceReviewDecision.APPROVED
        ),
        actor=user,
    )
    return user, namespace, candidate


@pytest.mark.asyncio
async def test_http_versions_experience_and_requires_independent_activation_review(
    async_session,
) -> None:
    author, namespace, candidate = await _approved_candidate(
        async_session, "four-eyes"
    )
    reviewer = User(
        username="activation-reviewer",
        email="activation-reviewer@example.com",
        hashed_password="unused",
    )
    async_session.add(reviewer)
    await async_session.flush()
    reviewer_id = reviewer.id
    async_session.add(
        NamespaceMember(
            namespace_id=namespace.id,
            user_id=reviewer.id,
            role=NamespaceRole.DEVELOPER,
        )
    )
    await async_session.flush()

    body = "遇到同类失败时，先校验输入约束，再选择可恢复路径并记录结果。"
    applicability = "适用于相同失败簇的重复执行，不适用于孤立错误。"
    request_note = "请复核该经验是否可以作为控制面激活版本。"
    async with _client(async_session, author) as client:
        asset_response = await client.post(
            "/api/v2/evaluation-experience-assets",
            json={
                "namespace_id": namespace.id,
                "source_candidate_public_id": candidate.public_id,
                "name": "recover-recurring-input-failure",
                "description": "人工编写的恢复经验",
            },
        )
        assert asset_response.status_code == 201
        asset = asset_response.json()
        version_response = await client.post(
            f"/api/v2/evaluation-experience-assets/{asset['public_id']}/versions",
            json={
                "body": body,
                "applicability": applicability,
                "change_summary": "首个可审批草稿",
            },
        )
        assert version_response.status_code == 201
        version = version_response.json()["versions"][0]
        assert version["status"] == "DRAFT"
        request_response = await client.post(
            f"/api/v2/evaluation-experience-asset-versions/{version['public_id']}/activation-requests",
            json={"request_note": request_note},
        )
        assert request_response.status_code == 201
        requested_version = request_response.json()["versions"][0]
        activation_request = requested_version["activation_request"]
        assert requested_version["status"] == "PENDING_ACTIVATION"
        self_review = await client.post(
            f"/api/v2/evaluation-experience-activation-requests/{activation_request['public_id']}/reviews",
            json={"decision": "APPROVED", "comment": "self approval"},
        )
        assert self_review.status_code == 409
        assert "independent" in self_review.json()["detail"]

    review_comment = "已核对证据摘要和适用边界，同意激活。"
    reviewer = await async_session.get(User, reviewer_id)
    assert reviewer is not None
    async with _client(async_session, reviewer) as client:
        review_response = await client.post(
            f"/api/v2/evaluation-experience-activation-requests/{activation_request['public_id']}/reviews",
            json={"decision": "APPROVED", "comment": review_comment},
        )
        assert review_response.status_code == 201
        activated = review_response.json()
        assert activated["versions"][0]["status"] == "ACTIVE"
        assert (
            activated["versions"][0]["activation_request"]["review"][
                "reviewed_by_user_id"
            ]
            == reviewer_id
        )
        list_response = await client.get(
            "/api/v2/evaluation-experience-assets",
            params={"namespace_id": namespace.id},
        )
        assert list_response.status_code == 200
        assert list_response.json()[0]["versions"][0]["body"] == body

    event_types = {
        "EvaluationExperienceAssetVersionCreated",
        "EvaluationExperienceActivationRequested",
        "EvaluationExperienceActivationReviewed",
    }
    events = list(
        (
            await async_session.scalars(
                select(OutboxEvent).where(OutboxEvent.event_type.in_(event_types))
            )
        ).all()
    )
    assert {event.event_type for event in events} == event_types
    serialized_events = json.dumps(
        [event.payload_json for event in events], ensure_ascii=False
    )
    for private_text in (body, applicability, request_note, review_comment):
        assert private_text not in serialized_events

    audits = list(
        (
            await async_session.scalars(
                select(AuditLog).where(
                    AuditLog.action.in_(
                        {
                            "evaluation_experience_asset_version.created",
                            "evaluation_experience_activation.requested",
                            "evaluation_experience_activation.reviewed",
                        }
                    )
                )
            )
        ).all()
    )
    serialized_audits = json.dumps(
        [audit.details for audit in audits], ensure_ascii=False
    )
    for private_text in (body, applicability, request_note, review_comment):
        assert private_text not in serialized_audits


@pytest.mark.asyncio
async def test_approved_new_version_retires_previous_active_version(
    async_session,
) -> None:
    author, namespace, candidate = await _approved_candidate(
        async_session, "replacement"
    )
    reviewer = User(
        username="replacement-reviewer",
        email="replacement-reviewer@example.com",
        hashed_password="unused",
    )
    async_session.add(reviewer)
    await async_session.flush()
    reviewer_id = reviewer.id
    async_session.add(
        NamespaceMember(
            namespace_id=namespace.id,
            user_id=reviewer.id,
            role=NamespaceRole.DEVELOPER,
        )
    )
    await async_session.flush()

    async with _client(async_session, author) as client:
        asset = (
            await client.post(
                "/api/v2/evaluation-experience-assets",
                json={
                    "namespace_id": namespace.id,
                    "source_candidate_public_id": candidate.public_id,
                    "name": "version-replacement",
                },
            )
        ).json()
        activation_ids: list[str] = []
        for index in (1, 2):
            versioned = await client.post(
                f"/api/v2/evaluation-experience-assets/{asset['public_id']}/versions",
                json={
                    "body": f"第 {index} 版经验：先验证输入，再执行恢复步骤。",
                    "applicability": "用于重复发生且证据充分的失败簇。",
                },
            )
            assert versioned.status_code == 201
            version = versioned.json()["versions"][-1]
            requested = await client.post(
                f"/api/v2/evaluation-experience-asset-versions/{version['public_id']}/activation-requests",
                json={},
            )
            assert requested.status_code == 201
            activation_ids.append(
                requested.json()["versions"][-1]["activation_request"]["public_id"]
            )
            if index == 1:
                break

    reviewer = await async_session.get(User, reviewer_id)
    assert reviewer is not None
    async with _client(async_session, reviewer) as client:
        first = await client.post(
            f"/api/v2/evaluation-experience-activation-requests/{activation_ids[0]}/reviews",
            json={"decision": "APPROVED"},
        )
        assert first.status_code == 201

    async with _client(async_session, author) as client:
        versioned = await client.post(
            f"/api/v2/evaluation-experience-assets/{asset['public_id']}/versions",
            json={
                "body": "第 2 版经验：增加恢复后复核，并保留结构化结果。",
                "applicability": "用于重复发生且证据充分的失败簇。",
            },
        )
        assert versioned.status_code == 201
        version = versioned.json()["versions"][-1]
        requested = await client.post(
            f"/api/v2/evaluation-experience-asset-versions/{version['public_id']}/activation-requests",
            json={},
        )
        assert requested.status_code == 201
        second_request_id = requested.json()["versions"][-1][
            "activation_request"
        ]["public_id"]

    reviewer = await async_session.get(User, reviewer_id)
    assert reviewer is not None
    async with _client(async_session, reviewer) as client:
        second = await client.post(
            f"/api/v2/evaluation-experience-activation-requests/{second_request_id}/reviews",
            json={"decision": "APPROVED"},
        )
        assert second.status_code == 201
        statuses = [version["status"] for version in second.json()["versions"]]
        assert statuses == ["RETIRED", "ACTIVE"]
