from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.evaluation import (
    Evaluation,
    EvaluationComparison,
    EvaluationComparisonOutcome,
    EvaluationDatasetVersion,
    EvaluationResultCompleteness,
    EvaluationResultManifest,
    EvaluatorVersion,
    Experiment,
    RegressionPolicy,
    RegressionPolicyStatus,
    RegressionPolicyVersion,
)
from app.models.user import User
from app.schemas.evaluation import (
    EvaluationComparisonCreate,
    RegressionPolicyCreate,
    RegressionPolicyVersionCreate,
)
from app.services.audit_service import audit
from app.services.evaluation_service import (
    EvaluationHubConflictError,
    EvaluationHubNotFoundError,
    EvaluationHubStateError,
)
from app.services.outbox_event_service import (
    build_evaluation_comparison_created,
    enqueue_domain_event,
)
from app.services.tenant_write_service import require_active_namespace


_SAFE_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_POLICY_SCHEMA_NAME = "duckdock-regression-policy"
_POLICY_SCHEMA_VERSION = "1.0"
_COMPARISON_SCHEMA_NAME = "duckdock-evaluation-comparison"
_COMPARISON_SCHEMA_VERSION = "1.0"
_FLOAT_EPSILON = 1e-12


@dataclass(frozen=True, slots=True)
class EvaluationComparisonPin:
    evaluation: Evaluation
    experiment: Experiment
    dataset_version: EvaluationDatasetVersion
    evaluator_version: EvaluatorVersion
    manifest: EvaluationResultManifest


@dataclass(frozen=True, slots=True)
class EvaluationComparisonView:
    comparison: EvaluationComparison
    baseline: EvaluationComparisonPin
    candidate: EvaluationComparisonPin
    policy: RegressionPolicy
    policy_version: RegressionPolicyVersion


def _public_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _utcnow_mysql_safe() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _digest(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _policy_payload(request: RegressionPolicyVersionCreate) -> dict:
    return {
        "schema_name": _POLICY_SCHEMA_NAME,
        "schema_version": _POLICY_SCHEMA_VERSION,
        "minimum_candidate_score": request.minimum_candidate_score,
        "maximum_score_drop": request.maximum_score_drop,
        "maximum_pass_rate_drop": request.maximum_pass_rate_drop,
        "require_complete_results": True,
    }


async def create_regression_policy(
    db: AsyncSession,
    *,
    request: RegressionPolicyCreate,
    actor: User,
) -> RegressionPolicy:
    await require_active_namespace(db, request.namespace_id)
    existing = await db.scalar(
        select(RegressionPolicy.id).where(
            RegressionPolicy.namespace_id == request.namespace_id,
            RegressionPolicy.name == request.name,
        )
    )
    if existing is not None:
        raise EvaluationHubConflictError(
            "RegressionPolicy name already exists"
        )
    current = _utcnow_mysql_safe()
    policy = RegressionPolicy(
        public_id=_public_id("rgp"),
        namespace_id=request.namespace_id,
        name=request.name,
        description=request.description,
        status=RegressionPolicyStatus.ACTIVE,
        created_at=current,
        updated_at=current,
    )
    db.add(policy)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="regression_policy.created",
        resource_type="regression_policy",
        resource_id=policy.id,
        namespace_id=policy.namespace_id,
        details={
            "public_id": policy.public_id,
            "name": policy.name,
        },
    )
    return policy


async def get_regression_policy(
    db: AsyncSession,
    *,
    public_id: str,
    namespace_id: int | None = None,
) -> RegressionPolicy:
    query = (
        select(RegressionPolicy)
        .options(selectinload(RegressionPolicy.versions))
        .where(RegressionPolicy.public_id == public_id)
    )
    if namespace_id is not None:
        query = query.where(
            RegressionPolicy.namespace_id == namespace_id
        )
    policy = (await db.execute(query)).scalar_one_or_none()
    if policy is None:
        raise EvaluationHubNotFoundError("RegressionPolicy not found")
    return policy


async def list_regression_policies(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int,
) -> list[RegressionPolicy]:
    return list(
        (
            await db.scalars(
                select(RegressionPolicy)
                .options(selectinload(RegressionPolicy.versions))
                .where(RegressionPolicy.namespace_id == namespace_id)
                .order_by(RegressionPolicy.created_at.desc())
                .limit(limit)
            )
        ).all()
    )


async def create_regression_policy_version(
    db: AsyncSession,
    *,
    policy: RegressionPolicy,
    request: RegressionPolicyVersionCreate,
    actor: User,
) -> RegressionPolicyVersion:
    if policy.status != RegressionPolicyStatus.ACTIVE:
        raise EvaluationHubStateError(
            "Retired RegressionPolicy cannot accept versions"
        )
    content_digest = _digest(_policy_payload(request))
    existing = await db.scalar(
        select(RegressionPolicyVersion.id).where(
            RegressionPolicyVersion.policy_id == policy.id,
            RegressionPolicyVersion.content_digest == content_digest,
        )
    )
    if existing is not None:
        raise EvaluationHubConflictError(
            "RegressionPolicyVersion digest already exists"
        )
    latest = await db.scalar(
        select(func.max(RegressionPolicyVersion.version)).where(
            RegressionPolicyVersion.policy_id == policy.id
        )
    )
    version = RegressionPolicyVersion(
        public_id=_public_id("rpv"),
        policy_id=policy.id,
        version=int(latest or 0) + 1,
        content_digest=content_digest,
        minimum_candidate_score=request.minimum_candidate_score,
        maximum_score_drop=request.maximum_score_drop,
        maximum_pass_rate_drop=request.maximum_pass_rate_drop,
        require_complete_results=True,
        created_by_user_id=actor.id,
        created_at=_utcnow_mysql_safe(),
    )
    db.add(version)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="regression_policy_version.created",
        resource_type="regression_policy_version",
        resource_id=version.id,
        namespace_id=policy.namespace_id,
        details={
            "public_id": version.public_id,
            "policy_public_id": policy.public_id,
            "version": version.version,
            "content_digest": version.content_digest,
            "minimum_candidate_score": version.minimum_candidate_score,
            "maximum_score_drop": version.maximum_score_drop,
            "maximum_pass_rate_drop": (
                version.maximum_pass_rate_drop
            ),
            "require_complete_results": True,
        },
    )
    return version


async def _load_pin(
    db: AsyncSession,
    *,
    namespace_id: int,
    evaluation_public_id: str,
    manifest_public_id: str,
) -> EvaluationComparisonPin:
    row = (
        await db.execute(
            select(
                Evaluation,
                Experiment,
                EvaluationDatasetVersion,
                EvaluatorVersion,
                EvaluationResultManifest,
            )
            .join(Experiment, Experiment.id == Evaluation.experiment_id)
            .join(
                EvaluationDatasetVersion,
                EvaluationDatasetVersion.id
                == Experiment.dataset_version_id,
            )
            .join(
                EvaluatorVersion,
                EvaluatorVersion.id == Evaluation.evaluator_version_id,
            )
            .join(
                EvaluationResultManifest,
                EvaluationResultManifest.evaluation_id == Evaluation.id,
            )
            .where(
                Evaluation.namespace_id == namespace_id,
                Evaluation.public_id == evaluation_public_id,
                EvaluationResultManifest.public_id
                == manifest_public_id,
            )
        )
    ).one_or_none()
    if row is None:
        raise EvaluationHubNotFoundError(
            "Pinned Evaluation result not found"
        )
    return EvaluationComparisonPin(
        evaluation=row[0],
        experiment=row[1],
        dataset_version=row[2],
        evaluator_version=row[3],
        manifest=row[4],
    )


async def _load_policy_version(
    db: AsyncSession,
    *,
    namespace_id: int,
    public_id: str,
) -> tuple[RegressionPolicy, RegressionPolicyVersion]:
    row = (
        await db.execute(
            select(RegressionPolicy, RegressionPolicyVersion)
            .join(
                RegressionPolicyVersion,
                RegressionPolicyVersion.policy_id == RegressionPolicy.id,
            )
            .where(
                RegressionPolicy.namespace_id == namespace_id,
                RegressionPolicy.status == RegressionPolicyStatus.ACTIVE,
                RegressionPolicyVersion.public_id == public_id,
            )
        )
    ).one_or_none()
    if row is None:
        raise EvaluationHubNotFoundError(
            "Active RegressionPolicyVersion not found"
        )
    return row[0], row[1]


def _validate_comparable(
    baseline: EvaluationComparisonPin,
    candidate: EvaluationComparisonPin,
) -> None:
    if baseline.evaluation.id == candidate.evaluation.id:
        raise EvaluationHubConflictError(
            "Baseline and candidate evaluations must differ"
        )
    if (
        baseline.dataset_version.id != candidate.dataset_version.id
        or baseline.evaluator_version.id != candidate.evaluator_version.id
        or baseline.experiment.target_type
        != candidate.experiment.target_type
        or baseline.manifest.provider != candidate.manifest.provider
        or baseline.manifest.provider_dataset_ref
        != candidate.manifest.provider_dataset_ref
        or baseline.manifest.schema_name
        != candidate.manifest.schema_name
        or baseline.manifest.schema_version
        != candidate.manifest.schema_version
        or baseline.manifest.expected_count
        != candidate.manifest.expected_count
    ):
        raise EvaluationHubConflictError(
            "Pinned results are not comparable"
        )


def _pass_rate(manifest: EvaluationResultManifest) -> float | None:
    if manifest.scored_count == 0:
        return None
    return manifest.passed_count / manifest.scored_count


def _comparison_result(
    *,
    baseline: EvaluationComparisonPin,
    candidate: EvaluationComparisonPin,
    policy_version: RegressionPolicyVersion,
) -> dict:
    baseline_score = baseline.manifest.score
    candidate_score = candidate.manifest.score
    baseline_pass_rate = _pass_rate(baseline.manifest)
    candidate_pass_rate = _pass_rate(candidate.manifest)
    score_delta = (
        candidate_score - baseline_score
        if baseline_score is not None and candidate_score is not None
        else None
    )
    pass_rate_delta = (
        candidate_pass_rate - baseline_pass_rate
        if (
            baseline_pass_rate is not None
            and candidate_pass_rate is not None
        )
        else None
    )

    complete = (
        baseline.manifest.completeness
        == EvaluationResultCompleteness.COMPLETE
        and candidate.manifest.completeness
        == EvaluationResultCompleteness.COMPLETE
    )
    if not complete:
        outcome = EvaluationComparisonOutcome.INCONCLUSIVE
        reason_code = "incomplete_result"
        score_floor_breached = False
        score_drop_breached = False
        pass_rate_drop_breached = False
    elif score_delta is None:
        outcome = EvaluationComparisonOutcome.INCONCLUSIVE
        reason_code = "missing_aggregate_score"
        score_floor_breached = False
        score_drop_breached = False
        pass_rate_drop_breached = False
    elif pass_rate_delta is None:
        outcome = EvaluationComparisonOutcome.INCONCLUSIVE
        reason_code = "missing_scored_items"
        score_floor_breached = False
        score_drop_breached = False
        pass_rate_drop_breached = False
    else:
        score_floor_breached = (
            policy_version.minimum_candidate_score is not None
            and candidate_score
            < policy_version.minimum_candidate_score - _FLOAT_EPSILON
        )
        score_drop_breached = (
            score_delta
            < -policy_version.maximum_score_drop - _FLOAT_EPSILON
        )
        pass_rate_drop_breached = (
            pass_rate_delta
            < -policy_version.maximum_pass_rate_drop - _FLOAT_EPSILON
        )
        if (
            score_floor_breached
            or score_drop_breached
            or pass_rate_drop_breached
        ):
            outcome = EvaluationComparisonOutcome.REGRESSION
            reason_code = "regression_policy_breached"
        else:
            outcome = EvaluationComparisonOutcome.PASS
            reason_code = "comparison_passed"

    return {
        "outcome": outcome,
        "reason_code": reason_code,
        "baseline_score": baseline_score,
        "candidate_score": candidate_score,
        "score_delta": score_delta,
        "baseline_pass_rate": baseline_pass_rate,
        "candidate_pass_rate": candidate_pass_rate,
        "pass_rate_delta": pass_rate_delta,
        "score_floor_breached": score_floor_breached,
        "score_drop_breached": score_drop_breached,
        "pass_rate_drop_breached": pass_rate_drop_breached,
    }


def _pin_payload(pin: EvaluationComparisonPin) -> dict:
    return {
        "evaluation_public_id": pin.evaluation.public_id,
        "experiment_public_id": pin.experiment.public_id,
        "manifest_public_id": pin.manifest.public_id,
        "dataset_version_public_id": pin.dataset_version.public_id,
        "dataset_content_digest": pin.dataset_version.content_digest,
        "evaluator_version_public_id": pin.evaluator_version.public_id,
        "evaluator_config_digest": pin.evaluator_version.config_digest,
        "target_type": pin.experiment.target_type,
        "target_ref": pin.experiment.target_ref,
        "target_digest": pin.experiment.target_digest,
        "provider": pin.manifest.provider.value,
        "provider_dataset_ref": pin.manifest.provider_dataset_ref,
        "provider_experiment_ref": pin.manifest.provider_experiment_ref,
        "result_content_digest": pin.manifest.content_digest,
        "result_completeness": pin.manifest.completeness.value,
        "score": pin.manifest.score,
        "scored_count": pin.manifest.scored_count,
        "passed_count": pin.manifest.passed_count,
    }


async def create_evaluation_comparison(
    db: AsyncSession,
    *,
    request: EvaluationComparisonCreate,
    idempotency_key: str,
    actor: User,
) -> EvaluationComparison:
    if _SAFE_IDEMPOTENCY_KEY.fullmatch(idempotency_key) is None:
        raise EvaluationHubStateError(
            "Comparison idempotency key is invalid"
        )
    await require_active_namespace(db, request.namespace_id)
    baseline = await _load_pin(
        db,
        namespace_id=request.namespace_id,
        evaluation_public_id=request.baseline_evaluation_public_id,
        manifest_public_id=request.baseline_manifest_public_id,
    )
    candidate = await _load_pin(
        db,
        namespace_id=request.namespace_id,
        evaluation_public_id=request.candidate_evaluation_public_id,
        manifest_public_id=request.candidate_manifest_public_id,
    )
    policy, policy_version = await _load_policy_version(
        db,
        namespace_id=request.namespace_id,
        public_id=request.policy_version_public_id,
    )
    _validate_comparable(baseline, candidate)

    existing_by_key = await db.scalar(
        select(EvaluationComparison).where(
            EvaluationComparison.namespace_id == request.namespace_id,
            EvaluationComparison.idempotency_key == idempotency_key,
        )
    )
    exact_ids = (
        baseline.manifest.id,
        candidate.manifest.id,
        policy_version.id,
    )
    if existing_by_key is not None:
        if (
            existing_by_key.baseline_manifest_id,
            existing_by_key.candidate_manifest_id,
            existing_by_key.policy_version_id,
        ) == exact_ids:
            return existing_by_key
        raise EvaluationHubConflictError(
            "Comparison idempotency key is already bound"
        )
    existing_exact = await db.scalar(
        select(EvaluationComparison).where(
            EvaluationComparison.baseline_manifest_id
            == baseline.manifest.id,
            EvaluationComparison.candidate_manifest_id
            == candidate.manifest.id,
            EvaluationComparison.policy_version_id == policy_version.id,
        )
    )
    if existing_exact is not None:
        return existing_exact

    result = _comparison_result(
        baseline=baseline,
        candidate=candidate,
        policy_version=policy_version,
    )
    comparison_payload = {
        "schema_name": _COMPARISON_SCHEMA_NAME,
        "schema_version": _COMPARISON_SCHEMA_VERSION,
        "namespace_id": request.namespace_id,
        "baseline": _pin_payload(baseline),
        "candidate": _pin_payload(candidate),
        "policy": {
            "policy_public_id": policy.public_id,
            "policy_name": policy.name,
            "version_public_id": policy_version.public_id,
            "version": policy_version.version,
            "content_digest": policy_version.content_digest,
            "minimum_candidate_score": (
                policy_version.minimum_candidate_score
            ),
            "maximum_score_drop": policy_version.maximum_score_drop,
            "maximum_pass_rate_drop": (
                policy_version.maximum_pass_rate_drop
            ),
            "require_complete_results": True,
        },
        "result": {
            key: (
                value.value
                if isinstance(value, EvaluationComparisonOutcome)
                else value
            )
            for key, value in result.items()
        },
    }
    comparison = EvaluationComparison(
        public_id=_public_id("cmp"),
        namespace_id=request.namespace_id,
        baseline_evaluation_id=baseline.evaluation.id,
        candidate_evaluation_id=candidate.evaluation.id,
        baseline_manifest_id=baseline.manifest.id,
        candidate_manifest_id=candidate.manifest.id,
        policy_version_id=policy_version.id,
        idempotency_key=idempotency_key,
        outcome=result["outcome"],
        reason_code=result["reason_code"],
        baseline_score=result["baseline_score"],
        candidate_score=result["candidate_score"],
        score_delta=result["score_delta"],
        baseline_pass_rate=result["baseline_pass_rate"],
        candidate_pass_rate=result["candidate_pass_rate"],
        pass_rate_delta=result["pass_rate_delta"],
        score_floor_breached=result["score_floor_breached"],
        score_drop_breached=result["score_drop_breached"],
        pass_rate_drop_breached=result["pass_rate_drop_breached"],
        schema_name=_COMPARISON_SCHEMA_NAME,
        schema_version=_COMPARISON_SCHEMA_VERSION,
        reproducibility_digest=_digest(comparison_payload),
        created_by_user_id=actor.id,
        created_at=_utcnow_mysql_safe(),
    )
    db.add(comparison)
    await db.flush()
    await enqueue_domain_event(
        db,
        build_evaluation_comparison_created(
            comparison,
            baseline_evaluation_public_id=baseline.evaluation.public_id,
            baseline_manifest_public_id=baseline.manifest.public_id,
            candidate_evaluation_public_id=candidate.evaluation.public_id,
            candidate_manifest_public_id=candidate.manifest.public_id,
            policy_version_public_id=policy_version.public_id,
        ),
        guaranteed_new=True,
    )
    await audit(
        db,
        user=actor,
        action="evaluation_comparison.created",
        resource_type="evaluation_comparison",
        resource_id=comparison.id,
        namespace_id=comparison.namespace_id,
        details={
            "public_id": comparison.public_id,
            "baseline_evaluation_public_id": (
                baseline.evaluation.public_id
            ),
            "baseline_manifest_public_id": baseline.manifest.public_id,
            "candidate_evaluation_public_id": (
                candidate.evaluation.public_id
            ),
            "candidate_manifest_public_id": candidate.manifest.public_id,
            "policy_version_public_id": policy_version.public_id,
            "outcome": comparison.outcome.value,
            "reason_code": comparison.reason_code,
            "score_delta": comparison.score_delta,
            "pass_rate_delta": comparison.pass_rate_delta,
            "reproducibility_digest": (
                comparison.reproducibility_digest
            ),
        },
    )
    return comparison


async def get_evaluation_comparison(
    db: AsyncSession,
    *,
    public_id: str,
    namespace_id: int | None = None,
) -> EvaluationComparison:
    query = select(EvaluationComparison).where(
        EvaluationComparison.public_id == public_id
    )
    if namespace_id is not None:
        query = query.where(
            EvaluationComparison.namespace_id == namespace_id
        )
    comparison = (await db.execute(query)).scalar_one_or_none()
    if comparison is None:
        raise EvaluationHubNotFoundError(
            "EvaluationComparison not found"
        )
    return comparison


async def list_evaluation_comparisons(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int,
) -> list[EvaluationComparison]:
    return list(
        (
            await db.scalars(
                select(EvaluationComparison)
                .where(
                    EvaluationComparison.namespace_id == namespace_id
                )
                .order_by(EvaluationComparison.created_at.desc())
                .limit(limit)
            )
        ).all()
    )


async def load_evaluation_comparison_view(
    db: AsyncSession,
    *,
    comparison: EvaluationComparison,
) -> EvaluationComparisonView:
    baseline_evaluation = await db.get(
        Evaluation,
        comparison.baseline_evaluation_id,
    )
    candidate_evaluation = await db.get(
        Evaluation,
        comparison.candidate_evaluation_id,
    )
    baseline_manifest = await db.get(
        EvaluationResultManifest,
        comparison.baseline_manifest_id,
    )
    candidate_manifest = await db.get(
        EvaluationResultManifest,
        comparison.candidate_manifest_id,
    )
    policy_version = await db.get(
        RegressionPolicyVersion,
        comparison.policy_version_id,
    )
    if (
        baseline_evaluation is None
        or candidate_evaluation is None
        or baseline_manifest is None
        or candidate_manifest is None
        or policy_version is None
    ):
        raise EvaluationHubStateError(
            "EvaluationComparison reference is missing"
        )
    baseline_experiment = await db.get(
        Experiment,
        baseline_evaluation.experiment_id,
    )
    candidate_experiment = await db.get(
        Experiment,
        candidate_evaluation.experiment_id,
    )
    policy = await db.get(RegressionPolicy, policy_version.policy_id)
    if (
        baseline_experiment is None
        or candidate_experiment is None
        or policy is None
    ):
        raise EvaluationHubStateError(
            "EvaluationComparison governance reference is missing"
        )
    baseline_dataset_version = await db.get(
        EvaluationDatasetVersion,
        baseline_experiment.dataset_version_id,
    )
    candidate_dataset_version = await db.get(
        EvaluationDatasetVersion,
        candidate_experiment.dataset_version_id,
    )
    baseline_evaluator_version = await db.get(
        EvaluatorVersion,
        baseline_evaluation.evaluator_version_id,
    )
    candidate_evaluator_version = await db.get(
        EvaluatorVersion,
        candidate_evaluation.evaluator_version_id,
    )
    if (
        baseline_dataset_version is None
        or candidate_dataset_version is None
        or baseline_evaluator_version is None
        or candidate_evaluator_version is None
    ):
        raise EvaluationHubStateError(
            "EvaluationComparison immutable pin is missing"
        )
    return EvaluationComparisonView(
        comparison=comparison,
        baseline=EvaluationComparisonPin(
            evaluation=baseline_evaluation,
            experiment=baseline_experiment,
            dataset_version=baseline_dataset_version,
            evaluator_version=baseline_evaluator_version,
            manifest=baseline_manifest,
        ),
        candidate=EvaluationComparisonPin(
            evaluation=candidate_evaluation,
            experiment=candidate_experiment,
            dataset_version=candidate_dataset_version,
            evaluator_version=candidate_evaluator_version,
            manifest=candidate_manifest,
        ),
        policy=policy,
        policy_version=policy_version,
    )
