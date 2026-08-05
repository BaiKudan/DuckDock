from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from app.api.v2.router import api_router
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.evaluation import (
    EvaluationDatasetCurationBatch,
    EvaluationSamplingPolicyVersion,
    EvaluationSamplingRun,
    EvaluationSamplingRunItem,
)
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.outbox import OutboxEvent
from app.models.user import User
from app.schemas.evaluation import (
    EvaluationDatasetCreate,
    EvaluationSamplingPolicyCreate,
    EvaluationSamplingPolicyVersionCreate,
    EvaluationSamplingRunCreate,
)
from app.services import evaluation_curation_service
from app.services.evaluation_curation_service import (
    list_trace_dataset_candidates,
)
from app.services.evaluation_ports import TraceDatasetCandidate
from app.services.evaluation_sampling_service import (
    create_evaluation_sampling_policy,
    create_evaluation_sampling_policy_version,
    run_evaluation_sampling_policy,
)
from app.services.evaluation_service import (
    EvaluationHubStateError,
    create_evaluation_dataset,
)


NOW = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)
TRACES = [character * 32 for character in "12345"]
OBSERVATIONS = [character * 16 for character in "abcde"]


async def _seed_namespace(db, suffix: str):
    user = User(
        username=f"sampling-{suffix}",
        email=f"sampling-{suffix}@example.test",
        hashed_password="unused",
    )
    db.add(user)
    await db.flush()
    namespace = Namespace(name=f"sampling-{suffix}", owner_id=user.id)
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
            name=f"sampling-{suffix}",
            sync_provider=False,
            provider_dataset_ref=f"provider-{suffix}",
        ),
        actor=user,
    )


class _FakeCandidateReader:
    def __init__(self, count: int = 5) -> None:
        self.calls = 0
        self.count = count
        self.kwargs: dict = {}

    async def list_candidates(self, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        return [
            TraceDatasetCandidate(
                source_trace_ref=TRACES[index],
                source_observation_ref=OBSERVATIONS[index],
                name="production-agent-loop",
                observation_type="SPAN",
                start_time=NOW + timedelta(seconds=index),
                end_time=NOW + timedelta(seconds=index + 1),
                environment="production",
            )
            for index in reversed(range(self.count))
        ]


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


async def _policy_and_version(db, user, namespace, suffix: str):
    policy = await create_evaluation_sampling_policy(
        db,
        request=EvaluationSamplingPolicyCreate(
            namespace_id=namespace.id,
            name=f"stable-production-{suffix}",
        ),
        actor=user,
    )
    version = await create_evaluation_sampling_policy_version(
        db,
        policy=policy,
        request=EvaluationSamplingPolicyVersionCreate(
            sample_size=2,
            minimum_sample_size=2,
            candidate_limit=5,
            observation_name="production-agent-loop",
            observation_type="span",
            environment="production",
        ),
        actor=user,
    )
    return policy, version


@pytest.mark.asyncio
async def test_sampling_run_is_reproducible_content_free_and_idempotent(
    async_session,
) -> None:
    user, namespace = await _seed_namespace(async_session, "service")
    dataset = await _seed_dataset(
        async_session, user, namespace, "service"
    )
    policy, version = await _policy_and_version(
        async_session, user, namespace, "service"
    )
    reader = _FakeCandidateReader()
    request = EvaluationSamplingRunCreate(
        dataset_public_id=dataset.public_id,
        from_start_time=NOW - timedelta(hours=1),
        to_start_time=NOW + timedelta(hours=1),
    )

    run = await run_evaluation_sampling_policy(
        async_session,
        version=version,
        request=request,
        idempotency_key="sampling-run-service",
        actor=user,
        reader=reader,
    )
    replay = await run_evaluation_sampling_policy(
        async_session,
        version=version,
        request=request,
        idempotency_key="sampling-run-service",
        actor=user,
        reader=reader,
    )

    assert replay.id == run.id
    assert reader.calls == 1
    assert reader.kwargs["name"] == "production-agent-loop"
    assert reader.kwargs["observation_type"] == "SPAN"
    assert reader.kwargs["environment"] == "production"
    assert reader.kwargs["limit"] == 5
    assert run.candidate_count == 5
    assert run.eligible_count == 5
    assert run.selected_count == 2
    assert len(run.items) == 2
    assert [item.rank_digest for item in run.items] == sorted(
        item.rank_digest for item in run.items
    )
    assert run.selection_digest == run.curation_batch.selection_digest
    assert run.curation_batch.item_count == 2
    assert len(run.run_digest) == 64
    assert (
        await async_session.scalar(
            select(func.count(EvaluationSamplingRun.id))
        )
        == 1
    )
    assert (
        await async_session.scalar(
            select(func.count(EvaluationSamplingRunItem.id))
        )
        == 2
    )
    event = (
        await async_session.execute(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_public_id == run.public_id
            )
        )
    ).scalar_one()
    assert event.event_type == "EvaluationSamplingRunCreated"
    encoded = str(event.payload_json).lower()
    assert "input" not in encoded
    assert "output" not in encoded
    assert "production-agent-loop" not in encoded

    _, candidates = await list_trace_dataset_candidates(
        async_session,
        dataset_public_id=dataset.public_id,
        from_start_time=NOW - timedelta(hours=1),
        to_start_time=NOW + timedelta(hours=1),
        name=None,
        observation_type=None,
        environment=None,
        root_only=True,
        limit=5,
        reader=reader,
    )
    governed = {
        (
            view.candidate.source_trace_ref,
            view.candidate.source_observation_ref,
        )
        for view in candidates
        if view.already_governed
    }
    assert governed == {
        (item.source_trace_ref, item.source_observation_ref)
        for item in run.items
    }
    assert not any(view.already_materialized for view in candidates)


@pytest.mark.asyncio
async def test_sampling_run_fails_closed_below_minimum(async_session) -> None:
    user, namespace = await _seed_namespace(async_session, "minimum")
    dataset = await _seed_dataset(
        async_session, user, namespace, "minimum"
    )
    _, version = await _policy_and_version(
        async_session, user, namespace, "minimum"
    )

    with pytest.raises(EvaluationHubStateError, match="minimum"):
        await run_evaluation_sampling_policy(
            async_session,
            version=version,
            request=EvaluationSamplingRunCreate(
                dataset_public_id=dataset.public_id,
                from_start_time=NOW - timedelta(hours=1),
                to_start_time=NOW + timedelta(hours=1),
            ),
            idempotency_key="sampling-run-minimum",
            actor=user,
            reader=_FakeCandidateReader(count=1),
        )

    assert (
        await async_session.scalar(
            select(func.count(EvaluationSamplingRun.id))
        )
        == 0
    )
    assert (
        await async_session.scalar(
            select(func.count(EvaluationDatasetCurationBatch.id))
        )
        == 0
    )


@pytest.mark.asyncio
async def test_sampling_http_policy_version_run_and_list(
    async_session,
    monkeypatch,
) -> None:
    user, namespace = await _seed_namespace(async_session, "http")
    dataset = await _seed_dataset(async_session, user, namespace, "http")
    reader = _FakeCandidateReader(count=3)
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

    async with _client(async_session, user) as client:
        policy_response = await client.post(
            "/api/v2/evaluation-sampling-policies",
            json={
                "namespace_id": namespace.id,
                "name": "http-production-sample",
                "description": "metadata only",
            },
        )
        assert policy_response.status_code == 201, policy_response.text
        policy = policy_response.json()
        assert policy["versions"] == []

        version_response = await client.post(
            (
                "/api/v2/evaluation-sampling-policies/"
                f"{policy['public_id']}/versions"
            ),
            json={
                "sample_size": 2,
                "minimum_sample_size": 1,
                "candidate_limit": 3,
                "observation_name": "production-agent-loop",
                "observation_type": "SPAN",
                "environment": "production",
                "root_only": True,
            },
        )
        assert version_response.status_code == 201, version_response.text
        version = version_response.json()
        assert version["version"] == 1
        assert version["exclude_governed"] is True

        run_response = await client.post(
            (
                "/api/v2/evaluation-sampling-policy-versions/"
                f"{version['public_id']}/runs"
            ),
            headers={"Idempotency-Key": "sampling-run-http"},
            json={
                "dataset_public_id": dataset.public_id,
                "from_start_time": (
                    NOW - timedelta(hours=1)
                ).isoformat(),
                "to_start_time": (NOW + timedelta(hours=1)).isoformat(),
            },
        )
        assert run_response.status_code == 201, run_response.text
        run = run_response.json()
        assert run["policy_public_id"] == policy["public_id"]
        assert run["policy_version_public_id"] == version["public_id"]
        assert run["dataset_public_id"] == dataset.public_id
        assert run["selected_count"] == 2
        assert len(run["items"]) == 2
        assert "input" not in run
        assert "output" not in run

        policies = await client.get(
            "/api/v2/evaluation-sampling-policies",
            params={"namespace_id": namespace.id},
        )
        assert policies.status_code == 200, policies.text
        assert policies.json()[0]["versions"][0]["public_id"] == version[
            "public_id"
        ]

        runs = await client.get(
            "/api/v2/evaluation-sampling-runs",
            params={"namespace_id": namespace.id},
        )
        assert runs.status_code == 200, runs.text
        assert runs.json()[0]["public_id"] == run["public_id"]

        batches = await client.get(
            "/api/v2/evaluation-dataset-curation-batches",
            params={"namespace_id": namespace.id},
        )
        assert batches.status_code == 200, batches.text
        assert batches.json()[0]["public_id"] == run[
            "curation_batch_public_id"
        ]
        assert batches.json()[0]["status"] == "PENDING_REVIEW"

    assert (
        await async_session.scalar(
            select(func.count(EvaluationSamplingPolicyVersion.id))
        )
        == 1
    )
