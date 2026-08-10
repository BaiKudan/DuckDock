from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

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
    EvaluationAnnotationProviderStatus,
    EvaluationDatasetCurationBatch,
    EvaluationDatasetCurationItem,
)
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.outbox import OutboxEvent
from app.models.user import User
from app.adapters.evaluation.langfuse import LangfuseAnnotationQueueAdapter
from app.schemas.evaluation import EvaluationDatasetCreate
from app.services import evaluation_annotation_service
from app.services.evaluation_annotation_service import (
    acknowledge_annotation_dispatch,
    create_annotation_dispatch,
    create_annotation_queue_binding,
    lease_annotation_dispatch,
    load_annotation_dispatch_request,
    reconcile_annotation_dispatch,
)
from app.services.evaluation_ports import (
    AnnotationQueueDescriptor,
    AnnotationQueueItemSnapshot,
    PromotionEvidenceSource,
    PromotionQualityRule,
)
from app.services.evaluation_service import create_evaluation_dataset


NOW = datetime(2026, 8, 3, 3, 0, tzinfo=timezone.utc)
TRACES = ("1" * 32, "2" * 32)
OBSERVATIONS = ("a" * 16, "b" * 16)


class _FakeAnnotationAdapter:
    def __init__(self) -> None:
        self.ensure_calls = 0
        self.completed = False
        self.descriptor = AnnotationQueueDescriptor(
            provider_queue_ref="queue-next010",
            name="duckdock-next010",
            description="content-free test queue",
            score_config_ids=("score-quality",),
            created_at=NOW,
            updated_at=NOW,
        )

    async def list_queues(self, *, limit: int):
        assert limit <= 100
        return [self.descriptor]

    async def get_queue(self, *, queue_ref: str):
        assert queue_ref == self.descriptor.provider_queue_ref
        return self.descriptor

    def _snapshots(self, observation_refs):
        status = "COMPLETED" if self.completed else "PENDING"
        return [
            AnnotationQueueItemSnapshot(
                provider_queue_item_ref=f"queue-item-{observation_ref}",
                source_observation_ref=observation_ref,
                status=status,
                created_at=NOW,
                updated_at=NOW + timedelta(minutes=int(self.completed)),
                completed_at=(NOW + timedelta(minutes=1) if self.completed else None),
            )
            for observation_ref in observation_refs
        ]

    async def ensure_observations(self, *, queue_ref: str, observation_refs):
        assert queue_ref == self.descriptor.provider_queue_ref
        self.ensure_calls += 1
        return self._snapshots(observation_refs)

    async def get_observation_items(self, *, queue_ref: str, observation_refs):
        assert queue_ref == self.descriptor.provider_queue_ref
        return self._snapshots(observation_refs)


class _FakeProviderAnnotationQueues:
    def __init__(self) -> None:
        self.created: list[object] = []
        self.queue = SimpleNamespace(
            id="queue-next010",
            name="duckdock-next010",
            description="content-free test queue",
            score_config_ids=["score-quality"],
            created_at=NOW,
            updated_at=NOW,
        )

    def get_queue(self, queue_ref: str):
        assert queue_ref == self.queue.id
        return self.queue

    def list_queue_items(self, queue_ref: str, *, page: int, limit: int):
        assert queue_ref == self.queue.id
        assert page == 1
        assert limit == 100
        return SimpleNamespace(
            data=list(self.created),
            meta=SimpleNamespace(total_pages=1),
        )

    def create_queue_item(self, queue_ref: str, *, object_id, object_type):
        assert queue_ref == self.queue.id
        assert object_type.value == "OBSERVATION"
        value = SimpleNamespace(
            id=f"queue-item-{len(self.created) + 1}",
            queue_id=queue_ref,
            object_id=object_id,
            object_type=object_type,
            status=SimpleNamespace(value="PENDING"),
            completed_at=None,
            created_at=NOW,
            updated_at=NOW,
        )
        self.created.append(value)
        return value


class _FakeProviderPromotionScores:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def get_many_v3(self, **kwargs):
        self.calls.append(kwargs)
        subject = SimpleNamespace(
            kind="observation",
            id=OBSERVATIONS[0],
            trace_id=TRACES[0],
        )
        return SimpleNamespace(
            data=[
                SimpleNamespace(
                    id="score-newest",
                    subject=subject,
                    source="ANNOTATION",
                    config_id="score-quality",
                    queue_id="queue-next010",
                    data_type="NUMERIC",
                    value=0.9,
                    updated_at=NOW + timedelta(minutes=2),
                ),
                SimpleNamespace(
                    id="score-older",
                    subject=subject,
                    source="ANNOTATION",
                    config_id="score-quality",
                    queue_id="queue-next010",
                    data_type="NUMERIC",
                    value=0.4,
                    updated_at=NOW + timedelta(minutes=1),
                ),
            ],
            meta=SimpleNamespace(cursor=None),
        )


class _FakeProviderPromotionObservations:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def get_many(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            data=[
                SimpleNamespace(
                    id=OBSERVATIONS[0],
                    name="agent.loop",
                    type=SimpleNamespace(value="GENERATION"),
                    environment="development",
                )
            ],
            meta=SimpleNamespace(cursor=None),
        )


@pytest.mark.asyncio
async def test_langfuse_annotation_adapter_reconciles_before_create(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.adapters.evaluation.langfuse.require_langfuse_sdk_compatibility",
        lambda: "4.14.2",
    )
    provider = _FakeProviderAnnotationQueues()
    adapter = LangfuseAnnotationQueueAdapter(
        client=SimpleNamespace(
            api=SimpleNamespace(annotation_queues=provider),
        )
    )

    first = await adapter.ensure_observations(
        queue_ref=provider.queue.id,
        observation_refs=OBSERVATIONS,
    )
    second = await adapter.ensure_observations(
        queue_ref=provider.queue.id,
        observation_refs=OBSERVATIONS,
    )

    assert [value.provider_queue_item_ref for value in first] == [
        value.provider_queue_item_ref for value in second
    ]
    assert len(provider.created) == 2


@pytest.mark.asyncio
async def test_langfuse_promotion_adapter_uses_scores_v3_and_metadata_only(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.adapters.evaluation.langfuse.require_langfuse_sdk_compatibility",
        lambda: "4.14.2",
    )
    queues = _FakeProviderAnnotationQueues()
    scores = _FakeProviderPromotionScores()
    observations = _FakeProviderPromotionObservations()
    adapter = LangfuseAnnotationQueueAdapter(
        client=SimpleNamespace(
            api=SimpleNamespace(
                annotation_queues=queues,
                scores_v3=scores,
                observations=observations,
            ),
        )
    )

    evidence = await adapter.evaluate_promotion_evidence(
        queue_ref=queues.queue.id,
        quality_rule=PromotionQualityRule(
            score_config_id="score-quality",
            score_data_type="NUMERIC",
            minimum_numeric_score=0.8,
            accepted_values=(),
        ),
        diversity_dimension="OBSERVATION_NAME",
        sources=(
            PromotionEvidenceSource(
                source_trace_ref=TRACES[0],
                source_observation_ref=OBSERVATIONS[0],
            ),
        ),
    )

    assert len(evidence) == 1
    assert evidence[0].score_present is True
    assert evidence[0].quality_passed is True
    assert evidence[0].diversity_bucket_present is True
    assert len(evidence[0].score_evidence_digest or "") == 64
    assert len(evidence[0].diversity_bucket_digest or "") == 64
    assert scores.calls == [
        {
            "fields": "details,subject,annotation",
            "source": "ANNOTATION",
            "data_type": "NUMERIC",
            "config_id": "score-quality",
            "queue_id": "queue-next010",
            "trace_id": TRACES[0],
            "observation_id": OBSERVATIONS[0],
            "limit": 100,
            "cursor": None,
        }
    ]
    assert observations.calls == [
        {
            "fields": "core,basic",
            "trace_id": TRACES[0],
            "limit": 1000,
            "cursor": None,
        }
    ]
    assert adapter._quality_passed(
        True,
        PromotionQualityRule(
            score_config_id="score-boolean",
            score_data_type="BOOLEAN",
            minimum_numeric_score=None,
            accepted_values=(True,),
        ),
    )
    assert adapter._quality_passed(
        "golden",
        PromotionQualityRule(
            score_config_id="score-categorical",
            score_data_type="CATEGORICAL",
            minimum_numeric_score=None,
            accepted_values=("golden", "acceptable"),
        ),
    )


async def _seed(db, suffix: str):
    user = User(
        username=f"annotation-{suffix}",
        email=f"annotation-{suffix}@example.com",
        hashed_password="unused",
    )
    db.add(user)
    await db.flush()
    namespace = Namespace(name=f"annotation-{suffix}", owner_id=user.id)
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
            name=f"annotation-{suffix}",
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
        selection_digest="c" * 64,
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
            zip(TRACES, OBSERVATIONS, strict=True),
            start=1,
        )
    ]
    db.add(batch)
    await db.flush()
    return user, namespace, batch


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
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_annotation_dispatch_is_decoupled_idempotent_and_content_free(
    async_session,
) -> None:
    user, namespace, batch = await _seed(async_session, "service")
    adapter = _FakeAnnotationAdapter()
    binding = await create_annotation_queue_binding(
        async_session,
        namespace_id=namespace.id,
        provider_queue_ref=adapter.descriptor.provider_queue_ref,
        actor=user,
        adapter=adapter,
    )
    dispatch = await create_annotation_dispatch(
        async_session,
        binding_public_id=binding.public_id,
        curation_batch_public_id=batch.public_id,
        idempotency_key="annotation-dispatch-service",
        actor=user,
    )
    replay = await create_annotation_dispatch(
        async_session,
        binding_public_id=binding.public_id,
        curation_batch_public_id=batch.public_id,
        idempotency_key="annotation-dispatch-service",
        actor=user,
    )

    assert replay.id == dispatch.id
    assert adapter.ensure_calls == 0
    assert dispatch.status == EvaluationAnnotationDispatchStatus.PENDING
    assert dispatch.item_count == 2
    assert dispatch.sampling_run is None
    worker_now = dispatch.available_at + timedelta(minutes=1)

    lease = await lease_annotation_dispatch(
        async_session,
        dispatch_public_id=dispatch.public_id,
        worker_id="annotation-worker:test",
        lease_seconds=300,
        max_attempts=5,
        now=worker_now,
    )
    assert lease is not None
    request = await load_annotation_dispatch_request(
        async_session,
        dispatch_public_id=dispatch.public_id,
        worker_id="annotation-worker:test",
    )
    assert request.expected_score_config_ids == ("score-quality",)
    snapshots = await adapter.ensure_observations(
        queue_ref=request.provider_queue_ref,
        observation_refs=request.observation_refs,
    )
    dispatch = await acknowledge_annotation_dispatch(
        async_session,
        dispatch_public_id=dispatch.public_id,
        worker_id="annotation-worker:test",
        snapshots=snapshots,
        now=worker_now,
    )
    assert dispatch is not None
    assert dispatch.status == EvaluationAnnotationDispatchStatus.SYNCED
    assert dispatch.synced_count == 2
    assert dispatch.completed_count == 0
    assert all(
        item.provider_annotation_status
        == EvaluationAnnotationProviderStatus.PENDING
        for item in dispatch.items
    )

    adapter.completed = True
    reconciled = await reconcile_annotation_dispatch(
        async_session,
        public_id=dispatch.public_id,
        actor=user,
        adapter=adapter,
    )
    assert reconciled.completed_count == 2
    assert batch.review is None

    events = list(
        (
            await async_session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_public_id == dispatch.public_id
                )
            )
        ).all()
    )
    assert [event.event_type for event in events] == [
        "EvaluationAnnotationDispatchRequested",
        "EvaluationAnnotationDispatchSynchronized",
    ]
    audits = list(
        (
            await async_session.scalars(
                select(AuditLog).where(
                    AuditLog.resource_type
                    == "evaluation_annotation_dispatch"
                )
            )
        ).all()
    )
    encoded = str(
        [event.payload_json for event in events]
        + [audit.details for audit in audits]
    ).lower()
    assert "input" not in encoded
    assert "output" not in encoded
    assert "content-free test queue" not in encoded


@pytest.mark.asyncio
async def test_annotation_queue_http_binding_and_dispatch(
    async_session,
    monkeypatch,
) -> None:
    user, namespace, batch = await _seed(async_session, "http")
    adapter = _FakeAnnotationAdapter()
    monkeypatch.setattr(
        evaluation_annotation_service,
        "_selected_adapter",
        lambda _adapter: adapter,
    )

    async with _client(async_session, user) as client:
        provider_response = await client.get(
            "/api/v2/evaluation-annotation-queue-bindings/provider-queues",
            params={"namespace_id": namespace.id},
        )
        assert provider_response.status_code == 200
        assert provider_response.json()[0]["already_bound"] is False

        binding_response = await client.post(
            "/api/v2/evaluation-annotation-queue-bindings",
            json={
                "namespace_id": namespace.id,
                "provider_queue_ref": adapter.descriptor.provider_queue_ref,
            },
        )
        assert binding_response.status_code == 201
        binding = binding_response.json()
        assert binding["score_config_ids"] == ["score-quality"]

        dispatch_response = await client.post(
            f"/api/v2/evaluation-annotation-queue-bindings/{binding['public_id']}/dispatches",
            headers={"Idempotency-Key": "annotation-http-dispatch"},
            json={"curation_batch_public_id": batch.public_id},
        )
        assert dispatch_response.status_code == 201
        payload = dispatch_response.json()
        assert payload["status"] == "PENDING"
        assert payload["item_count"] == 2
        assert payload["provider_queue_name"] == "duckdock-next010"

        list_response = await client.get(
            "/api/v2/evaluation-annotation-dispatches",
            params={"namespace_id": namespace.id},
        )
        assert list_response.status_code == 200
        assert [value["public_id"] for value in list_response.json()] == [
            payload["public_id"]
        ]
