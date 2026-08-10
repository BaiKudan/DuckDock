from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from app.adapters.evaluation.langfuse import (
    LangfuseTraceCandidateReader,
    LangfuseTraceDatasetMaterializer,
)
from app.api.v2.router import api_router
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.evaluation import (
    EvaluationDatasetCurationDecision,
    EvaluationDatasetCurationMaterializedItem,
    EvaluationDatasetVersion,
)
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.outbox import OutboxEvent
from app.models.user import User
from app.schemas.evaluation import (
    EvaluationDatasetCreate,
    EvaluationDatasetCurationBatchCreate,
    EvaluationDatasetCurationItemCreate,
    EvaluationDatasetCurationReviewCreate,
)
from app.services import evaluation_curation_service
from app.services.evaluation_curation_service import (
    create_evaluation_dataset_curation_batch,
    list_trace_dataset_candidates,
    materialize_evaluation_dataset_curation_batch,
    review_evaluation_dataset_curation_batch,
)
from app.services.evaluation_ports import (
    TraceDatasetBatchItemReceipt,
    TraceDatasetBatchMaterializationReceipt,
    TraceDatasetCandidate,
)
from app.services.evaluation_service import (
    EvaluationHubStateError,
    create_evaluation_dataset,
)


TRACE_1 = "1" * 32
TRACE_2 = "2" * 32
OBSERVATION_1 = "a" * 16
OBSERVATION_2 = "b" * 16
NOW = datetime(2026, 7, 31, 8, 0, tzinfo=timezone.utc)


async def _seed_namespace(db, suffix: str):
    user = User(
        username=f"curation-{suffix}",
        email=f"curation-{suffix}@example.test",
        hashed_password="unused",
    )
    db.add(user)
    await db.flush()
    namespace = Namespace(name=f"curation-{suffix}", owner_id=user.id)
    db.add(namespace)
    await db.flush()
    db.add(
        NamespaceMember(
            namespace_id=namespace.id,
            user_id=user.id,
            role=NamespaceRole.ADMIN,
        )
    )
    await db.flush()
    return user, namespace


async def _seed_dataset(db, user, namespace, suffix: str):
    return await create_evaluation_dataset(
        db,
        request=EvaluationDatasetCreate(
            namespace_id=namespace.id,
            name=f"curation-{suffix}",
            sync_provider=False,
            provider_dataset_ref=f"provider-{suffix}",
        ),
        actor=user,
    )


def _batch_request() -> EvaluationDatasetCurationBatchCreate:
    return EvaluationDatasetCurationBatchCreate(
        items=[
            EvaluationDatasetCurationItemCreate(
                trace_id=TRACE_2,
                observation_id=OBSERVATION_2,
            ),
            EvaluationDatasetCurationItemCreate(
                trace_id=TRACE_1,
                observation_id=OBSERVATION_1,
            ),
        ]
    )


class _FakeCandidateReader:
    def __init__(self) -> None:
        self.calls = 0

    async def list_candidates(self, **_kwargs):
        self.calls += 1
        return [
            TraceDatasetCandidate(
                source_trace_ref=TRACE_1,
                source_observation_ref=OBSERVATION_1,
                name="safe-operation-name",
                observation_type="SPAN",
                start_time=NOW,
                end_time=NOW + timedelta(seconds=1),
                environment="test",
            )
        ]


class _FakeBatchMaterializer:
    def __init__(self, *, provider_dataset_ref: str) -> None:
        self.provider_dataset_ref = provider_dataset_ref
        self.calls = 0

    async def materialize_batch(self, **kwargs):
        self.calls += 1
        assert [source.source_trace_ref for source in kwargs["sources"]] == [
            TRACE_1,
            TRACE_2,
        ]
        return TraceDatasetBatchMaterializationReceipt(
            provider_dataset_ref=self.provider_dataset_ref,
            provider_version_ref="2026-07-31T08:00:00.001000Z",
            manifest_digest="d" * 64,
            item_count=7,
            items=(
                TraceDatasetBatchItemReceipt(
                    source_trace_ref=TRACE_1,
                    source_observation_ref=OBSERVATION_1,
                    provider_dataset_item_ref=(
                        "11111111-1111-4111-8111-111111111111"
                    ),
                ),
                TraceDatasetBatchItemReceipt(
                    source_trace_ref=TRACE_2,
                    source_observation_ref=OBSERVATION_2,
                    provider_dataset_item_ref=(
                        "22222222-2222-4222-8222-222222222222"
                    ),
                ),
            ),
        )


class _FakeLangfuseClient:
    def __init__(self) -> None:
        self.observation_calls: list[dict] = []
        self.created: list[dict] = []
        self.observations = {
            TRACE_1: SimpleNamespace(
                id=OBSERVATION_1,
                trace_id=TRACE_1,
                parent_observation_id=None,
                is_root_observation=True,
                name="candidate-one",
                type="SPAN",
                environment="test",
                start_time=NOW,
                end_time=NOW + timedelta(seconds=1),
                input='{"secret_question":"one"}',
                output='{"secret_answer":"one"}',
            ),
            TRACE_2: SimpleNamespace(
                id=OBSERVATION_2,
                trace_id=TRACE_2,
                parent_observation_id=None,
                is_root_observation=True,
                name="candidate-two",
                type="GENERATION",
                environment="test",
                start_time=NOW,
                end_time=NOW + timedelta(seconds=2),
                input='{"secret_question":"two"}',
                output='{"secret_answer":"two"}',
            ),
        }
        self.items: list[SimpleNamespace] = []
        self.api = SimpleNamespace(
            datasets=SimpleNamespace(
                get=lambda _name: SimpleNamespace(id="provider-adapter")
            ),
            observations=SimpleNamespace(get_many=self._get_observations),
            dataset_items=SimpleNamespace(list=self._list_items),
        )

    def _get_observations(self, **kwargs):
        self.observation_calls.append(kwargs)
        trace_id = kwargs.get("trace_id")
        data = (
            [self.observations[trace_id]]
            if trace_id is not None
            else list(self.observations.values())
        )
        return SimpleNamespace(data=data, meta=SimpleNamespace(cursor=None))

    def _list_items(self, **_kwargs):
        return SimpleNamespace(
            data=self.items,
            meta=SimpleNamespace(total_pages=1),
        )

    def create_dataset_item(self, **kwargs):
        self.created.append(kwargs)
        item = SimpleNamespace(
            id=kwargs["id"],
            updated_at=NOW,
            status=SimpleNamespace(value="ACTIVE"),
            source_trace_id=kwargs["source_trace_id"],
            source_observation_id=kwargs["source_observation_id"],
        )
        self.items = [
            value for value in self.items if value.id != kwargs["id"]
        ] + [item]
        return item


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
async def test_candidate_reader_never_requests_observation_io() -> None:
    client = _FakeLangfuseClient()
    reader = LangfuseTraceCandidateReader(client=client)

    values = await reader.list_candidates(
        from_start_time=NOW - timedelta(hours=1),
        to_start_time=NOW + timedelta(hours=1),
        name=None,
        observation_type=None,
        environment=None,
        root_only=True,
        limit=20,
    )

    assert len(values) == 2
    assert client.observation_calls[0]["fields"] == "core,basic,time"
    assert "io" not in client.observation_calls[0]["fields"]
    assert not hasattr(values[0], "input")
    assert not hasattr(values[0], "output")


@pytest.mark.asyncio
async def test_batch_adapter_validates_all_io_then_pins_one_snapshot() -> None:
    client = _FakeLangfuseClient()
    materializer = LangfuseTraceDatasetMaterializer(client=client)

    receipt = await materializer.materialize_batch(
        dataset_public_id="eds_adapter",
        provider_dataset_name="duckdock.ns1.adapter",
        provider_dataset_ref="provider-adapter",
        sources=[
            SimpleNamespace(
                source_trace_ref=TRACE_1,
                source_observation_ref=OBSERVATION_1,
            ),
            SimpleNamespace(
                source_trace_ref=TRACE_2,
                source_observation_ref=OBSERVATION_2,
            ),
        ],
    )

    assert len(client.created) == 2
    assert len(receipt.items) == 2
    assert receipt.item_count == 2
    assert len(receipt.manifest_digest) == 64
    assert not hasattr(receipt, "input")


@pytest.mark.asyncio
async def test_approved_batch_materializes_as_one_immutable_version(
    async_session,
) -> None:
    user, namespace = await _seed_namespace(async_session, "service")
    dataset = await _seed_dataset(
        async_session,
        user,
        namespace,
        "service",
    )
    candidate_reader = _FakeCandidateReader()
    _, candidates = await list_trace_dataset_candidates(
        async_session,
        dataset_public_id=dataset.public_id,
        from_start_time=NOW - timedelta(hours=1),
        to_start_time=NOW + timedelta(hours=1),
        name=None,
        observation_type=None,
        environment=None,
        root_only=True,
        limit=20,
        reader=candidate_reader,
    )
    assert not candidates[0].already_materialized
    assert not candidates[0].already_governed

    batch = await create_evaluation_dataset_curation_batch(
        async_session,
        dataset_public_id=dataset.public_id,
        request=_batch_request(),
        idempotency_key="curation-submit-service",
        actor=user,
    )
    replay = await create_evaluation_dataset_curation_batch(
        async_session,
        dataset_public_id=dataset.public_id,
        request=_batch_request(),
        idempotency_key="curation-submit-service",
        actor=user,
    )
    assert replay.id == batch.id
    assert [item.source_trace_ref for item in batch.items] == [
        TRACE_1,
        TRACE_2,
    ]

    with pytest.raises(
        EvaluationHubStateError,
        match="approved",
    ):
        await materialize_evaluation_dataset_curation_batch(
            async_session,
            batch_public_id=batch.public_id,
            idempotency_key="curation-materialize-early",
            actor=user,
            materializer=_FakeBatchMaterializer(
                provider_dataset_ref=dataset.provider_dataset_ref or ""
            ),
        )

    batch = await review_evaluation_dataset_curation_batch(
        async_session,
        batch_public_id=batch.public_id,
        request=EvaluationDatasetCurationReviewCreate(
            decision=EvaluationDatasetCurationDecision.APPROVED,
            comment="verified metadata selection",
        ),
        idempotency_key="curation-review-service",
        actor=user,
    )
    materializer = _FakeBatchMaterializer(
        provider_dataset_ref=dataset.provider_dataset_ref or ""
    )
    batch = await materialize_evaluation_dataset_curation_batch(
        async_session,
        batch_public_id=batch.public_id,
        idempotency_key="curation-materialize-service",
        actor=user,
        materializer=materializer,
    )
    replay = await materialize_evaluation_dataset_curation_batch(
        async_session,
        batch_public_id=batch.public_id,
        idempotency_key="curation-materialize-service",
        actor=user,
        materializer=materializer,
    )

    assert replay.materialization is not None
    assert materializer.calls == 1
    assert batch.materialization is not None
    assert batch.materialization.item_count == 2
    assert batch.materialization.dataset_version.version == 1
    assert batch.materialization.dataset_version.item_count == 7
    assert (
        await async_session.scalar(
            select(func.count(EvaluationDatasetVersion.id)).where(
                EvaluationDatasetVersion.dataset_id == dataset.id
            )
        )
        == 1
    )
    assert (
        await async_session.scalar(
            select(
                func.count(EvaluationDatasetCurationMaterializedItem.id)
            )
        )
        == 2
    )
    events = list(
        (
            await async_session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_public_id.in_(
                        [
                            batch.public_id,
                            batch.review.public_id,
                            batch.materialization.public_id,
                        ]
                    )
                )
            )
        ).all()
    )
    assert {event.event_type for event in events} == {
        "EvaluationDatasetCurationSubmitted",
        "EvaluationDatasetCurationReviewed",
        "EvaluationDatasetCurationMaterialized",
    }
    encoded = str([event.payload_json for event in events]).lower()
    assert "secret_question" not in encoded
    assert "secret_answer" not in encoded


@pytest.mark.asyncio
async def test_rejected_batch_can_never_materialize(async_session) -> None:
    user, namespace = await _seed_namespace(async_session, "rejected")
    dataset = await _seed_dataset(
        async_session,
        user,
        namespace,
        "rejected",
    )
    batch = await create_evaluation_dataset_curation_batch(
        async_session,
        dataset_public_id=dataset.public_id,
        request=_batch_request(),
        idempotency_key="curation-submit-rejected",
        actor=user,
    )
    await review_evaluation_dataset_curation_batch(
        async_session,
        batch_public_id=batch.public_id,
        request=EvaluationDatasetCurationReviewCreate(
            decision=EvaluationDatasetCurationDecision.REJECTED,
            comment="source quality is insufficient",
        ),
        idempotency_key="curation-review-rejected",
        actor=user,
    )

    with pytest.raises(EvaluationHubStateError, match="approved"):
        await materialize_evaluation_dataset_curation_batch(
            async_session,
            batch_public_id=batch.public_id,
            idempotency_key="curation-materialize-rejected",
            actor=user,
            materializer=_FakeBatchMaterializer(
                provider_dataset_ref=dataset.provider_dataset_ref or ""
            ),
        )


@pytest.mark.asyncio
async def test_curation_http_candidate_submit_approve_and_materialize(
    async_session,
    monkeypatch,
) -> None:
    user, namespace = await _seed_namespace(async_session, "http")
    dataset = await _seed_dataset(async_session, user, namespace, "http")
    reader = _FakeCandidateReader()
    materializer = _FakeBatchMaterializer(
        provider_dataset_ref=dataset.provider_dataset_ref or ""
    )
    monkeypatch.setattr(
        evaluation_curation_service.langfuse_service,
        "client",
        lambda: object(),
    )
    monkeypatch.setattr(
        evaluation_curation_service,
        "LangfuseTraceCandidateReader",
        lambda **_kwargs: reader,
    )
    monkeypatch.setattr(
        evaluation_curation_service,
        "LangfuseTraceDatasetMaterializer",
        lambda **_kwargs: materializer,
    )

    async with _client(async_session, user) as client:
        candidates = await client.get(
            "/api/v2/evaluation-trace-candidates",
            params={
                "dataset_public_id": dataset.public_id,
                "from_start_time": (
                    NOW - timedelta(hours=1)
                ).isoformat(),
                "to_start_time": (NOW + timedelta(hours=1)).isoformat(),
            },
        )
        assert candidates.status_code == 200, candidates.text
        candidate = candidates.json()[0]
        assert candidate["source_trace_ref"] == TRACE_1
        assert candidate["already_governed"] is False
        assert "input" not in candidate
        assert "output" not in candidate

        submitted = await client.post(
            (
                f"/api/v2/evaluation-datasets/{dataset.public_id}"
                "/curation-batches"
            ),
            json=_batch_request().model_dump(),
            headers={"Idempotency-Key": "curation-submit-http"},
        )
        assert submitted.status_code == 201, submitted.text
        batch = submitted.json()
        assert batch["status"] == "PENDING_REVIEW"
        assert batch["item_count"] == 2

        reviewed = await client.post(
            (
                "/api/v2/evaluation-dataset-curation-batches/"
                f"{batch['public_id']}/reviews"
            ),
            json={"decision": "APPROVED", "comment": "looks good"},
            headers={"Idempotency-Key": "curation-review-http"},
        )
        assert reviewed.status_code == 201, reviewed.text
        assert reviewed.json()["status"] == "APPROVED"

        materialized = await client.post(
            (
                "/api/v2/evaluation-dataset-curation-batches/"
                f"{batch['public_id']}/materializations"
            ),
            headers={"Idempotency-Key": "curation-materialize-http"},
        )
        assert materialized.status_code == 201, materialized.text
        payload = materialized.json()
        assert payload["status"] == "MATERIALIZED"
        assert payload["materialization"]["selected_item_count"] == 2
        assert payload["materialization"]["dataset_item_count"] == 7
        assert all(
            item["provider_dataset_item_ref"] is not None
            for item in payload["items"]
        )

        listed = await client.get(
            "/api/v2/evaluation-dataset-curation-batches",
            params={"namespace_id": namespace.id},
        )
        assert listed.status_code == 200, listed.text
        assert listed.json()[0]["public_id"] == batch["public_id"]
        assert listed.json()[0]["status"] == "MATERIALIZED"
