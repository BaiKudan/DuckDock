from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.evaluation import (
    EvaluationSemanticClusteringOutcome,
    EvaluationSemanticClusteringPolicyVersion,
    EvaluationSemanticClusteringRun,
    EvaluationSemanticClusteringRunItem,
    EvaluationSemanticRegressionComparison,
    EvaluationSemanticRegressionOutcome,
    EvaluationSemanticRegressionPolicy,
    EvaluationSemanticRegressionPolicyStatus,
    EvaluationSemanticRegressionPolicyVersion,
)
from app.models.user import User
from app.schemas.evaluation import (
    EvaluationSemanticRegressionComparisonCreate,
    EvaluationSemanticRegressionPolicyCreate,
    EvaluationSemanticRegressionPolicyVersionCreate,
)
from app.services.audit_service import audit
from app.services.evaluation_service import (
    EvaluationHubConflictError,
    EvaluationHubNotFoundError,
    EvaluationHubStateError,
)
from app.services.outbox_event_service import (
    build_evaluation_semantic_regression_comparison_created,
    enqueue_domain_event,
)
from app.services.tenant_write_service import require_active_namespace


_SAFE_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_POLICY_SCHEMA_NAME = "duckdock-semantic-clustering-regression-policy"
_POLICY_SCHEMA_VERSION = "1.0"
_COMPARISON_SCHEMA_NAME = "duckdock-semantic-clustering-regression-comparison"
_COMPARISON_SCHEMA_VERSION = "1.0"
_FLOAT_EPSILON = 1e-12


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


def _rounded(value: float) -> float:
    return round(value, 12)


def _policy_payload(
    request: EvaluationSemanticRegressionPolicyVersionCreate,
) -> dict:
    return {
        "schema_name": _POLICY_SCHEMA_NAME,
        "schema_version": _POLICY_SCHEMA_VERSION,
        "minimum_pairwise_assignment_agreement": (request.minimum_pairwise_assignment_agreement),
        "maximum_cluster_count_change_ratio": (request.maximum_cluster_count_change_ratio),
        "maximum_mean_centroid_similarity_drop": (request.maximum_mean_centroid_similarity_drop),
        "maximum_eligible_cluster_ratio_drop": (request.maximum_eligible_cluster_ratio_drop),
        "require_exact_source_content": True,
    }


def _policy_options():
    return selectinload(EvaluationSemanticRegressionPolicy.versions)


def _run_options():
    return (
        selectinload(EvaluationSemanticClusteringRun.policy_version).selectinload(
            EvaluationSemanticClusteringPolicyVersion.policy
        ),
        selectinload(EvaluationSemanticClusteringRun.source_case_routing_run),
        selectinload(EvaluationSemanticClusteringRun.items).selectinload(
            EvaluationSemanticClusteringRunItem.source_case_routing_item
        ),
    )


def _comparison_options():
    return (
        selectinload(EvaluationSemanticRegressionComparison.policy_version).selectinload(
            EvaluationSemanticRegressionPolicyVersion.policy
        ),
        selectinload(EvaluationSemanticRegressionComparison.baseline_run).selectinload(
            EvaluationSemanticClusteringRun.policy_version
        ),
        selectinload(EvaluationSemanticRegressionComparison.candidate_run).selectinload(
            EvaluationSemanticClusteringRun.policy_version
        ),
    )


async def create_evaluation_semantic_regression_policy(
    db: AsyncSession,
    *,
    request: EvaluationSemanticRegressionPolicyCreate,
    actor: User,
) -> EvaluationSemanticRegressionPolicy:
    await require_active_namespace(db, request.namespace_id)
    existing = await db.scalar(
        select(EvaluationSemanticRegressionPolicy.id).where(
            EvaluationSemanticRegressionPolicy.namespace_id == request.namespace_id,
            EvaluationSemanticRegressionPolicy.name == request.name,
        )
    )
    if existing is not None:
        raise EvaluationHubConflictError("semantic regression policy name already exists")
    current = _utcnow_mysql_safe()
    policy = EvaluationSemanticRegressionPolicy(
        public_id=_public_id("esrp"),
        namespace_id=request.namespace_id,
        name=request.name,
        description=request.description,
        status=EvaluationSemanticRegressionPolicyStatus.ACTIVE,
        created_at=current,
        updated_at=current,
    )
    db.add(policy)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="evaluation_semantic_regression_policy.created",
        resource_type="evaluation_semantic_regression_policy",
        resource_id=policy.id,
        namespace_id=policy.namespace_id,
        details={"public_id": policy.public_id, "name": policy.name},
    )
    return await get_evaluation_semantic_regression_policy(
        db, public_id=policy.public_id
    )


async def get_evaluation_semantic_regression_policy(
    db: AsyncSession,
    *,
    public_id: str,
    namespace_id: int | None = None,
) -> EvaluationSemanticRegressionPolicy:
    query = (
        select(EvaluationSemanticRegressionPolicy)
        .options(_policy_options())
        .where(EvaluationSemanticRegressionPolicy.public_id == public_id)
    )
    if namespace_id is not None:
        query = query.where(EvaluationSemanticRegressionPolicy.namespace_id == namespace_id)
    policy = (await db.execute(query)).scalar_one_or_none()
    if policy is None:
        raise EvaluationHubNotFoundError("EvaluationSemanticRegressionPolicy not found")
    return policy


async def list_evaluation_semantic_regression_policies(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int = 100,
) -> list[EvaluationSemanticRegressionPolicy]:
    return list(
        (
            await db.scalars(
                select(EvaluationSemanticRegressionPolicy)
                .options(_policy_options())
                .where(EvaluationSemanticRegressionPolicy.namespace_id == namespace_id)
                .order_by(EvaluationSemanticRegressionPolicy.created_at.desc())
                .limit(limit)
            )
        ).all()
    )


async def create_evaluation_semantic_regression_policy_version(
    db: AsyncSession,
    *,
    policy: EvaluationSemanticRegressionPolicy,
    request: EvaluationSemanticRegressionPolicyVersionCreate,
    actor: User,
) -> EvaluationSemanticRegressionPolicyVersion:
    if policy.status != EvaluationSemanticRegressionPolicyStatus.ACTIVE:
        raise EvaluationHubStateError("semantic regression policy is not active")
    config_digest = _digest(_policy_payload(request))
    existing = await db.scalar(
        select(EvaluationSemanticRegressionPolicyVersion.id).where(
            EvaluationSemanticRegressionPolicyVersion.policy_id == policy.id,
            EvaluationSemanticRegressionPolicyVersion.config_digest == config_digest,
        )
    )
    if existing is not None:
        raise EvaluationHubConflictError("identical semantic regression policy version already exists")
    latest = await db.scalar(
        select(func.max(EvaluationSemanticRegressionPolicyVersion.version)).where(
            EvaluationSemanticRegressionPolicyVersion.policy_id == policy.id
        )
    )
    version = EvaluationSemanticRegressionPolicyVersion(
        public_id=_public_id("esrv"),
        policy_id=policy.id,
        version=int(latest or 0) + 1,
        config_digest=config_digest,
        minimum_pairwise_assignment_agreement=(request.minimum_pairwise_assignment_agreement),
        maximum_cluster_count_change_ratio=(request.maximum_cluster_count_change_ratio),
        maximum_mean_centroid_similarity_drop=(request.maximum_mean_centroid_similarity_drop),
        maximum_eligible_cluster_ratio_drop=(request.maximum_eligible_cluster_ratio_drop),
        require_exact_source_content=True,
        schema_name=_POLICY_SCHEMA_NAME,
        schema_version=_POLICY_SCHEMA_VERSION,
        created_by_user_id=actor.id,
        created_at=_utcnow_mysql_safe(),
    )
    db.add(version)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="evaluation_semantic_regression_policy_version.created",
        resource_type="evaluation_semantic_regression_policy_version",
        resource_id=version.id,
        namespace_id=policy.namespace_id,
        details={
            "public_id": version.public_id,
            "policy_public_id": policy.public_id,
            "version": version.version,
            "config_digest": version.config_digest,
            "minimum_pairwise_assignment_agreement": (version.minimum_pairwise_assignment_agreement),
            "maximum_cluster_count_change_ratio": (version.maximum_cluster_count_change_ratio),
            "maximum_mean_centroid_similarity_drop": (version.maximum_mean_centroid_similarity_drop),
            "maximum_eligible_cluster_ratio_drop": (version.maximum_eligible_cluster_ratio_drop),
            "require_exact_source_content": True,
        },
    )
    return version


async def get_evaluation_semantic_regression_policy_version(
    db: AsyncSession,
    *,
    public_id: str,
    namespace_id: int | None = None,
) -> EvaluationSemanticRegressionPolicyVersion:
    query = (
        select(EvaluationSemanticRegressionPolicyVersion)
        .options(selectinload(EvaluationSemanticRegressionPolicyVersion.policy))
        .where(EvaluationSemanticRegressionPolicyVersion.public_id == public_id)
    )
    if namespace_id is not None:
        query = query.join(EvaluationSemanticRegressionPolicy).where(
            EvaluationSemanticRegressionPolicy.namespace_id == namespace_id
        )
    version = (await db.execute(query)).scalar_one_or_none()
    if version is None:
        raise EvaluationHubNotFoundError("EvaluationSemanticRegressionPolicyVersion not found")
    return version


async def _load_run(
    db: AsyncSession,
    *,
    namespace_id: int,
    public_id: str,
) -> EvaluationSemanticClusteringRun:
    run = (
        await db.execute(
            select(EvaluationSemanticClusteringRun)
            .options(*_run_options())
            .where(
                EvaluationSemanticClusteringRun.namespace_id == namespace_id,
                EvaluationSemanticClusteringRun.public_id == public_id,
            )
        )
    ).scalar_one_or_none()
    if run is None:
        raise EvaluationHubNotFoundError("EvaluationSemanticClusteringRun not found")
    return run


def _source_key(item: EvaluationSemanticClusteringRunItem) -> tuple[str, str]:
    source = item.source_case_routing_item
    return source.source_trace_ref, source.source_observation_ref


def _pairwise_assignment_agreement(
    baseline: dict[tuple[str, str], EvaluationSemanticClusteringRunItem],
    candidate: dict[tuple[str, str], EvaluationSemanticClusteringRunItem],
) -> float:
    keys = sorted(baseline)
    agreeing = 0
    total = 0
    for left_index, left in enumerate(keys):
        for right in keys[left_index + 1 :]:
            baseline_same = baseline[left].semantic_cluster_digest == baseline[right].semantic_cluster_digest
            candidate_same = candidate[left].semantic_cluster_digest == candidate[right].semantic_cluster_digest
            agreeing += int(baseline_same == candidate_same)
            total += 1
    if total == 0:
        raise EvaluationHubStateError("semantic regression requires at least two source items")
    return _rounded(agreeing / total)


def _comparison_result(
    *,
    baseline: EvaluationSemanticClusteringRun,
    candidate: EvaluationSemanticClusteringRun,
    version: EvaluationSemanticRegressionPolicyVersion,
) -> dict:
    if baseline.source_case_routing_run_id != candidate.source_case_routing_run_id:
        raise EvaluationHubConflictError("semantic regression runs must pin the same Case Routing run")
    baseline_items = {_source_key(item): item for item in baseline.items}
    candidate_items = {_source_key(item): item for item in candidate.items}
    if baseline_items.keys() != candidate_items.keys() or len(baseline_items) < 2:
        raise EvaluationHubConflictError("semantic regression runs must contain the same source set")

    content_matches = all(
        baseline_items[key].content_digest == candidate_items[key].content_digest for key in baseline_items
    )
    baseline_mean = _rounded(sum(item.similarity_to_centroid for item in baseline_items.values()) / len(baseline_items))
    candidate_mean = _rounded(
        sum(item.similarity_to_centroid for item in candidate_items.values()) / len(candidate_items)
    )
    cluster_change = _rounded(
        abs(candidate.cluster_count - baseline.cluster_count) / max(candidate.cluster_count, baseline.cluster_count)
    )
    baseline_eligible_ratio = _rounded(baseline.eligible_cluster_count / baseline.cluster_count)
    candidate_eligible_ratio = _rounded(candidate.eligible_cluster_count / candidate.cluster_count)
    eligible_drop = _rounded(max(0.0, baseline_eligible_ratio - candidate_eligible_ratio))
    centroid_drop = _rounded(max(0.0, baseline_mean - candidate_mean))

    agreement = _pairwise_assignment_agreement(baseline_items, candidate_items) if content_matches else None
    assignment_breached = bool(
        agreement is not None and agreement < version.minimum_pairwise_assignment_agreement - _FLOAT_EPSILON
    )
    cluster_count_breached = cluster_change > version.maximum_cluster_count_change_ratio + _FLOAT_EPSILON
    eligible_drop_breached = eligible_drop > version.maximum_eligible_cluster_ratio_drop + _FLOAT_EPSILON
    centroid_drop_breached = centroid_drop > version.maximum_mean_centroid_similarity_drop + _FLOAT_EPSILON

    if baseline.outcome != EvaluationSemanticClusteringOutcome.CLUSTERED:
        outcome = EvaluationSemanticRegressionOutcome.INCONCLUSIVE
        reasons = ["baseline_not_clustered"]
        assignment_breached = False
        cluster_count_breached = False
        eligible_drop_breached = False
        centroid_drop_breached = False
    elif not content_matches:
        outcome = EvaluationSemanticRegressionOutcome.INCONCLUSIVE
        reasons = ["source_content_changed"]
        assignment_breached = False
        cluster_count_breached = False
        eligible_drop_breached = False
        centroid_drop_breached = False
    else:
        reasons = []
        if candidate.outcome != EvaluationSemanticClusteringOutcome.CLUSTERED:
            reasons.append("candidate_not_clustered")
        if assignment_breached:
            reasons.append("pairwise_assignment_agreement_below_minimum")
        if cluster_count_breached:
            reasons.append("cluster_count_change_exceeded")
        if eligible_drop_breached:
            reasons.append("eligible_cluster_ratio_drop_exceeded")
        if centroid_drop_breached:
            reasons.append("centroid_similarity_drop_exceeded")
        if reasons:
            outcome = EvaluationSemanticRegressionOutcome.DRIFTED
        else:
            outcome = EvaluationSemanticRegressionOutcome.PASS
            reasons = ["semantic_regression_passed"]

    return {
        "outcome": outcome,
        "reason_codes": reasons,
        "source_item_count": len(baseline_items),
        "pairwise_assignment_agreement": agreement,
        "baseline_cluster_count": baseline.cluster_count,
        "candidate_cluster_count": candidate.cluster_count,
        "cluster_count_change_ratio": cluster_change,
        "baseline_eligible_cluster_ratio": baseline_eligible_ratio,
        "candidate_eligible_cluster_ratio": candidate_eligible_ratio,
        "eligible_cluster_ratio_drop": eligible_drop,
        "baseline_mean_centroid_similarity": baseline_mean,
        "candidate_mean_centroid_similarity": candidate_mean,
        "mean_centroid_similarity_drop": centroid_drop,
        "assignment_agreement_breached": assignment_breached,
        "cluster_count_change_breached": cluster_count_breached,
        "eligible_cluster_ratio_drop_breached": eligible_drop_breached,
        "centroid_similarity_drop_breached": centroid_drop_breached,
    }


def _run_pin(run: EvaluationSemanticClusteringRun) -> dict:
    return {
        "run_public_id": run.public_id,
        "policy_version_public_id": run.policy_version.public_id,
        "policy_version": run.policy_version.version,
        "source_case_routing_run_public_id": run.source_case_routing_run.public_id,
        "evidence_digest": run.evidence_digest,
        "clustering_digest": run.clustering_digest,
        "outcome": run.outcome.value,
        "source_item_count": run.source_item_count,
        "cluster_count": run.cluster_count,
        "eligible_cluster_count": run.eligible_cluster_count,
    }


async def create_evaluation_semantic_regression_comparison(
    db: AsyncSession,
    *,
    request: EvaluationSemanticRegressionComparisonCreate,
    idempotency_key: str,
    actor: User,
) -> EvaluationSemanticRegressionComparison:
    if _SAFE_IDEMPOTENCY_KEY.fullmatch(idempotency_key) is None:
        raise EvaluationHubStateError("semantic regression idempotency key is invalid")
    await require_active_namespace(db, request.namespace_id)
    baseline = await _load_run(
        db,
        namespace_id=request.namespace_id,
        public_id=request.baseline_run_public_id,
    )
    candidate = await _load_run(
        db,
        namespace_id=request.namespace_id,
        public_id=request.candidate_run_public_id,
    )
    if baseline.id == candidate.id:
        raise EvaluationHubConflictError("baseline and candidate semantic runs must differ")
    version = await get_evaluation_semantic_regression_policy_version(
        db,
        public_id=request.policy_version_public_id,
        namespace_id=request.namespace_id,
    )
    if version.policy.status != EvaluationSemanticRegressionPolicyStatus.ACTIVE:
        raise EvaluationHubStateError("semantic regression policy is not active")

    exact_ids = (baseline.id, candidate.id, version.id)
    existing_by_key = await db.scalar(
        select(EvaluationSemanticRegressionComparison).where(
            EvaluationSemanticRegressionComparison.namespace_id == request.namespace_id,
            EvaluationSemanticRegressionComparison.idempotency_key == idempotency_key,
        )
    )
    if existing_by_key is not None:
        if (
            existing_by_key.baseline_run_id,
            existing_by_key.candidate_run_id,
            existing_by_key.policy_version_id,
        ) == exact_ids:
            return await get_evaluation_semantic_regression_comparison(db, public_id=existing_by_key.public_id)
        raise EvaluationHubConflictError("semantic regression idempotency key is already bound")
    existing_exact = await db.scalar(
        select(EvaluationSemanticRegressionComparison).where(
            EvaluationSemanticRegressionComparison.baseline_run_id == baseline.id,
            EvaluationSemanticRegressionComparison.candidate_run_id == candidate.id,
            EvaluationSemanticRegressionComparison.policy_version_id == version.id,
        )
    )
    if existing_exact is not None:
        return await get_evaluation_semantic_regression_comparison(db, public_id=existing_exact.public_id)

    result = _comparison_result(
        baseline=baseline,
        candidate=candidate,
        version=version,
    )
    payload = {
        "schema_name": _COMPARISON_SCHEMA_NAME,
        "schema_version": _COMPARISON_SCHEMA_VERSION,
        "namespace_id": request.namespace_id,
        "baseline": _run_pin(baseline),
        "candidate": _run_pin(candidate),
        "policy": {
            "policy_public_id": version.policy.public_id,
            "policy_version_public_id": version.public_id,
            "policy_version": version.version,
            "config_digest": version.config_digest,
            "minimum_pairwise_assignment_agreement": (version.minimum_pairwise_assignment_agreement),
            "maximum_cluster_count_change_ratio": (version.maximum_cluster_count_change_ratio),
            "maximum_mean_centroid_similarity_drop": (version.maximum_mean_centroid_similarity_drop),
            "maximum_eligible_cluster_ratio_drop": (version.maximum_eligible_cluster_ratio_drop),
            "require_exact_source_content": True,
        },
        "result": {
            key: (value.value if isinstance(value, EvaluationSemanticRegressionOutcome) else value)
            for key, value in result.items()
        },
    }
    comparison = EvaluationSemanticRegressionComparison(
        public_id=_public_id("esrc"),
        namespace_id=request.namespace_id,
        baseline_run_id=baseline.id,
        candidate_run_id=candidate.id,
        policy_version_id=version.id,
        baseline_run=baseline,
        candidate_run=candidate,
        policy_version=version,
        idempotency_key=idempotency_key,
        outcome=result["outcome"],
        reason_codes_json=result["reason_codes"],
        source_item_count=result["source_item_count"],
        pairwise_assignment_agreement=result["pairwise_assignment_agreement"],
        baseline_cluster_count=result["baseline_cluster_count"],
        candidate_cluster_count=result["candidate_cluster_count"],
        cluster_count_change_ratio=result["cluster_count_change_ratio"],
        baseline_eligible_cluster_ratio=result["baseline_eligible_cluster_ratio"],
        candidate_eligible_cluster_ratio=result["candidate_eligible_cluster_ratio"],
        eligible_cluster_ratio_drop=result["eligible_cluster_ratio_drop"],
        baseline_mean_centroid_similarity=result["baseline_mean_centroid_similarity"],
        candidate_mean_centroid_similarity=result["candidate_mean_centroid_similarity"],
        mean_centroid_similarity_drop=result["mean_centroid_similarity_drop"],
        assignment_agreement_breached=result["assignment_agreement_breached"],
        cluster_count_change_breached=result["cluster_count_change_breached"],
        eligible_cluster_ratio_drop_breached=result["eligible_cluster_ratio_drop_breached"],
        centroid_similarity_drop_breached=result["centroid_similarity_drop_breached"],
        reproducibility_digest=_digest(payload),
        schema_name=_COMPARISON_SCHEMA_NAME,
        schema_version=_COMPARISON_SCHEMA_VERSION,
        created_by_user_id=actor.id,
        created_at=_utcnow_mysql_safe(),
    )
    db.add(comparison)
    await db.flush()
    await enqueue_domain_event(
        db,
        build_evaluation_semantic_regression_comparison_created(comparison),
        guaranteed_new=True,
    )
    await audit(
        db,
        user=actor,
        action="evaluation_semantic_regression_comparison.created",
        resource_type="evaluation_semantic_regression_comparison",
        resource_id=comparison.id,
        namespace_id=comparison.namespace_id,
        details={
            "public_id": comparison.public_id,
            "baseline_run_public_id": baseline.public_id,
            "candidate_run_public_id": candidate.public_id,
            "policy_version_public_id": version.public_id,
            "outcome": comparison.outcome.value,
            "reason_codes": list(comparison.reason_codes_json),
            "source_item_count": comparison.source_item_count,
            "pairwise_assignment_agreement": (comparison.pairwise_assignment_agreement),
            "cluster_count_change_ratio": comparison.cluster_count_change_ratio,
            "eligible_cluster_ratio_drop": comparison.eligible_cluster_ratio_drop,
            "mean_centroid_similarity_drop": (comparison.mean_centroid_similarity_drop),
            "reproducibility_digest": comparison.reproducibility_digest,
        },
    )
    return await get_evaluation_semantic_regression_comparison(db, public_id=comparison.public_id)


async def get_evaluation_semantic_regression_comparison(
    db: AsyncSession,
    *,
    public_id: str,
    namespace_id: int | None = None,
) -> EvaluationSemanticRegressionComparison:
    query = (
        select(EvaluationSemanticRegressionComparison)
        .options(*_comparison_options())
        .where(EvaluationSemanticRegressionComparison.public_id == public_id)
    )
    if namespace_id is not None:
        query = query.where(EvaluationSemanticRegressionComparison.namespace_id == namespace_id)
    comparison = (await db.execute(query)).scalar_one_or_none()
    if comparison is None:
        raise EvaluationHubNotFoundError("EvaluationSemanticRegressionComparison not found")
    return comparison


async def list_evaluation_semantic_regression_comparisons(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int = 100,
) -> list[EvaluationSemanticRegressionComparison]:
    return list(
        (
            await db.scalars(
                select(EvaluationSemanticRegressionComparison)
                .options(*_comparison_options())
                .where(EvaluationSemanticRegressionComparison.namespace_id == namespace_id)
                .order_by(EvaluationSemanticRegressionComparison.created_at.desc())
                .limit(limit)
            )
        ).all()
    )
