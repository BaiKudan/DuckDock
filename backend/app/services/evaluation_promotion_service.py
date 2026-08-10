from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters.evaluation.langfuse import (
    LangfuseAnnotationQueueAdapter,
    LangfuseAnnotationQueueAdapterError,
)
from app.models.evaluation import (
    EvaluationAnnotationDispatch,
    EvaluationAnnotationDispatchStatus,
    EvaluationAnnotationProviderStatus,
    EvaluationPromotionOutcome,
    EvaluationPromotionPolicy,
    EvaluationPromotionPolicyStatus,
    EvaluationPromotionPolicyVersion,
    EvaluationPromotionRun,
    EvaluationPromotionRunItem,
)
from app.models.user import User
from app.schemas.evaluation import (
    EvaluationPromotionPolicyCreate,
    EvaluationPromotionPolicyVersionCreate,
)
from app.services.audit_service import audit
from app.services.evaluation_ports import (
    PromotionEvidenceItem,
    PromotionEvidencePort,
    PromotionEvidenceSource,
    PromotionQualityRule,
)
from app.services.evaluation_service import (
    EvaluationHubConflictError,
    EvaluationHubNotFoundError,
    EvaluationHubProviderError,
    EvaluationHubStateError,
)
from app.services.langfuse_service import langfuse_service
from app.services.outbox_event_service import (
    build_evaluation_promotion_run_created,
    enqueue_domain_event,
)
from app.services.tenant_write_service import require_active_namespace


_POLICY_SCHEMA_NAME = "duckdock-annotation-promotion-policy"
_POLICY_SCHEMA_VERSION = "1.0"
_RUN_SCHEMA_NAME = "duckdock-annotation-promotion-run"
_RUN_SCHEMA_VERSION = "1.0"


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


def _policy_options():
    return selectinload(EvaluationPromotionPolicy.versions).selectinload(
        EvaluationPromotionPolicyVersion.binding
    )


def _version_options():
    return (
        selectinload(EvaluationPromotionPolicyVersion.policy),
        selectinload(EvaluationPromotionPolicyVersion.binding),
    )


def _run_options():
    return (
        selectinload(EvaluationPromotionRun.policy_version).selectinload(
            EvaluationPromotionPolicyVersion.policy
        ),
        selectinload(EvaluationPromotionRun.policy_version).selectinload(
            EvaluationPromotionPolicyVersion.binding
        ),
        selectinload(EvaluationPromotionRun.dispatch).selectinload(
            EvaluationAnnotationDispatch.curation_batch
        ),
        selectinload(EvaluationPromotionRun.items),
    )


async def create_evaluation_promotion_policy(
    db: AsyncSession,
    *,
    request: EvaluationPromotionPolicyCreate,
    actor: User,
) -> EvaluationPromotionPolicy:
    await require_active_namespace(db, request.namespace_id)
    existing = await db.scalar(
        select(EvaluationPromotionPolicy.id).where(
            EvaluationPromotionPolicy.namespace_id == request.namespace_id,
            EvaluationPromotionPolicy.name == request.name,
        )
    )
    if existing is not None:
        raise EvaluationHubConflictError(
            "EvaluationPromotionPolicy name already exists"
        )
    now = _utcnow_mysql_safe()
    policy = EvaluationPromotionPolicy(
        public_id=_public_id("epp"),
        namespace_id=request.namespace_id,
        name=request.name,
        description=request.description,
        status=EvaluationPromotionPolicyStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    policy.versions = []
    db.add(policy)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="evaluation_promotion_policy.created",
        resource_type="evaluation_promotion_policy",
        resource_id=policy.id,
        namespace_id=policy.namespace_id,
        details={
            "public_id": policy.public_id,
            "name": policy.name,
            "status": policy.status.value,
        },
    )
    return policy


async def get_evaluation_promotion_policy(
    db: AsyncSession,
    *,
    public_id: str,
) -> EvaluationPromotionPolicy:
    policy = (
        await db.execute(
            select(EvaluationPromotionPolicy)
            .options(_policy_options())
            .where(EvaluationPromotionPolicy.public_id == public_id)
        )
    ).scalar_one_or_none()
    if policy is None:
        raise EvaluationHubNotFoundError("EvaluationPromotionPolicy not found")
    return policy


async def list_evaluation_promotion_policies(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int,
) -> list[EvaluationPromotionPolicy]:
    return list(
        (
            await db.scalars(
                select(EvaluationPromotionPolicy)
                .options(_policy_options())
                .where(EvaluationPromotionPolicy.namespace_id == namespace_id)
                .order_by(EvaluationPromotionPolicy.created_at.desc())
                .limit(limit)
            )
        ).all()
    )


def _policy_payload(
    request: EvaluationPromotionPolicyVersionCreate,
    *,
    provider_queue_ref: str,
) -> dict:
    return {
        "schema_name": _POLICY_SCHEMA_NAME,
        "schema_version": _POLICY_SCHEMA_VERSION,
        "binding_public_id": request.binding_public_id,
        "provider_queue_ref": provider_queue_ref,
        "score_config_id": request.score_config_id,
        "score_data_type": request.score_data_type.value,
        "minimum_numeric_score": request.minimum_numeric_score,
        "accepted_values": request.accepted_values,
        "diversity_dimension": request.diversity_dimension.value,
        "min_distinct_buckets": request.min_distinct_buckets,
        "require_all_completed": True,
        "require_all_quality_passed": True,
        "automatic_materialization": False,
    }


async def create_evaluation_promotion_policy_version(
    db: AsyncSession,
    *,
    policy: EvaluationPromotionPolicy,
    request: EvaluationPromotionPolicyVersionCreate,
    actor: User,
) -> EvaluationPromotionPolicyVersion:
    from app.models.evaluation import EvaluationAnnotationQueueBinding

    if policy.status != EvaluationPromotionPolicyStatus.ACTIVE:
        raise EvaluationHubStateError(
            "Retired EvaluationPromotionPolicy cannot accept versions"
        )
    binding = (
        await db.execute(
            select(EvaluationAnnotationQueueBinding).where(
                EvaluationAnnotationQueueBinding.public_id
                == request.binding_public_id
            )
        )
    ).scalar_one_or_none()
    if binding is None or binding.namespace_id != policy.namespace_id:
        raise EvaluationHubNotFoundError(
            "EvaluationAnnotationQueueBinding not found"
        )
    if request.score_config_id not in binding.score_config_ids_json:
        raise EvaluationHubStateError(
            "Promotion score config is not pinned by the queue binding"
        )
    config_digest = _digest(
        _policy_payload(
            request,
            provider_queue_ref=binding.provider_queue_ref,
        )
    )
    existing = await db.scalar(
        select(EvaluationPromotionPolicyVersion.id).where(
            EvaluationPromotionPolicyVersion.policy_id == policy.id,
            EvaluationPromotionPolicyVersion.config_digest == config_digest,
        )
    )
    if existing is not None:
        raise EvaluationHubConflictError(
            "EvaluationPromotionPolicyVersion digest already exists"
        )
    latest = await db.scalar(
        select(func.max(EvaluationPromotionPolicyVersion.version)).where(
            EvaluationPromotionPolicyVersion.policy_id == policy.id
        )
    )
    version = EvaluationPromotionPolicyVersion(
        public_id=_public_id("epv"),
        policy_id=policy.id,
        binding_id=binding.id,
        version=int(latest or 0) + 1,
        config_digest=config_digest,
        score_config_id=request.score_config_id,
        score_data_type=request.score_data_type,
        minimum_numeric_score=request.minimum_numeric_score,
        accepted_values_json=list(request.accepted_values),
        diversity_dimension=request.diversity_dimension,
        min_distinct_buckets=request.min_distinct_buckets,
        schema_name=_POLICY_SCHEMA_NAME,
        schema_version=_POLICY_SCHEMA_VERSION,
        created_by_user_id=actor.id,
        created_at=_utcnow_mysql_safe(),
    )
    version.policy = policy
    version.binding = binding
    version.runs = []
    db.add(version)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="evaluation_promotion_policy_version.created",
        resource_type="evaluation_promotion_policy_version",
        resource_id=version.id,
        namespace_id=policy.namespace_id,
        details={
            "public_id": version.public_id,
            "policy_public_id": policy.public_id,
            "binding_public_id": binding.public_id,
            "provider_queue_ref": binding.provider_queue_ref,
            "version": version.version,
            "config_digest": version.config_digest,
            "score_config_id": version.score_config_id,
            "score_data_type": version.score_data_type.value,
            "diversity_dimension": version.diversity_dimension.value,
            "min_distinct_buckets": version.min_distinct_buckets,
            "schema_name": version.schema_name,
            "schema_version": version.schema_version,
        },
    )
    return version


async def get_evaluation_promotion_policy_version(
    db: AsyncSession,
    *,
    public_id: str,
) -> EvaluationPromotionPolicyVersion:
    version = (
        await db.execute(
            select(EvaluationPromotionPolicyVersion)
            .options(*_version_options())
            .where(EvaluationPromotionPolicyVersion.public_id == public_id)
        )
    ).scalar_one_or_none()
    if version is None:
        raise EvaluationHubNotFoundError(
            "EvaluationPromotionPolicyVersion not found"
        )
    return version


async def get_evaluation_promotion_run(
    db: AsyncSession,
    *,
    public_id: str,
) -> EvaluationPromotionRun:
    run = (
        await db.execute(
            select(EvaluationPromotionRun)
            .options(*_run_options())
            .where(EvaluationPromotionRun.public_id == public_id)
        )
    ).scalar_one_or_none()
    if run is None:
        raise EvaluationHubNotFoundError("EvaluationPromotionRun not found")
    return run


async def list_evaluation_promotion_runs(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int,
) -> list[EvaluationPromotionRun]:
    return list(
        (
            await db.scalars(
                select(EvaluationPromotionRun)
                .options(*_run_options())
                .where(EvaluationPromotionRun.namespace_id == namespace_id)
                .order_by(EvaluationPromotionRun.created_at.desc())
                .limit(limit)
            )
        ).all()
    )


def _request_digest(
    *,
    version: EvaluationPromotionPolicyVersion,
    dispatch: EvaluationAnnotationDispatch,
) -> str:
    return _digest(
        {
            "schema_name": _RUN_SCHEMA_NAME,
            "schema_version": _RUN_SCHEMA_VERSION,
            "policy_version_public_id": version.public_id,
            "policy_config_digest": version.config_digest,
            "dispatch_public_id": dispatch.public_id,
            "dispatch_request_digest": dispatch.request_digest,
        }
    )


def _selected_adapter(
    adapter: PromotionEvidencePort | None,
) -> PromotionEvidencePort:
    if adapter is not None:
        return adapter
    client = langfuse_service.client()
    if client is None:
        raise EvaluationHubProviderError("Langfuse provider is not configured")
    return LangfuseAnnotationQueueAdapter(client=client)


def _validate_evidence(
    dispatch: EvaluationAnnotationDispatch,
    values: list[PromotionEvidenceItem],
) -> dict[str, PromotionEvidenceItem]:
    mapped = {value.source_observation_ref: value for value in values}
    expected = {
        item.source_observation_ref: item.source_trace_ref
        for item in dispatch.items
    }
    if len(mapped) != len(values) or set(mapped) != set(expected):
        raise EvaluationHubStateError(
            "Promotion evidence does not match the annotation dispatch"
        )
    if any(
        value.source_trace_ref != expected[value.source_observation_ref]
        for value in values
    ):
        raise EvaluationHubStateError(
            "Promotion evidence trace identity does not match the dispatch"
        )
    return mapped


def _evidence_digest(
    *,
    version: EvaluationPromotionPolicyVersion,
    dispatch: EvaluationAnnotationDispatch,
    evidence: dict[str, PromotionEvidenceItem],
) -> str:
    return _digest(
        {
            "schema_name": _RUN_SCHEMA_NAME,
            "schema_version": _RUN_SCHEMA_VERSION,
            "policy_config_digest": version.config_digest,
            "dispatch_request_digest": dispatch.request_digest,
            "items": [
                {
                    "position": item.position,
                    "source_trace_ref": item.source_trace_ref,
                    "source_observation_ref": item.source_observation_ref,
                    "score_present": evidence[
                        item.source_observation_ref
                    ].score_present,
                    "quality_passed": evidence[
                        item.source_observation_ref
                    ].quality_passed,
                    "diversity_bucket_present": evidence[
                        item.source_observation_ref
                    ].diversity_bucket_present,
                    "score_evidence_digest": evidence[
                        item.source_observation_ref
                    ].score_evidence_digest,
                    "diversity_bucket_digest": evidence[
                        item.source_observation_ref
                    ].diversity_bucket_digest,
                }
                for item in sorted(dispatch.items, key=lambda value: value.position)
            ],
        }
    )


async def run_evaluation_promotion_policy(
    db: AsyncSession,
    *,
    version_public_id: str,
    dispatch_public_id: str,
    idempotency_key: str,
    actor: User,
    adapter: PromotionEvidencePort | None = None,
) -> EvaluationPromotionRun:
    version = await get_evaluation_promotion_policy_version(
        db, public_id=version_public_id
    )
    dispatch = (
        await db.execute(
            select(EvaluationAnnotationDispatch)
            .options(
                selectinload(EvaluationAnnotationDispatch.binding),
                selectinload(EvaluationAnnotationDispatch.curation_batch),
                selectinload(EvaluationAnnotationDispatch.items),
            )
            .where(EvaluationAnnotationDispatch.public_id == dispatch_public_id)
        )
    ).scalar_one_or_none()
    if dispatch is None or dispatch.namespace_id != version.policy.namespace_id:
        raise EvaluationHubNotFoundError(
            "EvaluationAnnotationDispatch not found"
        )
    if dispatch.binding_id != version.binding_id:
        raise EvaluationHubStateError(
            "Promotion policy version targets another queue binding"
        )
    if (
        dispatch.status != EvaluationAnnotationDispatchStatus.SYNCED
        or dispatch.completed_count != dispatch.item_count
        or any(
            item.provider_annotation_status
            != EvaluationAnnotationProviderStatus.COMPLETED
            for item in dispatch.items
        )
    ):
        raise EvaluationHubStateError(
            "Promotion requires a fully completed annotation dispatch"
        )
    request_digest = _request_digest(version=version, dispatch=dispatch)
    existing_by_key = (
        await db.execute(
            select(EvaluationPromotionRun)
            .options(*_run_options())
            .where(
                EvaluationPromotionRun.namespace_id == dispatch.namespace_id,
                EvaluationPromotionRun.idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if existing_by_key is not None:
        if existing_by_key.request_digest == request_digest:
            return existing_by_key
        raise EvaluationHubConflictError(
            "Promotion run idempotency key is already bound"
        )

    sources = [
        PromotionEvidenceSource(
            source_trace_ref=item.source_trace_ref,
            source_observation_ref=item.source_observation_ref,
        )
        for item in sorted(dispatch.items, key=lambda value: value.position)
    ]
    try:
        values = await _selected_adapter(adapter).evaluate_promotion_evidence(
            queue_ref=dispatch.provider_queue_ref,
            quality_rule=PromotionQualityRule(
                score_config_id=version.score_config_id,
                score_data_type=version.score_data_type.value,
                minimum_numeric_score=version.minimum_numeric_score,
                accepted_values=tuple(version.accepted_values_json or []),
            ),
            diversity_dimension=version.diversity_dimension.value,
            sources=sources,
        )
    except LangfuseAnnotationQueueAdapterError as exc:
        raise EvaluationHubProviderError(str(exc)) from exc
    except EvaluationHubProviderError:
        raise
    except Exception as exc:
        raise EvaluationHubProviderError(
            "Promotion evidence provider operation failed"
        ) from exc
    evidence = _validate_evidence(dispatch, values)
    evidence_digest = _evidence_digest(
        version=version,
        dispatch=dispatch,
        evidence=evidence,
    )
    existing_evidence = (
        await db.execute(
            select(EvaluationPromotionRun)
            .options(*_run_options())
            .where(
                EvaluationPromotionRun.policy_version_id == version.id,
                EvaluationPromotionRun.dispatch_id == dispatch.id,
                EvaluationPromotionRun.evidence_digest == evidence_digest,
            )
        )
    ).scalar_one_or_none()
    if existing_evidence is not None:
        return existing_evidence

    scored_count = sum(value.score_present for value in values)
    passed_count = sum(value.quality_passed for value in values)
    bucket_digests = {
        value.diversity_bucket_digest
        for value in values
        if value.diversity_bucket_present
        and value.diversity_bucket_digest is not None
    }
    reason_codes: list[str] = []
    if scored_count != dispatch.item_count:
        reason_codes.append("missing_score")
    if passed_count != dispatch.item_count:
        reason_codes.append("quality_threshold_not_met")
    if any(not value.diversity_bucket_present for value in values):
        reason_codes.append("missing_diversity_metadata")
    if len(bucket_digests) < version.min_distinct_buckets:
        reason_codes.append("insufficient_diversity")
    outcome = (
        EvaluationPromotionOutcome.RECOMMENDED
        if not reason_codes
        else EvaluationPromotionOutcome.BLOCKED
    )
    if not reason_codes:
        reason_codes = ["promotion_criteria_met"]

    run = EvaluationPromotionRun(
        public_id=_public_id("epr"),
        namespace_id=dispatch.namespace_id,
        policy_version_id=version.id,
        dispatch_id=dispatch.id,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        evidence_digest=evidence_digest,
        outcome=outcome,
        reason_codes_json=reason_codes,
        item_count=dispatch.item_count,
        completed_count=dispatch.completed_count,
        scored_count=scored_count,
        passed_count=passed_count,
        distinct_bucket_count=len(bucket_digests),
        schema_name=_RUN_SCHEMA_NAME,
        schema_version=_RUN_SCHEMA_VERSION,
        created_by_user_id=actor.id,
        created_at=_utcnow_mysql_safe(),
    )
    run.policy_version = version
    run.dispatch = dispatch
    run.items = []
    for item in sorted(dispatch.items, key=lambda value: value.position):
        value = evidence[item.source_observation_ref]
        if not value.score_present:
            item_reason = "missing_score"
        elif not value.quality_passed:
            item_reason = "quality_threshold_not_met"
        elif not value.diversity_bucket_present:
            item_reason = "missing_diversity_metadata"
        else:
            item_reason = "eligible"
        run.items.append(
            EvaluationPromotionRunItem(
                position=item.position,
                source_trace_ref=item.source_trace_ref,
                source_observation_ref=item.source_observation_ref,
                score_present=value.score_present,
                quality_passed=value.quality_passed,
                diversity_bucket_present=value.diversity_bucket_present,
                score_evidence_digest=value.score_evidence_digest,
                diversity_bucket_digest=value.diversity_bucket_digest,
                reason_code=item_reason,
            )
        )
    db.add(run)
    await db.flush()
    await enqueue_domain_event(
        db,
        build_evaluation_promotion_run_created(run),
        guaranteed_new=True,
    )
    await audit(
        db,
        user=actor,
        action="evaluation_promotion_run.created",
        resource_type="evaluation_promotion_run",
        resource_id=run.id,
        namespace_id=run.namespace_id,
        details={
            "public_id": run.public_id,
            "policy_public_id": version.policy.public_id,
            "policy_version_public_id": version.public_id,
            "binding_public_id": version.binding.public_id,
            "dispatch_public_id": dispatch.public_id,
            "curation_batch_public_id": dispatch.curation_batch.public_id,
            "request_digest": run.request_digest,
            "evidence_digest": run.evidence_digest,
            "outcome": run.outcome.value,
            "reason_codes": list(run.reason_codes_json),
            "item_count": run.item_count,
            "completed_count": run.completed_count,
            "scored_count": run.scored_count,
            "passed_count": run.passed_count,
            "distinct_bucket_count": run.distinct_bucket_count,
            "schema_name": run.schema_name,
            "schema_version": run.schema_version,
        },
    )
    return run
