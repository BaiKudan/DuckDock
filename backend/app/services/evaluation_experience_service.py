from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.evaluation import (
    EvaluationCaseRoutingLane,
    EvaluationCaseRoutingOutcome,
    EvaluationCaseRoutingPolicyVersion,
    EvaluationCaseRoutingRun,
    EvaluationCaseRoutingRunItem,
    EvaluationExperienceCandidate,
    EvaluationExperienceCandidateEvidence,
    EvaluationExperienceCandidateReview,
    EvaluationExperienceCandidateStatus,
    EvaluationExperienceExtractionOutcome,
    EvaluationExperienceExtractionRun,
    EvaluationFailureCategory,
    EvaluationFailureTaxonomyPolicy,
    EvaluationFailureTaxonomyPolicyStatus,
    EvaluationFailureTaxonomyPolicyVersion,
    EvaluationSemanticClusteringPolicyVersion,
    EvaluationSemanticClusteringRun,
)
from app.models.user import User
from app.schemas.evaluation import (
    EvaluationExperienceCandidateReviewCreate,
    EvaluationExperienceExtractionRunCreate,
    EvaluationFailureTaxonomyPolicyCreate,
    EvaluationFailureTaxonomyPolicyVersionCreate,
)
from app.services.audit_service import audit
from app.services.evaluation_case_routing_service import (
    get_evaluation_case_routing_policy_version,
    get_evaluation_case_routing_run,
)
from app.services.evaluation_service import (
    EvaluationHubConflictError,
    EvaluationHubNotFoundError,
    EvaluationHubStateError,
)
from app.services.evaluation_semantic_clustering_service import (
    get_evaluation_semantic_clustering_policy_version,
    get_evaluation_semantic_clustering_run,
)
from app.services.outbox_event_service import (
    build_evaluation_experience_candidate_reviewed,
    build_evaluation_experience_extraction_run_created,
    enqueue_domain_event,
)
from app.services.tenant_write_service import require_active_namespace


_POLICY_SCHEMA_NAME = "duckdock-failure-taxonomy-policy"
_POLICY_SCHEMA_VERSION = "1.0"
_RUN_SCHEMA_NAME = "duckdock-experience-extraction-run"
_RUN_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class _FailureCluster:
    cluster_digest: str
    items: tuple[EvaluationCaseRoutingRunItem, ...]
    category: EvaluationFailureCategory
    rank_digest: str
    evidence_digest: str

    @property
    def source_run_count(self) -> int:
        return len({item.source_promotion_run_id for item in self.items})


def _public_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _utcnow_mysql_safe() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _policy_options():
    return (
        selectinload(EvaluationFailureTaxonomyPolicy.versions)
        .selectinload(
            EvaluationFailureTaxonomyPolicyVersion.source_case_routing_policy_version
        )
        .selectinload(EvaluationCaseRoutingPolicyVersion.policy),
        selectinload(EvaluationFailureTaxonomyPolicy.versions)
        .selectinload(
            EvaluationFailureTaxonomyPolicyVersion.source_semantic_clustering_policy_version
        )
        .selectinload(EvaluationSemanticClusteringPolicyVersion.policy),
    )


def _version_options():
    return (
        selectinload(EvaluationFailureTaxonomyPolicyVersion.policy),
        selectinload(
            EvaluationFailureTaxonomyPolicyVersion.source_case_routing_policy_version
        ).selectinload(EvaluationCaseRoutingPolicyVersion.policy),
        selectinload(
            EvaluationFailureTaxonomyPolicyVersion.source_semantic_clustering_policy_version
        ).selectinload(EvaluationSemanticClusteringPolicyVersion.policy),
    )


def _candidate_options():
    return (
        selectinload(EvaluationExperienceCandidate.evidence_items)
        .selectinload(
            EvaluationExperienceCandidateEvidence.source_case_routing_item
        )
        .selectinload(EvaluationCaseRoutingRunItem.source_promotion_run),
        selectinload(EvaluationExperienceCandidate.review),
    )


def _run_options():
    return (
        selectinload(EvaluationExperienceExtractionRun.policy_version)
        .selectinload(EvaluationFailureTaxonomyPolicyVersion.policy),
        selectinload(EvaluationExperienceExtractionRun.policy_version)
        .selectinload(
            EvaluationFailureTaxonomyPolicyVersion.source_case_routing_policy_version
        )
        .selectinload(EvaluationCaseRoutingPolicyVersion.policy),
        selectinload(EvaluationExperienceExtractionRun.source_case_routing_run),
        selectinload(
            EvaluationExperienceExtractionRun.source_semantic_clustering_run
        ).selectinload(EvaluationSemanticClusteringRun.items),
        selectinload(EvaluationExperienceExtractionRun.candidates)
        .selectinload(EvaluationExperienceCandidate.evidence_items)
        .selectinload(
            EvaluationExperienceCandidateEvidence.source_case_routing_item
        )
        .selectinload(EvaluationCaseRoutingRunItem.source_promotion_run),
        selectinload(EvaluationExperienceExtractionRun.candidates).selectinload(
            EvaluationExperienceCandidate.review
        ),
    )


async def create_evaluation_failure_taxonomy_policy(
    db: AsyncSession,
    *,
    request: EvaluationFailureTaxonomyPolicyCreate,
    actor: User,
) -> EvaluationFailureTaxonomyPolicy:
    await require_active_namespace(db, request.namespace_id)
    existing = await db.scalar(
        select(EvaluationFailureTaxonomyPolicy.id).where(
            EvaluationFailureTaxonomyPolicy.namespace_id
            == request.namespace_id,
            EvaluationFailureTaxonomyPolicy.name == request.name,
        )
    )
    if existing is not None:
        raise EvaluationHubConflictError(
            "EvaluationFailureTaxonomyPolicy name already exists"
        )
    now = _utcnow_mysql_safe()
    policy = EvaluationFailureTaxonomyPolicy(
        public_id=_public_id("eftp"),
        namespace_id=request.namespace_id,
        name=request.name,
        description=request.description,
        status=EvaluationFailureTaxonomyPolicyStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    policy.versions = []
    db.add(policy)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="evaluation_failure_taxonomy_policy.created",
        resource_type="evaluation_failure_taxonomy_policy",
        resource_id=policy.id,
        namespace_id=policy.namespace_id,
        details={
            "public_id": policy.public_id,
            "name": policy.name,
            "status": policy.status.value,
        },
    )
    return policy


async def get_evaluation_failure_taxonomy_policy(
    db: AsyncSession,
    *,
    public_id: str,
) -> EvaluationFailureTaxonomyPolicy:
    policy = await db.scalar(
        select(EvaluationFailureTaxonomyPolicy)
        .options(*_policy_options())
        .where(EvaluationFailureTaxonomyPolicy.public_id == public_id)
    )
    if policy is None:
        raise EvaluationHubNotFoundError(
            "EvaluationFailureTaxonomyPolicy not found"
        )
    return policy


async def list_evaluation_failure_taxonomy_policies(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int = 100,
) -> list[EvaluationFailureTaxonomyPolicy]:
    rows = await db.scalars(
        select(EvaluationFailureTaxonomyPolicy)
        .options(*_policy_options())
        .where(EvaluationFailureTaxonomyPolicy.namespace_id == namespace_id)
        .order_by(EvaluationFailureTaxonomyPolicy.created_at.desc())
        .limit(limit)
    )
    return list(rows.unique())


async def create_evaluation_failure_taxonomy_policy_version(
    db: AsyncSession,
    *,
    policy: EvaluationFailureTaxonomyPolicy,
    request: EvaluationFailureTaxonomyPolicyVersionCreate,
    actor: User,
) -> EvaluationFailureTaxonomyPolicyVersion:
    await require_active_namespace(db, policy.namespace_id)
    if policy.status != EvaluationFailureTaxonomyPolicyStatus.ACTIVE:
        raise EvaluationHubStateError("failure taxonomy policy is not active")
    source_version = await get_evaluation_case_routing_policy_version(
        db,
        public_id=request.source_case_routing_policy_version_public_id,
    )
    if source_version.policy.namespace_id != policy.namespace_id:
        raise EvaluationHubStateError(
            "case routing policy version belongs to another namespace"
        )
    semantic_version = None
    if request.source_semantic_clustering_policy_version_public_id is not None:
        semantic_version = await get_evaluation_semantic_clustering_policy_version(
            db,
            public_id=(
                request.source_semantic_clustering_policy_version_public_id
            ),
        )
        if semantic_version.policy.namespace_id != policy.namespace_id:
            raise EvaluationHubStateError(
                "semantic clustering policy version belongs to another namespace"
            )
        if (
            semantic_version.source_case_routing_policy_version_id
            != source_version.id
        ):
            raise EvaluationHubStateError(
                "semantic clustering policy version does not match case routing policy version"
            )
    config_payload = {
        "source_case_routing_policy_version_public_id": source_version.public_id,
        "source_case_routing_policy_version_config_digest": source_version.config_digest,
        "source_semantic_clustering_policy_version_public_id": (
            semantic_version.public_id if semantic_version is not None else None
        ),
        "source_semantic_clustering_policy_version_config_digest": (
            semantic_version.config_digest if semantic_version is not None else None
        ),
        "min_cluster_occurrences": request.min_cluster_occurrences,
        "min_source_runs": request.min_source_runs,
        "include_isolated": request.include_isolated,
        "max_candidates": request.max_candidates,
        "schema_name": _POLICY_SCHEMA_NAME,
        "schema_version": _POLICY_SCHEMA_VERSION,
    }
    config_digest = _digest(config_payload)
    duplicate = await db.scalar(
        select(EvaluationFailureTaxonomyPolicyVersion.id).where(
            EvaluationFailureTaxonomyPolicyVersion.policy_id == policy.id,
            EvaluationFailureTaxonomyPolicyVersion.config_digest
            == config_digest,
        )
    )
    if duplicate is not None:
        raise EvaluationHubConflictError(
            "identical failure taxonomy policy version already exists"
        )
    latest = await db.scalar(
        select(func.max(EvaluationFailureTaxonomyPolicyVersion.version)).where(
            EvaluationFailureTaxonomyPolicyVersion.policy_id == policy.id
        )
    )
    version = EvaluationFailureTaxonomyPolicyVersion(
        public_id=_public_id("eftv"),
        policy_id=policy.id,
        source_case_routing_policy_version_id=source_version.id,
        source_semantic_clustering_policy_version_id=(
            semantic_version.id if semantic_version is not None else None
        ),
        version=(latest or 0) + 1,
        config_digest=config_digest,
        min_cluster_occurrences=request.min_cluster_occurrences,
        min_source_runs=request.min_source_runs,
        include_isolated=request.include_isolated,
        max_candidates=request.max_candidates,
        schema_name=_POLICY_SCHEMA_NAME,
        schema_version=_POLICY_SCHEMA_VERSION,
        created_by_user_id=actor.id,
        created_at=_utcnow_mysql_safe(),
    )
    version.policy = policy
    version.source_case_routing_policy_version = source_version
    version.source_semantic_clustering_policy_version = semantic_version
    version.runs = []
    db.add(version)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="evaluation_failure_taxonomy_policy_version.created",
        resource_type="evaluation_failure_taxonomy_policy_version",
        resource_id=version.id,
        namespace_id=policy.namespace_id,
        details={
            "public_id": version.public_id,
            "policy_public_id": policy.public_id,
            "version": version.version,
            "source_case_routing_policy_version_public_id": source_version.public_id,
            "source_semantic_clustering_policy_version_public_id": (
                semantic_version.public_id if semantic_version is not None else None
            ),
            "config_digest": version.config_digest,
            "min_cluster_occurrences": version.min_cluster_occurrences,
            "min_source_runs": version.min_source_runs,
            "include_isolated": version.include_isolated,
            "max_candidates": version.max_candidates,
        },
    )
    return version


async def get_evaluation_failure_taxonomy_policy_version(
    db: AsyncSession,
    *,
    public_id: str,
) -> EvaluationFailureTaxonomyPolicyVersion:
    version = await db.scalar(
        select(EvaluationFailureTaxonomyPolicyVersion)
        .options(*_version_options())
        .where(EvaluationFailureTaxonomyPolicyVersion.public_id == public_id)
    )
    if version is None:
        raise EvaluationHubNotFoundError(
            "EvaluationFailureTaxonomyPolicyVersion not found"
        )
    return version


def _category(
    *,
    item_count: int,
    source_run_count: int,
    version: EvaluationFailureTaxonomyPolicyVersion,
) -> EvaluationFailureCategory:
    if (
        item_count >= version.min_cluster_occurrences
        and source_run_count >= version.min_source_runs
    ):
        return EvaluationFailureCategory.CROSS_RUN_RECURRING
    if item_count >= version.min_cluster_occurrences:
        return EvaluationFailureCategory.SINGLE_RUN_RECURRING
    return EvaluationFailureCategory.ISOLATED


def _clusters(
    *,
    version: EvaluationFailureTaxonomyPolicyVersion,
    source_run: EvaluationCaseRoutingRun,
    semantic_run: EvaluationSemanticClusteringRun | None,
) -> list[_FailureCluster]:
    semantic_items = (
        {
            value.source_case_routing_item_id: value
            for value in semantic_run.items
        }
        if semantic_run is not None
        else {}
    )
    grouped: dict[str, list[EvaluationCaseRoutingRunItem]] = {}
    for item in sorted(source_run.items, key=lambda value: value.position):
        semantic_item = semantic_items.get(item.id)
        cluster_digest = (
            semantic_item.semantic_cluster_digest
            if semantic_item is not None
            else item.cluster_digest
        )
        if (
            item.selected
            and item.lane == EvaluationCaseRoutingLane.BAD_CASE
            and cluster_digest is not None
        ):
            grouped.setdefault(cluster_digest, []).append(item)
    clusters: list[_FailureCluster] = []
    for cluster_digest, mutable_items in grouped.items():
        items = tuple(mutable_items)
        source_run_count = len(
            {item.source_promotion_run_id for item in items}
        )
        category = _category(
            item_count=len(items),
            source_run_count=source_run_count,
            version=version,
        )
        evidence_payload = [
            {
                "source_case_routing_item_position": item.position,
                "source_promotion_run_public_id": item.source_promotion_run.public_id,
                "source_trace_ref": item.source_trace_ref,
                "source_observation_ref": item.source_observation_ref,
                "cluster_digest": cluster_digest,
                "content_digest": (
                    semantic_items[item.id].content_digest
                    if item.id in semantic_items
                    else None
                ),
                "embedding_digest": (
                    semantic_items[item.id].embedding_digest
                    if item.id in semantic_items
                    else None
                ),
                "reason_code": item.reason_code,
            }
            for item in items
        ]
        clusters.append(
            _FailureCluster(
                cluster_digest=cluster_digest,
                items=items,
                category=category,
                rank_digest=_digest(
                    {
                        "policy_config_digest": version.config_digest,
                        "cluster_digest": cluster_digest,
                        "category": category.value,
                    }
                ),
                evidence_digest=_digest(evidence_payload),
            )
        )
    return clusters


def _cluster_sort_key(value: _FailureCluster) -> tuple[int, int, int, str]:
    priority = {
        EvaluationFailureCategory.CROSS_RUN_RECURRING: 0,
        EvaluationFailureCategory.SINGLE_RUN_RECURRING: 1,
        EvaluationFailureCategory.ISOLATED: 2,
    }[value.category]
    return (
        priority,
        -value.source_run_count,
        -len(value.items),
        value.rank_digest,
    )


async def run_evaluation_experience_extraction(
    db: AsyncSession,
    *,
    version_public_id: str,
    request: EvaluationExperienceExtractionRunCreate,
    idempotency_key: str,
    actor: User,
) -> EvaluationExperienceExtractionRun:
    version = await get_evaluation_failure_taxonomy_policy_version(
        db, public_id=version_public_id
    )
    await require_active_namespace(db, version.policy.namespace_id)
    if version.policy.status != EvaluationFailureTaxonomyPolicyStatus.ACTIVE:
        raise EvaluationHubStateError("failure taxonomy policy is not active")
    source_run = await get_evaluation_case_routing_run(
        db, public_id=request.source_case_routing_run_public_id
    )
    if source_run.namespace_id != version.policy.namespace_id:
        raise EvaluationHubStateError(
            "case routing run belongs to another namespace"
        )
    if (
        source_run.policy_version_id
        != version.source_case_routing_policy_version_id
    ):
        raise EvaluationHubStateError(
            "case routing run does not match the pinned policy version"
        )
    if source_run.outcome != EvaluationCaseRoutingOutcome.ROUTED:
        raise EvaluationHubStateError("case routing run is not ROUTED")
    semantic_run = None
    semantic_version = version.source_semantic_clustering_policy_version
    if semantic_version is not None:
        if request.source_semantic_clustering_run_public_id is None:
            raise EvaluationHubStateError(
                "semantic clustering run is required by the taxonomy policy version"
            )
        semantic_run = await get_evaluation_semantic_clustering_run(
            db,
            public_id=request.source_semantic_clustering_run_public_id,
        )
        if semantic_run.namespace_id != version.policy.namespace_id:
            raise EvaluationHubStateError(
                "semantic clustering run belongs to another namespace"
            )
        if semantic_run.policy_version_id != semantic_version.id:
            raise EvaluationHubStateError(
                "semantic clustering run does not match the pinned policy version"
            )
        if semantic_run.source_case_routing_run_id != source_run.id:
            raise EvaluationHubStateError(
                "semantic clustering run does not match the case routing run"
            )
    elif request.source_semantic_clustering_run_public_id is not None:
        raise EvaluationHubStateError(
            "taxonomy policy version does not pin semantic clustering"
        )
    request_digest = _digest(
        {
            "policy_version_public_id": version.public_id,
            "source_case_routing_run_public_id": source_run.public_id,
            "source_semantic_clustering_run_public_id": (
                semantic_run.public_id if semantic_run is not None else None
            ),
        }
    )
    existing = await db.scalar(
        select(EvaluationExperienceExtractionRun)
        .options(*_run_options())
        .where(
            EvaluationExperienceExtractionRun.namespace_id
            == version.policy.namespace_id,
            EvaluationExperienceExtractionRun.idempotency_key
            == idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_digest != request_digest:
            raise EvaluationHubConflictError(
                "idempotency key already used for another extraction request"
            )
        return existing

    clusters = _clusters(
        version=version,
        source_run=source_run,
        semantic_run=semantic_run,
    )
    if not clusters:
        raise EvaluationHubStateError(
            "case routing run has no selected Bad Case clusters"
        )
    eligible = [
        value
        for value in clusters
        if version.include_isolated
        or value.category != EvaluationFailureCategory.ISOLATED
    ]
    eligible.sort(key=_cluster_sort_key)
    selected = eligible[: version.max_candidates]
    evidence_digest = _digest(
        {
            "source_case_routing_run_public_id": source_run.public_id,
            "source_case_routing_run_digest": source_run.routing_digest,
            "source_semantic_clustering_run_public_id": (
                semantic_run.public_id if semantic_run is not None else None
            ),
            "source_semantic_clustering_digest": (
                semantic_run.clustering_digest if semantic_run is not None else None
            ),
            "clusters": [
                {
                    "cluster_digest": value.cluster_digest,
                    "category": value.category.value,
                    "evidence_digest": value.evidence_digest,
                    "source_item_count": len(value.items),
                    "source_run_count": value.source_run_count,
                }
                for value in sorted(
                    clusters, key=lambda item: item.cluster_digest
                )
            ],
        }
    )
    outcome = (
        EvaluationExperienceExtractionOutcome.EXTRACTED
        if selected
        else EvaluationExperienceExtractionOutcome.BLOCKED
    )
    reason_codes = (
        ["experience_candidates_extracted"]
        if selected
        else ["no_eligible_failure_clusters"]
    )
    extraction_digest = _digest(
        {
            "policy_config_digest": version.config_digest,
            "evidence_digest": evidence_digest,
            "outcome": outcome.value,
            "reason_codes": reason_codes,
            "selected": [
                {
                    "cluster_digest": value.cluster_digest,
                    "category": value.category.value,
                    "rank_digest": value.rank_digest,
                    "evidence_digest": value.evidence_digest,
                }
                for value in selected
            ],
        }
    )
    duplicate = await db.scalar(
        select(EvaluationExperienceExtractionRun.id).where(
            EvaluationExperienceExtractionRun.policy_version_id == version.id,
            EvaluationExperienceExtractionRun.source_case_routing_run_id
            == source_run.id,
            EvaluationExperienceExtractionRun.evidence_digest
            == evidence_digest,
        )
    )
    if duplicate is not None:
        raise EvaluationHubConflictError(
            "identical Experience extraction already exists"
        )

    now = _utcnow_mysql_safe()
    run = EvaluationExperienceExtractionRun(
        public_id=_public_id("eer"),
        namespace_id=version.policy.namespace_id,
        policy_version_id=version.id,
        source_case_routing_run_id=source_run.id,
        source_semantic_clustering_run_id=(
            semantic_run.id if semantic_run is not None else None
        ),
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        evidence_digest=evidence_digest,
        extraction_digest=extraction_digest,
        outcome=outcome,
        reason_codes_json=reason_codes,
        source_bad_case_count=sum(len(value.items) for value in clusters),
        cluster_count=len(clusters),
        eligible_cluster_count=len(eligible),
        candidate_count=len(selected),
        schema_name=_RUN_SCHEMA_NAME,
        schema_version=_RUN_SCHEMA_VERSION,
        created_by_user_id=actor.id,
        created_at=now,
    )
    run.policy_version = version
    run.source_case_routing_run = source_run
    run.source_semantic_clustering_run = semantic_run
    run.candidates = []
    for position, value in enumerate(selected, start=1):
        candidate = EvaluationExperienceCandidate(
            public_id=_public_id("eec"),
            position=position,
            category=value.category,
            status=EvaluationExperienceCandidateStatus.PENDING_REVIEW,
            cluster_digest=value.cluster_digest,
            rank_digest=value.rank_digest,
            evidence_digest=value.evidence_digest,
            source_item_count=len(value.items),
            source_run_count=value.source_run_count,
            reason_code=(
                "semantic_failure_cluster_candidate"
                if semantic_run is not None
                else "metadata_failure_cluster_candidate"
            ),
            created_at=now,
        )
        candidate.evidence_items = [
            EvaluationExperienceCandidateEvidence(
                position=evidence_position,
                source_case_routing_item_id=item.id,
                source_case_routing_item=item,
            )
            for evidence_position, item in enumerate(value.items, start=1)
        ]
        candidate.review = None
        run.candidates.append(candidate)
    db.add(run)
    await db.flush()
    await enqueue_domain_event(
        db,
        build_evaluation_experience_extraction_run_created(run),
        guaranteed_new=True,
    )
    await audit(
        db,
        user=actor,
        action="evaluation_experience_extraction_run.created",
        resource_type="evaluation_experience_extraction_run",
        resource_id=run.id,
        namespace_id=run.namespace_id,
        details={
            "public_id": run.public_id,
            "policy_public_id": version.policy.public_id,
            "policy_version_public_id": version.public_id,
            "source_case_routing_run_public_id": source_run.public_id,
            "source_semantic_clustering_run_public_id": (
                semantic_run.public_id if semantic_run is not None else None
            ),
            "outcome": run.outcome.value,
            "reason_codes": list(run.reason_codes_json),
            "source_bad_case_count": run.source_bad_case_count,
            "cluster_count": run.cluster_count,
            "eligible_cluster_count": run.eligible_cluster_count,
            "candidate_count": run.candidate_count,
            "extraction_digest": run.extraction_digest,
        },
    )
    return run


async def get_evaluation_experience_extraction_run(
    db: AsyncSession,
    *,
    public_id: str,
) -> EvaluationExperienceExtractionRun:
    run = await db.scalar(
        select(EvaluationExperienceExtractionRun)
        .options(*_run_options())
        .where(EvaluationExperienceExtractionRun.public_id == public_id)
    )
    if run is None:
        raise EvaluationHubNotFoundError(
            "EvaluationExperienceExtractionRun not found"
        )
    return run


async def list_evaluation_experience_extraction_runs(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int = 100,
) -> list[EvaluationExperienceExtractionRun]:
    rows = await db.scalars(
        select(EvaluationExperienceExtractionRun)
        .options(*_run_options())
        .where(EvaluationExperienceExtractionRun.namespace_id == namespace_id)
        .order_by(EvaluationExperienceExtractionRun.created_at.desc())
        .limit(limit)
    )
    return list(rows.unique())


async def get_evaluation_experience_candidate(
    db: AsyncSession,
    *,
    public_id: str,
) -> EvaluationExperienceCandidate:
    candidate = await db.scalar(
        select(EvaluationExperienceCandidate)
        .options(
            *_candidate_options(),
            selectinload(EvaluationExperienceCandidate.run)
            .selectinload(EvaluationExperienceExtractionRun.policy_version)
            .selectinload(EvaluationFailureTaxonomyPolicyVersion.policy),
        )
        .where(EvaluationExperienceCandidate.public_id == public_id)
    )
    if candidate is None:
        raise EvaluationHubNotFoundError(
            "EvaluationExperienceCandidate not found"
        )
    return candidate


async def review_evaluation_experience_candidate(
    db: AsyncSession,
    *,
    public_id: str,
    request: EvaluationExperienceCandidateReviewCreate,
    actor: User,
) -> EvaluationExperienceCandidate:
    candidate = await get_evaluation_experience_candidate(
        db, public_id=public_id
    )
    namespace_id = candidate.run.policy_version.policy.namespace_id
    await require_active_namespace(db, namespace_id)
    if candidate.status != EvaluationExperienceCandidateStatus.PENDING_REVIEW:
        raise EvaluationHubConflictError(
            "Experience candidate already has a final review"
        )
    now = _utcnow_mysql_safe()
    review_digest = _digest(
        {
            "candidate_public_id": candidate.public_id,
            "candidate_evidence_digest": candidate.evidence_digest,
            "decision": request.decision.value,
            "comment_digest": (
                _digest({"comment": request.comment})
                if request.comment is not None
                else None
            ),
            "reviewed_by_user_id": actor.id,
            "created_at": now.isoformat(),
        }
    )
    review = EvaluationExperienceCandidateReview(
        public_id=_public_id("eecr"),
        candidate_id=candidate.id,
        decision=request.decision,
        comment=request.comment,
        review_digest=review_digest,
        reviewed_by_user_id=actor.id,
        created_at=now,
    )
    candidate.review = review
    candidate.status = EvaluationExperienceCandidateStatus(
        request.decision.value
    )
    db.add(review)
    await db.flush()
    await enqueue_domain_event(
        db,
        build_evaluation_experience_candidate_reviewed(candidate),
        guaranteed_new=True,
    )
    await audit(
        db,
        user=actor,
        action="evaluation_experience_candidate.reviewed",
        resource_type="evaluation_experience_candidate",
        resource_id=candidate.id,
        namespace_id=namespace_id,
        details={
            "public_id": candidate.public_id,
            "run_public_id": candidate.run.public_id,
            "decision": review.decision.value,
            "category": candidate.category.value,
            "review_digest": review.review_digest,
            "evidence_digest": candidate.evidence_digest,
        },
    )
    return candidate
