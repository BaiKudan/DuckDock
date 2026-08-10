from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters.evaluation.langfuse import (
    LangfuseOpenAICompatibleSemanticAdapter,
    LangfuseSemanticEmbeddingAdapterError,
)
from app.core.config import settings
from app.models.evaluation import (
    EvaluationCaseRoutingLane,
    EvaluationCaseRoutingOutcome,
    EvaluationCaseRoutingPolicyVersion,
    EvaluationCaseRoutingRunItem,
    EvaluationSemanticClusteringOutcome,
    EvaluationSemanticClusteringPolicy,
    EvaluationSemanticClusteringPolicyStatus,
    EvaluationSemanticClusteringPolicyVersion,
    EvaluationSemanticClusteringRun,
    EvaluationSemanticClusteringRunItem,
)
from app.models.user import User
from app.schemas.evaluation import (
    EvaluationSemanticClusteringPolicyCreate,
    EvaluationSemanticClusteringPolicyVersionCreate,
    EvaluationSemanticClusteringRunCreate,
)
from app.services.audit_service import audit
from app.services.evaluation_case_routing_service import (
    get_evaluation_case_routing_policy_version,
    get_evaluation_case_routing_run,
)
from app.services.evaluation_ports import (
    SemanticClusteringSource,
    SemanticEmbeddingEvidence,
    SemanticEmbeddingEvidencePort,
)
from app.services.evaluation_service import (
    EvaluationHubConflictError,
    EvaluationHubNotFoundError,
    EvaluationHubProviderError,
    EvaluationHubStateError,
)
from app.services.langfuse_service import langfuse_service
from app.services.outbox_event_service import (
    build_evaluation_semantic_clustering_run_created,
    enqueue_domain_event,
)
from app.services.tenant_write_service import require_active_namespace


_POLICY_SCHEMA_NAME = "duckdock-semantic-clustering-policy"
_POLICY_SCHEMA_VERSION = "1.0"
_RUN_SCHEMA_NAME = "duckdock-semantic-clustering-run"
_RUN_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class _SourceEvidence:
    item: EvaluationCaseRoutingRunItem
    evidence: SemanticEmbeddingEvidence
    vector: tuple[float, ...]


@dataclass(frozen=True)
class _ClusterReceipt:
    source: _SourceEvidence
    semantic_cluster_digest: str
    cluster_size: int
    similarity_to_centroid: float


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
        selectinload(EvaluationSemanticClusteringPolicy.versions)
        .selectinload(
            EvaluationSemanticClusteringPolicyVersion.source_case_routing_policy_version
        )
        .selectinload(EvaluationCaseRoutingPolicyVersion.policy)
    )


def _version_options():
    return (
        selectinload(EvaluationSemanticClusteringPolicyVersion.policy),
        selectinload(
            EvaluationSemanticClusteringPolicyVersion.source_case_routing_policy_version
        ).selectinload(EvaluationCaseRoutingPolicyVersion.policy),
    )


def _run_options():
    return (
        selectinload(EvaluationSemanticClusteringRun.policy_version).selectinload(
            EvaluationSemanticClusteringPolicyVersion.policy
        ),
        selectinload(EvaluationSemanticClusteringRun.policy_version)
        .selectinload(
            EvaluationSemanticClusteringPolicyVersion.source_case_routing_policy_version
        )
        .selectinload(EvaluationCaseRoutingPolicyVersion.policy),
        selectinload(EvaluationSemanticClusteringRun.source_case_routing_run),
        selectinload(EvaluationSemanticClusteringRun.items).selectinload(
            EvaluationSemanticClusteringRunItem.source_case_routing_item
        ),
    )


async def create_evaluation_semantic_clustering_policy(
    db: AsyncSession,
    *,
    request: EvaluationSemanticClusteringPolicyCreate,
    actor: User,
) -> EvaluationSemanticClusteringPolicy:
    await require_active_namespace(db, request.namespace_id)
    existing = await db.scalar(
        select(EvaluationSemanticClusteringPolicy.id).where(
            EvaluationSemanticClusteringPolicy.namespace_id == request.namespace_id,
            EvaluationSemanticClusteringPolicy.name == request.name,
        )
    )
    if existing is not None:
        raise EvaluationHubConflictError(
            "EvaluationSemanticClusteringPolicy name already exists"
        )
    now = _utcnow_mysql_safe()
    policy = EvaluationSemanticClusteringPolicy(
        public_id=_public_id("escp"),
        namespace_id=request.namespace_id,
        name=request.name,
        description=request.description,
        status=EvaluationSemanticClusteringPolicyStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    policy.versions = []
    db.add(policy)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="evaluation_semantic_clustering_policy.created",
        resource_type="evaluation_semantic_clustering_policy",
        resource_id=policy.id,
        namespace_id=policy.namespace_id,
        details={"public_id": policy.public_id, "name": policy.name},
    )
    return policy


async def get_evaluation_semantic_clustering_policy(
    db: AsyncSession, *, public_id: str
) -> EvaluationSemanticClusteringPolicy:
    policy = await db.scalar(
        select(EvaluationSemanticClusteringPolicy)
        .options(_policy_options())
        .where(EvaluationSemanticClusteringPolicy.public_id == public_id)
    )
    if policy is None:
        raise EvaluationHubNotFoundError(
            "EvaluationSemanticClusteringPolicy not found"
        )
    return policy


async def list_evaluation_semantic_clustering_policies(
    db: AsyncSession, *, namespace_id: int, limit: int = 100
) -> list[EvaluationSemanticClusteringPolicy]:
    values = await db.scalars(
        select(EvaluationSemanticClusteringPolicy)
        .options(_policy_options())
        .where(EvaluationSemanticClusteringPolicy.namespace_id == namespace_id)
        .order_by(EvaluationSemanticClusteringPolicy.created_at.desc())
        .limit(limit)
    )
    return list(values.unique())


async def create_evaluation_semantic_clustering_policy_version(
    db: AsyncSession,
    *,
    policy: EvaluationSemanticClusteringPolicy,
    request: EvaluationSemanticClusteringPolicyVersionCreate,
    actor: User,
) -> EvaluationSemanticClusteringPolicyVersion:
    await require_active_namespace(db, policy.namespace_id)
    if policy.status != EvaluationSemanticClusteringPolicyStatus.ACTIVE:
        raise EvaluationHubStateError("semantic clustering policy is not active")
    source_version = await get_evaluation_case_routing_policy_version(
        db, public_id=request.source_case_routing_policy_version_public_id
    )
    if source_version.policy.namespace_id != policy.namespace_id:
        raise EvaluationHubStateError(
            "case routing policy version belongs to another namespace"
        )
    config_payload = {
        "source_case_routing_policy_version_public_id": source_version.public_id,
        "source_case_routing_policy_version_config_digest": source_version.config_digest,
        "embedding_profile": request.embedding_profile,
        "model_ref": request.model_ref,
        "dimensions": request.dimensions,
        "similarity_threshold": request.similarity_threshold,
        "min_cluster_size": request.min_cluster_size,
        "max_items": request.max_items,
        "max_content_chars": request.max_content_chars,
        "schema_name": _POLICY_SCHEMA_NAME,
        "schema_version": _POLICY_SCHEMA_VERSION,
    }
    config_digest = _digest(config_payload)
    duplicate = await db.scalar(
        select(EvaluationSemanticClusteringPolicyVersion.id).where(
            EvaluationSemanticClusteringPolicyVersion.policy_id == policy.id,
            EvaluationSemanticClusteringPolicyVersion.config_digest == config_digest,
        )
    )
    if duplicate is not None:
        raise EvaluationHubConflictError(
            "identical semantic clustering policy version already exists"
        )
    latest = await db.scalar(
        select(func.max(EvaluationSemanticClusteringPolicyVersion.version)).where(
            EvaluationSemanticClusteringPolicyVersion.policy_id == policy.id
        )
    )
    version = EvaluationSemanticClusteringPolicyVersion(
        public_id=_public_id("escv"),
        policy_id=policy.id,
        source_case_routing_policy_version_id=source_version.id,
        version=(latest or 0) + 1,
        config_digest=config_digest,
        embedding_profile=request.embedding_profile,
        model_ref=request.model_ref,
        dimensions=request.dimensions,
        similarity_threshold=request.similarity_threshold,
        min_cluster_size=request.min_cluster_size,
        max_items=request.max_items,
        max_content_chars=request.max_content_chars,
        schema_name=_POLICY_SCHEMA_NAME,
        schema_version=_POLICY_SCHEMA_VERSION,
        created_by_user_id=actor.id,
        created_at=_utcnow_mysql_safe(),
    )
    version.policy = policy
    version.source_case_routing_policy_version = source_version
    version.runs = []
    db.add(version)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="evaluation_semantic_clustering_policy_version.created",
        resource_type="evaluation_semantic_clustering_policy_version",
        resource_id=version.id,
        namespace_id=policy.namespace_id,
        details={
            "public_id": version.public_id,
            "policy_public_id": policy.public_id,
            "version": version.version,
            "embedding_profile": version.embedding_profile,
            "model_ref": version.model_ref,
            "dimensions": version.dimensions,
            "config_digest": version.config_digest,
        },
    )
    return version


async def get_evaluation_semantic_clustering_policy_version(
    db: AsyncSession, *, public_id: str
) -> EvaluationSemanticClusteringPolicyVersion:
    version = await db.scalar(
        select(EvaluationSemanticClusteringPolicyVersion)
        .options(*_version_options())
        .where(EvaluationSemanticClusteringPolicyVersion.public_id == public_id)
    )
    if version is None:
        raise EvaluationHubNotFoundError(
            "EvaluationSemanticClusteringPolicyVersion not found"
        )
    return version


def _normalized(vector: tuple[float, ...]) -> tuple[float, ...]:
    norm = math.sqrt(sum(value * value for value in vector))
    if not math.isfinite(norm) or norm <= 0:
        raise EvaluationHubProviderError("embedding provider returned a zero vector")
    return tuple(value / norm for value in vector)


def _centroid(values: list[tuple[float, ...]]) -> tuple[float, ...]:
    mean = tuple(sum(parts) / len(values) for parts in zip(*values, strict=True))
    return _normalized(mean)


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    return max(-1.0, min(1.0, sum(a * b for a, b in zip(left, right, strict=True))))


def _cluster_sources(
    sources: list[_SourceEvidence], *, threshold: float
) -> list[list[_SourceEvidence]]:
    clusters: list[list[_SourceEvidence]] = []
    centroids: list[tuple[float, ...]] = []
    for source in sources:
        scores = [_cosine(source.vector, value) for value in centroids]
        candidates = [
            (score, -index, index)
            for index, score in enumerate(scores)
            if score >= threshold
        ]
        if not candidates:
            clusters.append([source])
            centroids.append(source.vector)
            continue
        _, _, selected_index = max(candidates)
        clusters[selected_index].append(source)
        centroids[selected_index] = _centroid(
            [value.vector for value in clusters[selected_index]]
        )
    return clusters


def _default_adapter(
    version: EvaluationSemanticClusteringPolicyVersion,
) -> SemanticEmbeddingEvidencePort:
    if not settings.SEMANTIC_EMBEDDING_ENABLED:
        raise EvaluationHubProviderError("semantic embedding provider is disabled")
    if not settings.SEMANTIC_EMBEDDING_BASE_URL:
        raise EvaluationHubProviderError("semantic embedding provider is not configured")
    if version.embedding_profile != settings.SEMANTIC_EMBEDDING_PROFILE:
        raise EvaluationHubProviderError(
            "semantic embedding profile is not available in this deployment"
        )
    client = langfuse_service.client()
    if client is None:
        raise EvaluationHubProviderError("Langfuse provider is not configured")
    try:
        return LangfuseOpenAICompatibleSemanticAdapter(
            client=client,
            embedding_base_url=settings.SEMANTIC_EMBEDDING_BASE_URL,
            api_key=settings.SEMANTIC_EMBEDDING_API_KEY,
            timeout_seconds=settings.SEMANTIC_EMBEDDING_TIMEOUT_SECONDS,
        )
    except ValueError as exc:
        raise EvaluationHubProviderError(
            "semantic embedding provider configuration is invalid"
        ) from exc


async def run_evaluation_semantic_clustering(
    db: AsyncSession,
    *,
    version_public_id: str,
    request: EvaluationSemanticClusteringRunCreate,
    idempotency_key: str,
    actor: User,
    adapter: SemanticEmbeddingEvidencePort | None = None,
    allow_duplicate_evidence: bool = False,
) -> EvaluationSemanticClusteringRun:
    version = await get_evaluation_semantic_clustering_policy_version(
        db, public_id=version_public_id
    )
    await require_active_namespace(db, version.policy.namespace_id)
    if version.policy.status != EvaluationSemanticClusteringPolicyStatus.ACTIVE:
        raise EvaluationHubStateError("semantic clustering policy is not active")
    source_run = await get_evaluation_case_routing_run(
        db, public_id=request.source_case_routing_run_public_id
    )
    if source_run.namespace_id != version.policy.namespace_id:
        raise EvaluationHubStateError("case routing run belongs to another namespace")
    if source_run.policy_version_id != version.source_case_routing_policy_version_id:
        raise EvaluationHubStateError(
            "case routing run does not match the pinned policy version"
        )
    if source_run.outcome != EvaluationCaseRoutingOutcome.ROUTED:
        raise EvaluationHubStateError("case routing run is not ROUTED")
    request_digest = _digest(
        {
            "policy_version_public_id": version.public_id,
            "source_case_routing_run_public_id": source_run.public_id,
        }
    )
    existing = await db.scalar(
        select(EvaluationSemanticClusteringRun)
        .options(*_run_options())
        .where(
            EvaluationSemanticClusteringRun.namespace_id == version.policy.namespace_id,
            EvaluationSemanticClusteringRun.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_digest != request_digest:
            raise EvaluationHubConflictError(
                "idempotency key already used for another semantic clustering request"
            )
        return existing
    source_items = [
        value
        for value in sorted(source_run.items, key=lambda item: item.position)
        if value.selected and value.lane == EvaluationCaseRoutingLane.BAD_CASE
    ][: version.max_items]
    if not source_items:
        raise EvaluationHubStateError("case routing run has no selected Bad Cases")
    port = adapter or _default_adapter(version)
    try:
        evidence_values = await port.embed_observations(
            sources=[
                SemanticClusteringSource(
                    source_trace_ref=value.source_trace_ref,
                    source_observation_ref=value.source_observation_ref,
                )
                for value in source_items
            ],
            model_ref=version.model_ref,
            dimensions=version.dimensions,
            max_content_chars=version.max_content_chars,
        )
    except LangfuseSemanticEmbeddingAdapterError as exc:
        raise EvaluationHubProviderError("semantic embedding evidence failed") from exc
    except EvaluationHubProviderError:
        raise
    except Exception as exc:
        raise EvaluationHubProviderError("semantic embedding evidence failed") from exc
    item_by_ref = {
        (value.source_trace_ref, value.source_observation_ref): value
        for value in source_items
    }
    evidence_by_ref = {
        (value.source_trace_ref, value.source_observation_ref): value
        for value in evidence_values
    }
    if len(evidence_by_ref) != len(evidence_values) or set(evidence_by_ref) != set(item_by_ref):
        raise EvaluationHubProviderError(
            "semantic embedding evidence did not match requested sources"
        )
    normalized_sources: list[_SourceEvidence] = []
    for key in sorted(item_by_ref):
        evidence = evidence_by_ref[key]
        if len(evidence.vector) != version.dimensions:
            raise EvaluationHubProviderError(
                "semantic embedding evidence dimensions do not match policy"
            )
        normalized_sources.append(
            _SourceEvidence(
                item=item_by_ref[key],
                evidence=evidence,
                vector=_normalized(evidence.vector),
            )
        )
    evidence_digest = _digest(
        [
            {
                "source_trace_ref": value.evidence.source_trace_ref,
                "source_observation_ref": value.evidence.source_observation_ref,
                "content_digest": value.evidence.content_digest,
                "embedding_digest": value.evidence.embedding_digest,
            }
            for value in normalized_sources
        ]
    )
    if not allow_duplicate_evidence:
        duplicate = await db.scalar(
            select(EvaluationSemanticClusteringRun.id).where(
                EvaluationSemanticClusteringRun.policy_version_id == version.id,
                EvaluationSemanticClusteringRun.source_case_routing_run_id == source_run.id,
                EvaluationSemanticClusteringRun.evidence_digest == evidence_digest,
            )
        )
        if duplicate is not None:
            raise EvaluationHubConflictError("identical semantic clustering run already exists")
    clusters = _cluster_sources(
        normalized_sources, threshold=version.similarity_threshold
    )
    receipts: list[_ClusterReceipt] = []
    for members in clusters:
        member_payload = [
            {
                "source_trace_ref": value.evidence.source_trace_ref,
                "source_observation_ref": value.evidence.source_observation_ref,
                "content_digest": value.evidence.content_digest,
                "embedding_digest": value.evidence.embedding_digest,
            }
            for value in sorted(
                members,
                key=lambda value: (
                    value.evidence.source_trace_ref,
                    value.evidence.source_observation_ref,
                ),
            )
        ]
        cluster_digest = _digest(
            {"policy_config_digest": version.config_digest, "members": member_payload}
        )
        centroid = _centroid([value.vector for value in members])
        for value in members:
            receipts.append(
                _ClusterReceipt(
                    source=value,
                    semantic_cluster_digest=cluster_digest,
                    cluster_size=len(members),
                    similarity_to_centroid=round(
                        _cosine(value.vector, centroid), 9
                    ),
                )
            )
    receipts.sort(
        key=lambda value: (
            value.source.evidence.source_trace_ref,
            value.source.evidence.source_observation_ref,
        )
    )
    eligible_count = sum(
        len(value) >= version.min_cluster_size for value in clusters
    )
    outcome = (
        EvaluationSemanticClusteringOutcome.CLUSTERED
        if eligible_count
        else EvaluationSemanticClusteringOutcome.BLOCKED
    )
    reason_codes = (
        ["semantic_failure_clusters_found"]
        if eligible_count
        else ["no_eligible_semantic_failure_clusters"]
    )
    clustering_digest = _digest(
        {
            "policy_config_digest": version.config_digest,
            "evidence_digest": evidence_digest,
            "outcome": outcome.value,
            "items": [
                {
                    "source_trace_ref": value.source.evidence.source_trace_ref,
                    "source_observation_ref": value.source.evidence.source_observation_ref,
                    "semantic_cluster_digest": value.semantic_cluster_digest,
                    "cluster_size": value.cluster_size,
                    "similarity_to_centroid": value.similarity_to_centroid,
                }
                for value in receipts
            ],
        }
    )
    now = _utcnow_mysql_safe()
    run = EvaluationSemanticClusteringRun(
        public_id=_public_id("escr"),
        namespace_id=version.policy.namespace_id,
        policy_version_id=version.id,
        source_case_routing_run_id=source_run.id,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        evidence_digest=evidence_digest,
        clustering_digest=clustering_digest,
        outcome=outcome,
        reason_codes_json=reason_codes,
        source_item_count=len(normalized_sources),
        cluster_count=len(clusters),
        eligible_cluster_count=eligible_count,
        schema_name=_RUN_SCHEMA_NAME,
        schema_version=_RUN_SCHEMA_VERSION,
        created_by_user_id=actor.id,
        created_at=now,
    )
    run.policy_version = version
    run.source_case_routing_run = source_run
    run.items = []
    for position, receipt in enumerate(receipts, start=1):
        source = receipt.source
        run.items.append(
            EvaluationSemanticClusteringRunItem(
                position=position,
                source_case_routing_item_id=source.item.id,
                source_case_routing_item=source.item,
                semantic_cluster_digest=receipt.semantic_cluster_digest,
                cluster_size=receipt.cluster_size,
                similarity_to_centroid=receipt.similarity_to_centroid,
                content_digest=source.evidence.content_digest,
                embedding_digest=source.evidence.embedding_digest,
            )
        )
    db.add(run)
    await db.flush()
    await enqueue_domain_event(
        db,
        build_evaluation_semantic_clustering_run_created(run),
        guaranteed_new=True,
    )
    await audit(
        db,
        user=actor,
        action="evaluation_semantic_clustering_run.created",
        resource_type="evaluation_semantic_clustering_run",
        resource_id=run.id,
        namespace_id=run.namespace_id,
        details={
            "public_id": run.public_id,
            "policy_version_public_id": version.public_id,
            "source_case_routing_run_public_id": source_run.public_id,
            "embedding_profile": version.embedding_profile,
            "model_ref": version.model_ref,
            "outcome": run.outcome.value,
            "source_item_count": run.source_item_count,
            "cluster_count": run.cluster_count,
            "eligible_cluster_count": run.eligible_cluster_count,
            "evidence_digest": run.evidence_digest,
            "clustering_digest": run.clustering_digest,
        },
    )
    return run


async def get_evaluation_semantic_clustering_run(
    db: AsyncSession, *, public_id: str
) -> EvaluationSemanticClusteringRun:
    run = await db.scalar(
        select(EvaluationSemanticClusteringRun)
        .options(*_run_options())
        .where(EvaluationSemanticClusteringRun.public_id == public_id)
    )
    if run is None:
        raise EvaluationHubNotFoundError("EvaluationSemanticClusteringRun not found")
    return run


async def list_evaluation_semantic_clustering_runs(
    db: AsyncSession, *, namespace_id: int, limit: int = 100
) -> list[EvaluationSemanticClusteringRun]:
    values = await db.scalars(
        select(EvaluationSemanticClusteringRun)
        .options(*_run_options())
        .where(EvaluationSemanticClusteringRun.namespace_id == namespace_id)
        .order_by(EvaluationSemanticClusteringRun.created_at.desc())
        .limit(limit)
    )
    return list(values.unique())
