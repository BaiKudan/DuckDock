from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.evaluation import (
    EvaluationDataset,
    EvaluationSamplingPolicy,
    EvaluationSamplingPolicyStatus,
    EvaluationSamplingPolicyVersion,
    EvaluationSamplingRun,
    EvaluationSamplingRunItem,
)
from app.models.user import User
from app.schemas.evaluation import (
    EvaluationDatasetCurationBatchCreate,
    EvaluationDatasetCurationItemCreate,
    EvaluationSamplingPolicyCreate,
    EvaluationSamplingPolicyVersionCreate,
    EvaluationSamplingRunCreate,
)
from app.services.audit_service import audit
from app.services.evaluation_curation_service import (
    create_evaluation_dataset_curation_batch,
    list_trace_dataset_candidates,
)
from app.services.evaluation_ports import TraceDatasetCandidateReaderPort
from app.services.evaluation_service import (
    EvaluationHubConflictError,
    EvaluationHubNotFoundError,
    EvaluationHubStateError,
)
from app.services.outbox_event_service import (
    build_evaluation_sampling_run_created,
    enqueue_domain_event,
)
from app.services.tenant_write_service import require_active_namespace


_POLICY_SCHEMA_NAME = "duckdock-trace-sampling-policy"
_POLICY_SCHEMA_VERSION = "1.0"
_RUN_SCHEMA_NAME = "duckdock-trace-sampling-run"
_RUN_SCHEMA_VERSION = "1.0"


def _public_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


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


def _policy_payload(request: EvaluationSamplingPolicyVersionCreate) -> dict:
    return {
        "schema_name": _POLICY_SCHEMA_NAME,
        "schema_version": _POLICY_SCHEMA_VERSION,
        "strategy": request.strategy.value,
        "sample_size": request.sample_size,
        "minimum_sample_size": request.minimum_sample_size,
        "candidate_limit": request.candidate_limit,
        "observation_name": request.observation_name,
        "observation_type": request.observation_type,
        "environment": request.environment,
        "root_only": request.root_only,
        "exclude_governed": True,
    }


def _run_load_options():
    return (
        selectinload(EvaluationSamplingRun.policy_version).selectinload(
            EvaluationSamplingPolicyVersion.policy
        ),
        selectinload(EvaluationSamplingRun.dataset),
        selectinload(EvaluationSamplingRun.curation_batch),
        selectinload(EvaluationSamplingRun.items),
    )


async def create_evaluation_sampling_policy(
    db: AsyncSession,
    *,
    request: EvaluationSamplingPolicyCreate,
    actor: User,
) -> EvaluationSamplingPolicy:
    await require_active_namespace(db, request.namespace_id)
    existing = await db.scalar(
        select(EvaluationSamplingPolicy.id).where(
            EvaluationSamplingPolicy.namespace_id == request.namespace_id,
            EvaluationSamplingPolicy.name == request.name,
        )
    )
    if existing is not None:
        raise EvaluationHubConflictError(
            "EvaluationSamplingPolicy name already exists"
        )
    current = _utcnow_mysql_safe()
    policy = EvaluationSamplingPolicy(
        public_id=_public_id("esp"),
        namespace_id=request.namespace_id,
        name=request.name,
        description=request.description,
        status=EvaluationSamplingPolicyStatus.ACTIVE,
        created_at=current,
        updated_at=current,
    )
    policy.versions = []
    db.add(policy)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="evaluation_sampling_policy.created",
        resource_type="evaluation_sampling_policy",
        resource_id=policy.id,
        namespace_id=policy.namespace_id,
        details={
            "public_id": policy.public_id,
            "name": policy.name,
            "status": policy.status.value,
        },
    )
    return policy


async def get_evaluation_sampling_policy(
    db: AsyncSession,
    *,
    public_id: str,
) -> EvaluationSamplingPolicy:
    policy = (
        await db.execute(
            select(EvaluationSamplingPolicy)
            .options(selectinload(EvaluationSamplingPolicy.versions))
            .where(EvaluationSamplingPolicy.public_id == public_id)
        )
    ).scalar_one_or_none()
    if policy is None:
        raise EvaluationHubNotFoundError(
            "EvaluationSamplingPolicy not found"
        )
    return policy


async def list_evaluation_sampling_policies(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int,
) -> list[EvaluationSamplingPolicy]:
    return list(
        (
            await db.scalars(
                select(EvaluationSamplingPolicy)
                .options(selectinload(EvaluationSamplingPolicy.versions))
                .where(
                    EvaluationSamplingPolicy.namespace_id == namespace_id
                )
                .order_by(EvaluationSamplingPolicy.created_at.desc())
                .limit(limit)
            )
        ).all()
    )


async def create_evaluation_sampling_policy_version(
    db: AsyncSession,
    *,
    policy: EvaluationSamplingPolicy,
    request: EvaluationSamplingPolicyVersionCreate,
    actor: User,
) -> EvaluationSamplingPolicyVersion:
    if policy.status != EvaluationSamplingPolicyStatus.ACTIVE:
        raise EvaluationHubStateError(
            "Retired EvaluationSamplingPolicy cannot accept versions"
        )
    config_digest = _digest(_policy_payload(request))
    existing = await db.scalar(
        select(EvaluationSamplingPolicyVersion.id).where(
            EvaluationSamplingPolicyVersion.policy_id == policy.id,
            EvaluationSamplingPolicyVersion.config_digest == config_digest,
        )
    )
    if existing is not None:
        raise EvaluationHubConflictError(
            "EvaluationSamplingPolicyVersion digest already exists"
        )
    latest = await db.scalar(
        select(func.max(EvaluationSamplingPolicyVersion.version)).where(
            EvaluationSamplingPolicyVersion.policy_id == policy.id
        )
    )
    version = EvaluationSamplingPolicyVersion(
        public_id=_public_id("esv"),
        policy_id=policy.id,
        version=int(latest or 0) + 1,
        config_digest=config_digest,
        strategy=request.strategy,
        sample_size=request.sample_size,
        minimum_sample_size=request.minimum_sample_size,
        candidate_limit=request.candidate_limit,
        observation_name=request.observation_name,
        observation_type=request.observation_type,
        environment=request.environment,
        root_only=request.root_only,
        exclude_governed=True,
        schema_name=_POLICY_SCHEMA_NAME,
        schema_version=_POLICY_SCHEMA_VERSION,
        created_by_user_id=actor.id,
        created_at=_utcnow_mysql_safe(),
    )
    version.policy = policy
    version.runs = []
    db.add(version)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="evaluation_sampling_policy_version.created",
        resource_type="evaluation_sampling_policy_version",
        resource_id=version.id,
        namespace_id=policy.namespace_id,
        details={
            "public_id": version.public_id,
            "policy_public_id": policy.public_id,
            "version": version.version,
            "config_digest": version.config_digest,
            "strategy": version.strategy.value,
            "sample_size": version.sample_size,
            "minimum_sample_size": version.minimum_sample_size,
            "candidate_limit": version.candidate_limit,
            "observation_name": version.observation_name,
            "observation_type": version.observation_type,
            "environment": version.environment,
            "root_only": version.root_only,
            "exclude_governed": True,
            "schema_name": version.schema_name,
            "schema_version": version.schema_version,
        },
    )
    return version


async def get_evaluation_sampling_policy_version(
    db: AsyncSession,
    *,
    public_id: str,
) -> EvaluationSamplingPolicyVersion:
    version = (
        await db.execute(
            select(EvaluationSamplingPolicyVersion)
            .options(selectinload(EvaluationSamplingPolicyVersion.policy))
            .where(EvaluationSamplingPolicyVersion.public_id == public_id)
        )
    ).scalar_one_or_none()
    if version is None:
        raise EvaluationHubNotFoundError(
            "EvaluationSamplingPolicyVersion not found"
        )
    return version


async def get_evaluation_sampling_run(
    db: AsyncSession,
    *,
    public_id: str,
) -> EvaluationSamplingRun:
    run = (
        await db.execute(
            select(EvaluationSamplingRun)
            .options(*_run_load_options())
            .where(EvaluationSamplingRun.public_id == public_id)
        )
    ).scalar_one_or_none()
    if run is None:
        raise EvaluationHubNotFoundError("EvaluationSamplingRun not found")
    return run


async def list_evaluation_sampling_runs(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int,
) -> list[EvaluationSamplingRun]:
    return list(
        (
            await db.scalars(
                select(EvaluationSamplingRun)
                .options(*_run_load_options())
                .where(EvaluationSamplingRun.namespace_id == namespace_id)
                .order_by(EvaluationSamplingRun.created_at.desc())
                .limit(limit)
            )
        ).all()
    )


def _run_digest(
    *,
    version: EvaluationSamplingPolicyVersion,
    dataset_public_id: str,
    from_start_time: datetime,
    to_start_time: datetime,
) -> str:
    return _digest(
        {
            "schema_name": _RUN_SCHEMA_NAME,
            "schema_version": _RUN_SCHEMA_VERSION,
            "policy_version_public_id": version.public_id,
            "policy_config_digest": version.config_digest,
            "dataset_public_id": dataset_public_id,
            "from_start_time": _utc_iso(from_start_time),
            "to_start_time": _utc_iso(to_start_time),
        }
    )


def _rank_digest(
    *,
    version: EvaluationSamplingPolicyVersion,
    dataset_public_id: str,
    source_trace_ref: str,
    source_observation_ref: str,
) -> str:
    return _digest(
        {
            "strategy": version.strategy.value,
            "policy_config_digest": version.config_digest,
            "dataset_public_id": dataset_public_id,
            "source_trace_ref": source_trace_ref,
            "source_observation_ref": source_observation_ref,
        }
    )


async def run_evaluation_sampling_policy(
    db: AsyncSession,
    *,
    version: EvaluationSamplingPolicyVersion,
    request: EvaluationSamplingRunCreate,
    idempotency_key: str,
    actor: User,
    reader: TraceDatasetCandidateReaderPort | None = None,
) -> EvaluationSamplingRun:
    from_start_time = _utc(request.from_start_time)
    to_start_time = _utc(request.to_start_time)
    request_run_digest = _run_digest(
        version=version,
        dataset_public_id=request.dataset_public_id,
        from_start_time=from_start_time,
        to_start_time=to_start_time,
    )
    existing_by_key = (
        await db.execute(
            select(EvaluationSamplingRun)
            .options(*_run_load_options())
            .where(
                EvaluationSamplingRun.namespace_id
                == version.policy.namespace_id,
                EvaluationSamplingRun.idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if existing_by_key is not None:
        if existing_by_key.run_digest == request_run_digest:
            return existing_by_key
        raise EvaluationHubConflictError(
            "Sampling run idempotency key is already bound"
        )
    duplicate_run = await db.scalar(
        select(EvaluationSamplingRun.id).where(
            EvaluationSamplingRun.run_digest == request_run_digest,
            EvaluationSamplingRun.dataset.has(
                EvaluationDataset.public_id == request.dataset_public_id
            ),
        )
    )
    if duplicate_run is not None:
        raise EvaluationHubConflictError(
            "The same sampling policy window already exists"
        )

    dataset_identity = (
        await db.execute(
            select(EvaluationDataset).where(
                EvaluationDataset.public_id == request.dataset_public_id
            )
        )
    ).scalar_one_or_none()
    if (
        dataset_identity is None
        or dataset_identity.namespace_id != version.policy.namespace_id
    ):
        raise EvaluationHubNotFoundError("EvaluationDataset not found")
    dataset, candidate_views = await list_trace_dataset_candidates(
        db,
        dataset_public_id=request.dataset_public_id,
        from_start_time=from_start_time,
        to_start_time=to_start_time,
        name=version.observation_name,
        observation_type=version.observation_type,
        environment=version.environment,
        root_only=version.root_only,
        limit=version.candidate_limit,
        reader=reader,
    )
    eligible = [view for view in candidate_views if not view.already_governed]
    if len(eligible) < version.minimum_sample_size:
        raise EvaluationHubStateError(
            "Sampling window does not contain the minimum eligible sources"
        )
    ranked = sorted(
        (
            (
                _rank_digest(
                    version=version,
                    dataset_public_id=dataset.public_id,
                    source_trace_ref=view.candidate.source_trace_ref,
                    source_observation_ref=(
                        view.candidate.source_observation_ref
                    ),
                ),
                view,
            )
            for view in eligible
        ),
        key=lambda value: (
            value[0],
            value[1].candidate.source_trace_ref,
            value[1].candidate.source_observation_ref,
        ),
    )[: version.sample_size]
    batch = await create_evaluation_dataset_curation_batch(
        db,
        dataset_public_id=dataset.public_id,
        request=EvaluationDatasetCurationBatchCreate(
            items=[
                EvaluationDatasetCurationItemCreate(
                    trace_id=view.candidate.source_trace_ref,
                    observation_id=view.candidate.source_observation_ref,
                )
                for _, view in ranked
            ]
        ),
        idempotency_key=(
            "sampling-batch-"
            + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:48]
        ),
        actor=actor,
    )
    run = EvaluationSamplingRun(
        public_id=_public_id("esr"),
        namespace_id=dataset.namespace_id,
        policy_version_id=version.id,
        dataset_id=dataset.id,
        curation_batch_id=batch.id,
        idempotency_key=idempotency_key,
        from_start_time=from_start_time,
        to_start_time=to_start_time,
        candidate_count=len(candidate_views),
        eligible_count=len(eligible),
        selected_count=len(ranked),
        selection_digest=batch.selection_digest,
        run_digest=request_run_digest,
        schema_name=_RUN_SCHEMA_NAME,
        schema_version=_RUN_SCHEMA_VERSION,
        created_by_user_id=actor.id,
        created_at=_utcnow_mysql_safe(),
    )
    run.policy_version = version
    run.dataset = dataset
    run.curation_batch = batch
    run.items = [
        EvaluationSamplingRunItem(
            position=position,
            source_trace_ref=view.candidate.source_trace_ref,
            source_observation_ref=view.candidate.source_observation_ref,
            rank_digest=rank_digest,
        )
        for position, (rank_digest, view) in enumerate(ranked, start=1)
    ]
    db.add(run)
    await db.flush()
    await enqueue_domain_event(
        db,
        build_evaluation_sampling_run_created(run),
        guaranteed_new=True,
    )
    await audit(
        db,
        user=actor,
        action="evaluation_sampling_run.created",
        resource_type="evaluation_sampling_run",
        resource_id=run.id,
        namespace_id=run.namespace_id,
        details={
            "public_id": run.public_id,
            "policy_public_id": version.policy.public_id,
            "policy_version_public_id": version.public_id,
            "dataset_public_id": dataset.public_id,
            "curation_batch_public_id": batch.public_id,
            "strategy": version.strategy.value,
            "candidate_count": run.candidate_count,
            "eligible_count": run.eligible_count,
            "selected_count": run.selected_count,
            "selection_digest": run.selection_digest,
            "run_digest": run.run_digest,
            "schema_name": run.schema_name,
            "schema_version": run.schema_version,
        },
    )
    return run
