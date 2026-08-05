from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters.evaluation.langfuse import (
    LangfuseTraceDatasetMaterializer,
    LangfuseTraceDatasetMaterializerError,
)
from app.models.evaluation import (
    Evaluation,
    EvaluationDataset,
    EvaluationDatasetMaterialization,
    EvaluationDatasetSourceType,
    EvaluationDatasetStatus,
    EvaluationDatasetVersion,
    EvaluationProvider,
    EvaluationResultCompleteness,
    EvaluationStatus,
    Evaluator,
    EvaluatorStatus,
    EvaluatorVersion,
    Experiment,
    ExperimentStatus,
)
from app.models.user import User
from app.schemas.evaluation import (
    EvaluationComplete,
    EvaluationCreate,
    EvaluationDatasetCreate,
    TraceDatasetMaterializationCreate,
    EvaluationDatasetVersionCreate,
    EvaluatorCreate,
    EvaluatorVersionCreate,
    ExperimentCreate,
)
from app.services.audit_service import audit
from app.services.evaluation_ports import TraceDatasetMaterializerPort
from app.services.langfuse_service import langfuse_service
from app.services.outbox_event_service import (
    build_evaluation_dataset_materialized,
    build_evaluation_queued,
    enqueue_domain_event,
)
from app.services.tenant_write_service import require_active_namespace


class EvaluationHubError(ValueError):
    pass


class EvaluationHubNotFoundError(EvaluationHubError):
    pass


class EvaluationHubConflictError(EvaluationHubError):
    pass


class EvaluationHubProviderError(EvaluationHubError):
    pass


class EvaluationHubStateError(EvaluationHubError):
    pass


_SAFE_EXECUTION_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_TRACE_DATASET_SCHEMA_NAME = "langfuse-trace-dataset-index"
_TRACE_DATASET_SCHEMA_VERSION = "v4"


def _public_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _langfuse_dataset_name(*, namespace_id: int, name: str) -> str:
    return f"duckdock.ns{namespace_id}.{name}"


async def _sync_langfuse_dataset(
    *,
    namespace_id: int,
    name: str,
    description: str | None,
) -> str:
    client = langfuse_service.client()
    if client is None:
        raise EvaluationHubProviderError("Langfuse provider is not configured")
    provider_name = _langfuse_dataset_name(
        namespace_id=namespace_id,
        name=name,
    )
    try:
        dataset = await asyncio.to_thread(
            client.create_dataset,
            name=provider_name,
            description=description,
            metadata={
                "duckdock.namespace_id": namespace_id,
                "duckdock.dataset_name": name,
                "duckdock.content_policy": "provider-hosted",
            },
        )
    except Exception as exc:
        raise EvaluationHubProviderError(
            "Langfuse dataset synchronization failed"
        ) from exc
    provider_ref = getattr(dataset, "id", None)
    if not isinstance(provider_ref, str) or not provider_ref:
        raise EvaluationHubProviderError("Langfuse returned no dataset identity")
    return provider_ref


async def create_evaluation_dataset(
    db: AsyncSession,
    *,
    request: EvaluationDatasetCreate,
    actor: User,
) -> EvaluationDataset:
    await require_active_namespace(db, request.namespace_id)
    existing = await db.scalar(
        select(EvaluationDataset.id).where(
            EvaluationDataset.namespace_id == request.namespace_id,
            EvaluationDataset.name == request.name,
        )
    )
    if existing is not None:
        raise EvaluationHubConflictError("EvaluationDataset name already exists")

    provider_ref = request.provider_dataset_ref
    if request.sync_provider:
        provider_ref = await _sync_langfuse_dataset(
            namespace_id=request.namespace_id,
            name=request.name,
            description=request.description,
        )

    dataset = EvaluationDataset(
        public_id=_public_id("eds"),
        namespace_id=request.namespace_id,
        name=request.name,
        description=request.description,
        provider=request.provider,
        provider_dataset_ref=provider_ref,
    )
    db.add(dataset)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="evaluation_dataset.created",
        resource_type="evaluation_dataset",
        resource_id=dataset.id,
        namespace_id=dataset.namespace_id,
        details={
            "public_id": dataset.public_id,
            "provider": dataset.provider.value,
            "provider_synced": provider_ref is not None,
        },
    )
    return dataset


async def get_evaluation_dataset(
    db: AsyncSession,
    *,
    public_id: str,
    namespace_id: int | None = None,
) -> EvaluationDataset:
    query = (
        select(EvaluationDataset)
        .options(selectinload(EvaluationDataset.versions))
        .where(EvaluationDataset.public_id == public_id)
    )
    if namespace_id is not None:
        query = query.where(EvaluationDataset.namespace_id == namespace_id)
    dataset = (await db.execute(query)).scalar_one_or_none()
    if dataset is None:
        raise EvaluationHubNotFoundError("EvaluationDataset not found")
    return dataset


async def list_evaluation_datasets(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int,
) -> list[EvaluationDataset]:
    return list(
        (
            await db.scalars(
                select(EvaluationDataset)
                .options(selectinload(EvaluationDataset.versions))
                .where(EvaluationDataset.namespace_id == namespace_id)
                .order_by(EvaluationDataset.created_at.desc())
                .limit(limit)
            )
        ).all()
    )


async def create_evaluation_dataset_version(
    db: AsyncSession,
    *,
    dataset: EvaluationDataset,
    request: EvaluationDatasetVersionCreate,
    actor: User,
) -> EvaluationDatasetVersion:
    existing = await db.scalar(
        select(EvaluationDatasetVersion).where(
            EvaluationDatasetVersion.dataset_id == dataset.id,
            EvaluationDatasetVersion.content_digest == request.content_digest,
        )
    )
    if existing is not None:
        raise EvaluationHubConflictError(
            "EvaluationDatasetVersion digest already exists"
        )
    latest = await db.scalar(
        select(func.max(EvaluationDatasetVersion.version)).where(
            EvaluationDatasetVersion.dataset_id == dataset.id
        )
    )
    version = EvaluationDatasetVersion(
        public_id=_public_id("edv"),
        dataset_id=dataset.id,
        version=int(latest or 0) + 1,
        content_digest=request.content_digest,
        item_count=request.item_count,
        schema_name=request.schema_name,
        schema_version=request.schema_version,
        provider_version_ref=request.provider_version_ref,
    )
    db.add(version)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="evaluation_dataset_version.created",
        resource_type="evaluation_dataset_version",
        resource_id=version.id,
        namespace_id=dataset.namespace_id,
        details={
            "public_id": version.public_id,
            "dataset_public_id": dataset.public_id,
            "version": version.version,
            "content_digest": version.content_digest,
            "item_count": version.item_count,
        },
    )
    return version


def _trace_materialization_request_digest(
    *,
    dataset_public_id: str,
    request: TraceDatasetMaterializationCreate,
) -> str:
    payload = {
        "schema_name": _TRACE_DATASET_SCHEMA_NAME,
        "schema_version": _TRACE_DATASET_SCHEMA_VERSION,
        "dataset_public_id": dataset_public_id,
        "trace_ref": request.trace_id,
        "observation_ref": request.observation_id or "__ROOT__",
        "mapping": "observation.input->input;observation.output->expected_output",
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _materialization_load_options():
    return (
        selectinload(EvaluationDatasetMaterialization.dataset),
        selectinload(EvaluationDatasetMaterialization.dataset_version),
    )


async def materialize_evaluation_dataset_trace(
    db: AsyncSession,
    *,
    dataset_public_id: str,
    request: TraceDatasetMaterializationCreate,
    idempotency_key: str,
    actor: User,
    materializer: TraceDatasetMaterializerPort | None = None,
) -> EvaluationDatasetMaterialization:
    request_digest = _trace_materialization_request_digest(
        dataset_public_id=dataset_public_id,
        request=request,
    )
    existing_by_key = (
        await db.execute(
            select(EvaluationDatasetMaterialization)
            .options(*_materialization_load_options())
            .where(
                EvaluationDatasetMaterialization.idempotency_key
                == idempotency_key,
                EvaluationDatasetMaterialization.namespace_id.in_(
                    select(EvaluationDataset.namespace_id).where(
                        EvaluationDataset.public_id == dataset_public_id
                    )
                ),
            )
        )
    ).scalar_one_or_none()
    if existing_by_key is not None:
        if existing_by_key.request_digest == request_digest:
            return existing_by_key
        raise EvaluationHubConflictError(
            "Trace2Dataset idempotency key is already bound"
        )

    dataset = (
        await db.execute(
            select(EvaluationDataset)
            .where(EvaluationDataset.public_id == dataset_public_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if dataset is None:
        raise EvaluationHubNotFoundError("EvaluationDataset not found")
    if dataset.status != EvaluationDatasetStatus.ACTIVE:
        raise EvaluationHubStateError("EvaluationDataset is not active")
    if (
        dataset.provider != EvaluationProvider.LANGFUSE
        or not dataset.provider_dataset_ref
    ):
        raise EvaluationHubStateError(
            "Trace2Dataset requires a synchronized Langfuse Dataset"
        )

    existing_by_key = (
        await db.execute(
            select(EvaluationDatasetMaterialization)
            .options(*_materialization_load_options())
            .where(
                EvaluationDatasetMaterialization.namespace_id
                == dataset.namespace_id,
                EvaluationDatasetMaterialization.idempotency_key
                == idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if existing_by_key is not None:
        if existing_by_key.request_digest == request_digest:
            return existing_by_key
        raise EvaluationHubConflictError(
            "Trace2Dataset idempotency key is already bound"
        )

    selected_materializer = materializer
    if selected_materializer is None:
        client = langfuse_service.client()
        if client is None:
            raise EvaluationHubProviderError(
                "Langfuse provider is not configured"
            )
        selected_materializer = LangfuseTraceDatasetMaterializer(client=client)
    try:
        receipt = await selected_materializer.materialize(
            dataset_public_id=dataset.public_id,
            provider_dataset_name=_langfuse_dataset_name(
                namespace_id=dataset.namespace_id,
                name=dataset.name,
            ),
            provider_dataset_ref=dataset.provider_dataset_ref,
            trace_ref=request.trace_id,
            observation_ref=request.observation_id,
        )
    except LangfuseTraceDatasetMaterializerError as exc:
        raise EvaluationHubProviderError(str(exc)) from exc
    except Exception as exc:
        raise EvaluationHubProviderError(
            "Trace2Dataset provider operation failed"
        ) from exc

    existing_source = (
        await db.execute(
            select(EvaluationDatasetMaterialization)
            .options(*_materialization_load_options())
            .where(
                EvaluationDatasetMaterialization.dataset_id == dataset.id,
                EvaluationDatasetMaterialization.source_trace_ref
                == receipt.source_trace_ref,
                EvaluationDatasetMaterialization.source_observation_ref
                == receipt.source_observation_ref,
            )
        )
    ).scalar_one_or_none()
    if existing_source is not None:
        raise EvaluationHubConflictError(
            "Trace observation is already materialized in this Dataset"
        )
    if receipt.provider_dataset_ref != dataset.provider_dataset_ref:
        raise EvaluationHubProviderError(
            "Trace2Dataset provider receipt targets another Dataset"
        )

    version = await create_evaluation_dataset_version(
        db,
        dataset=dataset,
        request=EvaluationDatasetVersionCreate(
            content_digest=receipt.manifest_digest,
            item_count=receipt.item_count,
            schema_name=_TRACE_DATASET_SCHEMA_NAME,
            schema_version=_TRACE_DATASET_SCHEMA_VERSION,
            provider_version_ref=receipt.provider_version_ref,
        ),
        actor=actor,
    )
    materialization = EvaluationDatasetMaterialization(
        public_id=_public_id("edm"),
        namespace_id=dataset.namespace_id,
        dataset_id=dataset.id,
        dataset_version_id=version.id,
        source_type=EvaluationDatasetSourceType.LANGFUSE_TRACE,
        source_trace_ref=receipt.source_trace_ref,
        source_observation_ref=receipt.source_observation_ref,
        provider_dataset_item_ref=receipt.provider_dataset_item_ref,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        schema_name=_TRACE_DATASET_SCHEMA_NAME,
        schema_version=_TRACE_DATASET_SCHEMA_VERSION,
        created_by_user_id=actor.id,
    )
    materialization.dataset = dataset
    materialization.dataset_version = version
    db.add(materialization)
    await db.flush()
    await enqueue_domain_event(
        db,
        build_evaluation_dataset_materialized(materialization),
        guaranteed_new=True,
    )
    await audit(
        db,
        user=actor,
        action="evaluation_dataset.trace_materialized",
        resource_type="evaluation_dataset_materialization",
        resource_id=materialization.id,
        namespace_id=materialization.namespace_id,
        details={
            "public_id": materialization.public_id,
            "dataset_public_id": dataset.public_id,
            "dataset_version_public_id": version.public_id,
            "source_type": materialization.source_type.value,
            "source_trace_ref": materialization.source_trace_ref,
            "source_observation_ref": (
                materialization.source_observation_ref
            ),
            "provider_dataset_item_ref": (
                materialization.provider_dataset_item_ref
            ),
            "manifest_digest": version.content_digest,
            "item_count": version.item_count,
        },
    )
    return materialization


async def list_evaluation_dataset_materializations(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int,
) -> list[EvaluationDatasetMaterialization]:
    return list(
        (
            await db.scalars(
                select(EvaluationDatasetMaterialization)
                .options(*_materialization_load_options())
                .where(
                    EvaluationDatasetMaterialization.namespace_id
                    == namespace_id
                )
                .order_by(
                    EvaluationDatasetMaterialization.created_at.desc()
                )
                .limit(limit)
            )
        ).all()
    )


async def create_evaluator(
    db: AsyncSession,
    *,
    request: EvaluatorCreate,
    actor: User,
) -> Evaluator:
    await require_active_namespace(db, request.namespace_id)
    existing = await db.scalar(
        select(Evaluator.id).where(
            Evaluator.namespace_id == request.namespace_id,
            Evaluator.name == request.name,
        )
    )
    if existing is not None:
        raise EvaluationHubConflictError("Evaluator name already exists")
    evaluator = Evaluator(
        public_id=_public_id("evr"),
        namespace_id=request.namespace_id,
        name=request.name,
        kind=request.kind,
        provider=request.provider,
        provider_evaluator_ref=request.provider_evaluator_ref,
    )
    db.add(evaluator)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="evaluator.created",
        resource_type="evaluator",
        resource_id=evaluator.id,
        namespace_id=evaluator.namespace_id,
        details={
            "public_id": evaluator.public_id,
            "kind": evaluator.kind.value,
            "provider": evaluator.provider.value,
        },
    )
    return evaluator


async def get_evaluator(
    db: AsyncSession,
    *,
    public_id: str,
    namespace_id: int | None = None,
) -> Evaluator:
    query = (
        select(Evaluator)
        .options(selectinload(Evaluator.versions))
        .where(Evaluator.public_id == public_id)
    )
    if namespace_id is not None:
        query = query.where(Evaluator.namespace_id == namespace_id)
    evaluator = (await db.execute(query)).scalar_one_or_none()
    if evaluator is None:
        raise EvaluationHubNotFoundError("Evaluator not found")
    return evaluator


async def list_evaluators(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int,
) -> list[Evaluator]:
    return list(
        (
            await db.scalars(
                select(Evaluator)
                .options(selectinload(Evaluator.versions))
                .where(Evaluator.namespace_id == namespace_id)
                .order_by(Evaluator.created_at.desc())
                .limit(limit)
            )
        ).all()
    )


async def create_evaluator_version(
    db: AsyncSession,
    *,
    evaluator: Evaluator,
    request: EvaluatorVersionCreate,
    actor: User,
) -> EvaluatorVersion:
    existing = await db.scalar(
        select(EvaluatorVersion).where(
            EvaluatorVersion.evaluator_id == evaluator.id,
            EvaluatorVersion.config_digest == request.config_digest,
        )
    )
    if existing is not None:
        raise EvaluationHubConflictError("EvaluatorVersion digest already exists")
    latest = await db.scalar(
        select(func.max(EvaluatorVersion.version)).where(
            EvaluatorVersion.evaluator_id == evaluator.id
        )
    )
    version = EvaluatorVersion(
        public_id=_public_id("evv"),
        evaluator_id=evaluator.id,
        version=int(latest or 0) + 1,
        config_digest=request.config_digest,
        implementation_ref=request.implementation_ref,
        rubric_version=request.rubric_version,
        provider_version_ref=request.provider_version_ref,
    )
    if request.activate:
        evaluator.status = EvaluatorStatus.ACTIVE
    db.add(version)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="evaluator_version.created",
        resource_type="evaluator_version",
        resource_id=version.id,
        namespace_id=evaluator.namespace_id,
        details={
            "public_id": version.public_id,
            "evaluator_public_id": evaluator.public_id,
            "version": version.version,
            "config_digest": version.config_digest,
            "activated": request.activate,
        },
    )
    return version


async def create_experiment(
    db: AsyncSession,
    *,
    request: ExperimentCreate,
    actor: User,
) -> Experiment:
    await require_active_namespace(db, request.namespace_id)
    existing = await db.scalar(
        select(Experiment.id).where(
            Experiment.namespace_id == request.namespace_id,
            Experiment.name == request.name,
        )
    )
    if existing is not None:
        raise EvaluationHubConflictError("Experiment name already exists")
    dataset_version = (
        await db.execute(
            select(EvaluationDatasetVersion)
            .join(
                EvaluationDataset,
                EvaluationDataset.id == EvaluationDatasetVersion.dataset_id,
            )
            .where(
                EvaluationDatasetVersion.public_id
                == request.dataset_version_public_id,
                EvaluationDataset.namespace_id == request.namespace_id,
            )
        )
    ).scalar_one_or_none()
    if dataset_version is None:
        raise EvaluationHubNotFoundError("EvaluationDatasetVersion not found")
    experiment = Experiment(
        public_id=_public_id("exp"),
        namespace_id=request.namespace_id,
        name=request.name,
        dataset_version_id=dataset_version.id,
        target_type=request.target_type,
        target_ref=request.target_ref,
        target_digest=request.target_digest,
        provider=request.provider,
        provider_experiment_ref=request.provider_experiment_ref,
    )
    db.add(experiment)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="evaluation_experiment.created",
        resource_type="evaluation_experiment",
        resource_id=experiment.id,
        namespace_id=experiment.namespace_id,
        details={
            "public_id": experiment.public_id,
            "dataset_version_public_id": dataset_version.public_id,
            "target_type": experiment.target_type,
            "target_ref": experiment.target_ref,
            "target_digest": experiment.target_digest,
        },
    )
    return experiment


async def get_experiment(
    db: AsyncSession,
    *,
    public_id: str,
    namespace_id: int | None = None,
) -> Experiment:
    query = select(Experiment).where(Experiment.public_id == public_id)
    if namespace_id is not None:
        query = query.where(Experiment.namespace_id == namespace_id)
    experiment = (await db.execute(query)).scalar_one_or_none()
    if experiment is None:
        raise EvaluationHubNotFoundError("Experiment not found")
    return experiment


async def list_experiments(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int,
) -> list[Experiment]:
    return list(
        (
            await db.scalars(
                select(Experiment)
                .where(Experiment.namespace_id == namespace_id)
                .order_by(Experiment.created_at.desc())
                .limit(limit)
            )
        ).all()
    )


async def create_evaluation(
    db: AsyncSession,
    *,
    experiment: Experiment,
    request: EvaluationCreate,
    actor: User,
    execution_key: str | None = None,
) -> Evaluation:
    evaluator_version = (
        await db.execute(
            select(EvaluatorVersion)
            .join(Evaluator, Evaluator.id == EvaluatorVersion.evaluator_id)
            .where(
                EvaluatorVersion.public_id
                == request.evaluator_version_public_id,
                Evaluator.namespace_id == experiment.namespace_id,
                Evaluator.status == EvaluatorStatus.ACTIVE,
            )
        )
    ).scalar_one_or_none()
    if evaluator_version is None:
        raise EvaluationHubNotFoundError(
            "Active EvaluatorVersion not found"
        )
    stable_execution_key = execution_key or (
        f"evaluation:{experiment.public_id}:{evaluator_version.public_id}"
    )
    if _SAFE_EXECUTION_KEY.fullmatch(stable_execution_key) is None:
        raise EvaluationHubError("Evaluation execution key is invalid")
    existing_by_key = (
        await db.execute(
            select(Evaluation).where(
                Evaluation.namespace_id == experiment.namespace_id,
                Evaluation.execution_key == stable_execution_key,
            )
        )
    ).scalar_one_or_none()
    if existing_by_key is not None:
        if (
            existing_by_key.experiment_id == experiment.id
            and existing_by_key.evaluator_version_id == evaluator_version.id
        ):
            return existing_by_key
        raise EvaluationHubConflictError(
            "Evaluation execution key is already bound"
        )
    existing = await db.scalar(
        select(Evaluation.id).where(
            Evaluation.experiment_id == experiment.id,
            Evaluation.evaluator_version_id == evaluator_version.id,
        )
    )
    if existing is not None:
        raise EvaluationHubConflictError(
            "Evaluation already exists for this evaluator version"
        )
    now = datetime.now(timezone.utc)
    worker_managed = experiment.provider == EvaluationProvider.LANGFUSE
    evaluation = Evaluation(
        public_id=_public_id("eval"),
        namespace_id=experiment.namespace_id,
        experiment_id=experiment.id,
        evaluator_version_id=evaluator_version.id,
        status=(
            EvaluationStatus.PENDING
            if worker_managed
            else EvaluationStatus.RUNNING
        ),
        execution_key=stable_execution_key,
        provider_evaluation_ref=request.provider_evaluation_ref,
        available_at=now if worker_managed else None,
        started_at=None if worker_managed else now,
    )
    if experiment.status == ExperimentStatus.DRAFT:
        experiment.status = ExperimentStatus.RUNNING
        experiment.started_at = now
    elif experiment.status != ExperimentStatus.RUNNING:
        raise EvaluationHubStateError("Experiment does not accept evaluations")
    db.add(evaluation)
    await db.flush()
    if worker_managed:
        await enqueue_domain_event(
            db,
            build_evaluation_queued(
                namespace_id=evaluation.namespace_id,
                evaluation_public_id=evaluation.public_id,
                experiment_public_id=experiment.public_id,
                evaluator_version_public_id=evaluator_version.public_id,
                status=evaluation.status.value,
                available_at=now,
            ),
            guaranteed_new=True,
        )
    await audit(
        db,
        user=actor,
        action=(
            "evaluation.queued"
            if worker_managed
            else "evaluation.started"
        ),
        resource_type="evaluation",
        resource_id=evaluation.id,
        namespace_id=evaluation.namespace_id,
        details={
            "public_id": evaluation.public_id,
            "experiment_public_id": experiment.public_id,
            "evaluator_version_public_id": evaluator_version.public_id,
        },
    )
    return evaluation


async def complete_evaluation(
    db: AsyncSession,
    *,
    evaluation: Evaluation,
    request: EvaluationComplete,
    actor: User,
) -> Evaluation:
    if evaluation.status != EvaluationStatus.RUNNING:
        raise EvaluationHubStateError("Evaluation is not running")
    if evaluation.lease_owner is not None:
        raise EvaluationHubStateError(
            "Worker-owned evaluation cannot be completed manually"
        )
    now = datetime.now(timezone.utc)
    evaluation.score = request.score
    evaluation.total_count = request.total_count
    evaluation.processed_count = request.passed_count + request.failed_count
    evaluation.scored_count = request.passed_count + request.failed_count
    evaluation.passed_count = request.passed_count
    evaluation.failed_count = request.failed_count
    evaluation.error_count = (
        request.total_count - request.passed_count - request.failed_count
    )
    evaluation.result_completeness = (
        EvaluationResultCompleteness.COMPLETE
        if evaluation.error_count == 0
        else EvaluationResultCompleteness.PARTIAL
    )
    evaluation.status = (
        EvaluationStatus.COMPLETED
        if evaluation.result_completeness
        == EvaluationResultCompleteness.COMPLETE
        else EvaluationStatus.PARTIAL
    )
    evaluation.ended_at = now
    evaluation.lease_owner = None
    evaluation.lease_expires_at = None
    if request.provider_evaluation_ref is not None:
        evaluation.provider_evaluation_ref = request.provider_evaluation_ref
    await db.flush()

    unfinished = await db.scalar(
        select(func.count(Evaluation.id)).where(
            Evaluation.experiment_id == evaluation.experiment_id,
            Evaluation.status.in_(
                [EvaluationStatus.PENDING, EvaluationStatus.RUNNING]
            ),
        )
    )
    if int(unfinished or 0) == 0:
        experiment = await db.get(Experiment, evaluation.experiment_id)
        if experiment is not None:
            statuses = list(
                (
                    await db.scalars(
                        select(Evaluation.status).where(
                            Evaluation.experiment_id == evaluation.experiment_id
                        )
                    )
                ).all()
            )
            if EvaluationStatus.FAILED in statuses:
                experiment.status = ExperimentStatus.FAILED
            elif EvaluationStatus.PARTIAL in statuses:
                experiment.status = ExperimentStatus.PARTIAL
            elif EvaluationStatus.CANCELLED in statuses:
                experiment.status = ExperimentStatus.CANCELLED
            else:
                experiment.status = ExperimentStatus.COMPLETED
            experiment.ended_at = now
    await audit(
        db,
        user=actor,
        action=(
            "evaluation.completed"
            if evaluation.status == EvaluationStatus.COMPLETED
            else "evaluation.partial"
        ),
        resource_type="evaluation",
        resource_id=evaluation.id,
        namespace_id=evaluation.namespace_id,
        details={
            "public_id": evaluation.public_id,
            "score": evaluation.score,
            "total_count": evaluation.total_count,
            "processed_count": evaluation.processed_count,
            "scored_count": evaluation.scored_count,
            "passed_count": evaluation.passed_count,
            "failed_count": evaluation.failed_count,
            "error_count": evaluation.error_count,
            "result_completeness": evaluation.result_completeness.value,
        },
    )
    return evaluation


async def get_evaluation(
    db: AsyncSession,
    *,
    public_id: str,
    namespace_id: int | None = None,
) -> Evaluation:
    query = select(Evaluation).where(Evaluation.public_id == public_id)
    if namespace_id is not None:
        query = query.where(Evaluation.namespace_id == namespace_id)
    evaluation = (await db.execute(query)).scalar_one_or_none()
    if evaluation is None:
        raise EvaluationHubNotFoundError("Evaluation not found")
    return evaluation


async def list_evaluations(
    db: AsyncSession,
    *,
    namespace_id: int,
    experiment_id: int | None,
    limit: int,
) -> list[Evaluation]:
    query = select(Evaluation).where(Evaluation.namespace_id == namespace_id)
    if experiment_id is not None:
        query = query.where(Evaluation.experiment_id == experiment_id)
    return list(
        (
            await db.scalars(
                query.order_by(Evaluation.created_at.desc()).limit(limit)
            )
        ).all()
    )
