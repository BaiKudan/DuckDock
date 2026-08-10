from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import func, select

from app.models.audit import AuditLog
from app.models.evaluation import EvaluationSemanticClusteringRun
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
from app.services.evaluation_service import EvaluationHubProviderError
from test_failure_taxonomy_experience import _route


class _EmbeddingAdapter:
    async def embed_observations(
        self, *, sources, model_ref, dimensions, max_content_chars
    ):
        assert model_ref == "test-bge"
        assert dimensions == 2
        assert max_content_chars == 800
        vectors = ((1.0, 0.0), (0.99, 0.01))
        return [
            SemanticEmbeddingEvidence(
                source_trace_ref=source.source_trace_ref,
                source_observation_ref=source.source_observation_ref,
                content_digest=hashlib.sha256(
                    f"private-content-{index}".encode()
                ).hexdigest(),
                embedding_digest=hashlib.sha256(
                    f"private-vector-{index}".encode()
                ).hexdigest(),
                vector=vectors[index],
            )
            for index, source in enumerate(sources)
        ]


class _FailingEmbeddingAdapter:
    async def embed_observations(self, **kwargs):
        raise RuntimeError("private provider detail must not escape")


@pytest.mark.asyncio
async def test_http_semantic_clusters_feed_failure_taxonomy(
    async_session, monkeypatch
) -> None:
    user, namespace, routing_version, routing_run = await _route(
        async_session, "semantic-http", recurring=False
    )
    monkeypatch.setattr(
        "app.services.evaluation_semantic_clustering_service._default_adapter",
        lambda version: _EmbeddingAdapter(),
    )
    from test_evaluation_case_routing import _client

    async with _client(async_session, user) as client:
        policy_response = await client.post(
            "/api/v2/evaluation-semantic-clustering-policies",
            json={
                "namespace_id": namespace.id,
                "name": "semantic-failures-http",
            },
        )
        assert policy_response.status_code == 201
        policy = policy_response.json()
        version_response = await client.post(
            f"/api/v2/evaluation-semantic-clustering-policies/{policy['public_id']}/versions",
            json={
                "source_case_routing_policy_version_public_id": routing_version.public_id,
                "embedding_profile": "test-local",
                "model_ref": "test-bge",
                "dimensions": 2,
                "similarity_threshold": 0.95,
                "min_cluster_size": 2,
                "max_items": 2,
                "max_content_chars": 800,
            },
        )
        assert version_response.status_code == 201
        version = version_response.json()
        run_response = await client.post(
            f"/api/v2/evaluation-semantic-clustering-policy-versions/{version['public_id']}/runs",
            headers={"Idempotency-Key": "semantic-failure-http-run"},
            json={"source_case_routing_run_public_id": routing_run.public_id},
        )
        assert run_response.status_code == 201
        semantic_run = run_response.json()
        replay = await client.post(
            f"/api/v2/evaluation-semantic-clustering-policy-versions/{version['public_id']}/runs",
            headers={"Idempotency-Key": "semantic-failure-http-run"},
            json={"source_case_routing_run_public_id": routing_run.public_id},
        )
        assert replay.status_code == 201
        assert replay.json()["public_id"] == semantic_run["public_id"]

        taxonomy_policy_response = await client.post(
            "/api/v2/evaluation-failure-taxonomy-policies",
            json={
                "namespace_id": namespace.id,
                "name": "semantic-taxonomy-http",
            },
        )
        taxonomy_policy = taxonomy_policy_response.json()
        taxonomy_version_response = await client.post(
            f"/api/v2/evaluation-failure-taxonomy-policies/{taxonomy_policy['public_id']}/versions",
            json={
                "source_case_routing_policy_version_public_id": routing_version.public_id,
                "source_semantic_clustering_policy_version_public_id": version[
                    "public_id"
                ],
                "min_cluster_occurrences": 2,
                "min_source_runs": 2,
                "include_isolated": False,
                "max_candidates": 5,
            },
        )
        assert taxonomy_version_response.status_code == 201
        taxonomy_version = taxonomy_version_response.json()
        extraction_response = await client.post(
            f"/api/v2/evaluation-failure-taxonomy-policy-versions/{taxonomy_version['public_id']}/runs",
            headers={"Idempotency-Key": "semantic-extraction-http-run"},
            json={
                "source_case_routing_run_public_id": routing_run.public_id,
                "source_semantic_clustering_run_public_id": semantic_run[
                    "public_id"
                ],
            },
        )
        assert extraction_response.status_code == 201
        extraction = extraction_response.json()

    assert semantic_run["outcome"] == "CLUSTERED"
    assert semantic_run["source_item_count"] == 2
    assert semantic_run["cluster_count"] == 1
    assert semantic_run["eligible_cluster_count"] == 1
    assert len({value["semantic_cluster_digest"] for value in semantic_run["items"]}) == 1
    assert all(value["cluster_size"] == 2 for value in semantic_run["items"])
    assert all("vector" not in value and "content" not in value for value in semantic_run["items"])
    assert taxonomy_version[
        "source_semantic_clustering_policy_version_public_id"
    ] == version["public_id"]
    assert extraction["source_semantic_clustering_run_public_id"] == semantic_run[
        "public_id"
    ]
    assert extraction["candidate_count"] == 1
    assert extraction["candidates"][0]["reason_code"] == (
        "semantic_failure_cluster_candidate"
    )
    assert extraction["candidates"][0]["cluster_digest"] == semantic_run["items"][0][
        "semantic_cluster_digest"
    ]

    events = list(
        (
            await async_session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.event_type.in_(
                        (
                            "EvaluationSemanticClusteringRunCreated",
                            "EvaluationExperienceExtractionRunCreated",
                        )
                    )
                )
            )
        ).all()
    )
    serialized = json.dumps([value.payload_json for value in events])
    assert len(events) == 2
    assert "private-content" not in serialized
    assert "private-vector" not in serialized
    assert '"vector"' not in serialized
    audits = list(
        (
            await async_session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "evaluation_semantic_clustering_run.created"
                )
            )
        ).all()
    )
    assert len(audits) == 1
    assert "private" not in json.dumps(audits[0].details)


@pytest.mark.asyncio
async def test_provider_failure_is_sanitized_and_writes_no_run(async_session) -> None:
    user, namespace, routing_version, routing_run = await _route(
        async_session, "semantic-provider-failure", recurring=False
    )
    policy = await create_evaluation_semantic_clustering_policy(
        async_session,
        request=EvaluationSemanticClusteringPolicyCreate(
            namespace_id=namespace.id,
            name="semantic-provider-failure",
        ),
        actor=user,
    )
    version = await create_evaluation_semantic_clustering_policy_version(
        async_session,
        policy=policy,
        request=EvaluationSemanticClusteringPolicyVersionCreate(
            source_case_routing_policy_version_public_id=routing_version.public_id,
            embedding_profile="test-local",
            model_ref="test-bge",
            dimensions=2,
            max_content_chars=800,
        ),
        actor=user,
    )
    with pytest.raises(EvaluationHubProviderError) as exc_info:
        await run_evaluation_semantic_clustering(
            async_session,
            version_public_id=version.public_id,
            request=EvaluationSemanticClusteringRunCreate(
                source_case_routing_run_public_id=routing_run.public_id
            ),
            idempotency_key="semantic-provider-failure-run",
            actor=user,
            adapter=_FailingEmbeddingAdapter(),
        )
    assert str(exc_info.value) == "semantic embedding evidence failed"
    assert (
        await async_session.scalar(
            select(func.count(EvaluationSemanticClusteringRun.id)).where(
                EvaluationSemanticClusteringRun.policy_version_id == version.id
            )
        )
        == 0
    )
