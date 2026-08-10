from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.api.v2.router import api_router
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.evaluation import (
    Evaluation,
    EvaluationComparisonOutcome,
    EvaluationDataset,
    EvaluationDatasetVersion,
    EvaluationProvider,
    EvaluationResultCompleteness,
    EvaluationResultManifest,
    EvaluationStatus,
    Evaluator,
    EvaluatorKind,
    EvaluatorStatus,
    EvaluatorVersion,
    Experiment,
    ExperimentStatus,
)
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.outbox import OutboxEvent
from app.models.user import User
from app.schemas.evaluation import (
    EvaluationComparisonCreate,
    RegressionPolicyCreate,
    RegressionPolicyVersionCreate,
)
from app.services.evaluation_comparison_service import (
    create_evaluation_comparison,
    create_regression_policy,
    create_regression_policy_version,
    load_evaluation_comparison_view,
)
from app.services.evaluation_service import EvaluationHubConflictError
from app.services.evaluation_service import EvaluationHubNotFoundError


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


async def _seed_comparison_graph(
    db,
    *,
    suffix: str,
    baseline_score: float = 0.8,
    candidate_score: float = 0.9,
    candidate_completeness: EvaluationResultCompleteness = (
        EvaluationResultCompleteness.COMPLETE
    ),
    candidate_dataset_version: EvaluationDatasetVersion | None = None,
):
    user = User(
        username=f"compare-{suffix}",
        email=f"compare-{suffix}@example.test",
        hashed_password="unused",
    )
    db.add(user)
    await db.flush()
    namespace = Namespace(name=f"compare-{suffix}", owner_id=user.id)
    db.add(namespace)
    await db.flush()
    db.add(
        NamespaceMember(
            namespace_id=namespace.id,
            user_id=user.id,
            role=NamespaceRole.ADMIN,
        )
    )

    dataset = EvaluationDataset(
        public_id=f"eds_{suffix}",
        namespace_id=namespace.id,
        name=f"dataset-{suffix}",
        provider=EvaluationProvider.LANGFUSE,
        provider_dataset_ref=f"provider-dataset-{suffix}",
    )
    db.add(dataset)
    await db.flush()
    dataset_version = EvaluationDatasetVersion(
        public_id=f"edv_{suffix}",
        dataset_id=dataset.id,
        version=1,
        content_digest="a" * 64,
        item_count=10,
        schema_name="duckdock-eval-case",
        schema_version="1",
        provider_version_ref="2026-07-31T00:00:00Z",
    )
    db.add(dataset_version)

    evaluator = Evaluator(
        public_id=f"evr_{suffix}",
        namespace_id=namespace.id,
        name=f"evaluator-{suffix}",
        kind=EvaluatorKind.RULE,
        provider=EvaluationProvider.CUSTOM,
        status=EvaluatorStatus.ACTIVE,
    )
    db.add(evaluator)
    await db.flush()
    evaluator_version = EvaluatorVersion(
        public_id=f"evv_{suffix}",
        evaluator_id=evaluator.id,
        version=1,
        config_digest="b" * 64,
        implementation_ref="rule://exact_match",
    )
    db.add(evaluator_version)
    await db.flush()

    baseline_experiment = Experiment(
        public_id=f"exp_baseline_{suffix}",
        namespace_id=namespace.id,
        name=f"baseline-{suffix}",
        dataset_version_id=dataset_version.id,
        target_type="agent-release-candidate",
        target_ref=f"candidate:baseline:{suffix}",
        target_digest="c" * 64,
        provider=EvaluationProvider.LANGFUSE,
        status=ExperimentStatus.COMPLETED,
    )
    candidate_experiment = Experiment(
        public_id=f"exp_candidate_{suffix}",
        namespace_id=namespace.id,
        name=f"candidate-{suffix}",
        dataset_version_id=(
            candidate_dataset_version.id
            if candidate_dataset_version is not None
            else dataset_version.id
        ),
        target_type="agent-release-candidate",
        target_ref=f"candidate:new:{suffix}",
        target_digest="d" * 64,
        provider=EvaluationProvider.LANGFUSE,
        status=(
            ExperimentStatus.COMPLETED
            if candidate_completeness
            == EvaluationResultCompleteness.COMPLETE
            else ExperimentStatus.PARTIAL
        ),
    )
    db.add_all([baseline_experiment, candidate_experiment])
    await db.flush()

    baseline_evaluation = Evaluation(
        public_id=f"eval_baseline_{suffix}",
        namespace_id=namespace.id,
        experiment_id=baseline_experiment.id,
        evaluator_version_id=evaluator_version.id,
        status=EvaluationStatus.COMPLETED,
        provider_evaluation_ref=f"provider-baseline-{suffix}",
        score=baseline_score,
        total_count=10,
        processed_count=10,
        scored_count=10,
        passed_count=round(baseline_score * 10),
        failed_count=10 - round(baseline_score * 10),
        error_count=0,
        result_completeness=EvaluationResultCompleteness.COMPLETE,
    )
    candidate_error_count = (
        0
        if candidate_completeness == EvaluationResultCompleteness.COMPLETE
        else 1
    )
    candidate_scored_count = 10 - candidate_error_count
    candidate_passed_count = min(
        round(candidate_score * candidate_scored_count),
        candidate_scored_count,
    )
    candidate_evaluation = Evaluation(
        public_id=f"eval_candidate_{suffix}",
        namespace_id=namespace.id,
        experiment_id=candidate_experiment.id,
        evaluator_version_id=evaluator_version.id,
        status=(
            EvaluationStatus.COMPLETED
            if candidate_completeness
            == EvaluationResultCompleteness.COMPLETE
            else EvaluationStatus.PARTIAL
        ),
        provider_evaluation_ref=f"provider-candidate-{suffix}",
        score=candidate_score,
        total_count=10,
        processed_count=candidate_scored_count,
        scored_count=candidate_scored_count,
        passed_count=candidate_passed_count,
        failed_count=candidate_scored_count - candidate_passed_count,
        error_count=candidate_error_count,
        result_completeness=candidate_completeness,
    )
    db.add_all([baseline_evaluation, candidate_evaluation])
    await db.flush()

    baseline_manifest = EvaluationResultManifest(
        public_id=f"erm_baseline_{suffix}",
        namespace_id=namespace.id,
        evaluation_id=baseline_evaluation.id,
        version=1,
        provider=EvaluationProvider.LANGFUSE,
        provider_dataset_ref=dataset.provider_dataset_ref,
        provider_experiment_ref=baseline_evaluation.provider_evaluation_ref,
        schema_name="langfuse-experiment-result",
        schema_version="sdk-v4",
        content_digest="e" * 64,
        score=baseline_score,
        expected_count=10,
        processed_count=10,
        scored_count=10,
        passed_count=baseline_evaluation.passed_count,
        failed_count=baseline_evaluation.failed_count,
        error_count=0,
        completeness=EvaluationResultCompleteness.COMPLETE,
        loss_reason=None,
    )
    candidate_manifest = EvaluationResultManifest(
        public_id=f"erm_candidate_{suffix}",
        namespace_id=namespace.id,
        evaluation_id=candidate_evaluation.id,
        version=1,
        provider=EvaluationProvider.LANGFUSE,
        provider_dataset_ref=dataset.provider_dataset_ref,
        provider_experiment_ref=candidate_evaluation.provider_evaluation_ref,
        schema_name="langfuse-experiment-result",
        schema_version="sdk-v4",
        content_digest="f" * 64,
        score=candidate_score,
        expected_count=10,
        processed_count=candidate_scored_count,
        scored_count=candidate_scored_count,
        passed_count=candidate_passed_count,
        failed_count=candidate_scored_count - candidate_passed_count,
        error_count=candidate_error_count,
        completeness=candidate_completeness,
        loss_reason=(
            None
            if candidate_completeness
            == EvaluationResultCompleteness.COMPLETE
            else "provider_item_loss"
        ),
    )
    db.add_all([baseline_manifest, candidate_manifest])
    await db.flush()
    return SimpleNamespace(
        user=user,
        namespace=namespace,
        dataset=dataset,
        dataset_version=dataset_version,
        evaluator_version=evaluator_version,
        baseline_experiment=baseline_experiment,
        candidate_experiment=candidate_experiment,
        baseline_evaluation=baseline_evaluation,
        candidate_evaluation=candidate_evaluation,
        baseline_manifest=baseline_manifest,
        candidate_manifest=candidate_manifest,
    )


async def _policy_version(db, graph, *, minimum=0.75, score_drop=0.05):
    policy = await create_regression_policy(
        db,
        request=RegressionPolicyCreate(
            namespace_id=graph.namespace.id,
            name="release-quality",
        ),
        actor=graph.user,
    )
    version = await create_regression_policy_version(
        db,
        policy=policy,
        request=RegressionPolicyVersionCreate(
            minimum_candidate_score=minimum,
            maximum_score_drop=score_drop,
            maximum_pass_rate_drop=score_drop,
        ),
        actor=graph.user,
    )
    return policy, version


def _comparison_request(graph, policy_version):
    return EvaluationComparisonCreate(
        namespace_id=graph.namespace.id,
        baseline_evaluation_public_id=(
            graph.baseline_evaluation.public_id
        ),
        baseline_manifest_public_id=graph.baseline_manifest.public_id,
        candidate_evaluation_public_id=(
            graph.candidate_evaluation.public_id
        ),
        candidate_manifest_public_id=graph.candidate_manifest.public_id,
        policy_version_public_id=policy_version.public_id,
    )


async def test_exact_comparison_passes_and_survives_evaluation_retry(
    async_session,
) -> None:
    graph = await _seed_comparison_graph(
        async_session,
        suffix="pass",
        baseline_score=0.8,
        candidate_score=0.9,
    )
    policy, version = await _policy_version(async_session, graph)
    request = _comparison_request(graph, version)
    comparison = await create_evaluation_comparison(
        async_session,
        request=request,
        idempotency_key="comparison:pass:v1",
        actor=graph.user,
    )
    replay = await create_evaluation_comparison(
        async_session,
        request=request,
        idempotency_key="comparison:pass:v1",
        actor=graph.user,
    )
    assert replay.id == comparison.id
    assert comparison.outcome == EvaluationComparisonOutcome.PASS
    assert comparison.score_delta == pytest.approx(0.1)
    assert comparison.pass_rate_delta == pytest.approx(0.1)
    assert len(comparison.reproducibility_digest) == 64
    second_version = await create_regression_policy_version(
        async_session,
        policy=policy,
        request=RegressionPolicyVersionCreate(
            minimum_candidate_score=0.8,
            maximum_score_drop=0.01,
            maximum_pass_rate_drop=0.01,
        ),
        actor=graph.user,
    )
    with pytest.raises(
        EvaluationHubConflictError,
        match="idempotency key is already bound",
    ):
        await create_evaluation_comparison(
            async_session,
            request=_comparison_request(graph, second_version),
            idempotency_key="comparison:pass:v1",
            actor=graph.user,
        )

    graph.candidate_evaluation.status = EvaluationStatus.PENDING
    graph.candidate_evaluation.provider_evaluation_ref = None
    graph.candidate_evaluation.score = None
    graph.candidate_evaluation.total_count = 0
    graph.candidate_evaluation.processed_count = 0
    graph.candidate_evaluation.scored_count = 0
    graph.candidate_evaluation.passed_count = 0
    graph.candidate_evaluation.failed_count = 0
    graph.candidate_evaluation.error_count = 0
    graph.candidate_evaluation.result_completeness = None
    await async_session.flush()

    view = await load_evaluation_comparison_view(
        async_session,
        comparison=comparison,
    )
    assert view.candidate.manifest.score == 0.9
    assert view.candidate.manifest.public_id == (
        graph.candidate_manifest.public_id
    )
    event = await async_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_public_id == comparison.public_id
        )
    )
    assert event is not None
    assert event.event_type == "EvaluationComparisonCreated"
    assert "input" not in repr(event.payload_json).lower()
    assert "output" not in repr(event.payload_json).lower()


async def test_comparison_marks_regression_and_partial_inconclusive(
    async_session,
) -> None:
    regression_graph = await _seed_comparison_graph(
        async_session,
        suffix="regression",
        baseline_score=0.9,
        candidate_score=0.7,
    )
    _policy, version = await _policy_version(
        async_session,
        regression_graph,
        minimum=0.8,
        score_drop=0.05,
    )
    regression = await create_evaluation_comparison(
        async_session,
        request=_comparison_request(regression_graph, version),
        idempotency_key="comparison:regression:v1",
        actor=regression_graph.user,
    )
    assert regression.outcome == EvaluationComparisonOutcome.REGRESSION
    assert regression.reason_code == "regression_policy_breached"
    assert regression.score_floor_breached is True
    assert regression.score_drop_breached is True
    assert regression.pass_rate_drop_breached is True

    partial_graph = await _seed_comparison_graph(
        async_session,
        suffix="partial",
        baseline_score=0.9,
        candidate_score=0.7,
        candidate_completeness=EvaluationResultCompleteness.PARTIAL,
    )
    _partial_policy, partial_version = await _policy_version(
        async_session,
        partial_graph,
    )
    partial = await create_evaluation_comparison(
        async_session,
        request=_comparison_request(partial_graph, partial_version),
        idempotency_key="comparison:partial:v1",
        actor=partial_graph.user,
    )
    assert partial.outcome == EvaluationComparisonOutcome.INCONCLUSIVE
    assert partial.reason_code == "incomplete_result"
    assert partial.score_floor_breached is False
    assert partial.score_drop_breached is False
    assert partial.pass_rate_drop_breached is False


async def test_comparison_rejects_different_dataset_version(
    async_session,
) -> None:
    graph = await _seed_comparison_graph(
        async_session,
        suffix="mismatch",
    )
    second_version = EvaluationDatasetVersion(
        public_id="edv_mismatch_second",
        dataset_id=graph.dataset.id,
        version=2,
        content_digest="9" * 64,
        item_count=10,
        schema_name="duckdock-eval-case",
        schema_version="1",
        provider_version_ref="2026-07-31T01:00:00Z",
    )
    async_session.add(second_version)
    await async_session.flush()
    graph.candidate_experiment.dataset_version_id = (
        second_version.id
    )
    await async_session.flush()
    _policy, version = await _policy_version(async_session, graph)

    with pytest.raises(
        EvaluationHubConflictError,
        match="not comparable",
    ):
        await create_evaluation_comparison(
            async_session,
            request=_comparison_request(graph, version),
            idempotency_key="comparison:mismatch:v1",
            actor=graph.user,
        )

    latest_request = _comparison_request(graph, version).model_copy(
        update={"baseline_manifest_public_id": "latest"}
    )
    with pytest.raises(EvaluationHubNotFoundError):
        await create_evaluation_comparison(
            async_session,
            request=latest_request,
            idempotency_key="comparison:no-latest:v1",
            actor=graph.user,
        )


async def test_evaluation_comparison_api_returns_reproducible_pins(
    async_session,
) -> None:
    graph = await _seed_comparison_graph(
        async_session,
        suffix="api",
        baseline_score=0.8,
        candidate_score=0.9,
    )
    async with _client(async_session, graph.user) as client:
        policy_response = await client.post(
            "/api/v2/regression-policies",
            json={
                "namespace_id": graph.namespace.id,
                "name": "api-release-quality",
            },
        )
        assert policy_response.status_code == 201, policy_response.text
        policy = policy_response.json()
        version_response = await client.post(
            f"/api/v2/regression-policies/{policy['public_id']}/versions",
            json={
                "minimum_candidate_score": 0.75,
                "maximum_score_drop": 0.05,
                "maximum_pass_rate_drop": 0.05,
            },
        )
        assert version_response.status_code == 201, version_response.text
        version = version_response.json()
        comparison_response = await client.post(
            "/api/v2/evaluation-comparisons",
            headers={"Idempotency-Key": "comparison:api:v1"},
            json={
                "namespace_id": graph.namespace.id,
                "baseline_evaluation_public_id": (
                    graph.baseline_evaluation.public_id
                ),
                "baseline_manifest_public_id": (
                    graph.baseline_manifest.public_id
                ),
                "candidate_evaluation_public_id": (
                    graph.candidate_evaluation.public_id
                ),
                "candidate_manifest_public_id": (
                    graph.candidate_manifest.public_id
                ),
                "policy_version_public_id": version["public_id"],
            },
        )
        assert (
            comparison_response.status_code == 201
        ), comparison_response.text
        comparison = comparison_response.json()
        get_response = await client.get(
            (
                "/api/v2/evaluation-comparisons/"
                f"{comparison['public_id']}"
            )
        )
        assert get_response.status_code == 200, get_response.text
    body = get_response.json()
    assert body["outcome"] == "PASS"
    assert body["baseline"]["manifest_public_id"] == (
        graph.baseline_manifest.public_id
    )
    assert body["candidate"]["target_digest"] == "d" * 64
    assert body["policy"]["version_public_id"] == version["public_id"]
    assert len(body["reproducibility_digest"]) == 64
    serialized = repr(body).lower()
    assert "expected_output" not in serialized
    assert "'input'" not in serialized
    assert "'output'" not in serialized
