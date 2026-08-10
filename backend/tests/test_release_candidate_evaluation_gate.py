from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import select

from app.api.v2.router import api_router
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.audit import AuditLog
from app.models.control_plane import (
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
)
from app.models.deployment import AgentDeployment, AgentDeploymentStatus
from app.models.evaluation import (
    Evaluation,
    EvaluationComparison,
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
    RegressionPolicy,
    RegressionPolicyStatus,
    RegressionPolicyVersion,
)
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.outbox import OutboxEvent
from app.models.release import ReleaseCandidateReviewDecision
from app.models.user import User
from app.schemas.release import (
    ReleaseCandidateEvaluationBindingCreate,
    ReleaseCandidateEvaluationReviewCreate,
    ReleaseCandidateSelectorIn,
)
from app.services.deployment_service import (
    DeploymentImmutableError,
    update_registered_deployment,
)
from app.services.release_candidate_evaluation_service import (
    ReleaseCandidateEvaluationConflictError,
    ReleaseCandidateEvaluationStateError,
    create_release_candidate_evaluation_binding,
    create_release_candidate_evaluation_review,
    evaluate_release_candidate_gate,
)
from app.services.release_evidence_ports import ReleaseEvidenceSelector


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


async def _seed_subject(db, suffix: str):
    user = User(
        username=f"release-eval-{suffix}",
        email=f"release-eval-{suffix}@example.test",
        hashed_password="unused",
    )
    db.add(user)
    await db.flush()
    namespace = Namespace(
        name=f"release-eval-{suffix}",
        owner_id=user.id,
    )
    db.add(namespace)
    await db.flush()
    db.add(
        NamespaceMember(
            namespace_id=namespace.id,
            user_id=user.id,
            role=NamespaceRole.ADMIN,
        )
    )
    runtime = RuntimeInstance(
        namespace_id=namespace.id,
        provider=RuntimeProvider.CUSTOM,
        name=f"release-eval-runtime-{suffix}",
        deploy_type=RuntimeDeployType.PRIVATE,
    )
    db.add(runtime)
    await db.flush()
    deployment = AgentDeployment(
        public_id=f"dep_release_{suffix}",
        namespace_id=namespace.id,
        runtime_id=runtime.id,
        external_deployment_id=f"release-agent-{suffix}",
        environment="staging",
        revision=f"2026.07.31-{suffix}",
        configuration_digest="d" * 64,
        status=AgentDeploymentStatus.REGISTERED,
        created_by_user_id=user.id,
    )
    db.add(deployment)
    await db.flush()
    return user, namespace, deployment


async def _seed_comparison(
    db,
    *,
    namespace: Namespace,
    user: User,
    suffix: str,
    candidate_ref: str,
    target_digest: str,
    outcome: EvaluationComparisonOutcome,
) -> EvaluationComparison:
    dataset = EvaluationDataset(
        public_id=f"eds_release_{suffix}",
        namespace_id=namespace.id,
        name=f"release-dataset-{suffix}",
        provider=EvaluationProvider.LANGFUSE,
        provider_dataset_ref=f"provider-release-{suffix}",
    )
    evaluator = Evaluator(
        public_id=f"evr_release_{suffix}",
        namespace_id=namespace.id,
        name=f"release-evaluator-{suffix}",
        kind=EvaluatorKind.RULE,
        provider=EvaluationProvider.CUSTOM,
        status=EvaluatorStatus.ACTIVE,
    )
    db.add_all([dataset, evaluator])
    await db.flush()
    dataset_version = EvaluationDatasetVersion(
        public_id=f"edv_release_{suffix}",
        dataset_id=dataset.id,
        version=1,
        content_digest="a" * 64,
        item_count=2,
        schema_name="duckdock-eval-case",
        schema_version="1.0",
    )
    evaluator_version = EvaluatorVersion(
        public_id=f"evv_release_{suffix}",
        evaluator_id=evaluator.id,
        version=1,
        config_digest="b" * 64,
        implementation_ref="rule://release-exact",
    )
    db.add_all([dataset_version, evaluator_version])
    await db.flush()
    baseline_experiment = Experiment(
        public_id=f"exp_release_baseline_{suffix}",
        namespace_id=namespace.id,
        name=f"release-baseline-{suffix}",
        dataset_version_id=dataset_version.id,
        target_type="agent-release-candidate",
        target_ref=f"candidate:baseline:{suffix}",
        target_digest="c" * 64,
        provider=EvaluationProvider.LANGFUSE,
        status=ExperimentStatus.COMPLETED,
    )
    candidate_experiment = Experiment(
        public_id=f"exp_release_candidate_{suffix}",
        namespace_id=namespace.id,
        name=f"release-candidate-{suffix}",
        dataset_version_id=dataset_version.id,
        target_type="agent-release-candidate",
        target_ref=candidate_ref,
        target_digest=target_digest,
        provider=EvaluationProvider.LANGFUSE,
        status=ExperimentStatus.COMPLETED,
    )
    db.add_all([baseline_experiment, candidate_experiment])
    await db.flush()
    baseline_evaluation = Evaluation(
        public_id=f"eval_release_baseline_{suffix}",
        namespace_id=namespace.id,
        experiment_id=baseline_experiment.id,
        evaluator_version_id=evaluator_version.id,
        status=EvaluationStatus.COMPLETED,
        provider_evaluation_ref=f"provider-baseline-{suffix}",
        score=0.8,
        total_count=2,
        processed_count=2,
        scored_count=2,
        passed_count=2,
        failed_count=0,
        error_count=0,
        result_completeness=EvaluationResultCompleteness.COMPLETE,
    )
    candidate_score = (
        0.9 if outcome == EvaluationComparisonOutcome.PASS else 0.4
    )
    candidate_evaluation = Evaluation(
        public_id=f"eval_release_candidate_{suffix}",
        namespace_id=namespace.id,
        experiment_id=candidate_experiment.id,
        evaluator_version_id=evaluator_version.id,
        status=EvaluationStatus.COMPLETED,
        provider_evaluation_ref=f"provider-candidate-{suffix}",
        score=candidate_score,
        total_count=2,
        processed_count=2,
        scored_count=2,
        passed_count=2 if candidate_score >= 0.5 else 1,
        failed_count=0 if candidate_score >= 0.5 else 1,
        error_count=0,
        result_completeness=EvaluationResultCompleteness.COMPLETE,
    )
    db.add_all([baseline_evaluation, candidate_evaluation])
    await db.flush()
    baseline_manifest = EvaluationResultManifest(
        public_id=f"erm_release_baseline_{suffix}",
        namespace_id=namespace.id,
        evaluation_id=baseline_evaluation.id,
        version=1,
        provider=EvaluationProvider.LANGFUSE,
        provider_dataset_ref=dataset.provider_dataset_ref,
        provider_experiment_ref=baseline_evaluation.provider_evaluation_ref,
        schema_name="langfuse-experiment-result",
        schema_version="sdk-v4",
        content_digest="e" * 64,
        score=0.8,
        expected_count=2,
        processed_count=2,
        scored_count=2,
        passed_count=2,
        failed_count=0,
        error_count=0,
        completeness=EvaluationResultCompleteness.COMPLETE,
    )
    candidate_manifest = EvaluationResultManifest(
        public_id=f"erm_release_candidate_{suffix}",
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
        expected_count=2,
        processed_count=2,
        scored_count=2,
        passed_count=candidate_evaluation.passed_count,
        failed_count=candidate_evaluation.failed_count,
        error_count=0,
        completeness=EvaluationResultCompleteness.COMPLETE,
    )
    policy = RegressionPolicy(
        public_id=f"rgp_release_{suffix}",
        namespace_id=namespace.id,
        name=f"release-policy-{suffix}",
        status=RegressionPolicyStatus.ACTIVE,
    )
    db.add_all([baseline_manifest, candidate_manifest, policy])
    await db.flush()
    policy_version = RegressionPolicyVersion(
        public_id=f"rpv_release_{suffix}",
        policy_id=policy.id,
        version=1,
        content_digest="1" * 64,
        minimum_candidate_score=0.5,
        maximum_score_drop=0.1,
        maximum_pass_rate_drop=0.1,
        require_complete_results=True,
        created_by_user_id=user.id,
    )
    db.add(policy_version)
    await db.flush()
    comparison = EvaluationComparison(
        public_id=f"cmp_release_{suffix}",
        namespace_id=namespace.id,
        baseline_evaluation_id=baseline_evaluation.id,
        candidate_evaluation_id=candidate_evaluation.id,
        baseline_manifest_id=baseline_manifest.id,
        candidate_manifest_id=candidate_manifest.id,
        policy_version_id=policy_version.id,
        idempotency_key=f"release-comparison-{suffix}",
        outcome=outcome,
        reason_code=(
            "comparison_passed"
            if outcome == EvaluationComparisonOutcome.PASS
            else (
                "regression_detected"
                if outcome == EvaluationComparisonOutcome.REGRESSION
                else "incomplete_result"
            )
        ),
        baseline_score=0.8,
        candidate_score=candidate_score,
        score_delta=candidate_score - 0.8,
        baseline_pass_rate=1.0,
        candidate_pass_rate=(
            1.0 if candidate_score >= 0.5 else 0.5
        ),
        pass_rate_delta=(
            0.0 if candidate_score >= 0.5 else -0.5
        ),
        score_floor_breached=(
            outcome == EvaluationComparisonOutcome.REGRESSION
        ),
        score_drop_breached=(
            outcome == EvaluationComparisonOutcome.REGRESSION
        ),
        pass_rate_drop_breached=(
            outcome == EvaluationComparisonOutcome.REGRESSION
        ),
        schema_name="duckdock-evaluation-comparison",
        schema_version="1.0",
        reproducibility_digest=(
            "2" * 64
            if outcome == EvaluationComparisonOutcome.PASS
            else (
                "3" * 64
                if outcome == EvaluationComparisonOutcome.REGRESSION
                else "4" * 64
            )
        ),
        created_by_user_id=user.id,
    )
    db.add(comparison)
    await db.flush()
    return comparison


def _binding_request(
    namespace: Namespace,
    deployment: AgentDeployment,
    comparison: EvaluationComparison,
    candidate_ref: str,
) -> ReleaseCandidateEvaluationBindingCreate:
    return ReleaseCandidateEvaluationBindingCreate(
        namespace_id=namespace.id,
        release_candidate_ref=candidate_ref,
        deployment_public_id=deployment.public_id,
        deployment_revision=deployment.revision,
        evaluation_comparison_public_id=comparison.public_id,
    )


def test_release_review_openapi_contract_is_explicit() -> None:
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v2")
    spec = app.openapi()

    review_path = spec["paths"]["/api/v2/release-evidence/reviews"]
    assert review_path["post"]["operationId"] == (
        "reviewReleaseCandidateEvaluation"
    )
    assert review_path["get"]["operationId"] == (
        "listReleaseCandidateEvaluationReviews"
    )
    idempotency = {
        item["name"]: item
        for item in review_path["post"]["parameters"]
    }["Idempotency-Key"]
    assert idempotency["in"] == "header"
    assert idempotency["required"] is True
    request_schema = review_path["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    assert request_schema["$ref"].endswith(
        "/ReleaseCandidateEvaluationReviewCreate"
    )
    binding_schema = spec["components"]["schemas"][
        "ReleaseCandidateEvaluationBindingOut"
    ]
    assert "review" in binding_schema["properties"]
    assert (
        spec["components"]["schemas"][
            "ReleaseCandidateReviewDecision"
        ]["enum"]
        == ["APPROVED", "REJECTED"]
    )


async def test_exact_pass_binding_enforces_gate_and_freezes_deployment(
    async_session,
) -> None:
    user, namespace, deployment = await _seed_subject(
        async_session,
        "pass",
    )
    candidate_ref = "candidate:release:pass"
    comparison = await _seed_comparison(
        async_session,
        namespace=namespace,
        user=user,
        suffix="pass",
        candidate_ref=candidate_ref,
        target_digest=deployment.configuration_digest,
        outcome=EvaluationComparisonOutcome.PASS,
    )
    request = _binding_request(
        namespace,
        deployment,
        comparison,
        candidate_ref,
    )
    binding = await create_release_candidate_evaluation_binding(
        async_session,
        request=request,
        idempotency_key="release-binding-pass",
        actor=user,
    )
    replay = await create_release_candidate_evaluation_binding(
        async_session,
        request=request,
        idempotency_key="release-binding-pass",
        actor=user,
    )
    assert replay.id == binding.id

    selector = ReleaseEvidenceSelector(
        namespace_id=namespace.id,
        release_candidate_ref=candidate_ref,
        deployment_public_id=deployment.public_id,
        deployment_revision=deployment.revision,
    )
    decision = await evaluate_release_candidate_gate(
        async_session,
        selector=selector,
        actor=user,
    )
    assert decision.outcome == "PASS"
    assert decision.reason_codes == ("runtime_evaluation_passed",)
    assert [item.evidence_id for item in decision.evidence] == [
        binding.public_id
    ]
    automatic_digest = decision.decision_digest
    assert len(automatic_digest) == 64

    review_request = ReleaseCandidateEvaluationReviewCreate(
        namespace_id=namespace.id,
        binding_public_id=binding.public_id,
        decision=ReleaseCandidateReviewDecision.APPROVED,
        comment="Exact candidate evidence reviewed.",
    )
    review = await create_release_candidate_evaluation_review(
        async_session,
        request=review_request,
        idempotency_key="release-review-pass",
        actor=user,
    )
    review_replay = await create_release_candidate_evaluation_review(
        async_session,
        request=review_request,
        idempotency_key="release-review-pass",
        actor=user,
    )
    assert review_replay.id == review.id

    reviewed_decision = await evaluate_release_candidate_gate(
        async_session,
        selector=selector,
        actor=user,
    )
    assert reviewed_decision.outcome == "PASS"
    assert reviewed_decision.reason_codes == (
        "runtime_evaluation_passed",
    )
    assert [
        item.evidence_kind for item in reviewed_decision.evidence
    ] == ["evaluation_comparison", "manual_review"]
    assert reviewed_decision.decision_digest != automatic_digest

    with pytest.raises(DeploymentImmutableError, match="release"):
        await update_registered_deployment(
            async_session,
            deployment.id,
            revision="mutated",
            actor=user,
        )

    outbox = await async_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_public_id == binding.public_id
        )
    )
    assert outbox is not None
    assert outbox.event_type == "ReleaseCandidateEvaluationBound"
    assert outbox.payload_json["binding_digest"] == binding.binding_digest
    assert "prompt" not in outbox.payload_json
    review_outbox = await async_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_public_id == review.public_id
        )
    )
    assert review_outbox is not None
    assert (
        review_outbox.event_type
        == "ReleaseCandidateEvaluationReviewed"
    )
    assert review_outbox.payload_json["decision"] == "APPROVED"
    assert "comment" not in review_outbox.payload_json
    actions = set(
        (
            await async_session.scalars(
                select(AuditLog.action).where(
                    AuditLog.namespace_id == namespace.id
                )
            )
        ).all()
    )
    assert "release_candidate.evaluation_bound" in actions
    assert "release_candidate.evaluation_reviewed" in actions
    assert "release_candidate.gate_evaluated" in actions


async def test_rejected_review_is_immutable_and_blocks_gate(
    async_session,
) -> None:
    user, namespace, deployment = await _seed_subject(
        async_session,
        "review-rejected",
    )
    candidate_ref = "candidate:release:review-rejected"
    comparison = await _seed_comparison(
        async_session,
        namespace=namespace,
        user=user,
        suffix="review-rejected",
        candidate_ref=candidate_ref,
        target_digest=deployment.configuration_digest,
        outcome=EvaluationComparisonOutcome.PASS,
    )
    binding = await create_release_candidate_evaluation_binding(
        async_session,
        request=_binding_request(
            namespace,
            deployment,
            comparison,
            candidate_ref,
        ),
        idempotency_key="release-binding-review-rejected",
        actor=user,
    )
    with pytest.raises(ValidationError, match="at least 5"):
        ReleaseCandidateEvaluationReviewCreate(
            namespace_id=namespace.id,
            binding_public_id=binding.public_id,
            decision=ReleaseCandidateReviewDecision.REJECTED,
            comment="no",
        )

    review = await create_release_candidate_evaluation_review(
        async_session,
        request=ReleaseCandidateEvaluationReviewCreate(
            namespace_id=namespace.id,
            binding_public_id=binding.public_id,
            decision=ReleaseCandidateReviewDecision.REJECTED,
            comment="Candidate behavior is unsafe.",
        ),
        idempotency_key="release-review-rejected",
        actor=user,
    )
    decision = await evaluate_release_candidate_gate(
        async_session,
        selector=ReleaseEvidenceSelector(
            namespace_id=namespace.id,
            release_candidate_ref=candidate_ref,
            deployment_public_id=deployment.public_id,
            deployment_revision=deployment.revision,
        ),
        actor=user,
    )
    assert decision.outcome == "BLOCKED"
    assert decision.reason_codes == ("manual_review_rejected",)
    assert decision.evidence[-1].evidence_id == review.public_id
    assert decision.evidence[-1].verdict == "fail"

    with pytest.raises(
        ReleaseCandidateEvaluationConflictError,
        match="already has a final review",
    ):
        await create_release_candidate_evaluation_review(
            async_session,
            request=ReleaseCandidateEvaluationReviewCreate(
                namespace_id=namespace.id,
                binding_public_id=binding.public_id,
                decision=ReleaseCandidateReviewDecision.APPROVED,
                comment="Attempted override.",
            ),
            idempotency_key="release-review-override",
            actor=user,
        )


async def test_missing_regression_and_inconclusive_evidence_block(
    async_session,
) -> None:
    user, namespace, deployment = await _seed_subject(
        async_session,
        "blocked",
    )
    missing_selector = ReleaseEvidenceSelector(
        namespace_id=namespace.id,
        release_candidate_ref="candidate:release:missing",
        deployment_public_id=deployment.public_id,
        deployment_revision=deployment.revision,
    )
    missing = await evaluate_release_candidate_gate(
        async_session,
        selector=missing_selector,
        actor=user,
    )
    assert missing.outcome == "BLOCKED"
    assert missing.reason_codes == ("runtime_evaluation_missing",)

    for suffix, outcome, reason in (
        (
            "regression",
            EvaluationComparisonOutcome.REGRESSION,
            "runtime_evaluation_regression",
        ),
        (
            "inconclusive",
            EvaluationComparisonOutcome.INCONCLUSIVE,
            "runtime_evaluation_inconclusive",
        ),
    ):
        candidate_ref = f"candidate:release:{suffix}"
        comparison = await _seed_comparison(
            async_session,
            namespace=namespace,
            user=user,
            suffix=suffix,
            candidate_ref=candidate_ref,
            target_digest=deployment.configuration_digest,
            outcome=outcome,
        )
        await create_release_candidate_evaluation_binding(
            async_session,
            request=_binding_request(
                namespace,
                deployment,
                comparison,
                candidate_ref,
            ),
            idempotency_key=f"release-binding-{suffix}",
            actor=user,
        )
        decision = await evaluate_release_candidate_gate(
            async_session,
            selector=ReleaseEvidenceSelector(
                namespace_id=namespace.id,
                release_candidate_ref=candidate_ref,
                deployment_public_id=deployment.public_id,
                deployment_revision=deployment.revision,
            ),
            actor=user,
        )
        assert decision.outcome == "BLOCKED"
        assert decision.reason_codes == (reason,)


async def test_binding_rejects_target_mismatch_and_latest_alias(
    async_session,
) -> None:
    user, namespace, deployment = await _seed_subject(
        async_session,
        "mismatch",
    )
    comparison = await _seed_comparison(
        async_session,
        namespace=namespace,
        user=user,
        suffix="mismatch",
        candidate_ref="candidate:actual",
        target_digest=deployment.configuration_digest,
        outcome=EvaluationComparisonOutcome.PASS,
    )
    with pytest.raises(
        ReleaseCandidateEvaluationStateError,
        match="target_ref",
    ):
        await create_release_candidate_evaluation_binding(
            async_session,
            request=_binding_request(
                namespace,
                deployment,
                comparison,
                "candidate:different",
            ),
            idempotency_key="release-binding-mismatch",
            actor=user,
        )
    with pytest.raises(ValidationError, match="latest"):
        ReleaseCandidateSelectorIn(
            namespace_id=namespace.id,
            release_candidate_ref="latest",
            deployment_public_id=deployment.public_id,
            deployment_revision=deployment.revision,
        )


async def test_release_evidence_api_returns_reproducible_pins(
    async_session,
) -> None:
    user, namespace, deployment = await _seed_subject(
        async_session,
        "api",
    )
    candidate_ref = "candidate:release:api"
    comparison = await _seed_comparison(
        async_session,
        namespace=namespace,
        user=user,
        suffix="api",
        candidate_ref=candidate_ref,
        target_digest=deployment.configuration_digest,
        outcome=EvaluationComparisonOutcome.PASS,
    )
    body = _binding_request(
        namespace,
        deployment,
        comparison,
        candidate_ref,
    ).model_dump(mode="json")
    async with _client(async_session, user) as client:
        created = await client.post(
            "/api/v2/release-evidence/bindings",
            json=body,
            headers={"Idempotency-Key": "release-binding-api"},
        )
        assert created.status_code == 201, created.text
        binding = created.json()
        assert binding["deployment_revision"] == deployment.revision
        assert (
            binding["evaluation_comparison_public_id"]
            == comparison.public_id
        )
        assert binding["comparison_outcome"] == "PASS"
        assert len(binding["binding_digest"]) == 64
        assert binding["review"] is None

        reviewed = await client.post(
            "/api/v2/release-evidence/reviews",
            json={
                "namespace_id": namespace.id,
                "binding_public_id": binding["public_id"],
                "decision": "APPROVED",
                "comment": "Reviewed through the API.",
            },
            headers={"Idempotency-Key": "release-review-api"},
        )
        assert reviewed.status_code == 201, reviewed.text
        review = reviewed.json()
        assert review["binding_public_id"] == binding["public_id"]
        assert review["decision"] == "APPROVED"
        assert len(review["review_digest"]) == 64

        fetched = await client.get(
            "/api/v2/release-evidence/bindings/"
            f"{binding['public_id']}"
        )
        assert fetched.status_code == 200
        assert fetched.json()["review"] == review

        listed_reviews = await client.get(
            "/api/v2/release-evidence/reviews",
            params={"namespace_id": namespace.id},
        )
        assert listed_reviews.status_code == 200
        assert listed_reviews.json() == [review]

        gate = await client.post(
            "/api/v2/release-gates/evaluations",
            json={
                key: body[key]
                for key in (
                    "namespace_id",
                    "release_candidate_ref",
                    "deployment_public_id",
                    "deployment_revision",
                )
            },
        )
        assert gate.status_code == 200, gate.text
        decision = gate.json()
        assert decision["outcome"] == "PASS"
        assert decision["evidence_count"] == 2
        assert decision["evidence"][0]["evidence_id"] == binding["public_id"]
        assert decision["evidence"][1]["evidence_id"] == review["public_id"]
        assert len(decision["decision_digest"]) == 64
