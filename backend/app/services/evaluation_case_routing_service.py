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
    EvaluationCaseRoutingPolicy,
    EvaluationCaseRoutingPolicyStatus,
    EvaluationCaseRoutingPolicyVersion,
    EvaluationCaseRoutingRun,
    EvaluationCaseRoutingRunItem,
    EvaluationDataset,
    EvaluationDatasetStatus,
    EvaluationPromotionDiversityDimension,
    EvaluationPromotionPolicyVersion,
    EvaluationPromotionRun,
    EvaluationPromotionRunItem,
    EvaluationProvider,
)
from app.models.user import User
from app.schemas.evaluation import (
    EvaluationCaseRoutingPolicyCreate,
    EvaluationCaseRoutingPolicyVersionCreate,
    EvaluationCaseRoutingRunCreate,
    EvaluationDatasetCurationBatchCreate,
    EvaluationDatasetCurationItemCreate,
)
from app.services.audit_service import audit
from app.services.evaluation_curation_service import (
    create_evaluation_dataset_curation_batch,
)
from app.services.evaluation_promotion_service import (
    get_evaluation_promotion_policy_version,
)
from app.services.evaluation_service import (
    EvaluationHubConflictError,
    EvaluationHubNotFoundError,
    EvaluationHubStateError,
)
from app.services.outbox_event_service import (
    build_evaluation_case_routing_run_created,
    enqueue_domain_event,
)
from app.services.tenant_write_service import require_active_namespace


_POLICY_SCHEMA_NAME = "duckdock-case-routing-policy"
_POLICY_SCHEMA_VERSION = "1.0"
_RUN_SCHEMA_NAME = "duckdock-case-routing-run"
_RUN_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class _RoutingCandidate:
    source_run: EvaluationPromotionRun
    item: EvaluationPromotionRunItem
    lane: EvaluationCaseRoutingLane
    cluster_digest: str | None
    rank_digest: str | None
    reason_code: str

    @property
    def source_key(self) -> tuple[str, str]:
        return (self.item.source_trace_ref, self.item.source_observation_ref)


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
    return (
        selectinload(EvaluationCaseRoutingPolicy.versions)
        .selectinload(
            EvaluationCaseRoutingPolicyVersion.source_promotion_policy_version
        )
        .selectinload(EvaluationPromotionPolicyVersion.policy)
    )


def _version_options():
    return (
        selectinload(EvaluationCaseRoutingPolicyVersion.policy),
        selectinload(
            EvaluationCaseRoutingPolicyVersion.source_promotion_policy_version
        ).selectinload(EvaluationPromotionPolicyVersion.policy),
    )


def _run_options():
    return (
        selectinload(EvaluationCaseRoutingRun.policy_version).selectinload(
            EvaluationCaseRoutingPolicyVersion.policy
        ),
        selectinload(EvaluationCaseRoutingRun.policy_version)
        .selectinload(
            EvaluationCaseRoutingPolicyVersion.source_promotion_policy_version
        )
        .selectinload(EvaluationPromotionPolicyVersion.policy),
        selectinload(EvaluationCaseRoutingRun.golden_dataset),
        selectinload(EvaluationCaseRoutingRun.bad_case_dataset),
        selectinload(EvaluationCaseRoutingRun.golden_curation_batch),
        selectinload(EvaluationCaseRoutingRun.bad_case_curation_batch),
        selectinload(EvaluationCaseRoutingRun.items).selectinload(
            EvaluationCaseRoutingRunItem.source_promotion_run
        ),
    )


async def create_evaluation_case_routing_policy(
    db: AsyncSession,
    *,
    request: EvaluationCaseRoutingPolicyCreate,
    actor: User,
) -> EvaluationCaseRoutingPolicy:
    await require_active_namespace(db, request.namespace_id)
    existing = await db.scalar(
        select(EvaluationCaseRoutingPolicy.id).where(
            EvaluationCaseRoutingPolicy.namespace_id == request.namespace_id,
            EvaluationCaseRoutingPolicy.name == request.name,
        )
    )
    if existing is not None:
        raise EvaluationHubConflictError(
            "EvaluationCaseRoutingPolicy name already exists"
        )
    now = _utcnow_mysql_safe()
    policy = EvaluationCaseRoutingPolicy(
        public_id=_public_id("ecrp"),
        namespace_id=request.namespace_id,
        name=request.name,
        description=request.description,
        status=EvaluationCaseRoutingPolicyStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    policy.versions = []
    db.add(policy)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="evaluation_case_routing_policy.created",
        resource_type="evaluation_case_routing_policy",
        resource_id=policy.id,
        namespace_id=policy.namespace_id,
        details={
            "public_id": policy.public_id,
            "name": policy.name,
            "status": policy.status.value,
        },
    )
    return policy


async def get_evaluation_case_routing_policy(
    db: AsyncSession,
    *,
    public_id: str,
) -> EvaluationCaseRoutingPolicy:
    policy = (
        await db.execute(
            select(EvaluationCaseRoutingPolicy)
            .options(_policy_options())
            .where(EvaluationCaseRoutingPolicy.public_id == public_id)
        )
    ).scalar_one_or_none()
    if policy is None:
        raise EvaluationHubNotFoundError(
            "EvaluationCaseRoutingPolicy not found"
        )
    return policy


async def list_evaluation_case_routing_policies(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int,
) -> list[EvaluationCaseRoutingPolicy]:
    return list(
        (
            await db.scalars(
                select(EvaluationCaseRoutingPolicy)
                .options(_policy_options())
                .where(
                    EvaluationCaseRoutingPolicy.namespace_id == namespace_id
                )
                .order_by(EvaluationCaseRoutingPolicy.created_at.desc())
                .limit(limit)
            )
        ).all()
    )


def _policy_payload(
    request: EvaluationCaseRoutingPolicyVersionCreate,
    *,
    source_version: EvaluationPromotionPolicyVersion,
) -> dict:
    return {
        "schema_name": _POLICY_SCHEMA_NAME,
        "schema_version": _POLICY_SCHEMA_VERSION,
        "source_promotion_policy_version_public_id": source_version.public_id,
        "source_promotion_config_digest": source_version.config_digest,
        "cluster_dimension": source_version.diversity_dimension.value,
        "strategy": request.strategy.value,
        "golden_target_size": request.golden_target_size,
        "golden_min_items": request.golden_min_items,
        "bad_case_target_size": request.bad_case_target_size,
        "bad_case_min_items": request.bad_case_min_items,
        "automatic_review": False,
        "automatic_materialization": False,
    }


async def create_evaluation_case_routing_policy_version(
    db: AsyncSession,
    *,
    policy: EvaluationCaseRoutingPolicy,
    request: EvaluationCaseRoutingPolicyVersionCreate,
    actor: User,
) -> EvaluationCaseRoutingPolicyVersion:
    if policy.status != EvaluationCaseRoutingPolicyStatus.ACTIVE:
        raise EvaluationHubStateError(
            "Retired EvaluationCaseRoutingPolicy cannot accept versions"
        )
    source_version = await get_evaluation_promotion_policy_version(
        db,
        public_id=request.source_promotion_policy_version_public_id,
    )
    if source_version.policy.namespace_id != policy.namespace_id:
        raise EvaluationHubNotFoundError(
            "EvaluationPromotionPolicyVersion not found"
        )
    if (
        source_version.diversity_dimension
        == EvaluationPromotionDiversityDimension.NONE
    ):
        raise EvaluationHubStateError(
            "Case routing requires a Promotion version with a cluster dimension"
        )
    config_digest = _digest(
        _policy_payload(request, source_version=source_version)
    )
    existing = await db.scalar(
        select(EvaluationCaseRoutingPolicyVersion.id).where(
            EvaluationCaseRoutingPolicyVersion.policy_id == policy.id,
            EvaluationCaseRoutingPolicyVersion.config_digest == config_digest,
        )
    )
    if existing is not None:
        raise EvaluationHubConflictError(
            "EvaluationCaseRoutingPolicyVersion digest already exists"
        )
    latest = await db.scalar(
        select(func.max(EvaluationCaseRoutingPolicyVersion.version)).where(
            EvaluationCaseRoutingPolicyVersion.policy_id == policy.id
        )
    )
    version = EvaluationCaseRoutingPolicyVersion(
        public_id=_public_id("ecrv"),
        policy_id=policy.id,
        source_promotion_policy_version_id=source_version.id,
        version=int(latest or 0) + 1,
        config_digest=config_digest,
        strategy=request.strategy,
        golden_target_size=request.golden_target_size,
        golden_min_items=request.golden_min_items,
        bad_case_target_size=request.bad_case_target_size,
        bad_case_min_items=request.bad_case_min_items,
        schema_name=_POLICY_SCHEMA_NAME,
        schema_version=_POLICY_SCHEMA_VERSION,
        created_by_user_id=actor.id,
        created_at=_utcnow_mysql_safe(),
    )
    version.policy = policy
    version.source_promotion_policy_version = source_version
    version.runs = []
    db.add(version)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="evaluation_case_routing_policy_version.created",
        resource_type="evaluation_case_routing_policy_version",
        resource_id=version.id,
        namespace_id=policy.namespace_id,
        details={
            "public_id": version.public_id,
            "policy_public_id": policy.public_id,
            "source_promotion_policy_public_id": source_version.policy.public_id,
            "source_promotion_policy_version_public_id": source_version.public_id,
            "version": version.version,
            "config_digest": version.config_digest,
            "strategy": version.strategy.value,
            "golden_target_size": version.golden_target_size,
            "golden_min_items": version.golden_min_items,
            "bad_case_target_size": version.bad_case_target_size,
            "bad_case_min_items": version.bad_case_min_items,
            "schema_name": version.schema_name,
            "schema_version": version.schema_version,
        },
    )
    return version


async def get_evaluation_case_routing_policy_version(
    db: AsyncSession,
    *,
    public_id: str,
) -> EvaluationCaseRoutingPolicyVersion:
    version = (
        await db.execute(
            select(EvaluationCaseRoutingPolicyVersion)
            .options(*_version_options())
            .where(EvaluationCaseRoutingPolicyVersion.public_id == public_id)
        )
    ).scalar_one_or_none()
    if version is None:
        raise EvaluationHubNotFoundError(
            "EvaluationCaseRoutingPolicyVersion not found"
        )
    return version


async def get_evaluation_case_routing_run(
    db: AsyncSession,
    *,
    public_id: str,
) -> EvaluationCaseRoutingRun:
    run = (
        await db.execute(
            select(EvaluationCaseRoutingRun)
            .options(*_run_options())
            .where(EvaluationCaseRoutingRun.public_id == public_id)
        )
    ).scalar_one_or_none()
    if run is None:
        raise EvaluationHubNotFoundError("EvaluationCaseRoutingRun not found")
    return run


async def list_evaluation_case_routing_runs(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int,
) -> list[EvaluationCaseRoutingRun]:
    return list(
        (
            await db.scalars(
                select(EvaluationCaseRoutingRun)
                .options(*_run_options())
                .where(EvaluationCaseRoutingRun.namespace_id == namespace_id)
                .order_by(EvaluationCaseRoutingRun.created_at.desc())
                .limit(limit)
            )
        ).all()
    )


async def _datasets(
    db: AsyncSession,
    *,
    request: EvaluationCaseRoutingRunCreate,
    namespace_id: int,
) -> tuple[EvaluationDataset, EvaluationDataset]:
    values = list(
        (
            await db.scalars(
                select(EvaluationDataset).where(
                    EvaluationDataset.public_id.in_(
                        (
                            request.golden_dataset_public_id,
                            request.bad_case_dataset_public_id,
                        )
                    )
                )
            )
        ).all()
    )
    mapped = {value.public_id: value for value in values}
    golden = mapped.get(request.golden_dataset_public_id)
    bad_case = mapped.get(request.bad_case_dataset_public_id)
    if (
        golden is None
        or bad_case is None
        or golden.namespace_id != namespace_id
        or bad_case.namespace_id != namespace_id
    ):
        raise EvaluationHubNotFoundError("EvaluationDataset not found")
    for dataset in (golden, bad_case):
        if (
            dataset.status != EvaluationDatasetStatus.ACTIVE
            or dataset.provider != EvaluationProvider.LANGFUSE
            or not dataset.provider_dataset_ref
        ):
            raise EvaluationHubStateError(
                "Case routing requires active synchronized Langfuse Datasets"
            )
    return golden, bad_case


async def _promotion_runs(
    db: AsyncSession,
    *,
    public_ids: list[str],
    version: EvaluationCaseRoutingPolicyVersion,
) -> list[EvaluationPromotionRun]:
    runs = list(
        (
            await db.scalars(
                select(EvaluationPromotionRun)
                .options(selectinload(EvaluationPromotionRun.items))
                .where(EvaluationPromotionRun.public_id.in_(public_ids))
            )
        ).all()
    )
    if len(runs) != len(public_ids):
        raise EvaluationHubNotFoundError("EvaluationPromotionRun not found")
    if any(
        run.namespace_id != version.policy.namespace_id
        or run.policy_version_id
        != version.source_promotion_policy_version_id
        for run in runs
    ):
        raise EvaluationHubStateError(
            "Case routing requires runs from the pinned Promotion version"
        )
    if len({run.dispatch_id for run in runs}) != len(runs):
        raise EvaluationHubStateError(
            "Case routing source runs must target distinct dispatches"
        )
    source_keys = [
        (item.source_trace_ref, item.source_observation_ref)
        for run in runs
        for item in run.items
    ]
    if len(set(source_keys)) != len(source_keys):
        raise EvaluationHubStateError(
            "Case routing source runs contain overlapping Observations"
        )
    return sorted(runs, key=lambda value: value.public_id)


def _request_digest(
    *,
    version: EvaluationCaseRoutingPolicyVersion,
    request: EvaluationCaseRoutingRunCreate,
) -> str:
    return _digest(
        {
            "schema_name": _RUN_SCHEMA_NAME,
            "schema_version": _RUN_SCHEMA_VERSION,
            "policy_version_public_id": version.public_id,
            "policy_config_digest": version.config_digest,
            "promotion_run_public_ids": sorted(
                request.promotion_run_public_ids
            ),
            "golden_dataset_public_id": request.golden_dataset_public_id,
            "bad_case_dataset_public_id": request.bad_case_dataset_public_id,
        }
    )


def _evidence_digest(runs: list[EvaluationPromotionRun]) -> str:
    return _digest(
        {
            "schema_name": _RUN_SCHEMA_NAME,
            "schema_version": _RUN_SCHEMA_VERSION,
            "source_runs": [
                {
                    "public_id": run.public_id,
                    "evidence_digest": run.evidence_digest,
                    "items": [
                        {
                            "position": item.position,
                            "source_trace_ref": item.source_trace_ref,
                            "source_observation_ref": (
                                item.source_observation_ref
                            ),
                            "score_present": item.score_present,
                            "quality_passed": item.quality_passed,
                            "diversity_bucket_present": (
                                item.diversity_bucket_present
                            ),
                            "score_evidence_digest": (
                                item.score_evidence_digest
                            ),
                            "diversity_bucket_digest": (
                                item.diversity_bucket_digest
                            ),
                        }
                        for item in sorted(
                            run.items, key=lambda value: value.position
                        )
                    ],
                }
                for run in runs
            ],
        }
    )


def _rank_digest(
    *,
    version: EvaluationCaseRoutingPolicyVersion,
    lane: EvaluationCaseRoutingLane,
    item: EvaluationPromotionRunItem,
) -> str:
    return _digest(
        {
            "policy_config_digest": version.config_digest,
            "strategy": version.strategy.value,
            "lane": lane.value,
            "cluster_digest": item.diversity_bucket_digest,
            "source_trace_ref": item.source_trace_ref,
            "source_observation_ref": item.source_observation_ref,
        }
    )


def _candidates(
    *,
    version: EvaluationCaseRoutingPolicyVersion,
    runs: list[EvaluationPromotionRun],
) -> list[_RoutingCandidate]:
    values: list[_RoutingCandidate] = []
    for run in runs:
        for item in sorted(run.items, key=lambda value: value.position):
            if not item.score_present:
                lane = EvaluationCaseRoutingLane.EXCLUDED
                reason = "missing_score"
            elif (
                not item.diversity_bucket_present
                or item.diversity_bucket_digest is None
            ):
                lane = EvaluationCaseRoutingLane.EXCLUDED
                reason = "missing_cluster_metadata"
            elif item.quality_passed:
                lane = EvaluationCaseRoutingLane.GOLDEN
                reason = "golden_candidate"
            else:
                lane = EvaluationCaseRoutingLane.BAD_CASE
                reason = "bad_case_candidate"
            values.append(
                _RoutingCandidate(
                    source_run=run,
                    item=item,
                    lane=lane,
                    cluster_digest=(
                        item.diversity_bucket_digest
                        if lane != EvaluationCaseRoutingLane.EXCLUDED
                        else None
                    ),
                    rank_digest=(
                        _rank_digest(version=version, lane=lane, item=item)
                        if lane != EvaluationCaseRoutingLane.EXCLUDED
                        else None
                    ),
                    reason_code=reason,
                )
            )
    return values


def _balanced_selection(
    *,
    version: EvaluationCaseRoutingPolicyVersion,
    lane: EvaluationCaseRoutingLane,
    candidates: list[_RoutingCandidate],
    target_size: int,
) -> list[_RoutingCandidate]:
    groups: dict[str, list[_RoutingCandidate]] = {}
    for candidate in candidates:
        if candidate.lane == lane and candidate.cluster_digest is not None:
            groups.setdefault(candidate.cluster_digest, []).append(candidate)
    for values in groups.values():
        values.sort(
            key=lambda value: (
                value.rank_digest or "",
                value.item.source_trace_ref,
                value.item.source_observation_ref,
            )
        )
    cluster_order = sorted(
        groups,
        key=lambda cluster_digest: _digest(
            {
                "policy_config_digest": version.config_digest,
                "lane": lane.value,
                "cluster_digest": cluster_digest,
            }
        ),
    )
    selected: list[_RoutingCandidate] = []
    cursor = 0
    while len(selected) < target_size:
        added = False
        for cluster_digest in cluster_order:
            values = groups[cluster_digest]
            if cursor < len(values):
                selected.append(values[cursor])
                added = True
                if len(selected) == target_size:
                    break
        if not added:
            break
        cursor += 1
    return selected


def _routing_digest(
    *,
    version: EvaluationCaseRoutingPolicyVersion,
    evidence_digest: str,
    candidates: list[_RoutingCandidate],
    golden_selected: list[_RoutingCandidate],
    bad_case_selected: list[_RoutingCandidate],
    outcome: EvaluationCaseRoutingOutcome,
    reason_codes: list[str],
) -> str:
    selected_keys = {
        value.source_key for value in golden_selected + bad_case_selected
    }
    return _digest(
        {
            "schema_name": _RUN_SCHEMA_NAME,
            "schema_version": _RUN_SCHEMA_VERSION,
            "policy_config_digest": version.config_digest,
            "evidence_digest": evidence_digest,
            "outcome": outcome.value,
            "reason_codes": reason_codes,
            "items": [
                {
                    "source_run_public_id": value.source_run.public_id,
                    "source_trace_ref": value.item.source_trace_ref,
                    "source_observation_ref": (
                        value.item.source_observation_ref
                    ),
                    "lane": value.lane.value,
                    "cluster_digest": value.cluster_digest,
                    "rank_digest": value.rank_digest,
                    "selected": value.source_key in selected_keys,
                    "reason_code": value.reason_code,
                }
                for value in candidates
            ],
        }
    )


async def run_evaluation_case_routing_policy(
    db: AsyncSession,
    *,
    version_public_id: str,
    request: EvaluationCaseRoutingRunCreate,
    idempotency_key: str,
    actor: User,
) -> EvaluationCaseRoutingRun:
    version = await get_evaluation_case_routing_policy_version(
        db, public_id=version_public_id
    )
    golden_dataset, bad_case_dataset = await _datasets(
        db,
        request=request,
        namespace_id=version.policy.namespace_id,
    )
    request_digest = _request_digest(version=version, request=request)
    existing_by_key = (
        await db.execute(
            select(EvaluationCaseRoutingRun)
            .options(*_run_options())
            .where(
                EvaluationCaseRoutingRun.namespace_id
                == version.policy.namespace_id,
                EvaluationCaseRoutingRun.idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if existing_by_key is not None:
        if existing_by_key.request_digest == request_digest:
            return existing_by_key
        raise EvaluationHubConflictError(
            "Case routing idempotency key is already bound"
        )

    source_runs = await _promotion_runs(
        db,
        public_ids=request.promotion_run_public_ids,
        version=version,
    )
    evidence_digest = _evidence_digest(source_runs)
    existing_evidence = (
        await db.execute(
            select(EvaluationCaseRoutingRun)
            .options(*_run_options())
            .where(
                EvaluationCaseRoutingRun.policy_version_id == version.id,
                EvaluationCaseRoutingRun.golden_dataset_id
                == golden_dataset.id,
                EvaluationCaseRoutingRun.bad_case_dataset_id
                == bad_case_dataset.id,
                EvaluationCaseRoutingRun.evidence_digest == evidence_digest,
            )
        )
    ).scalar_one_or_none()
    if existing_evidence is not None:
        return existing_evidence

    candidates = _candidates(version=version, runs=source_runs)
    golden_candidates = [
        value
        for value in candidates
        if value.lane == EvaluationCaseRoutingLane.GOLDEN
    ]
    bad_case_candidates = [
        value
        for value in candidates
        if value.lane == EvaluationCaseRoutingLane.BAD_CASE
    ]
    excluded = [
        value
        for value in candidates
        if value.lane == EvaluationCaseRoutingLane.EXCLUDED
    ]
    golden_selected = _balanced_selection(
        version=version,
        lane=EvaluationCaseRoutingLane.GOLDEN,
        candidates=candidates,
        target_size=version.golden_target_size,
    )
    bad_case_selected = _balanced_selection(
        version=version,
        lane=EvaluationCaseRoutingLane.BAD_CASE,
        candidates=candidates,
        target_size=version.bad_case_target_size,
    )
    reason_codes: list[str] = []
    if len(golden_selected) < version.golden_min_items:
        reason_codes.append("insufficient_golden_candidates")
    if len(bad_case_selected) < version.bad_case_min_items:
        reason_codes.append("insufficient_bad_case_candidates")
    outcome = (
        EvaluationCaseRoutingOutcome.ROUTED
        if not reason_codes
        else EvaluationCaseRoutingOutcome.BLOCKED
    )
    if not reason_codes:
        reason_codes = ["golden_and_bad_case_subsets_routed"]
    routing_digest = _routing_digest(
        version=version,
        evidence_digest=evidence_digest,
        candidates=candidates,
        golden_selected=golden_selected,
        bad_case_selected=bad_case_selected,
        outcome=outcome,
        reason_codes=reason_codes,
    )

    golden_batch = None
    bad_case_batch = None
    if outcome == EvaluationCaseRoutingOutcome.ROUTED:
        routing_key_digest = hashlib.sha256(
            idempotency_key.encode("utf-8")
        ).hexdigest()[:48]
        golden_batch = await create_evaluation_dataset_curation_batch(
            db,
            dataset_public_id=golden_dataset.public_id,
            request=EvaluationDatasetCurationBatchCreate(
                items=[
                    EvaluationDatasetCurationItemCreate(
                        trace_id=value.item.source_trace_ref,
                        observation_id=value.item.source_observation_ref,
                    )
                    for value in golden_selected
                ]
            ),
            idempotency_key=f"case-routing-g-{routing_key_digest}",
            actor=actor,
        )
        bad_case_batch = await create_evaluation_dataset_curation_batch(
            db,
            dataset_public_id=bad_case_dataset.public_id,
            request=EvaluationDatasetCurationBatchCreate(
                items=[
                    EvaluationDatasetCurationItemCreate(
                        trace_id=value.item.source_trace_ref,
                        observation_id=value.item.source_observation_ref,
                    )
                    for value in bad_case_selected
                ]
            ),
            idempotency_key=f"case-routing-b-{routing_key_digest}",
            actor=actor,
        )

    selected_keys = {
        value.source_key for value in golden_selected + bad_case_selected
    }
    run = EvaluationCaseRoutingRun(
        public_id=_public_id("ecrr"),
        namespace_id=version.policy.namespace_id,
        policy_version_id=version.id,
        golden_dataset_id=golden_dataset.id,
        bad_case_dataset_id=bad_case_dataset.id,
        golden_curation_batch_id=(golden_batch.id if golden_batch else None),
        bad_case_curation_batch_id=(
            bad_case_batch.id if bad_case_batch else None
        ),
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        evidence_digest=evidence_digest,
        routing_digest=routing_digest,
        outcome=outcome,
        reason_codes_json=reason_codes,
        source_run_count=len(source_runs),
        candidate_count=len(candidates),
        golden_candidate_count=len(golden_candidates),
        bad_case_candidate_count=len(bad_case_candidates),
        excluded_count=len(excluded),
        golden_selected_count=len(golden_selected),
        bad_case_selected_count=len(bad_case_selected),
        golden_cluster_count=len(
            {value.cluster_digest for value in golden_candidates}
        ),
        bad_case_cluster_count=len(
            {value.cluster_digest for value in bad_case_candidates}
        ),
        schema_name=_RUN_SCHEMA_NAME,
        schema_version=_RUN_SCHEMA_VERSION,
        created_by_user_id=actor.id,
        created_at=_utcnow_mysql_safe(),
    )
    run.policy_version = version
    run.golden_dataset = golden_dataset
    run.bad_case_dataset = bad_case_dataset
    run.golden_curation_batch = golden_batch
    run.bad_case_curation_batch = bad_case_batch
    run.items = [
        EvaluationCaseRoutingRunItem(
            source_promotion_run_id=value.source_run.id,
            position=position,
            source_trace_ref=value.item.source_trace_ref,
            source_observation_ref=value.item.source_observation_ref,
            lane=value.lane,
            selected=value.source_key in selected_keys,
            cluster_digest=value.cluster_digest,
            rank_digest=value.rank_digest,
            reason_code=value.reason_code,
            source_promotion_run=value.source_run,
        )
        for position, value in enumerate(candidates, start=1)
    ]
    db.add(run)
    await db.flush()
    await enqueue_domain_event(
        db,
        build_evaluation_case_routing_run_created(run),
        guaranteed_new=True,
    )
    await audit(
        db,
        user=actor,
        action="evaluation_case_routing_run.created",
        resource_type="evaluation_case_routing_run",
        resource_id=run.id,
        namespace_id=run.namespace_id,
        details={
            "public_id": run.public_id,
            "policy_public_id": version.policy.public_id,
            "policy_version_public_id": version.public_id,
            "source_promotion_policy_version_public_id": (
                version.source_promotion_policy_version.public_id
            ),
            "golden_dataset_public_id": golden_dataset.public_id,
            "bad_case_dataset_public_id": bad_case_dataset.public_id,
            "golden_curation_batch_public_id": (
                golden_batch.public_id if golden_batch else None
            ),
            "bad_case_curation_batch_public_id": (
                bad_case_batch.public_id if bad_case_batch else None
            ),
            "request_digest": run.request_digest,
            "evidence_digest": run.evidence_digest,
            "routing_digest": run.routing_digest,
            "outcome": run.outcome.value,
            "reason_codes": list(run.reason_codes_json),
            "source_run_count": run.source_run_count,
            "candidate_count": run.candidate_count,
            "golden_candidate_count": run.golden_candidate_count,
            "bad_case_candidate_count": run.bad_case_candidate_count,
            "excluded_count": run.excluded_count,
            "golden_selected_count": run.golden_selected_count,
            "bad_case_selected_count": run.bad_case_selected_count,
            "golden_cluster_count": run.golden_cluster_count,
            "bad_case_cluster_count": run.bad_case_cluster_count,
            "schema_name": run.schema_name,
            "schema_version": run.schema_version,
        },
    )
    return run
