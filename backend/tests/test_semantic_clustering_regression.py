from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.outbox import OutboxEvent
from app.schemas.evaluation import (
    EvaluationSemanticClusteringPolicyCreate,
    EvaluationSemanticClusteringPolicyVersionCreate,
    EvaluationSemanticClusteringRunCreate,
)
from app.services.evaluation_ports import SemanticEmbeddingEvidence
from app.services.evaluation_semantic_clustering_service import (
    create_evaluation_semantic_clustering_policy,
    create_evaluation_semantic_clustering_policy_version,
    run_evaluation_semantic_clustering,
)
from test_evaluation_case_routing import _client
from test_failure_taxonomy_experience import _route


class _VectorAdapter:
    def __init__(self, vectors: tuple[tuple[float, ...], ...]) -> None:
        self._vectors = vectors

    async def embed_observations(self, *, sources, model_ref, dimensions, max_content_chars):
        assert model_ref == "test-bge"
        assert dimensions == 2
        assert max_content_chars == 800
        return [
            SemanticEmbeddingEvidence(
                source_trace_ref=source.source_trace_ref,
                source_observation_ref=source.source_observation_ref,
                content_digest=hashlib.sha256(f"same-private-content-{index}".encode()).hexdigest(),
                embedding_digest=hashlib.sha256(repr(self._vectors[index]).encode()).hexdigest(),
                vector=self._vectors[index],
            )
            for index, source in enumerate(sources)
        ]


async def _semantic_run(
    db,
    *,
    user,
    namespace,
    routing_version,
    routing_run,
    suffix: str,
    vectors: tuple[tuple[float, ...], ...],
):
    policy = await create_evaluation_semantic_clustering_policy(
        db,
        request=EvaluationSemanticClusteringPolicyCreate(
            namespace_id=namespace.id,
            name=f"semantic-regression-{suffix}",
        ),
        actor=user,
    )
    version = await create_evaluation_semantic_clustering_policy_version(
        db,
        policy=policy,
        request=EvaluationSemanticClusteringPolicyVersionCreate(
            source_case_routing_policy_version_public_id=routing_version.public_id,
            embedding_profile="test-local",
            model_ref="test-bge",
            dimensions=2,
            similarity_threshold=0.95,
            min_cluster_size=2,
            max_items=2,
            max_content_chars=800,
        ),
        actor=user,
    )
    return await run_evaluation_semantic_clustering(
        db,
        version_public_id=version.public_id,
        request=EvaluationSemanticClusteringRunCreate(source_case_routing_run_public_id=routing_run.public_id),
        idempotency_key=f"semantic-regression-{suffix}-run",
        actor=user,
        adapter=_VectorAdapter(vectors),
    )


@pytest.mark.asyncio
async def test_http_semantic_regression_pass_and_drift_are_reproducible(
    async_session,
) -> None:
    user, namespace, routing_version, routing_run = await _route(async_session, "semantic-regression", recurring=False)
    baseline = await _semantic_run(
        async_session,
        user=user,
        namespace=namespace,
        routing_version=routing_version,
        routing_run=routing_run,
        suffix="baseline",
        vectors=((1.0, 0.0), (0.99, 0.01)),
    )
    stable_candidate = await _semantic_run(
        async_session,
        user=user,
        namespace=namespace,
        routing_version=routing_version,
        routing_run=routing_run,
        suffix="stable",
        vectors=((1.0, 0.0), (0.99, 0.01)),
    )
    drifted_candidate = await _semantic_run(
        async_session,
        user=user,
        namespace=namespace,
        routing_version=routing_version,
        routing_run=routing_run,
        suffix="drifted",
        vectors=((1.0, 0.0), (0.0, 1.0)),
    )

    async with _client(async_session, user) as client:
        policy_response = await client.post(
            "/api/v2/evaluation-semantic-regression-policies",
            json={
                "namespace_id": namespace.id,
                "name": "semantic-cluster-quality",
            },
        )
        assert policy_response.status_code == 201
        policy = policy_response.json()
        version_response = await client.post(
            f"/api/v2/evaluation-semantic-regression-policies/{policy['public_id']}/versions",
            json={
                "minimum_pairwise_assignment_agreement": 0.9,
                "maximum_cluster_count_change_ratio": 0.25,
                "maximum_mean_centroid_similarity_drop": 0.1,
                "maximum_eligible_cluster_ratio_drop": 0.25,
            },
        )
        assert version_response.status_code == 201
        version = version_response.json()

        stable_response = await client.post(
            "/api/v2/evaluation-semantic-regression-comparisons",
            headers={"Idempotency-Key": "semantic-regression-stable-compare"},
            json={
                "namespace_id": namespace.id,
                "baseline_run_public_id": baseline.public_id,
                "candidate_run_public_id": stable_candidate.public_id,
                "policy_version_public_id": version["public_id"],
            },
        )
        assert stable_response.status_code == 201
        stable = stable_response.json()
        replay = await client.post(
            "/api/v2/evaluation-semantic-regression-comparisons",
            headers={"Idempotency-Key": "semantic-regression-stable-compare"},
            json={
                "namespace_id": namespace.id,
                "baseline_run_public_id": baseline.public_id,
                "candidate_run_public_id": stable_candidate.public_id,
                "policy_version_public_id": version["public_id"],
            },
        )
        assert replay.status_code == 201
        assert replay.json()["public_id"] == stable["public_id"]

        drifted_response = await client.post(
            "/api/v2/evaluation-semantic-regression-comparisons",
            headers={"Idempotency-Key": "semantic-regression-drifted-compare"},
            json={
                "namespace_id": namespace.id,
                "baseline_run_public_id": baseline.public_id,
                "candidate_run_public_id": drifted_candidate.public_id,
                "policy_version_public_id": version["public_id"],
            },
        )
        assert drifted_response.status_code == 201
        drifted = drifted_response.json()

        listed = await client.get(
            "/api/v2/evaluation-semantic-regression-comparisons",
            params={"namespace_id": namespace.id},
        )
        assert listed.status_code == 200
        assert len(listed.json()) == 2

    assert stable["outcome"] == "PASS"
    assert stable["reason_codes"] == ["semantic_regression_passed"]
    assert stable["pairwise_assignment_agreement"] == 1.0
    assert stable["cluster_count_change_ratio"] == 0.0
    assert stable["eligible_cluster_ratio_drop"] == 0.0
    assert drifted["outcome"] == "DRIFTED"
    assert drifted["pairwise_assignment_agreement"] == 0.0
    assert drifted["cluster_count_change_ratio"] == 0.5
    assert drifted["eligible_cluster_ratio_drop"] == 1.0
    assert "candidate_not_clustered" in drifted["reason_codes"]
    assert "pairwise_assignment_agreement_below_minimum" in drifted["reason_codes"]

    events = list(
        (
            await async_session.scalars(
                select(OutboxEvent).where(OutboxEvent.event_type == "EvaluationSemanticRegressionComparisonCreated")
            )
        ).all()
    )
    assert len(events) == 2
    serialized_events = json.dumps([event.payload_json for event in events])
    assert "same-private-content" not in serialized_events
    assert '"vector"' not in serialized_events
    assert '"embedding"' not in serialized_events
    audits = list(
        (
            await async_session.scalars(
                select(AuditLog).where(AuditLog.action == "evaluation_semantic_regression_comparison.created")
            )
        ).all()
    )
    assert len(audits) == 2
    assert "private" not in json.dumps([audit.details for audit in audits])
