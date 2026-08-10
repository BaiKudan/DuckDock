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
    EvaluationDatasetCurationBatch,
    EvaluationDatasetCurationItem,
    EvaluationPromotionOutcome,
)
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.outbox import OutboxEvent
from app.models.user import User
from app.schemas.evaluation import (
    EvaluationDatasetCreate,
    EvaluationPromotionPolicyCreate,
    EvaluationPromotionPolicyVersionCreate,
)
from app.services import evaluation_promotion_service
from app.services.evaluation_annotation_service import (
    create_annotation_dispatch,
    create_annotation_queue_binding,
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


NOW = datetime(2026, 8, 3, 6, 0, tzinfo=timezone.utc)
TRACES = ("3" * 32, "4" * 32)
OBSERVATIONS = ("c" * 16, "d" * 16)


class _QueueAdapter:
    descriptor = AnnotationQueueDescriptor(
        provider_queue_ref="queue-next011",
        name="duckdock-next011",
        description="quality promotion queue",
        score_config_ids=("score-next011",),
        created_at=NOW,
        updated_at=NOW,
    )

    async def get_queue(self, *, queue_ref: str):
        assert queue_ref == self.descriptor.provider_queue_ref
        return self.descriptor


class _PromotionAdapter:
    def __init__(
        self,
        *,
        passed: tuple[bool, bool] = (True, True),
        buckets: tuple[str | None, str | None] = ("1" * 64, "2" * 64),
    ) -> None:
        self.passed = passed
        self.buckets = buckets
        self.calls = 0

    async def evaluate_promotion_evidence(
        self,
        *,
        queue_ref,
        quality_rule,
        diversity_dimension,
        sources,
    ):
        assert queue_ref == "queue-next011"
        assert quality_rule.score_config_id == "score-next011"
        assert quality_rule.score_data_type == "NUMERIC"
        assert quality_rule.minimum_numeric_score == 0.8
        assert diversity_dimension == "OBSERVATION_NAME"
        self.calls += 1
        return [
            PromotionEvidenceItem(
                source_trace_ref=source.source_trace_ref,
                source_observation_ref=source.source_observation_ref,
                score_present=True,
                quality_passed=self.passed[index],
                diversity_bucket_present=self.buckets[index] is not None,
                score_evidence_digest=str(index + 7) * 64,
                diversity_bucket_digest=self.buckets[index],
            )
            for index, source in enumerate(sources)
        ]


async def _seed(db, suffix: str):
    user = User(
        username=f"promotion-{suffix}",
        email=f"promotion-{suffix}@example.com",
        hashed_password="unused",
    )
    db.add(user)
    await db.flush()
    namespace = Namespace(name=f"promotion-{suffix}", owner_id=user.id)
    db.add(namespace)
    await db.flush()
    db.add(
        NamespaceMember(
            namespace_id=namespace.id,
            user_id=user.id,
            role=NamespaceRole.ADMIN,
        )
    )
    dataset = await create_evaluation_dataset(
        db,
        request=EvaluationDatasetCreate(
            namespace_id=namespace.id,
            name=f"promotion-{suffix}",
            sync_provider=False,
            provider_dataset_ref=f"provider-{suffix}",
        ),
        actor=user,
    )
    batch = EvaluationDatasetCurationBatch(
        public_id=f"ecb_{suffix}",
        namespace_id=namespace.id,
        dataset_id=dataset.id,
        idempotency_key=f"curation-{suffix}",
        selection_digest=(suffix[0] if suffix else "e") * 64,
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
        for position, (trace_ref, observation_ref) in enumerate(
            zip(TRACES, OBSERVATIONS, strict=True), start=1
        )
    ]
    db.add(batch)
    await db.flush()
    queue_adapter = _QueueAdapter()
    binding = await create_annotation_queue_binding(
        db,
        namespace_id=namespace.id,
        provider_queue_ref=queue_adapter.descriptor.provider_queue_ref,
        actor=user,
        adapter=queue_adapter,
    )
    dispatch = await create_annotation_dispatch(
        db,
        binding_public_id=binding.public_id,
        curation_batch_public_id=batch.public_id,
        idempotency_key=f"promotion-dispatch-{suffix}",
        actor=user,
    )
    policy = await create_evaluation_promotion_policy(
        db,
        request=EvaluationPromotionPolicyCreate(
            namespace_id=namespace.id,
            name=f"quality-{suffix}",
        ),
        actor=user,
    )
    version = await create_evaluation_promotion_policy_version(
        db,
        policy=policy,
        request=EvaluationPromotionPolicyVersionCreate(
            binding_public_id=binding.public_id,
            score_config_id="score-next011",
            score_data_type="NUMERIC",
            minimum_numeric_score=0.8,
            diversity_dimension="OBSERVATION_NAME",
            min_distinct_buckets=2,
        ),
        actor=user,
    )
    return user, namespace, batch, dispatch, policy, version


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
async def test_promotion_requires_completed_dispatch(async_session) -> None:
    user, _, _, dispatch, _, version = await _seed(
        async_session, "incomplete"
    )
    with pytest.raises(
        EvaluationHubStateError,
        match="fully completed annotation dispatch",
    ):
        await run_evaluation_promotion_policy(
            async_session,
            version_public_id=version.public_id,
            dispatch_public_id=dispatch.public_id,
            idempotency_key="promotion-incomplete-run",
            actor=user,
            adapter=_PromotionAdapter(),
        )


@pytest.mark.asyncio
async def test_promotion_recommendation_is_idempotent_and_content_free(
    async_session,
) -> None:
    user, _, batch, dispatch, _, version = await _seed(
        async_session, "recommended"
    )
    _complete_dispatch(dispatch)
    adapter = _PromotionAdapter()
    run = await run_evaluation_promotion_policy(
        async_session,
        version_public_id=version.public_id,
        dispatch_public_id=dispatch.public_id,
        idempotency_key="promotion-recommended-run",
        actor=user,
        adapter=adapter,
    )
    replay = await run_evaluation_promotion_policy(
        async_session,
        version_public_id=version.public_id,
        dispatch_public_id=dispatch.public_id,
        idempotency_key="promotion-recommended-run",
        actor=user,
        adapter=adapter,
    )

    assert replay.id == run.id
    assert adapter.calls == 1
    assert run.outcome == EvaluationPromotionOutcome.RECOMMENDED
    assert run.reason_codes_json == ["promotion_criteria_met"]
    assert run.scored_count == 2
    assert run.passed_count == 2
    assert run.distinct_bucket_count == 2
    assert batch.review is None
    assert batch.materialization is None

    event = await async_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_public_id == run.public_id
        )
    )
    assert event is not None
    assert event.event_type == "EvaluationPromotionRunCreated"
    audit = await async_session.scalar(
        select(AuditLog).where(
            AuditLog.resource_type == "evaluation_promotion_run"
        )
    )
    assert audit is not None
    encoded = str([event.payload_json, audit.details]).lower()
    assert "input" not in encoded
    assert "output" not in encoded
    assert "comment" not in encoded
    assert "score_value" not in encoded


@pytest.mark.asyncio
async def test_promotion_http_blocks_failed_quality_and_keeps_human_review(
    async_session,
    monkeypatch,
) -> None:
    user, namespace, batch, dispatch, policy, version = await _seed(
        async_session, "blocked"
    )
    _complete_dispatch(dispatch)
    adapter = _PromotionAdapter(
        passed=(True, False), buckets=("1" * 64, "1" * 64)
    )
    monkeypatch.setattr(
        evaluation_promotion_service,
        "_selected_adapter",
        lambda _adapter: adapter,
    )

    async with _client(async_session, user) as client:
        policy_response = await client.get(
            f"/api/v2/evaluation-promotion-policies/{policy.public_id}"
        )
        assert policy_response.status_code == 200
        assert policy_response.json()["versions"][0][
            "binding_public_id"
        ] == version.binding.public_id

        run_response = await client.post(
            f"/api/v2/evaluation-promotion-policy-versions/{version.public_id}/runs",
            headers={"Idempotency-Key": "promotion-blocked-http"},
            json={"dispatch_public_id": dispatch.public_id},
        )
        assert run_response.status_code == 201
        payload = run_response.json()
        assert payload["outcome"] == "BLOCKED"
        assert payload["reason_codes"] == [
            "quality_threshold_not_met",
            "insufficient_diversity",
        ]
        assert payload["passed_count"] == 1
        assert payload["distinct_bucket_count"] == 1

        list_response = await client.get(
            "/api/v2/evaluation-promotion-runs",
            params={"namespace_id": namespace.id},
        )
        assert list_response.status_code == 200
        assert list_response.json()[0]["public_id"] == payload["public_id"]

    assert batch.review is None
    assert batch.materialization is None
