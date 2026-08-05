from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters.evaluation.langfuse import (
    LangfuseTraceCandidateReader,
    LangfuseTraceDatasetMaterializer,
    LangfuseTraceDatasetMaterializerError,
)
from app.models.evaluation import (
    EvaluationDataset,
    EvaluationDatasetCurationBatch,
    EvaluationDatasetCurationDecision,
    EvaluationDatasetCurationItem,
    EvaluationDatasetCurationMaterialization,
    EvaluationDatasetCurationMaterializedItem,
    EvaluationDatasetCurationReview,
    EvaluationDatasetMaterialization,
    EvaluationDatasetStatus,
    EvaluationProvider,
)
from app.models.user import User
from app.schemas.evaluation import (
    EvaluationDatasetCurationBatchCreate,
    EvaluationDatasetCurationReviewCreate,
    EvaluationDatasetVersionCreate,
)
from app.services.audit_service import audit
from app.services.evaluation_ports import (
    TraceDatasetCandidate,
    TraceDatasetCandidateReaderPort,
    TraceDatasetBatchSource,
    TraceDatasetMaterializerPort,
)
from app.services.evaluation_service import (
    EvaluationHubConflictError,
    EvaluationHubNotFoundError,
    EvaluationHubProviderError,
    EvaluationHubStateError,
    create_evaluation_dataset_version,
)
from app.services.langfuse_service import langfuse_service
from app.services.outbox_event_service import (
    build_evaluation_dataset_curation_materialized,
    build_evaluation_dataset_curation_reviewed,
    build_evaluation_dataset_curation_submitted,
    enqueue_domain_event,
)


_CURATION_SCHEMA_NAME = "langfuse-trace-dataset-curation"
_CURATION_SCHEMA_VERSION = "1.0"
_TRACE_DATASET_SCHEMA_NAME = "langfuse-trace-dataset-index"
_TRACE_DATASET_SCHEMA_VERSION = "v4"
_MAX_CANDIDATE_WINDOW = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class TraceDatasetCandidateView:
    candidate: TraceDatasetCandidate
    already_materialized: bool
    already_governed: bool


def _public_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _digest(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _langfuse_dataset_name(*, namespace_id: int, name: str) -> str:
    return f"duckdock.ns{namespace_id}.{name}"


def _batch_load_options():
    return (
        selectinload(EvaluationDatasetCurationBatch.dataset),
        selectinload(EvaluationDatasetCurationBatch.items),
        selectinload(EvaluationDatasetCurationBatch.review),
        selectinload(
            EvaluationDatasetCurationBatch.materialization
        ).selectinload(
            EvaluationDatasetCurationMaterialization.dataset_version
        ),
        selectinload(
            EvaluationDatasetCurationBatch.materialization
        ).selectinload(EvaluationDatasetCurationMaterialization.items),
    )


async def _get_batch(
    db: AsyncSession,
    *,
    public_id: str,
    for_update: bool = False,
) -> EvaluationDatasetCurationBatch:
    query = (
        select(EvaluationDatasetCurationBatch)
        .options(*_batch_load_options())
        .where(EvaluationDatasetCurationBatch.public_id == public_id)
    )
    if for_update:
        query = query.with_for_update()
    batch = (await db.execute(query)).scalar_one_or_none()
    if batch is None:
        raise EvaluationHubNotFoundError(
            "EvaluationDatasetCurationBatch not found"
        )
    return batch


async def list_trace_dataset_candidates(
    db: AsyncSession,
    *,
    dataset_public_id: str,
    from_start_time: datetime,
    to_start_time: datetime,
    name: str | None,
    observation_type: str | None,
    environment: str | None,
    root_only: bool,
    limit: int,
    reader: TraceDatasetCandidateReaderPort | None = None,
) -> tuple[EvaluationDataset, list[TraceDatasetCandidateView]]:
    if to_start_time <= from_start_time:
        raise EvaluationHubStateError(
            "Candidate time window must have a positive duration"
        )
    if to_start_time - from_start_time > _MAX_CANDIDATE_WINDOW:
        raise EvaluationHubStateError(
            "Candidate time window may not exceed seven days"
        )
    dataset = (
        await db.execute(
            select(EvaluationDataset).where(
                EvaluationDataset.public_id == dataset_public_id
            )
        )
    ).scalar_one_or_none()
    if dataset is None:
        raise EvaluationHubNotFoundError("EvaluationDataset not found")
    if (
        dataset.status != EvaluationDatasetStatus.ACTIVE
        or dataset.provider != EvaluationProvider.LANGFUSE
        or not dataset.provider_dataset_ref
    ):
        raise EvaluationHubStateError(
            "Trace curation requires an active synchronized Langfuse Dataset"
        )
    selected_reader = reader
    if selected_reader is None:
        client = langfuse_service.client()
        if client is None:
            raise EvaluationHubProviderError(
                "Langfuse provider is not configured"
            )
        selected_reader = LangfuseTraceCandidateReader(client=client)
    try:
        candidates = await selected_reader.list_candidates(
            from_start_time=from_start_time,
            to_start_time=to_start_time,
            name=name,
            observation_type=observation_type,
            environment=environment,
            root_only=root_only,
            limit=limit,
        )
    except LangfuseTraceDatasetMaterializerError as exc:
        raise EvaluationHubProviderError(str(exc)) from exc
    except Exception as exc:
        raise EvaluationHubProviderError(
            "Trace candidate provider operation failed"
        ) from exc

    materialized_sources = set(
        (
            await db.execute(
                select(
                    EvaluationDatasetMaterialization.source_trace_ref,
                    EvaluationDatasetMaterialization.source_observation_ref,
                ).where(
                    EvaluationDatasetMaterialization.dataset_id == dataset.id
                )
            )
        ).all()
    )
    batch_materialized_sources = set(
        (
            await db.execute(
                select(
                    EvaluationDatasetCurationItem.source_trace_ref,
                    EvaluationDatasetCurationItem.source_observation_ref,
                )
                .join(
                    EvaluationDatasetCurationBatch,
                    EvaluationDatasetCurationBatch.id
                    == EvaluationDatasetCurationItem.batch_id,
                )
                .join(
                    EvaluationDatasetCurationMaterializedItem,
                    EvaluationDatasetCurationMaterializedItem.curation_item_id
                    == EvaluationDatasetCurationItem.id,
                )
                .where(
                    EvaluationDatasetCurationBatch.dataset_id == dataset.id
                )
            )
        ).all()
    )
    materialized_sources |= batch_materialized_sources
    governed_sources = set(
        (
            await db.execute(
                select(
                    EvaluationDatasetCurationItem.source_trace_ref,
                    EvaluationDatasetCurationItem.source_observation_ref,
                )
                .join(
                    EvaluationDatasetCurationBatch,
                    EvaluationDatasetCurationBatch.id
                    == EvaluationDatasetCurationItem.batch_id,
                )
                .where(
                    EvaluationDatasetCurationBatch.dataset_id == dataset.id
                )
            )
        ).all()
    ) | materialized_sources
    return dataset, [
        TraceDatasetCandidateView(
            candidate=candidate,
            already_materialized=(
                candidate.source_trace_ref,
                candidate.source_observation_ref,
            )
            in materialized_sources,
            already_governed=(
                candidate.source_trace_ref,
                candidate.source_observation_ref,
            )
            in governed_sources,
        )
        for candidate in candidates
    ]


def _canonical_sources(
    request: EvaluationDatasetCurationBatchCreate,
) -> list[tuple[str, str]]:
    return sorted(
        (item.trace_id, item.observation_id) for item in request.items
    )


def _selection_digest(
    *,
    dataset_public_id: str,
    sources: list[tuple[str, str]],
) -> str:
    return _digest(
        {
            "schema_name": _CURATION_SCHEMA_NAME,
            "schema_version": _CURATION_SCHEMA_VERSION,
            "dataset_public_id": dataset_public_id,
            "sources": [
                {
                    "source_trace_ref": trace_ref,
                    "source_observation_ref": observation_ref,
                }
                for trace_ref, observation_ref in sources
            ],
        }
    )


async def create_evaluation_dataset_curation_batch(
    db: AsyncSession,
    *,
    dataset_public_id: str,
    request: EvaluationDatasetCurationBatchCreate,
    idempotency_key: str,
    actor: User,
) -> EvaluationDatasetCurationBatch:
    dataset = (
        await db.execute(
            select(EvaluationDataset)
            .where(EvaluationDataset.public_id == dataset_public_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if dataset is None:
        raise EvaluationHubNotFoundError("EvaluationDataset not found")
    if (
        dataset.status != EvaluationDatasetStatus.ACTIVE
        or dataset.provider != EvaluationProvider.LANGFUSE
        or not dataset.provider_dataset_ref
    ):
        raise EvaluationHubStateError(
            "Trace curation requires an active synchronized Langfuse Dataset"
        )

    sources = _canonical_sources(request)
    selection_digest = _selection_digest(
        dataset_public_id=dataset.public_id,
        sources=sources,
    )
    existing_by_key = (
        await db.execute(
            select(EvaluationDatasetCurationBatch)
            .options(*_batch_load_options())
            .where(
                EvaluationDatasetCurationBatch.namespace_id
                == dataset.namespace_id,
                EvaluationDatasetCurationBatch.idempotency_key
                == idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if existing_by_key is not None:
        if existing_by_key.selection_digest == selection_digest:
            return existing_by_key
        raise EvaluationHubConflictError(
            "Curation batch idempotency key is already bound"
        )
    existing_selection = await db.scalar(
        select(EvaluationDatasetCurationBatch.id).where(
            EvaluationDatasetCurationBatch.dataset_id == dataset.id,
            EvaluationDatasetCurationBatch.selection_digest
            == selection_digest,
        )
    )
    if existing_selection is not None:
        raise EvaluationHubConflictError(
            "The same curation selection already exists"
        )

    unavailable_sources = set(
        (
            await db.execute(
                select(
                    EvaluationDatasetMaterialization.source_trace_ref,
                    EvaluationDatasetMaterialization.source_observation_ref,
                ).where(
                    EvaluationDatasetMaterialization.dataset_id == dataset.id
                )
            )
        ).all()
    )
    unavailable_sources.update(
        (
            await db.execute(
                select(
                    EvaluationDatasetCurationItem.source_trace_ref,
                    EvaluationDatasetCurationItem.source_observation_ref,
                )
                .join(
                    EvaluationDatasetCurationBatch,
                    EvaluationDatasetCurationBatch.id
                    == EvaluationDatasetCurationItem.batch_id,
                )
                .where(
                    EvaluationDatasetCurationBatch.dataset_id == dataset.id
                )
            )
        ).all()
    )
    if any(source in unavailable_sources for source in sources):
        raise EvaluationHubConflictError(
            "A selected trace observation is already governed by this Dataset"
        )

    batch = EvaluationDatasetCurationBatch(
        public_id=_public_id("ecb"),
        namespace_id=dataset.namespace_id,
        dataset_id=dataset.id,
        idempotency_key=idempotency_key,
        selection_digest=selection_digest,
        item_count=len(sources),
        schema_name=_CURATION_SCHEMA_NAME,
        schema_version=_CURATION_SCHEMA_VERSION,
        submitted_by_user_id=actor.id,
    )
    batch.dataset = dataset
    batch.items = [
        EvaluationDatasetCurationItem(
            position=position,
            source_trace_ref=trace_ref,
            source_observation_ref=observation_ref,
        )
        for position, (trace_ref, observation_ref) in enumerate(
            sources,
            start=1,
        )
    ]
    batch.review = None
    batch.materialization = None
    db.add(batch)
    await db.flush()
    await enqueue_domain_event(
        db,
        build_evaluation_dataset_curation_submitted(batch),
        guaranteed_new=True,
    )
    await audit(
        db,
        user=actor,
        action="evaluation_dataset.curation_submitted",
        resource_type="evaluation_dataset_curation_batch",
        resource_id=batch.id,
        namespace_id=batch.namespace_id,
        details={
            "public_id": batch.public_id,
            "dataset_public_id": dataset.public_id,
            "selection_digest": batch.selection_digest,
            "item_count": batch.item_count,
            "schema_name": batch.schema_name,
            "schema_version": batch.schema_version,
        },
    )
    return batch


def _review_digest(
    *,
    batch: EvaluationDatasetCurationBatch,
    request: EvaluationDatasetCurationReviewCreate,
) -> str:
    return _digest(
        {
            "schema_name": _CURATION_SCHEMA_NAME,
            "schema_version": _CURATION_SCHEMA_VERSION,
            "batch_public_id": batch.public_id,
            "selection_digest": batch.selection_digest,
            "decision": request.decision.value,
            "comment": request.comment,
        }
    )


async def review_evaluation_dataset_curation_batch(
    db: AsyncSession,
    *,
    batch_public_id: str,
    request: EvaluationDatasetCurationReviewCreate,
    idempotency_key: str,
    actor: User,
) -> EvaluationDatasetCurationBatch:
    batch = await _get_batch(
        db,
        public_id=batch_public_id,
        for_update=True,
    )
    review_digest = _review_digest(batch=batch, request=request)
    existing_by_key = (
        await db.execute(
            select(EvaluationDatasetCurationReview)
            .options(
                selectinload(
                    EvaluationDatasetCurationReview.batch
                ).selectinload(EvaluationDatasetCurationBatch.dataset),
                selectinload(
                    EvaluationDatasetCurationReview.batch
                ).selectinload(EvaluationDatasetCurationBatch.items),
            )
            .where(
                EvaluationDatasetCurationReview.namespace_id
                == batch.namespace_id,
                EvaluationDatasetCurationReview.idempotency_key
                == idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if existing_by_key is not None:
        if (
            existing_by_key.batch_id == batch.id
            and existing_by_key.review_digest == review_digest
        ):
            batch.review = existing_by_key
            return batch
        raise EvaluationHubConflictError(
            "Curation review idempotency key is already bound"
        )
    if batch.review is not None:
        if batch.review.review_digest == review_digest:
            return batch
        raise EvaluationHubConflictError(
            "Curation batch already has a final review"
        )

    review = EvaluationDatasetCurationReview(
        public_id=_public_id("ecr"),
        namespace_id=batch.namespace_id,
        batch_id=batch.id,
        decision=request.decision,
        comment=request.comment,
        idempotency_key=idempotency_key,
        review_digest=review_digest,
        reviewed_by_user_id=actor.id,
    )
    review.batch = batch
    batch.review = review
    db.add(review)
    await db.flush()
    await enqueue_domain_event(
        db,
        build_evaluation_dataset_curation_reviewed(review),
        guaranteed_new=True,
    )
    await audit(
        db,
        user=actor,
        action="evaluation_dataset.curation_reviewed",
        resource_type="evaluation_dataset_curation_review",
        resource_id=review.id,
        namespace_id=review.namespace_id,
        details={
            "public_id": review.public_id,
            "batch_public_id": batch.public_id,
            "dataset_public_id": batch.dataset.public_id,
            "decision": review.decision.value,
            "review_digest": review.review_digest,
            "item_count": batch.item_count,
        },
    )
    return batch


def _materialization_digest(batch: EvaluationDatasetCurationBatch) -> str:
    return _digest(
        {
            "schema_name": _TRACE_DATASET_SCHEMA_NAME,
            "schema_version": _TRACE_DATASET_SCHEMA_VERSION,
            "batch_public_id": batch.public_id,
            "dataset_public_id": batch.dataset.public_id,
            "selection_digest": batch.selection_digest,
        }
    )


async def materialize_evaluation_dataset_curation_batch(
    db: AsyncSession,
    *,
    batch_public_id: str,
    idempotency_key: str,
    actor: User,
    materializer: TraceDatasetMaterializerPort | None = None,
) -> EvaluationDatasetCurationBatch:
    batch = await _get_batch(
        db,
        public_id=batch_public_id,
        for_update=True,
    )
    request_digest = _materialization_digest(batch)
    existing_by_key = (
        await db.execute(
            select(EvaluationDatasetCurationMaterialization)
            .options(
                selectinload(
                    EvaluationDatasetCurationMaterialization.batch
                ).selectinload(EvaluationDatasetCurationBatch.dataset),
                selectinload(
                    EvaluationDatasetCurationMaterialization.dataset_version
                ),
                selectinload(
                    EvaluationDatasetCurationMaterialization.items
                ),
            )
            .where(
                EvaluationDatasetCurationMaterialization.namespace_id
                == batch.namespace_id,
                EvaluationDatasetCurationMaterialization.idempotency_key
                == idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if existing_by_key is not None:
        if (
            existing_by_key.batch_id == batch.id
            and existing_by_key.request_digest == request_digest
        ):
            batch.materialization = existing_by_key
            return batch
        raise EvaluationHubConflictError(
            "Curation materialization idempotency key is already bound"
        )
    if batch.materialization is not None:
        raise EvaluationHubConflictError(
            "Curation batch is already materialized"
        )
    if (
        batch.review is None
        or batch.review.decision
        != EvaluationDatasetCurationDecision.APPROVED
    ):
        raise EvaluationHubStateError(
            "Only an approved curation batch can be materialized"
        )
    dataset = batch.dataset
    if (
        dataset.status != EvaluationDatasetStatus.ACTIVE
        or dataset.provider != EvaluationProvider.LANGFUSE
        or not dataset.provider_dataset_ref
    ):
        raise EvaluationHubStateError(
            "Trace curation requires an active synchronized Langfuse Dataset"
        )
    selected_materializer = materializer
    if selected_materializer is None:
        client = langfuse_service.client()
        if client is None:
            raise EvaluationHubProviderError(
                "Langfuse provider is not configured"
            )
        selected_materializer = LangfuseTraceDatasetMaterializer(
            client=client
        )
    sources = [
        TraceDatasetBatchSource(
            source_trace_ref=item.source_trace_ref,
            source_observation_ref=item.source_observation_ref,
        )
        for item in sorted(batch.items, key=lambda value: value.position)
    ]
    try:
        receipt = await selected_materializer.materialize_batch(
            dataset_public_id=dataset.public_id,
            provider_dataset_name=_langfuse_dataset_name(
                namespace_id=dataset.namespace_id,
                name=dataset.name,
            ),
            provider_dataset_ref=dataset.provider_dataset_ref,
            sources=sources,
        )
    except LangfuseTraceDatasetMaterializerError as exc:
        raise EvaluationHubProviderError(str(exc)) from exc
    except Exception as exc:
        raise EvaluationHubProviderError(
            "Trace curation provider operation failed"
        ) from exc
    if receipt.provider_dataset_ref != dataset.provider_dataset_ref:
        raise EvaluationHubProviderError(
            "Trace curation provider receipt targets another Dataset"
        )
    receipt_by_source = {
        (
            item.source_trace_ref,
            item.source_observation_ref,
        ): item
        for item in receipt.items
    }
    source_keys = {
        (source.source_trace_ref, source.source_observation_ref)
        for source in sources
    }
    if len(receipt_by_source) != len(sources) or (
        set(receipt_by_source) != source_keys
    ):
        raise EvaluationHubProviderError(
            "Trace curation provider receipt is incomplete"
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
    materialization_record = EvaluationDatasetCurationMaterialization(
        public_id=_public_id("ecm"),
        namespace_id=batch.namespace_id,
        batch_id=batch.id,
        dataset_version_id=version.id,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        item_count=batch.item_count,
        created_by_user_id=actor.id,
    )
    materialization_record.batch = batch
    materialization_record.dataset_version = version
    materialization_record.items = [
        EvaluationDatasetCurationMaterializedItem(
            curation_item_id=item.id,
            curation_item=item,
            provider_dataset_item_ref=receipt_by_source[
                (item.source_trace_ref, item.source_observation_ref)
            ].provider_dataset_item_ref,
        )
        for item in batch.items
    ]
    batch.materialization = materialization_record
    db.add(materialization_record)
    await db.flush()
    await enqueue_domain_event(
        db,
        build_evaluation_dataset_curation_materialized(
            materialization_record
        ),
        guaranteed_new=True,
    )
    await audit(
        db,
        user=actor,
        action="evaluation_dataset.curation_materialized",
        resource_type="evaluation_dataset_curation_materialization",
        resource_id=materialization_record.id,
        namespace_id=materialization_record.namespace_id,
        details={
            "public_id": materialization_record.public_id,
            "batch_public_id": batch.public_id,
            "dataset_public_id": dataset.public_id,
            "dataset_version_public_id": version.public_id,
            "selection_digest": batch.selection_digest,
            "manifest_digest": version.content_digest,
            "selected_item_count": batch.item_count,
            "dataset_item_count": version.item_count,
        },
    )
    return batch


async def list_evaluation_dataset_curation_batches(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int,
) -> list[EvaluationDatasetCurationBatch]:
    return list(
        (
            await db.scalars(
                select(EvaluationDatasetCurationBatch)
                .options(*_batch_load_options())
                .where(
                    EvaluationDatasetCurationBatch.namespace_id
                    == namespace_id
                )
                .order_by(
                    EvaluationDatasetCurationBatch.created_at.desc()
                )
                .limit(limit)
            )
        ).all()
    )


async def get_evaluation_dataset_curation_batch(
    db: AsyncSession,
    *,
    public_id: str,
) -> EvaluationDatasetCurationBatch:
    return await _get_batch(db, public_id=public_id)


def evaluation_dataset_curation_status(
    batch: EvaluationDatasetCurationBatch,
) -> str:
    if batch.materialization is not None:
        return "MATERIALIZED"
    if batch.review is None:
        return "PENDING_REVIEW"
    return batch.review.decision.value
