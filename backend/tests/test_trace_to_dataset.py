from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from app.adapters.evaluation.langfuse import (
    LangfuseTraceDatasetMaterializer,
    LangfuseTraceDatasetMaterializerError,
)
from app.api.v2.router import api_router
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.evaluation import (
    EvaluationDatasetMaterialization,
    EvaluationDatasetVersion,
)
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.outbox import OutboxEvent
from app.models.user import User
from app.schemas.evaluation import (
    EvaluationDatasetCreate,
    TraceDatasetMaterializationCreate,
)
from app.services import evaluation_service
from app.services.evaluation_ports import TraceDatasetMaterializationReceipt
from app.services.evaluation_service import (
    EvaluationHubConflictError,
    create_evaluation_dataset,
    materialize_evaluation_dataset_trace,
)


TRACE_REF = "a" * 32
OBSERVATION_REF = "b" * 16


async def _seed_namespace(db, suffix: str):
    user = User(
        username=f"trace-dataset-{suffix}",
        email=f"trace-dataset-{suffix}@example.test",
        hashed_password="unused",
    )
    db.add(user)
    await db.flush()
    namespace = Namespace(name=f"trace-dataset-{suffix}", owner_id=user.id)
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


class _FakeMaterializer:
    def __init__(self) -> None:
        self.calls = 0

    async def materialize(self, **kwargs):
        self.calls += 1
        assert kwargs["trace_ref"] == TRACE_REF
        return TraceDatasetMaterializationReceipt(
            provider_dataset_ref=kwargs["provider_dataset_ref"],
            provider_dataset_item_ref=(
                "12345678-1234-5678-9234-567812345678"
            ),
            source_trace_ref=TRACE_REF,
            source_observation_ref=OBSERVATION_REF,
            provider_version_ref="2026-07-31T07:00:00.001000Z",
            manifest_digest="c" * 64,
            item_count=3,
        )


class _FakeLangfuseClient:
    def __init__(self, *, include_output: bool = True) -> None:
        timestamp = datetime(2026, 7, 31, 7, 0, tzinfo=timezone.utc)
        self.created: dict | None = None
        self.observation = SimpleNamespace(
            id=OBSERVATION_REF,
            trace_id=TRACE_REF,
            parent_observation_id=None,
            is_root_observation=True,
            input='{"question":"why"}',
            output='{"answer":"because"}' if include_output else None,
        )
        self.new_item = SimpleNamespace(
            id="unused",
            updated_at=timestamp,
            status=SimpleNamespace(value="ACTIVE"),
            source_trace_id=TRACE_REF,
            source_observation_id=OBSERVATION_REF,
        )
        self.old_item = SimpleNamespace(
            id="00000000-0000-4000-8000-000000000001",
            updated_at=timestamp,
            status=SimpleNamespace(value="ACTIVE"),
            source_trace_id=None,
            source_observation_id=None,
        )
        self.api = SimpleNamespace(
            datasets=SimpleNamespace(
                get=lambda _name: SimpleNamespace(id="provider-dataset-1")
            ),
            observations=SimpleNamespace(get_many=self._get_observations),
            dataset_items=SimpleNamespace(list=self._list_items),
        )

    def _get_observations(self, **_kwargs):
        return SimpleNamespace(
            data=[self.observation],
            meta=SimpleNamespace(cursor=None),
        )

    def _list_items(self, **_kwargs):
        return SimpleNamespace(
            data=[self.old_item, self.new_item],
            meta=SimpleNamespace(total_pages=1),
        )

    def create_dataset_item(self, **kwargs):
        self.created = kwargs
        self.new_item.id = kwargs["id"]
        return self.new_item


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
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_langfuse_materializer_copies_io_and_returns_metadata_only() -> None:
    client = _FakeLangfuseClient()
    materializer = LangfuseTraceDatasetMaterializer(client=client)

    receipt = await materializer.materialize(
        dataset_public_id="eds_test",
        provider_dataset_name="duckdock.ns1.golden",
        provider_dataset_ref="provider-dataset-1",
        trace_ref=TRACE_REF,
        observation_ref=None,
    )

    assert client.created is not None
    assert client.created["input"] == {"question": "why"}
    assert client.created["expected_output"] == {"answer": "because"}
    assert client.created["source_trace_id"] == TRACE_REF
    assert client.created["source_observation_id"] == OBSERVATION_REF
    assert receipt.source_observation_ref == OBSERVATION_REF
    assert receipt.item_count == 2
    assert len(receipt.manifest_digest) == 64
    assert not hasattr(receipt, "input")
    assert not hasattr(receipt, "expected_output")


@pytest.mark.asyncio
async def test_langfuse_materializer_rejects_missing_observation_output() -> None:
    materializer = LangfuseTraceDatasetMaterializer(
        client=_FakeLangfuseClient(include_output=False)
    )

    with pytest.raises(
        LangfuseTraceDatasetMaterializerError,
        match="has no output",
    ):
        await materializer.materialize(
            dataset_public_id="eds_test",
            provider_dataset_name="duckdock.ns1.golden",
            provider_dataset_ref="provider-dataset-1",
            trace_ref=TRACE_REF,
            observation_ref=None,
        )


@pytest.mark.asyncio
async def test_trace_materialization_is_versioned_and_idempotent(
    async_session,
) -> None:
    user, namespace = await _seed_namespace(async_session, "service")
    dataset = await create_evaluation_dataset(
        async_session,
        request=EvaluationDatasetCreate(
            namespace_id=namespace.id,
            name="trace-golden",
            sync_provider=False,
            provider_dataset_ref="provider-dataset-1",
        ),
        actor=user,
    )
    adapter = _FakeMaterializer()
    request = TraceDatasetMaterializationCreate(trace_id=TRACE_REF)

    first = await materialize_evaluation_dataset_trace(
        async_session,
        dataset_public_id=dataset.public_id,
        request=request,
        idempotency_key="trace-materialization-1",
        actor=user,
        materializer=adapter,
    )
    replay = await materialize_evaluation_dataset_trace(
        async_session,
        dataset_public_id=dataset.public_id,
        request=request,
        idempotency_key="trace-materialization-1",
        actor=user,
        materializer=adapter,
    )

    assert replay.id == first.id
    assert adapter.calls == 1
    assert first.dataset_version.version == 1
    assert first.dataset_version.item_count == 3
    assert first.dataset_version.content_digest == "c" * 64
    assert first.source_trace_ref == TRACE_REF
    assert first.source_observation_ref == OBSERVATION_REF
    assert (
        await async_session.scalar(
            select(EvaluationDatasetMaterialization).where(
                EvaluationDatasetMaterialization.id == first.id
            )
        )
    ) is first
    version_count = await async_session.scalar(
        select(func.count(EvaluationDatasetVersion.id)).where(
            EvaluationDatasetVersion.dataset_id == dataset.id
        )
    )
    assert version_count == 1
    outbox = await async_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_public_id == first.public_id
        )
    )
    assert outbox is not None
    assert outbox.event_type == "EvaluationDatasetMaterialized"
    encoded = str(outbox.payload_json).lower()
    assert "question" not in encoded
    assert "answer" not in encoded

    with pytest.raises(
        EvaluationHubConflictError,
        match="already materialized",
    ):
        await materialize_evaluation_dataset_trace(
            async_session,
            dataset_public_id=dataset.public_id,
            request=request,
            idempotency_key="trace-materialization-2",
            actor=user,
            materializer=adapter,
        )


@pytest.mark.asyncio
async def test_trace_materialization_http_create_and_list(
    async_session,
    monkeypatch,
) -> None:
    user, namespace = await _seed_namespace(async_session, "http")
    dataset = await create_evaluation_dataset(
        async_session,
        request=EvaluationDatasetCreate(
            namespace_id=namespace.id,
            name="http-golden",
            sync_provider=False,
            provider_dataset_ref="provider-dataset-http",
        ),
        actor=user,
    )
    adapter = _FakeMaterializer()
    monkeypatch.setattr(
        evaluation_service.langfuse_service,
        "client",
        lambda: object(),
    )
    monkeypatch.setattr(
        evaluation_service,
        "LangfuseTraceDatasetMaterializer",
        lambda **_kwargs: adapter,
    )

    async with _client(async_session, user) as client:
        created = await client.post(
            (
                f"/api/v2/evaluation-datasets/{dataset.public_id}"
                "/trace-materializations"
            ),
            json={"trace_id": TRACE_REF},
            headers={"Idempotency-Key": "trace-materialization-http"},
        )
        assert created.status_code == 201, created.text
        payload = created.json()
        assert payload["dataset_public_id"] == dataset.public_id
        assert payload["dataset_version"] == 1
        assert payload["source_trace_ref"] == TRACE_REF
        assert payload["source_observation_ref"] == OBSERVATION_REF
        assert "input" not in payload
        assert "expected_output" not in payload

        listed = await client.get(
            "/api/v2/evaluation-dataset-materializations",
            params={"namespace_id": namespace.id},
        )
        assert listed.status_code == 200, listed.text
        assert [value["public_id"] for value in listed.json()] == [
            payload["public_id"]
        ]
