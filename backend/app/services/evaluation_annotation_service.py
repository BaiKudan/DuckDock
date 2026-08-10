from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters.evaluation.langfuse import (
    LangfuseAnnotationQueueAdapter,
    LangfuseAnnotationQueueAdapterError,
)
from app.models.evaluation import (
    EvaluationAnnotationDispatch,
    EvaluationAnnotationDispatchItem,
    EvaluationAnnotationDispatchStatus,
    EvaluationAnnotationItemSyncStatus,
    EvaluationAnnotationProviderStatus,
    EvaluationAnnotationQueueBinding,
    EvaluationAnnotationQueueBindingStatus,
    EvaluationDatasetCurationBatch,
    EvaluationProvider,
    EvaluationSamplingRun,
)
from app.models.user import User
from app.services.audit_service import audit
from app.services.evaluation_ports import (
    AnnotationQueueDescriptor,
    AnnotationQueueItemSnapshot,
    AnnotationQueuePort,
)
from app.services.evaluation_service import (
    EvaluationHubConflictError,
    EvaluationHubNotFoundError,
    EvaluationHubProviderError,
    EvaluationHubStateError,
)
from app.services.langfuse_service import langfuse_service
from app.services.outbox_event_service import (
    build_evaluation_annotation_dispatch_requested,
    build_evaluation_annotation_dispatch_synchronized,
    enqueue_domain_event,
)


_BINDING_SCHEMA_NAME = "langfuse-annotation-queue-binding"
_BINDING_SCHEMA_VERSION = "v4"
_DISPATCH_SCHEMA_NAME = "duckdock-annotation-queue-dispatch"
_DISPATCH_SCHEMA_VERSION = "1.0"
_SAFE_WORKER_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SAFE_ERROR_CODE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")


@dataclass(frozen=True, slots=True)
class AnnotationDispatchLease:
    dispatch_public_id: str
    worker_id: str
    attempt_count: int


@dataclass(frozen=True, slots=True)
class AnnotationDispatchRequest:
    dispatch_public_id: str
    provider_queue_ref: str
    expected_score_config_ids: tuple[str, ...]
    observation_refs: tuple[str, ...]


def _public_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _digest(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _safe_worker_id(value: str) -> str:
    if _SAFE_WORKER_ID.fullmatch(value) is None:
        raise EvaluationHubStateError("worker_id is invalid")
    return value


def _safe_error_code(value: str | None) -> str:
    candidate = (value or "").strip().lower()
    if _SAFE_ERROR_CODE.fullmatch(candidate):
        return candidate
    return "annotation_queue_provider_failed"


def _selected_adapter(
    adapter: AnnotationQueuePort | None,
) -> AnnotationQueuePort:
    if adapter is not None:
        return adapter
    client = langfuse_service.client()
    if client is None:
        raise EvaluationHubProviderError("Langfuse provider is not configured")
    return LangfuseAnnotationQueueAdapter(client=client)


def _binding_options():
    return (selectinload(EvaluationAnnotationQueueBinding.dispatches),)


def _dispatch_options():
    return (
        selectinload(EvaluationAnnotationDispatch.binding),
        selectinload(EvaluationAnnotationDispatch.curation_batch),
        selectinload(EvaluationAnnotationDispatch.sampling_run),
        selectinload(EvaluationAnnotationDispatch.items),
    )


async def _get_binding(
    db: AsyncSession,
    *,
    public_id: str,
    for_update: bool = False,
) -> EvaluationAnnotationQueueBinding:
    query = (
        select(EvaluationAnnotationQueueBinding)
        .options(*_binding_options())
        .where(EvaluationAnnotationQueueBinding.public_id == public_id)
    )
    if for_update:
        query = query.with_for_update()
    binding = (await db.execute(query)).scalar_one_or_none()
    if binding is None:
        raise EvaluationHubNotFoundError(
            "EvaluationAnnotationQueueBinding not found"
        )
    return binding


async def _get_dispatch(
    db: AsyncSession,
    *,
    public_id: str,
    for_update: bool = False,
) -> EvaluationAnnotationDispatch:
    query = (
        select(EvaluationAnnotationDispatch)
        .options(*_dispatch_options())
        .where(EvaluationAnnotationDispatch.public_id == public_id)
    )
    if for_update:
        query = query.with_for_update()
    dispatch = (await db.execute(query)).scalar_one_or_none()
    if dispatch is None:
        raise EvaluationHubNotFoundError(
            "EvaluationAnnotationDispatch not found"
        )
    return dispatch


async def list_provider_annotation_queues(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int = 100,
    adapter: AnnotationQueuePort | None = None,
) -> list[tuple[AnnotationQueueDescriptor, bool]]:
    try:
        queues = await _selected_adapter(adapter).list_queues(limit=limit)
    except LangfuseAnnotationQueueAdapterError as exc:
        raise EvaluationHubProviderError(str(exc)) from exc
    except EvaluationHubProviderError:
        raise
    except Exception as exc:
        raise EvaluationHubProviderError(
            "Annotation queue provider operation failed"
        ) from exc
    bound_refs = set(
        (
            await db.scalars(
                select(
                    EvaluationAnnotationQueueBinding.provider_queue_ref
                ).where(
                    EvaluationAnnotationQueueBinding.namespace_id
                    == namespace_id,
                    EvaluationAnnotationQueueBinding.status
                    == EvaluationAnnotationQueueBindingStatus.ACTIVE,
                )
            )
        ).all()
    )
    return [(queue, queue.provider_queue_ref in bound_refs) for queue in queues]


async def create_annotation_queue_binding(
    db: AsyncSession,
    *,
    namespace_id: int,
    provider_queue_ref: str,
    actor: User,
    adapter: AnnotationQueuePort | None = None,
) -> EvaluationAnnotationQueueBinding:
    try:
        descriptor = await _selected_adapter(adapter).get_queue(
            queue_ref=provider_queue_ref
        )
    except LangfuseAnnotationQueueAdapterError as exc:
        raise EvaluationHubProviderError(str(exc)) from exc
    except EvaluationHubProviderError:
        raise
    except Exception as exc:
        raise EvaluationHubProviderError(
            "Annotation queue provider operation failed"
        ) from exc
    existing = (
        await db.execute(
            select(EvaluationAnnotationQueueBinding)
            .options(*_binding_options())
            .where(
                EvaluationAnnotationQueueBinding.namespace_id == namespace_id,
                EvaluationAnnotationQueueBinding.provider
                == EvaluationProvider.LANGFUSE,
                EvaluationAnnotationQueueBinding.provider_queue_ref
                == descriptor.provider_queue_ref,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if (
            existing.provider_queue_name != descriptor.name
            or tuple(existing.score_config_ids_json)
            != descriptor.score_config_ids
            or _as_utc(existing.provider_updated_at)
            != _as_utc(descriptor.updated_at)
        ):
            raise EvaluationHubConflictError(
                "Annotation queue changed after it was bound"
            )
        return existing

    now = _utcnow()
    binding = EvaluationAnnotationQueueBinding(
        public_id=_public_id("eaq"),
        namespace_id=namespace_id,
        provider=EvaluationProvider.LANGFUSE,
        provider_queue_ref=descriptor.provider_queue_ref,
        provider_queue_name=descriptor.name,
        score_config_ids_json=list(descriptor.score_config_ids),
        provider_updated_at=descriptor.updated_at,
        status=EvaluationAnnotationQueueBindingStatus.ACTIVE,
        schema_name=_BINDING_SCHEMA_NAME,
        schema_version=_BINDING_SCHEMA_VERSION,
        created_by_user_id=actor.id,
        created_at=now,
        updated_at=now,
    )
    db.add(binding)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="evaluation_annotation_queue_binding.created",
        resource_type="evaluation_annotation_queue_binding",
        resource_id=binding.id,
        namespace_id=namespace_id,
        details={
            "public_id": binding.public_id,
            "provider": binding.provider.value,
            "provider_queue_ref": binding.provider_queue_ref,
            "provider_queue_name": binding.provider_queue_name,
            "score_config_count": len(binding.score_config_ids_json),
            "schema_name": binding.schema_name,
            "schema_version": binding.schema_version,
        },
    )
    return binding


async def get_annotation_queue_binding(
    db: AsyncSession,
    *,
    public_id: str,
) -> EvaluationAnnotationQueueBinding:
    return await _get_binding(db, public_id=public_id)


async def list_annotation_queue_bindings(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int = 100,
) -> list[EvaluationAnnotationQueueBinding]:
    return list(
        (
            await db.scalars(
                select(EvaluationAnnotationQueueBinding)
                .options(*_binding_options())
                .where(
                    EvaluationAnnotationQueueBinding.namespace_id
                    == namespace_id
                )
                .order_by(EvaluationAnnotationQueueBinding.created_at.desc())
                .limit(limit)
            )
        ).all()
    )


def _dispatch_request_digest(
    *,
    binding: EvaluationAnnotationQueueBinding,
    batch: EvaluationDatasetCurationBatch,
) -> str:
    return _digest(
        {
            "schema_name": _DISPATCH_SCHEMA_NAME,
            "schema_version": _DISPATCH_SCHEMA_VERSION,
            "binding_public_id": binding.public_id,
            "provider": binding.provider.value,
            "provider_queue_ref": binding.provider_queue_ref,
            "score_config_ids": sorted(binding.score_config_ids_json),
            "curation_batch_public_id": batch.public_id,
            "selection_digest": batch.selection_digest,
            "items": [
                {
                    "position": item.position,
                    "source_trace_ref": item.source_trace_ref,
                    "source_observation_ref": item.source_observation_ref,
                }
                for item in sorted(batch.items, key=lambda value: value.position)
            ],
        }
    )


async def create_annotation_dispatch(
    db: AsyncSession,
    *,
    binding_public_id: str,
    curation_batch_public_id: str,
    idempotency_key: str,
    actor: User,
) -> EvaluationAnnotationDispatch:
    binding = await _get_binding(db, public_id=binding_public_id)
    if binding.status != EvaluationAnnotationQueueBindingStatus.ACTIVE:
        raise EvaluationHubStateError("Annotation queue binding is not active")
    batch = (
        await db.execute(
            select(EvaluationDatasetCurationBatch)
            .options(selectinload(EvaluationDatasetCurationBatch.items))
            .where(
                EvaluationDatasetCurationBatch.public_id
                == curation_batch_public_id
            )
        )
    ).scalar_one_or_none()
    if batch is None or batch.namespace_id != binding.namespace_id:
        raise EvaluationHubNotFoundError(
            "EvaluationDatasetCurationBatch not found"
        )
    if not batch.items or len(batch.items) != batch.item_count:
        raise EvaluationHubStateError("Curation batch item snapshot is incomplete")
    request_digest = _dispatch_request_digest(binding=binding, batch=batch)
    existing_by_key = (
        await db.execute(
            select(EvaluationAnnotationDispatch)
            .options(*_dispatch_options())
            .where(
                EvaluationAnnotationDispatch.namespace_id
                == binding.namespace_id,
                EvaluationAnnotationDispatch.idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if existing_by_key is not None:
        if existing_by_key.request_digest != request_digest:
            raise EvaluationHubConflictError(
                "Annotation dispatch idempotency key was reused"
            )
        return existing_by_key
    existing_by_request = (
        await db.execute(
            select(EvaluationAnnotationDispatch)
            .options(*_dispatch_options())
            .where(
                EvaluationAnnotationDispatch.binding_id == binding.id,
                EvaluationAnnotationDispatch.request_digest == request_digest,
            )
        )
    ).scalar_one_or_none()
    if existing_by_request is not None:
        return existing_by_request

    sampling_run = (
        await db.execute(
            select(EvaluationSamplingRun).where(
                EvaluationSamplingRun.curation_batch_id == batch.id
            )
        )
    ).scalar_one_or_none()
    now = _utcnow()
    dispatch = EvaluationAnnotationDispatch(
        public_id=_public_id("ead"),
        namespace_id=binding.namespace_id,
        binding_id=binding.id,
        curation_batch_id=batch.id,
        sampling_run_id=sampling_run.id if sampling_run is not None else None,
        provider_queue_ref=binding.provider_queue_ref,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        status=EvaluationAnnotationDispatchStatus.PENDING,
        item_count=batch.item_count,
        synced_count=0,
        completed_count=0,
        failed_count=0,
        attempt_count=0,
        available_at=now,
        schema_name=_DISPATCH_SCHEMA_NAME,
        schema_version=_DISPATCH_SCHEMA_VERSION,
        created_by_user_id=actor.id,
        created_at=now,
        updated_at=now,
    )
    dispatch.binding = binding
    dispatch.curation_batch = batch
    dispatch.sampling_run = sampling_run
    dispatch.items = [
        EvaluationAnnotationDispatchItem(
            position=item.position,
            source_trace_ref=item.source_trace_ref,
            source_observation_ref=item.source_observation_ref,
            sync_status=EvaluationAnnotationItemSyncStatus.PENDING,
            attempt_count=0,
            updated_at=now,
        )
        for item in sorted(batch.items, key=lambda value: value.position)
    ]
    db.add(dispatch)
    await db.flush()
    await enqueue_domain_event(
        db,
        build_evaluation_annotation_dispatch_requested(dispatch),
        guaranteed_new=True,
    )
    await audit(
        db,
        user=actor,
        action="evaluation_annotation_dispatch.requested",
        resource_type="evaluation_annotation_dispatch",
        resource_id=dispatch.id,
        namespace_id=dispatch.namespace_id,
        details={
            "public_id": dispatch.public_id,
            "binding_public_id": binding.public_id,
            "provider_queue_ref": dispatch.provider_queue_ref,
            "curation_batch_public_id": batch.public_id,
            "sampling_run_public_id": (
                sampling_run.public_id if sampling_run is not None else None
            ),
            "request_digest": dispatch.request_digest,
            "item_count": dispatch.item_count,
            "schema_name": dispatch.schema_name,
            "schema_version": dispatch.schema_version,
        },
    )
    return dispatch


async def get_annotation_dispatch(
    db: AsyncSession,
    *,
    public_id: str,
) -> EvaluationAnnotationDispatch:
    return await _get_dispatch(db, public_id=public_id)


async def list_annotation_dispatches(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int = 100,
) -> list[EvaluationAnnotationDispatch]:
    return list(
        (
            await db.scalars(
                select(EvaluationAnnotationDispatch)
                .options(*_dispatch_options())
                .where(
                    EvaluationAnnotationDispatch.namespace_id == namespace_id
                )
                .order_by(EvaluationAnnotationDispatch.created_at.desc())
                .limit(limit)
            )
        ).all()
    )


async def list_due_annotation_dispatch_ids(
    db: AsyncSession,
    *,
    limit: int,
    now: datetime | None = None,
) -> list[str]:
    current = now or _utcnow()
    return list(
        (
            await db.scalars(
                select(EvaluationAnnotationDispatch.public_id)
                .where(
                    or_(
                        (
                            EvaluationAnnotationDispatch.status
                            == EvaluationAnnotationDispatchStatus.PENDING
                        )
                        & (
                            EvaluationAnnotationDispatch.available_at
                            <= current
                        ),
                        (
                            EvaluationAnnotationDispatch.status
                            == EvaluationAnnotationDispatchStatus.RUNNING
                        )
                        & (
                            EvaluationAnnotationDispatch.lease_expires_at
                            <= current
                        ),
                    )
                )
                .order_by(EvaluationAnnotationDispatch.available_at.asc())
                .limit(limit)
            )
        ).all()
    )


async def lease_annotation_dispatch(
    db: AsyncSession,
    *,
    dispatch_public_id: str,
    worker_id: str,
    lease_seconds: int,
    max_attempts: int,
    now: datetime | None = None,
) -> AnnotationDispatchLease | None:
    worker_id = _safe_worker_id(worker_id)
    if lease_seconds < 30 or lease_seconds > 3600:
        raise EvaluationHubStateError(
            "lease_seconds must be between 30 and 3600"
        )
    if max_attempts < 1 or max_attempts > 20:
        raise EvaluationHubStateError("max_attempts must be between 1 and 20")
    current = now or _utcnow()
    dispatch = await _get_dispatch(
        db,
        public_id=dispatch_public_id,
        for_update=True,
    )
    if dispatch.status in (
        EvaluationAnnotationDispatchStatus.SYNCED,
        EvaluationAnnotationDispatchStatus.FAILED,
    ):
        return None
    available = _as_utc(dispatch.available_at) <= current
    expired = (
        dispatch.status == EvaluationAnnotationDispatchStatus.RUNNING
        and dispatch.lease_expires_at is not None
        and _as_utc(dispatch.lease_expires_at) <= current
    )
    if dispatch.status == EvaluationAnnotationDispatchStatus.PENDING and not available:
        return None
    if dispatch.status == EvaluationAnnotationDispatchStatus.RUNNING and not expired:
        return None
    if dispatch.attempt_count >= max_attempts:
        dispatch.status = EvaluationAnnotationDispatchStatus.FAILED
        dispatch.error_code = "annotation_queue_attempts_exhausted"
        dispatch.failed_count = dispatch.item_count
        dispatch.lease_owner = None
        dispatch.lease_expires_at = None
        for item in dispatch.items:
            item.sync_status = EvaluationAnnotationItemSyncStatus.FAILED
            item.error_code = dispatch.error_code
        await db.flush()
        return None

    dispatch.status = EvaluationAnnotationDispatchStatus.RUNNING
    dispatch.lease_owner = worker_id
    dispatch.lease_expires_at = current + timedelta(seconds=lease_seconds)
    dispatch.attempt_count += 1
    dispatch.started_at = dispatch.started_at or current
    dispatch.error_code = None
    dispatch.failed_count = 0
    for item in dispatch.items:
        if item.sync_status != EvaluationAnnotationItemSyncStatus.SYNCED:
            item.sync_status = EvaluationAnnotationItemSyncStatus.PENDING
            item.attempt_count += 1
            item.error_code = None
    await audit(
        db,
        username="annotation-queue-worker",
        action="evaluation_annotation_dispatch.leased",
        resource_type="evaluation_annotation_dispatch",
        resource_id=dispatch.id,
        namespace_id=dispatch.namespace_id,
        details={
            "public_id": dispatch.public_id,
            "attempt_count": dispatch.attempt_count,
            "item_count": dispatch.item_count,
        },
    )
    await db.flush()
    return AnnotationDispatchLease(
        dispatch_public_id=dispatch.public_id,
        worker_id=worker_id,
        attempt_count=dispatch.attempt_count,
    )


async def load_annotation_dispatch_request(
    db: AsyncSession,
    *,
    dispatch_public_id: str,
    worker_id: str,
) -> AnnotationDispatchRequest:
    worker_id = _safe_worker_id(worker_id)
    dispatch = await _get_dispatch(db, public_id=dispatch_public_id)
    if (
        dispatch.status != EvaluationAnnotationDispatchStatus.RUNNING
        or dispatch.lease_owner != worker_id
    ):
        raise EvaluationHubStateError("Annotation dispatch lease is not owned")
    return AnnotationDispatchRequest(
        dispatch_public_id=dispatch.public_id,
        provider_queue_ref=dispatch.provider_queue_ref,
        expected_score_config_ids=tuple(
            sorted(dispatch.binding.score_config_ids_json)
        ),
        observation_refs=tuple(
            item.source_observation_ref for item in dispatch.items
        ),
    )


def _validate_snapshots(
    dispatch: EvaluationAnnotationDispatch,
    snapshots: list[AnnotationQueueItemSnapshot],
) -> dict[str, AnnotationQueueItemSnapshot]:
    mapped = {value.source_observation_ref: value for value in snapshots}
    expected = {item.source_observation_ref for item in dispatch.items}
    if len(mapped) != len(snapshots) or set(mapped) != expected:
        raise EvaluationHubStateError(
            "Annotation queue receipt does not match the dispatch"
        )
    return mapped


async def acknowledge_annotation_dispatch(
    db: AsyncSession,
    *,
    dispatch_public_id: str,
    worker_id: str,
    snapshots: list[AnnotationQueueItemSnapshot],
    now: datetime | None = None,
) -> EvaluationAnnotationDispatch | None:
    worker_id = _safe_worker_id(worker_id)
    current = now or _utcnow()
    dispatch = await _get_dispatch(
        db,
        public_id=dispatch_public_id,
        for_update=True,
    )
    if (
        dispatch.status != EvaluationAnnotationDispatchStatus.RUNNING
        or dispatch.lease_owner != worker_id
    ):
        return None
    mapped = _validate_snapshots(dispatch, snapshots)
    completed_count = 0
    for item in dispatch.items:
        snapshot = mapped[item.source_observation_ref]
        item.sync_status = EvaluationAnnotationItemSyncStatus.SYNCED
        item.provider_queue_item_ref = snapshot.provider_queue_item_ref
        item.provider_annotation_status = EvaluationAnnotationProviderStatus(
            snapshot.status
        )
        item.provider_created_at = snapshot.created_at
        item.provider_updated_at = snapshot.updated_at
        item.provider_completed_at = snapshot.completed_at
        item.last_reconciled_at = current
        item.error_code = None
        if snapshot.status == EvaluationAnnotationProviderStatus.COMPLETED.value:
            completed_count += 1
    dispatch.status = EvaluationAnnotationDispatchStatus.SYNCED
    dispatch.synced_count = dispatch.item_count
    dispatch.completed_count = completed_count
    dispatch.failed_count = 0
    dispatch.error_code = None
    dispatch.lease_owner = None
    dispatch.lease_expires_at = None
    dispatch.synced_at = current
    dispatch.last_reconciled_at = current
    await enqueue_domain_event(
        db,
        build_evaluation_annotation_dispatch_synchronized(dispatch),
        guaranteed_new=True,
    )
    await audit(
        db,
        username="annotation-queue-worker",
        action="evaluation_annotation_dispatch.synchronized",
        resource_type="evaluation_annotation_dispatch",
        resource_id=dispatch.id,
        namespace_id=dispatch.namespace_id,
        details={
            "public_id": dispatch.public_id,
            "binding_public_id": dispatch.binding.public_id,
            "provider_queue_ref": dispatch.provider_queue_ref,
            "curation_batch_public_id": dispatch.curation_batch.public_id,
            "request_digest": dispatch.request_digest,
            "item_count": dispatch.item_count,
            "synced_count": dispatch.synced_count,
            "completed_count": dispatch.completed_count,
            "attempt_count": dispatch.attempt_count,
        },
    )
    await db.flush()
    return dispatch


async def fail_annotation_dispatch(
    db: AsyncSession,
    *,
    dispatch_public_id: str,
    worker_id: str,
    error_code: str,
    max_attempts: int,
    base_retry_seconds: int,
    max_retry_seconds: int,
    now: datetime | None = None,
) -> EvaluationAnnotationDispatchStatus | None:
    worker_id = _safe_worker_id(worker_id)
    current = now or _utcnow()
    dispatch = await _get_dispatch(
        db,
        public_id=dispatch_public_id,
        for_update=True,
    )
    if (
        dispatch.status != EvaluationAnnotationDispatchStatus.RUNNING
        or dispatch.lease_owner != worker_id
    ):
        return None
    safe_code = _safe_error_code(error_code)
    terminal = dispatch.attempt_count >= max_attempts
    dispatch.status = (
        EvaluationAnnotationDispatchStatus.FAILED
        if terminal
        else EvaluationAnnotationDispatchStatus.PENDING
    )
    dispatch.error_code = safe_code
    dispatch.failed_count = dispatch.item_count
    dispatch.lease_owner = None
    dispatch.lease_expires_at = None
    if terminal:
        dispatch.available_at = current
    else:
        delay = min(
            max_retry_seconds,
            base_retry_seconds * (2 ** max(dispatch.attempt_count - 1, 0)),
        )
        dispatch.available_at = current + timedelta(seconds=delay)
    for item in dispatch.items:
        if item.sync_status != EvaluationAnnotationItemSyncStatus.SYNCED:
            item.sync_status = EvaluationAnnotationItemSyncStatus.FAILED
            item.error_code = safe_code
    await audit(
        db,
        username="annotation-queue-worker",
        action="evaluation_annotation_dispatch.failed",
        resource_type="evaluation_annotation_dispatch",
        resource_id=dispatch.id,
        namespace_id=dispatch.namespace_id,
        details={
            "public_id": dispatch.public_id,
            "status": dispatch.status.value,
            "error_code": safe_code,
            "attempt_count": dispatch.attempt_count,
        },
    )
    await db.flush()
    return dispatch.status


async def retry_annotation_dispatch(
    db: AsyncSession,
    *,
    public_id: str,
    actor: User,
) -> EvaluationAnnotationDispatch:
    dispatch = await _get_dispatch(db, public_id=public_id, for_update=True)
    if dispatch.status != EvaluationAnnotationDispatchStatus.FAILED:
        raise EvaluationHubStateError(
            "Only a failed annotation dispatch can be retried"
        )
    now = _utcnow()
    dispatch.status = EvaluationAnnotationDispatchStatus.PENDING
    dispatch.attempt_count = 0
    dispatch.failed_count = 0
    dispatch.error_code = None
    dispatch.available_at = now
    dispatch.lease_owner = None
    dispatch.lease_expires_at = None
    for item in dispatch.items:
        if item.sync_status != EvaluationAnnotationItemSyncStatus.SYNCED:
            item.sync_status = EvaluationAnnotationItemSyncStatus.PENDING
            item.attempt_count = 0
            item.error_code = None
    await audit(
        db,
        user=actor,
        action="evaluation_annotation_dispatch.retried",
        resource_type="evaluation_annotation_dispatch",
        resource_id=dispatch.id,
        namespace_id=dispatch.namespace_id,
        details={
            "public_id": dispatch.public_id,
            "item_count": dispatch.item_count,
        },
    )
    await db.flush()
    return dispatch


async def reconcile_annotation_dispatch(
    db: AsyncSession,
    *,
    public_id: str,
    actor: User,
    adapter: AnnotationQueuePort | None = None,
) -> EvaluationAnnotationDispatch:
    dispatch = await _get_dispatch(db, public_id=public_id)
    if dispatch.status != EvaluationAnnotationDispatchStatus.SYNCED:
        raise EvaluationHubStateError(
            "Only a synchronized annotation dispatch can be reconciled"
        )
    observation_refs = tuple(
        item.source_observation_ref for item in dispatch.items
    )
    try:
        snapshots = await _selected_adapter(adapter).get_observation_items(
            queue_ref=dispatch.provider_queue_ref,
            observation_refs=observation_refs,
        )
    except LangfuseAnnotationQueueAdapterError as exc:
        raise EvaluationHubProviderError(str(exc)) from exc
    except EvaluationHubProviderError:
        raise
    except Exception as exc:
        raise EvaluationHubProviderError(
            "Annotation queue reconciliation failed"
        ) from exc
    mapped = _validate_snapshots(dispatch, snapshots)
    current = _utcnow()
    dispatch = await _get_dispatch(db, public_id=public_id, for_update=True)
    completed_count = 0
    for item in dispatch.items:
        snapshot = mapped[item.source_observation_ref]
        if item.provider_queue_item_ref != snapshot.provider_queue_item_ref:
            raise EvaluationHubStateError(
                "Annotation queue item identity changed"
            )
        item.provider_annotation_status = EvaluationAnnotationProviderStatus(
            snapshot.status
        )
        item.provider_updated_at = snapshot.updated_at
        item.provider_completed_at = snapshot.completed_at
        item.last_reconciled_at = current
        if snapshot.status == EvaluationAnnotationProviderStatus.COMPLETED.value:
            completed_count += 1
    previous_completed_count = dispatch.completed_count
    dispatch.completed_count = completed_count
    dispatch.last_reconciled_at = current
    await audit(
        db,
        user=actor,
        action="evaluation_annotation_dispatch.reconciled",
        resource_type="evaluation_annotation_dispatch",
        resource_id=dispatch.id,
        namespace_id=dispatch.namespace_id,
        details={
            "public_id": dispatch.public_id,
            "provider_queue_ref": dispatch.provider_queue_ref,
            "item_count": dispatch.item_count,
            "previous_completed_count": previous_completed_count,
            "completed_count": completed_count,
        },
    )
    await db.flush()
    return dispatch
