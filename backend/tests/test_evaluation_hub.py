from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import select

from app.api.v2.router import api_router
from app.core.database import get_db
from app.core.deps import get_current_user
from app.adapters.evaluation.deepeval import DeepEvalAdapter
from app.models.evaluation import (
    EvaluationResultCompleteness,
    EvaluationResultManifest,
    EvaluationStatus,
    EvaluatorStatus,
    ExperimentStatus,
)
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.user import User
from app.schemas.evaluation import (
    EvaluationCreate,
    EvaluationComplete,
    EvaluationDatasetCreate,
    EvaluationDatasetVersionCreate,
    EvaluatorCreate,
    EvaluatorVersionCreate,
    ExperimentCreate,
)
from app.services import evaluation_service
from app.services.evaluation_ports import (
    EphemeralEvaluationCase,
    EvaluationResultManifestSummary,
    EvaluationRunRequest,
    EvaluationRunSummary,
    MetricSpec,
)
from app.services.evaluation_execution_service import (
    acknowledge_evaluation,
    fail_evaluation,
    lease_evaluation,
    request_evaluation_cancel,
    retry_failed_evaluation,
)
from app.services.evaluation_service import (
    EvaluationHubNotFoundError,
    EvaluationHubStateError,
    complete_evaluation,
    create_evaluation,
    create_evaluation_dataset,
    create_evaluation_dataset_version,
    create_evaluator,
    create_evaluator_version,
    create_experiment,
)


async def _seed_namespace(db, suffix: str):
    user = User(
        username=f"eval-{suffix}",
        email=f"eval-{suffix}@example.test",
        hashed_password="unused",
    )
    db.add(user)
    await db.flush()
    namespace = Namespace(name=f"eval-{suffix}", owner_id=user.id)
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


def _run_summary(
    *,
    provider_dataset_ref: str,
    provider_evaluation_ref: str,
    total_count: int,
    passed_count: int,
    failed_count: int,
    processed_count: int | None = None,
    scored_count: int | None = None,
    loss_reason: str | None = None,
) -> EvaluationRunSummary:
    processed = (
        total_count if processed_count is None else processed_count
    )
    scored = (
        passed_count + failed_count
        if scored_count is None
        else scored_count
    )
    errors = total_count - scored
    completeness = "COMPLETE" if errors == 0 else "PARTIAL"
    score = (
        (passed_count / scored)
        if scored
        else None
    )
    manifest = EvaluationResultManifestSummary(
        provider="LANGFUSE",
        provider_dataset_ref=provider_dataset_ref,
        provider_experiment_ref=provider_evaluation_ref,
        schema_name="langfuse-experiment-result",
        schema_version="sdk-v4",
        content_digest="f" * 64,
        expected_count=total_count,
        processed_count=processed,
        scored_count=scored,
        passed_count=passed_count,
        failed_count=failed_count,
        error_count=errors,
        completeness=completeness,
        loss_reason=loss_reason,
    )
    return EvaluationRunSummary(
        provider_evaluation_ref=provider_evaluation_ref,
        score=score,
        total_count=total_count,
        processed_count=processed,
        scored_count=scored,
        passed_count=passed_count,
        failed_count=failed_count,
        error_count=errors,
        completeness=completeness,
        loss_reason=loss_reason,
        result_manifest=manifest,
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
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        yield client


async def test_evaluation_hub_versioned_lifecycle(
    async_session,
    monkeypatch,
) -> None:
    user, namespace = await _seed_namespace(async_session, "lifecycle")

    async def _provider_sync(**kwargs):
        assert kwargs["namespace_id"] == namespace.id
        assert kwargs["name"] == "quality-golden"
        return "langfuse-dataset-1"

    monkeypatch.setattr(
        evaluation_service,
        "_sync_langfuse_dataset",
        _provider_sync,
    )
    dataset = await create_evaluation_dataset(
        async_session,
        request=EvaluationDatasetCreate(
            namespace_id=namespace.id,
            name="quality-golden",
        ),
        actor=user,
    )
    assert dataset.provider_dataset_ref == "langfuse-dataset-1"

    dataset_version = await create_evaluation_dataset_version(
        async_session,
        dataset=dataset,
        request=EvaluationDatasetVersionCreate(
            content_digest="a" * 64,
            item_count=12,
            schema_name="duckdock-eval-case",
            schema_version="1.0",
            provider_version_ref="2026-07-31T00:00:00Z",
        ),
        actor=user,
    )
    assert dataset_version.version == 1

    evaluator = await create_evaluator(
        async_session,
        request=EvaluatorCreate(
            namespace_id=namespace.id,
            name="answer-quality",
            kind="DEEPEVAL",
            provider="DEEPEVAL",
        ),
        actor=user,
    )
    evaluator_version = await create_evaluator_version(
        async_session,
        evaluator=evaluator,
        request=EvaluatorVersionCreate(
            config_digest="b" * 64,
            implementation_ref="deepeval://answer_relevancy",
            rubric_version="v1",
        ),
        actor=user,
    )
    assert evaluator.status == EvaluatorStatus.ACTIVE
    assert evaluator_version.version == 1

    experiment = await create_experiment(
        async_session,
        request=ExperimentCreate(
            namespace_id=namespace.id,
            name="agent-candidate-20260731",
            dataset_version_public_id=dataset_version.public_id,
            target_type="agent-release-candidate",
            target_ref="candidate:agent-a:20260731",
            target_digest="c" * 64,
            provider="LANGFUSE",
        ),
        actor=user,
    )
    evaluation = await create_evaluation(
        async_session,
        experiment=experiment,
        request=EvaluationCreate(
            evaluator_version_public_id=evaluator_version.public_id,
            provider_evaluation_ref="langfuse-experiment-item-set-1",
        ),
        actor=user,
    )
    assert evaluation.status == EvaluationStatus.PENDING
    assert experiment.status == ExperimentStatus.RUNNING

    lease = await lease_evaluation(
        async_session,
        evaluation_public_id=evaluation.public_id,
        worker_id="test-worker",
        lease_seconds=300,
        max_attempts=3,
    )
    assert lease is not None
    acknowledged = await acknowledge_evaluation(
        async_session,
        evaluation_public_id=evaluation.public_id,
        worker_id="test-worker",
        summary=_run_summary(
            provider_dataset_ref="langfuse-dataset-1",
            provider_evaluation_ref="langfuse-experiment-item-set-1",
            total_count=12,
            passed_count=10,
            failed_count=2,
        ),
    )
    assert acknowledged == EvaluationStatus.COMPLETED
    assert evaluation.status == EvaluationStatus.COMPLETED
    assert evaluation.score == pytest.approx(10 / 12)
    assert evaluation.result_completeness == EvaluationResultCompleteness.COMPLETE
    assert experiment.status == ExperimentStatus.COMPLETED
    assert experiment.dataset_version_id == dataset_version.id
    assert experiment.target_digest == "c" * 64
    manifest = await async_session.scalar(
        select(EvaluationResultManifest).where(
            EvaluationResultManifest.evaluation_id == evaluation.id
        )
    )
    assert manifest is not None
    assert manifest.version == 1
    assert manifest.provider_dataset_ref == "langfuse-dataset-1"
    assert manifest.expected_count == 12
    async with _client(async_session, user) as client:
        response = await client.get(
            f"/api/v2/evaluations/{evaluation.public_id}/result-manifests"
        )
    assert response.status_code == 200, response.text
    assert response.json() == [
        {
            "public_id": manifest.public_id,
            "namespace_id": namespace.id,
            "evaluation_public_id": evaluation.public_id,
            "version": 1,
            "provider": "LANGFUSE",
            "provider_dataset_ref": "langfuse-dataset-1",
            "provider_experiment_ref": "langfuse-experiment-item-set-1",
            "schema_name": "langfuse-experiment-result",
            "schema_version": "sdk-v4",
            "content_digest": "f" * 64,
            "score": pytest.approx(10 / 12),
            "expected_count": 12,
            "processed_count": 12,
            "scored_count": 12,
            "passed_count": 10,
            "failed_count": 2,
            "error_count": 0,
            "completeness": "COMPLETE",
            "loss_reason": None,
            "created_at": manifest.created_at.isoformat(),
        }
    ]


async def test_evaluation_execution_retry_cancel_and_manual_retry(
    async_session,
    monkeypatch,
) -> None:
    user, namespace = await _seed_namespace(async_session, "execution")

    async def _provider_sync(**_kwargs):
        return "langfuse-execution-dataset"

    monkeypatch.setattr(
        evaluation_service,
        "_sync_langfuse_dataset",
        _provider_sync,
    )
    dataset = await create_evaluation_dataset(
        async_session,
        request=EvaluationDatasetCreate(
            namespace_id=namespace.id,
            name="execution-golden",
        ),
        actor=user,
    )
    dataset_version = await create_evaluation_dataset_version(
        async_session,
        dataset=dataset,
        request=EvaluationDatasetVersionCreate(
            content_digest="4" * 64,
            item_count=2,
            schema_name="duckdock-eval-case",
            schema_version="1",
            provider_version_ref="2026-07-31T00:00:00Z",
        ),
        actor=user,
    )
    evaluator = await create_evaluator(
        async_session,
        request=EvaluatorCreate(
            namespace_id=namespace.id,
            name="execution-rule",
            kind="RULE",
            provider="CUSTOM",
        ),
        actor=user,
    )
    evaluator_version = await create_evaluator_version(
        async_session,
        evaluator=evaluator,
        request=EvaluatorVersionCreate(
            config_digest="5" * 64,
            implementation_ref="rule://exact_match",
        ),
        actor=user,
    )
    experiment = await create_experiment(
        async_session,
        request=ExperimentCreate(
            namespace_id=namespace.id,
            name="execution-experiment",
            dataset_version_public_id=dataset_version.public_id,
            target_type="dataset-replay",
            target_ref="provider://dataset-replay",
            target_digest="6" * 64,
            provider="LANGFUSE",
        ),
        actor=user,
    )
    evaluation = await create_evaluation(
        async_session,
        experiment=experiment,
        request=EvaluationCreate(
            evaluator_version_public_id=evaluator_version.public_id,
        ),
        actor=user,
        execution_key="execution:test:retry",
    )
    replay = await create_evaluation(
        async_session,
        experiment=experiment,
        request=EvaluationCreate(
            evaluator_version_public_id=evaluator_version.public_id,
        ),
        actor=user,
        execution_key="execution:test:retry",
    )
    assert replay.id == evaluation.id

    lease = await lease_evaluation(
        async_session,
        evaluation_public_id=evaluation.public_id,
        worker_id="worker-a",
        lease_seconds=300,
        max_attempts=3,
    )
    assert lease is not None
    retry_status = await fail_evaluation(
        async_session,
        evaluation_public_id=evaluation.public_id,
        worker_id="worker-a",
        error_code="langfuse_experiment_failed",
        max_attempts=3,
        base_retry_seconds=1,
        max_retry_seconds=10,
    )
    assert retry_status == EvaluationStatus.PENDING
    assert evaluation.attempt_count == 1
    assert evaluation.lease_owner is None

    evaluation.status = EvaluationStatus.FAILED
    evaluation.ended_at = evaluation.available_at
    retried = await retry_failed_evaluation(
        async_session,
        evaluation=evaluation,
        actor=user,
    )
    assert retried.status == EvaluationStatus.PENDING
    assert retried.attempt_count == 0

    cancel_result = await request_evaluation_cancel(
        async_session,
        evaluation=evaluation,
        actor=user,
    )
    assert cancel_result.status == EvaluationStatus.CANCELLED
    assert cancel_result.cancel_requested_at is not None

    reclaimed_experiment = await create_experiment(
        async_session,
        request=ExperimentCreate(
            namespace_id=namespace.id,
            name="execution-reclaim",
            dataset_version_public_id=dataset_version.public_id,
            target_type="dataset-replay",
            target_ref="provider://dataset-replay",
            target_digest="7" * 64,
            provider="LANGFUSE",
        ),
        actor=user,
    )
    reclaimed_evaluation = await create_evaluation(
        async_session,
        experiment=reclaimed_experiment,
        request=EvaluationCreate(
            evaluator_version_public_id=evaluator_version.public_id,
        ),
        actor=user,
        execution_key="execution:test:reclaim",
    )
    lease_started_at = reclaimed_evaluation.available_at + timedelta(
        seconds=1
    )
    original_lease = await lease_evaluation(
        async_session,
        evaluation_public_id=reclaimed_evaluation.public_id,
        worker_id="worker-original",
        lease_seconds=30,
        max_attempts=3,
        now=lease_started_at,
    )
    assert original_lease is not None
    with pytest.raises(EvaluationHubStateError):
        await complete_evaluation(
            async_session,
            evaluation=reclaimed_evaluation,
            request=EvaluationComplete(
                score=1,
                total_count=2,
                passed_count=2,
                failed_count=0,
            ),
            actor=user,
        )
    assert (
        await lease_evaluation(
            async_session,
            evaluation_public_id=reclaimed_evaluation.public_id,
            worker_id="worker-early",
            lease_seconds=30,
            max_attempts=3,
            now=lease_started_at + timedelta(seconds=29),
        )
        is None
    )
    replacement_lease = await lease_evaluation(
        async_session,
        evaluation_public_id=reclaimed_evaluation.public_id,
        worker_id="worker-replacement",
        lease_seconds=30,
        max_attempts=3,
        now=lease_started_at + timedelta(seconds=31),
    )
    assert replacement_lease is not None
    assert replacement_lease.attempt_count == 2
    summary = _run_summary(
        provider_dataset_ref="langfuse-execution-dataset",
        provider_evaluation_ref="langfuse-reclaimed",
        total_count=2,
        passed_count=1,
        failed_count=1,
    )
    assert not await acknowledge_evaluation(
        async_session,
        evaluation_public_id=reclaimed_evaluation.public_id,
        worker_id="worker-original",
        summary=summary,
    )
    assert await acknowledge_evaluation(
        async_session,
        evaluation_public_id=reclaimed_evaluation.public_id,
        worker_id="worker-replacement",
        summary=summary,
    )
    assert reclaimed_evaluation.status == EvaluationStatus.COMPLETED

    partial_experiment = await create_experiment(
        async_session,
        request=ExperimentCreate(
            namespace_id=namespace.id,
            name="execution-partial",
            dataset_version_public_id=dataset_version.public_id,
            target_type="dataset-replay",
            target_ref="provider://dataset-replay",
            target_digest="8" * 64,
            provider="LANGFUSE",
        ),
        actor=user,
    )
    partial_evaluation = await create_evaluation(
        async_session,
        experiment=partial_experiment,
        request=EvaluationCreate(
            evaluator_version_public_id=evaluator_version.public_id,
        ),
        actor=user,
        execution_key="execution:test:partial",
    )
    assert await lease_evaluation(
        async_session,
        evaluation_public_id=partial_evaluation.public_id,
        worker_id="worker-partial",
        lease_seconds=300,
        max_attempts=3,
    )
    partial_status = await acknowledge_evaluation(
        async_session,
        evaluation_public_id=partial_evaluation.public_id,
        worker_id="worker-partial",
        summary=_run_summary(
            provider_dataset_ref="langfuse-execution-dataset",
            provider_evaluation_ref="langfuse-partial",
            total_count=2,
            processed_count=1,
            scored_count=1,
            passed_count=1,
            failed_count=0,
            loss_reason="provider_item_loss",
        ),
    )
    assert partial_status == EvaluationStatus.PARTIAL
    assert partial_evaluation.status == EvaluationStatus.PARTIAL
    assert partial_evaluation.result_completeness == (
        EvaluationResultCompleteness.PARTIAL
    )
    assert partial_evaluation.error_count == 1
    assert partial_experiment.status == ExperimentStatus.PARTIAL
    original_manifest = await async_session.scalar(
        select(EvaluationResultManifest).where(
            EvaluationResultManifest.evaluation_id == partial_evaluation.id
        )
    )
    assert original_manifest is not None

    retried_partial = await retry_failed_evaluation(
        async_session,
        evaluation=partial_evaluation,
        actor=user,
    )
    assert retried_partial.status == EvaluationStatus.PENDING
    assert retried_partial.result_completeness is None
    assert retried_partial.error_count == 0
    retained_manifests = list(
        (
            await async_session.scalars(
                select(EvaluationResultManifest).where(
                    EvaluationResultManifest.evaluation_id
                    == partial_evaluation.id
                )
            )
        ).all()
    )
    assert [row.public_id for row in retained_manifests] == [
        original_manifest.public_id
    ]


async def test_langfuse_experiment_runner_returns_summary_only() -> None:
    from app.adapters.evaluation.langfuse import LangfuseExperimentRunner
    from app.adapters.evaluation.replay import DatasetReplayTargetAdapter

    version = datetime(2026, 7, 31, tzinfo=timezone.utc)
    captured: dict[str, object] = {}
    items = [
        SimpleNamespace(
            id="item-1",
            dataset_id="dataset-1",
            input={"prompt": "private one", "actual_output": "Paris"},
            expected_output="Paris",
            metadata={},
        ),
        SimpleNamespace(
            id="item-2",
            dataset_id="dataset-1",
            input={"prompt": "private two", "actual_output": "wrong"},
            expected_output="Berlin",
            metadata={},
        ),
    ]

    class _Dataset:
        def __init__(self):
            self.items = items

        def run_experiment(self, **kwargs):
            captured.update(kwargs)
            item_results = []
            for item in self.items:
                output = kwargs["task"](item=item)
                evaluation = kwargs["evaluators"][0](
                    input=item.input,
                    output=output,
                    expected_output=item.expected_output,
                )
                item_results.append(
                    SimpleNamespace(
                        item=item,
                        trace_id=f"trace-{item.id}",
                        evaluations=[evaluation],
                    )
                )
            return SimpleNamespace(
                item_results=item_results,
                dataset_run_id="dataset-run-1",
                experiment_id="experiment-1",
            )

    class _Client:
        def get_dataset(self, name, *, version):
            captured["dataset_name"] = name
            captured["dataset_version"] = version
            return _Dataset()

    summary = await LangfuseExperimentRunner(
        client=_Client(),
        target_adapter=DatasetReplayTargetAdapter(),
    ).run(
        request=EvaluationRunRequest(
            evaluation_public_id="eval_123",
            experiment_public_id="exp_123",
            experiment_name="smoke",
            namespace_id=1,
            dataset_name="duckdock.ns1.smoke",
            provider_dataset_ref="dataset-1",
            dataset_version=version,
            expected_item_count=2,
            target_type="dataset-replay",
            target_ref="provider://dataset-replay",
            target_digest="7" * 64,
            evaluator_kind="RULE",
            evaluator_provider="CUSTOM",
            evaluator_implementation_ref="rule://exact_match",
            evaluator_config_digest="8" * 64,
        )
    )

    assert summary.provider_evaluation_ref == "dataset-run-1"
    assert summary.score == 0.5
    assert summary.total_count == 2
    assert summary.processed_count == 2
    assert summary.scored_count == 2
    assert summary.passed_count == 1
    assert summary.failed_count == 1
    assert summary.error_count == 0
    assert summary.completeness == "COMPLETE"
    assert summary.loss_reason is None
    assert summary.result_manifest.provider_dataset_ref == "dataset-1"
    assert summary.result_manifest.schema_name == "langfuse-experiment-result"
    assert len(summary.result_manifest.content_digest) == 64
    assert captured["dataset_version"] == version
    assert "private" not in repr(summary)


async def test_langfuse_experiment_runner_marks_missing_scores_partial() -> None:
    from app.adapters.evaluation.langfuse import LangfuseExperimentRunner
    from app.adapters.evaluation.replay import DatasetReplayTargetAdapter

    version = datetime(2026, 7, 31, tzinfo=timezone.utc)
    item = SimpleNamespace(
        id="item-without-score",
        input={"actual_output": "Paris"},
        expected_output="Paris",
        metadata={},
    )

    class _Dataset:
        items = [item]

        def run_experiment(self, **_kwargs):
            return SimpleNamespace(
                item_results=[
                    SimpleNamespace(
                        item=item,
                        trace_id="trace-without-score",
                        evaluations=[],
                    )
                ],
                dataset_run_id="dataset-run-without-scores",
                experiment_id=None,
            )

    class _Client:
        def get_dataset(self, _name, *, version):
            assert version == datetime(2026, 7, 31, tzinfo=timezone.utc)
            return _Dataset()

    request = EvaluationRunRequest(
        evaluation_public_id="eval_missing_scores",
        experiment_public_id="exp_missing_scores",
        experiment_name="missing-scores",
        namespace_id=1,
        dataset_name="duckdock.ns1.missing-scores",
        provider_dataset_ref="dataset-without-scores",
        dataset_version=version,
        expected_item_count=1,
        target_type="dataset-replay",
        target_ref="provider://dataset-replay",
        target_digest="9" * 64,
        evaluator_kind="RULE",
        evaluator_provider="CUSTOM",
        evaluator_implementation_ref="rule://exact_match",
        evaluator_config_digest="a" * 64,
    )
    summary = await LangfuseExperimentRunner(
        client=_Client(),
        target_adapter=DatasetReplayTargetAdapter(),
    ).run(request=request)
    assert summary.score is None
    assert summary.processed_count == 1
    assert summary.scored_count == 0
    assert summary.error_count == 1
    assert summary.completeness == "PARTIAL"
    assert summary.loss_reason == "provider_score_loss"


async def test_experiment_rejects_cross_namespace_dataset_version(
    async_session,
) -> None:
    user_a, namespace_a = await _seed_namespace(async_session, "tenant-a")
    user_b, namespace_b = await _seed_namespace(async_session, "tenant-b")
    dataset = await create_evaluation_dataset(
        async_session,
        request=EvaluationDatasetCreate(
            namespace_id=namespace_a.id,
            name="tenant-a-only",
            provider="CUSTOM",
            sync_provider=False,
            provider_dataset_ref="custom:dataset-a",
        ),
        actor=user_a,
    )
    version = await create_evaluation_dataset_version(
        async_session,
        dataset=dataset,
        request=EvaluationDatasetVersionCreate(
            content_digest="d" * 64,
            item_count=1,
            schema_name="duckdock-eval-case",
            schema_version="1",
        ),
        actor=user_a,
    )

    with pytest.raises(EvaluationHubNotFoundError):
        await create_experiment(
            async_session,
            request=ExperimentCreate(
                namespace_id=namespace_b.id,
                name="cross-tenant",
                dataset_version_public_id=version.public_id,
                target_type="agent",
                target_ref="agent:b",
                target_digest="e" * 64,
                provider="CUSTOM",
            ),
            actor=user_b,
        )


async def test_evaluation_hub_api_lifecycle(async_session) -> None:
    user, namespace = await _seed_namespace(async_session, "api")

    async with _client(async_session, user) as client:
        dataset_response = await client.post(
            "/api/v2/evaluation-datasets",
            json={
                "namespace_id": namespace.id,
                "name": "api-golden",
                "provider": "CUSTOM",
                "sync_provider": False,
                "provider_dataset_ref": "custom:api-golden",
            },
        )
        assert dataset_response.status_code == 201, dataset_response.text
        dataset = dataset_response.json()
        assert dataset["versions"] == []

        version_response = await client.post(
            f"/api/v2/evaluation-datasets/{dataset['public_id']}/versions",
            json={
                "content_digest": "1" * 64,
                "item_count": 2,
                "schema_name": "duckdock-eval-case",
                "schema_version": "1.0",
            },
        )
        assert version_response.status_code == 201, version_response.text
        dataset_version = version_response.json()

        evaluator_response = await client.post(
            "/api/v2/evaluators",
            json={
                "namespace_id": namespace.id,
                "name": "api-quality",
                "kind": "DEEPEVAL",
                "provider": "DEEPEVAL",
            },
        )
        assert evaluator_response.status_code == 201, evaluator_response.text
        evaluator = evaluator_response.json()

        evaluator_version_response = await client.post(
            f"/api/v2/evaluators/{evaluator['public_id']}/versions",
            json={
                "config_digest": "2" * 64,
                "implementation_ref": "deepeval://answer_relevancy",
            },
        )
        assert (
            evaluator_version_response.status_code == 201
        ), evaluator_version_response.text
        evaluator_version = evaluator_version_response.json()

        experiment_response = await client.post(
            "/api/v2/evaluation-experiments",
            json={
                "namespace_id": namespace.id,
                "name": "api-experiment",
                "dataset_version_public_id": dataset_version["public_id"],
                "target_type": "agent-release-candidate",
                "target_ref": "candidate:api",
                "target_digest": "3" * 64,
                "provider": "CUSTOM",
            },
        )
        assert experiment_response.status_code == 201, experiment_response.text
        experiment = experiment_response.json()

        evaluation_response = await client.post(
            (
                "/api/v2/evaluation-experiments/"
                f"{experiment['public_id']}/evaluations"
            ),
            json={
                "evaluator_version_public_id": evaluator_version["public_id"],
                "provider_evaluation_ref": "custom:evaluation-api",
            },
        )
        assert evaluation_response.status_code == 201, evaluation_response.text
        evaluation = evaluation_response.json()
        assert evaluation["status"] == "RUNNING"

        complete_response = await client.post(
            f"/api/v2/evaluations/{evaluation['public_id']}/complete",
            json={
                "score": 1.0,
                "total_count": 2,
                "passed_count": 2,
                "failed_count": 0,
            },
        )
        assert complete_response.status_code == 200, complete_response.text
        assert complete_response.json()["status"] == "COMPLETED"

        cancel_experiment_response = await client.post(
            "/api/v2/evaluation-experiments",
            json={
                "namespace_id": namespace.id,
                "name": "api-cancel-experiment",
                "dataset_version_public_id": dataset_version["public_id"],
                "target_type": "agent-release-candidate",
                "target_ref": "candidate:api-cancel",
                "target_digest": "4" * 64,
                "provider": "CUSTOM",
            },
        )
        assert cancel_experiment_response.status_code == 201
        cancel_evaluation_response = await client.post(
            (
                "/api/v2/evaluation-experiments/"
                f"{cancel_experiment_response.json()['public_id']}/evaluations"
            ),
            json={
                "evaluator_version_public_id": evaluator_version["public_id"],
                "provider_evaluation_ref": "custom:evaluation-api-cancel",
            },
        )
        assert cancel_evaluation_response.status_code == 201
        cancel_response = await client.post(
            (
                "/api/v2/evaluations/"
                f"{cancel_evaluation_response.json()['public_id']}/cancel"
            )
        )
        assert cancel_response.status_code == 200, cancel_response.text
        assert cancel_response.json()["status"] == "CANCELLED"

        list_response = await client.get(
            "/api/v2/evaluations",
            params={
                "namespace_id": namespace.id,
                "experiment_public_id": experiment["public_id"],
            },
        )
        assert list_response.status_code == 200, list_response.text
        assert [item["public_id"] for item in list_response.json()] == [
            evaluation["public_id"]
        ]


def test_evaluation_contract_rejects_content_bearing_fields() -> None:
    with pytest.raises(ValidationError):
        EvaluationDatasetCreate.model_validate(
            {
                "namespace_id": 1,
                "name": "unsafe",
                "items": [{"input": "secret"}],
            }
        )
    with pytest.raises(ValidationError):
        EvaluatorVersionCreate.model_validate(
            {
                "config_digest": "f" * 64,
                "implementation_ref": "deepeval://answer_relevancy",
                "prompt": "secret prompt",
            }
        )


def test_deepeval_adapter_returns_summaries_without_content() -> None:
    seen: dict[str, object] = {}

    class _Metric:
        score = 0.91

        def measure(self, test_case):
            seen["case"] = test_case

    def _metric_factory(spec):
        seen["metric"] = spec
        return _Metric()

    def _case_factory(**kwargs):
        return SimpleNamespace(**kwargs)

    result = DeepEvalAdapter(
        metric_factory=_metric_factory,
        case_factory=_case_factory,
    ).evaluate(
        case=EphemeralEvaluationCase(
            input_text="private input",
            actual_output="private output",
        ),
        metrics=[MetricSpec(name="answer_relevancy", threshold=0.8)],
    )

    assert result[0].score == 0.91
    assert result[0].passed is True
    assert not hasattr(result[0], "reason")
    assert "private" not in repr(result)
    assert seen["case"].input == "private input"
