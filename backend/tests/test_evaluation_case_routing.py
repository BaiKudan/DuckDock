from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.api.v2.router import api_router
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.audit import AuditLog
from app.models.evaluation import (
    EvaluationAnnotationDispatchStatus,
    EvaluationAnnotationItemSyncStatus,
    EvaluationAnnotationProviderStatus,
    EvaluationCaseRoutingOutcome,
    EvaluationDatasetCurationBatch,
    EvaluationDatasetCurationItem,
)
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.outbox import OutboxEvent
from app.models.user import User
from app.schemas.evaluation import (
    EvaluationCaseRoutingPolicyCreate,
    EvaluationCaseRoutingPolicyVersionCreate,
    EvaluationCaseRoutingRunCreate,
    EvaluationDatasetCreate,
    EvaluationPromotionPolicyCreate,
    EvaluationPromotionPolicyVersionCreate,
)
from app.services.evaluation_annotation_service import (
    create_annotation_dispatch,
    create_annotation_queue_binding,
)
from app.services.evaluation_case_routing_service import (
    create_evaluation_case_routing_policy,
    create_evaluation_case_routing_policy_version,
    run_evaluation_case_routing_policy,
)
from app.services.evaluation_ports import (
    AnnotationQueueDescriptor,
    PromotionEvidenceItem,
)
from app.services.evaluation_promotion_service import (
    create_evaluation_promotion_policy,
    create_evaluation_promotion_policy_version,
    run_evaluation_promotion_policy,
)
from app.services.evaluation_service import (
    EvaluationHubStateError,
    create_evaluation_dataset,
)


NOW = datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc)
SOURCES = (
    ("1" * 32, "a" * 16, True, "1" * 64),
    ("2" * 32, "b" * 16, False, "1" * 64),
    ("3" * 32, "c" * 16, True, "2" * 64),
    ("4" * 32, "d" * 16, False, "2" * 64),
)


class _QueueAdapter:
    descriptor = AnnotationQueueDescriptor(
        provider_queue_ref="queue-next012",
        name="duckdock-next012",
        description="case routing queue",
        score_config_ids=("score-next012",),
        created_at=NOW,
        updated_at=NOW,
    )

    async def get_queue(self, *, queue_ref: str):
        assert queue_ref == self.descriptor.provider_queue_ref
        return self.descriptor


class _PromotionAdapter:
    async def evaluate_promotion_evidence(
        self,
        *,
        queue_ref,
        quality_rule,
        diversity_dimension,
        sources,
    ):
        assert queue_ref == "queue-next012"
        assert quality_rule.score_config_id == "score-next012"
        assert diversity_dimension == "OBSERVATION_NAME"
        evidence = {
            observation_ref: (passed, bucket)
            for _, observation_ref, passed, bucket in SOURCES
        }
        return [
            PromotionEvidenceItem(
                source_trace_ref=source.source_trace_ref,
                source_observation_ref=source.source_observation_ref,
                score_present=True,
                quality_passed=evidence[source.source_observation_ref][0],
                diversity_bucket_present=True,
                score_evidence_digest=(
                    source.source_observation_ref[0] * 64
                ),
                diversity_bucket_digest=(
                    evidence[source.source_observation_ref][1]
                ),
            )
            for source in sources
        ]


def _complete_dispatch(dispatch) -> None:
    dispatch.status = EvaluationAnnotationDispatchStatus.SYNCED
    dispatch.synced_count = dispatch.item_count
    dispatch.completed_count = dispatch.item_count
    dispatch.failed_count = 0
    dispatch.lease_owner = None
    dispatch.lease_expires_at = None
    dispatch.synced_at = NOW
    for item in dispatch.items:
        item.sync_status = EvaluationAnnotationItemSyncStatus.SYNCED
        item.provider_queue_item_ref = f"queue-item-{item.position}"
        item.provider_annotation_status = (
            EvaluationAnnotationProviderStatus.COMPLETED
        )
        item.provider_created_at = NOW
        item.provider_updated_at = NOW
        item.provider_completed_at = NOW


async def _seed(db, suffix: str):
    user = User(
        username=f"routing-{suffix}",
        email=f"routing-{suffix}@example.com",
        hashed_password="unused",
    )
    db.add(user)
    await db.flush()
    namespace = Namespace(name=f"routing-{suffix}", owner_id=user.id)
    db.add(namespace)
    await db.flush()
    db.add(
        NamespaceMember(
            namespace_id=namespace.id,
            user_id=user.id,
            role=NamespaceRole.ADMIN,
        )
    )
    source_dataset = await create_evaluation_dataset(
        db,
        request=EvaluationDatasetCreate(
            namespace_id=namespace.id,
            name=f"routing-source-{suffix}",
            sync_provider=False,
            provider_dataset_ref=f"routing-source-provider-{suffix}",
        ),
        actor=user,
    )
    golden_dataset = await create_evaluation_dataset(
        db,
        request=EvaluationDatasetCreate(
            namespace_id=namespace.id,
            name=f"routing-golden-{suffix}",
            sync_provider=False,
            provider_dataset_ref=f"routing-golden-provider-{suffix}",
        ),
        actor=user,
    )
    bad_case_dataset = await create_evaluation_dataset(
        db,
        request=EvaluationDatasetCreate(
            namespace_id=namespace.id,
            name=f"routing-bad-{suffix}",
            sync_provider=False,
            provider_dataset_ref=f"routing-bad-provider-{suffix}",
        ),
        actor=user,
    )
    queue_adapter = _QueueAdapter()
    binding = await create_annotation_queue_binding(
        db,
        namespace_id=namespace.id,
        provider_queue_ref=queue_adapter.descriptor.provider_queue_ref,
        actor=user,
        adapter=queue_adapter,
    )
    promotion_policy = await create_evaluation_promotion_policy(
        db,
        request=EvaluationPromotionPolicyCreate(
            namespace_id=namespace.id,
            name=f"routing-promotion-{suffix}",
        ),
        actor=user,
    )
    promotion_version = await create_evaluation_promotion_policy_version(
        db,
        policy=promotion_policy,
        request=EvaluationPromotionPolicyVersionCreate(
            binding_public_id=binding.public_id,
            score_config_id="score-next012",
            score_data_type="NUMERIC",
            minimum_numeric_score=0.8,
            diversity_dimension="OBSERVATION_NAME",
            min_distinct_buckets=1,
        ),
        actor=user,
    )
    promotion_runs = []
    for batch_index, batch_sources in enumerate(
        (SOURCES[:2], SOURCES[2:]), start=1
    ):
        batch = EvaluationDatasetCurationBatch(
            public_id=f"ecb_{suffix}_{batch_index}",
            namespace_id=namespace.id,
            dataset_id=source_dataset.id,
            idempotency_key=f"routing-source-batch-{suffix}-{batch_index}",
            selection_digest=str(batch_index) * 64,
            item_count=2,
            schema_name="langfuse-trace-dataset-curation",
            schema_version="1.0",
            submitted_by_user_id=user.id,
            created_at=NOW,
        )
        batch.items = [
            EvaluationDatasetCurationItem(
                position=position,
                source_trace_ref=trace_ref,
                source_observation_ref=observation_ref,
            )
            for position, (trace_ref, observation_ref, _, _) in enumerate(
                batch_sources, start=1
            )
        ]
        db.add(batch)
        await db.flush()
        dispatch = await create_annotation_dispatch(
            db,
            binding_public_id=binding.public_id,
            curation_batch_public_id=batch.public_id,
            idempotency_key=f"routing-dispatch-{suffix}-{batch_index}",
            actor=user,
        )
        _complete_dispatch(dispatch)
        promotion_runs.append(
            await run_evaluation_promotion_policy(
                db,
                version_public_id=promotion_version.public_id,
                dispatch_public_id=dispatch.public_id,
                idempotency_key=f"routing-promotion-run-{suffix}-{batch_index}",
                actor=user,
                adapter=_PromotionAdapter(),
            )
        )
    return (
        user,
        namespace,
        golden_dataset,
        bad_case_dataset,
        promotion_policy,
        promotion_version,
        promotion_runs,
    )


@asynccontextmanager
async def _client(db, user):
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v2")

    async def override_db():
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    async def override_current_user():
        return user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_current_user
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_case_routing_http_creates_balanced_review_batches(
    async_session,
) -> None:
    (
        user,
        namespace,
        golden_dataset,
        bad_case_dataset,
        _,
        promotion_version,
        promotion_runs,
    ) = await _seed(async_session, "http")

    async with _client(async_session, user) as client:
        policy_response = await client.post(
            "/api/v2/evaluation-case-routing-policies",
            json={
                "namespace_id": namespace.id,
                "name": "golden-bad-routing-http",
            },
        )
        assert policy_response.status_code == 201
        policy = policy_response.json()
        version_response = await client.post(
            f"/api/v2/evaluation-case-routing-policies/{policy['public_id']}/versions",
            json={
                "source_promotion_policy_version_public_id": (
                    promotion_version.public_id
                ),
                "golden_target_size": 2,
                "golden_min_items": 1,
                "bad_case_target_size": 2,
                "bad_case_min_items": 1,
            },
        )
        assert version_response.status_code == 201
        version = version_response.json()
        request = {
            "promotion_run_public_ids": [
                value.public_id for value in promotion_runs
            ],
            "golden_dataset_public_id": golden_dataset.public_id,
            "bad_case_dataset_public_id": bad_case_dataset.public_id,
        }
        run_response = await client.post(
            f"/api/v2/evaluation-case-routing-policy-versions/{version['public_id']}/runs",
            headers={"Idempotency-Key": "case-routing-http-run"},
            json=request,
        )
        assert run_response.status_code == 201
        payload = run_response.json()
        replay_response = await client.post(
            f"/api/v2/evaluation-case-routing-policy-versions/{version['public_id']}/runs",
            headers={"Idempotency-Key": "case-routing-http-run"},
            json=request,
        )
        assert replay_response.status_code == 201
        assert replay_response.json()["public_id"] == payload["public_id"]

    assert payload["outcome"] == "ROUTED"
    assert payload["reason_codes"] == [
        "golden_and_bad_case_subsets_routed"
    ]
    assert payload["candidate_count"] == 4
    assert payload["golden_selected_count"] == 2
    assert payload["bad_case_selected_count"] == 2
    assert payload["golden_cluster_count"] == 2
    assert payload["bad_case_cluster_count"] == 2
    assert payload["golden_curation_batch_public_id"] is not None
    assert payload["bad_case_curation_batch_public_id"] is not None
    assert {value["lane"] for value in payload["items"]} == {
        "GOLDEN",
        "BAD_CASE",
    }

    batches = list(
        (
            await async_session.scalars(
                select(EvaluationDatasetCurationBatch).where(
                    EvaluationDatasetCurationBatch.public_id.in_(
                        (
                            payload["golden_curation_batch_public_id"],
                            payload["bad_case_curation_batch_public_id"],
                        )
                    )
                )
            )
        ).all()
    )
    assert len(batches) == 2
    assert all(batch.review is None for batch in batches)
    assert all(batch.materialization is None for batch in batches)

    event = await async_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_public_id == payload["public_id"]
        )
    )
    audit_row = await async_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "evaluation_case_routing_run.created"
        )
    )
    assert event is not None
    assert event.event_type == "EvaluationCaseRoutingRunCreated"
    assert audit_row is not None
    encoded = str([event.payload_json, audit_row.details]).lower()
    assert "score_value" not in encoded
    assert "comment" not in encoded
    assert "input" not in encoded
    assert "output" not in encoded


@pytest.mark.asyncio
async def test_case_routing_blocks_atomically_below_minimum(
    async_session,
) -> None:
    (
        user,
        namespace,
        golden_dataset,
        bad_case_dataset,
        _,
        promotion_version,
        promotion_runs,
    ) = await _seed(async_session, "blocked")
    policy = await create_evaluation_case_routing_policy(
        async_session,
        request=EvaluationCaseRoutingPolicyCreate(
            namespace_id=namespace.id,
            name="golden-bad-routing-blocked",
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
            golden_target_size=3,
            golden_min_items=3,
            bad_case_target_size=3,
            bad_case_min_items=3,
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
        idempotency_key="case-routing-blocked-run",
        actor=user,
    )

    assert run.outcome == EvaluationCaseRoutingOutcome.BLOCKED
    assert run.reason_codes_json == [
        "insufficient_golden_candidates",
        "insufficient_bad_case_candidates",
    ]
    assert run.golden_curation_batch is None
    assert run.bad_case_curation_batch is None
    assert run.golden_selected_count == 2
    assert run.bad_case_selected_count == 2


@pytest.mark.asyncio
async def test_case_routing_rejects_unclustered_promotion_version(
    async_session,
) -> None:
    (
        user,
        namespace,
        _,
        _,
        promotion_policy,
        promotion_version,
        _,
    ) = await _seed(async_session, "unclustered")
    none_version = await create_evaluation_promotion_policy_version(
        async_session,
        policy=promotion_policy,
        request=EvaluationPromotionPolicyVersionCreate(
            binding_public_id=promotion_version.binding.public_id,
            score_config_id="score-next012",
            score_data_type="NUMERIC",
            minimum_numeric_score=0.8,
            diversity_dimension="NONE",
            min_distinct_buckets=1,
        ),
        actor=user,
    )
    policy = await create_evaluation_case_routing_policy(
        async_session,
        request=EvaluationCaseRoutingPolicyCreate(
            namespace_id=namespace.id,
            name="routing-unclustered",
        ),
        actor=user,
    )
    with pytest.raises(EvaluationHubStateError, match="cluster dimension"):
        await create_evaluation_case_routing_policy_version(
            async_session,
            policy=policy,
            request=EvaluationCaseRoutingPolicyVersionCreate(
                source_promotion_policy_version_public_id=(
                    none_version.public_id
                ),
            ),
            actor=user,
        )
