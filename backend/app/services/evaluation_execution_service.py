from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evaluation import (
    Evaluation,
    EvaluationDataset,
    EvaluationDatasetVersion,
    EvaluationProvider,
    EvaluationResultCompleteness,
    EvaluationResultManifest,
    EvaluationStatus,
    Evaluator,
    EvaluatorVersion,
    Experiment,
    ExperimentStatus,
)
from app.models.user import User
from app.services.audit_service import audit
from app.services.evaluation_ports import (
    EvaluationRunRequest,
    EvaluationRunSummary,
)
from app.services.outbox_event_service import (
    build_evaluation_result_manifest_registered,
    enqueue_domain_event,
)
from app.services.evaluation_service import (
    EvaluationHubNotFoundError,
    EvaluationHubStateError,
)


_SAFE_WORKER_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SAFE_ERROR_CODE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")
_SAFE_PROVIDER_REF = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,254}$"
)
_SAFE_SCHEMA_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class EvaluationExecutionError(ValueError):
    pass


class EvaluationExecutionConfigurationError(EvaluationExecutionError):
    pass


@dataclass(frozen=True, slots=True)
class EvaluationLease:
    evaluation_public_id: str
    worker_id: str
    attempt_count: int


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_worker_id(value: str) -> str:
    if _SAFE_WORKER_ID.fullmatch(value) is None:
        raise EvaluationExecutionError("worker_id is invalid")
    return value


def _safe_error_code(value: str | None) -> str:
    candidate = (value or "").strip().lower()
    if _SAFE_ERROR_CODE.fullmatch(candidate):
        return candidate
    return "evaluation_runner_failed"


def _parse_dataset_version(value: str | None) -> datetime:
    if value is None:
        raise EvaluationExecutionConfigurationError(
            "A pinned Langfuse dataset version is required"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvaluationExecutionConfigurationError(
            "Pinned dataset version is malformed"
        ) from exc
    if parsed.tzinfo is None:
        raise EvaluationExecutionConfigurationError(
            "Pinned dataset version must include a timezone"
        )
    return parsed.astimezone(timezone.utc)


async def _finish_experiment_if_terminal(
    db: AsyncSession,
    *,
    experiment_id: int,
    now: datetime,
) -> None:
    statuses = list(
        (
            await db.scalars(
                select(Evaluation.status).where(
                    Evaluation.experiment_id == experiment_id
                )
            )
        ).all()
    )
    if not statuses or any(
        status in (EvaluationStatus.PENDING, EvaluationStatus.RUNNING)
        for status in statuses
    ):
        return
    experiment = await db.get(Experiment, experiment_id)
    if experiment is None:
        return
    if any(status == EvaluationStatus.FAILED for status in statuses):
        experiment.status = ExperimentStatus.FAILED
    elif any(status == EvaluationStatus.PARTIAL for status in statuses):
        experiment.status = ExperimentStatus.PARTIAL
    elif any(status == EvaluationStatus.CANCELLED for status in statuses):
        experiment.status = ExperimentStatus.CANCELLED
    else:
        experiment.status = ExperimentStatus.COMPLETED
    experiment.ended_at = now


async def _cancel_locked(
    db: AsyncSession,
    *,
    evaluation: Evaluation,
    now: datetime,
) -> None:
    evaluation.status = EvaluationStatus.CANCELLED
    evaluation.ended_at = now
    evaluation.lease_owner = None
    evaluation.lease_expires_at = None
    evaluation.error_code = None
    await _finish_experiment_if_terminal(
        db,
        experiment_id=evaluation.experiment_id,
        now=now,
    )


async def lease_evaluation(
    db: AsyncSession,
    *,
    evaluation_public_id: str,
    worker_id: str,
    lease_seconds: int,
    max_attempts: int,
    now: datetime | None = None,
) -> EvaluationLease | None:
    worker_id = _safe_worker_id(worker_id)
    if lease_seconds < 30 or lease_seconds > 3600:
        raise EvaluationExecutionError(
            "lease_seconds must be between 30 and 3600"
        )
    if max_attempts < 1 or max_attempts > 20:
        raise EvaluationExecutionError(
            "max_attempts must be between 1 and 20"
        )
    current = now or _utcnow()
    evaluation = (
        await db.execute(
            select(Evaluation)
            .where(Evaluation.public_id == evaluation_public_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if evaluation is None:
        return None
    if evaluation.status in (
        EvaluationStatus.COMPLETED,
        EvaluationStatus.PARTIAL,
        EvaluationStatus.FAILED,
        EvaluationStatus.CANCELLED,
    ):
        return None
    if evaluation.cancel_requested_at is not None:
        await _cancel_locked(db, evaluation=evaluation, now=current)
        await audit(
            db,
            username="evaluation-worker",
            action="evaluation.cancelled",
            resource_type="evaluation",
            resource_id=evaluation.id,
            namespace_id=evaluation.namespace_id,
            details={"public_id": evaluation.public_id},
        )
        await db.flush()
        return None

    available = (
        evaluation.available_at is None
        or _as_utc(evaluation.available_at) <= current
    )
    expired = (
        evaluation.status == EvaluationStatus.RUNNING
        and evaluation.lease_expires_at is not None
        and _as_utc(evaluation.lease_expires_at) <= current
    )
    if evaluation.status == EvaluationStatus.PENDING and not available:
        return None
    if evaluation.status == EvaluationStatus.RUNNING and not expired:
        return None
    if evaluation.attempt_count >= max_attempts:
        evaluation.status = EvaluationStatus.FAILED
        evaluation.error_code = "evaluation_attempts_exhausted"
        evaluation.ended_at = current
        evaluation.lease_owner = None
        evaluation.lease_expires_at = None
        await _finish_experiment_if_terminal(
            db,
            experiment_id=evaluation.experiment_id,
            now=current,
        )
        await db.flush()
        return None

    evaluation.status = EvaluationStatus.RUNNING
    evaluation.lease_owner = worker_id
    evaluation.lease_expires_at = current + timedelta(seconds=lease_seconds)
    evaluation.attempt_count += 1
    evaluation.started_at = evaluation.started_at or current
    evaluation.error_code = None
    experiment = await db.get(Experiment, evaluation.experiment_id)
    if experiment is not None:
        experiment.status = ExperimentStatus.RUNNING
        experiment.started_at = experiment.started_at or current
        experiment.ended_at = None
    await audit(
        db,
        username="evaluation-worker",
        action="evaluation.leased",
        resource_type="evaluation",
        resource_id=evaluation.id,
        namespace_id=evaluation.namespace_id,
        details={
            "public_id": evaluation.public_id,
            "attempt_count": evaluation.attempt_count,
        },
    )
    await db.flush()
    return EvaluationLease(
        evaluation_public_id=evaluation.public_id,
        worker_id=worker_id,
        attempt_count=evaluation.attempt_count,
    )


async def load_evaluation_run_request(
    db: AsyncSession,
    *,
    evaluation_public_id: str,
    worker_id: str,
) -> EvaluationRunRequest:
    row = (
        await db.execute(
            select(
                Evaluation,
                Experiment,
                EvaluationDatasetVersion,
                EvaluationDataset,
                EvaluatorVersion,
                Evaluator,
            )
            .join(Experiment, Experiment.id == Evaluation.experiment_id)
            .join(
                EvaluationDatasetVersion,
                EvaluationDatasetVersion.id
                == Experiment.dataset_version_id,
            )
            .join(
                EvaluationDataset,
                EvaluationDataset.id
                == EvaluationDatasetVersion.dataset_id,
            )
            .join(
                EvaluatorVersion,
                EvaluatorVersion.id == Evaluation.evaluator_version_id,
            )
            .join(Evaluator, Evaluator.id == EvaluatorVersion.evaluator_id)
            .where(Evaluation.public_id == evaluation_public_id)
        )
    ).one_or_none()
    if row is None:
        raise EvaluationHubNotFoundError("Evaluation not found")
    evaluation, experiment, version, dataset, evaluator_version, evaluator = row
    if (
        evaluation.status != EvaluationStatus.RUNNING
        or evaluation.lease_owner != worker_id
    ):
        raise EvaluationHubStateError("Evaluation lease is not owned")
    if (
        experiment.provider != EvaluationProvider.LANGFUSE
        or dataset.provider != EvaluationProvider.LANGFUSE
        or not dataset.provider_dataset_ref
    ):
        raise EvaluationExecutionConfigurationError(
            "Evaluation is not backed by a Langfuse dataset"
        )
    return EvaluationRunRequest(
        evaluation_public_id=evaluation.public_id,
        experiment_public_id=experiment.public_id,
        experiment_name=experiment.name,
        namespace_id=evaluation.namespace_id,
        dataset_name=f"duckdock.ns{evaluation.namespace_id}.{dataset.name}",
        provider_dataset_ref=dataset.provider_dataset_ref,
        dataset_version=_parse_dataset_version(version.provider_version_ref),
        expected_item_count=version.item_count,
        target_type=experiment.target_type,
        target_ref=experiment.target_ref,
        target_digest=experiment.target_digest,
        evaluator_kind=evaluator.kind.value,
        evaluator_provider=evaluator.provider.value,
        evaluator_implementation_ref=evaluator_version.implementation_ref,
        evaluator_config_digest=evaluator_version.config_digest,
    )


async def renew_evaluation_lease(
    db: AsyncSession,
    *,
    evaluation_public_id: str,
    worker_id: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> bool:
    current = now or _utcnow()
    evaluation = (
        await db.execute(
            select(Evaluation)
            .where(Evaluation.public_id == evaluation_public_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        evaluation is None
        or evaluation.status != EvaluationStatus.RUNNING
        or evaluation.lease_owner != worker_id
        or evaluation.cancel_requested_at is not None
    ):
        return False
    evaluation.lease_expires_at = current + timedelta(seconds=lease_seconds)
    await db.flush()
    return True


async def acknowledge_evaluation(
    db: AsyncSession,
    *,
    evaluation_public_id: str,
    worker_id: str,
    summary: EvaluationRunSummary,
    now: datetime | None = None,
) -> EvaluationStatus | None:
    current = now or _utcnow()
    evaluation = (
        await db.execute(
            select(Evaluation)
            .where(Evaluation.public_id == evaluation_public_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        evaluation is None
        or evaluation.status != EvaluationStatus.RUNNING
        or evaluation.lease_owner != worker_id
    ):
        return None
    if evaluation.cancel_requested_at is not None:
        await _cancel_locked(db, evaluation=evaluation, now=current)
        await db.flush()
        return None
    try:
        completeness = EvaluationResultCompleteness(
            summary.completeness
        )
        manifest_completeness = EvaluationResultCompleteness(
            summary.result_manifest.completeness
        )
    except ValueError as exc:
        raise EvaluationExecutionError(
            "Evaluation result completeness is invalid"
        ) from exc
    if completeness != manifest_completeness:
        raise EvaluationExecutionError(
            "Evaluation and manifest completeness differ"
        )
    if (
        summary.total_count < 0
        or summary.processed_count < 0
        or summary.scored_count < 0
        or summary.passed_count < 0
        or summary.failed_count < 0
        or summary.error_count < 0
        or summary.processed_count > summary.total_count
        or summary.scored_count > summary.processed_count
        or summary.passed_count + summary.failed_count
        != summary.scored_count
        or summary.scored_count + summary.error_count
        != summary.total_count
    ):
        raise EvaluationExecutionError(
            "Evaluation result counts are inconsistent"
        )
    manifest_summary = summary.result_manifest
    if (
        manifest_summary.expected_count != summary.total_count
        or manifest_summary.processed_count != summary.processed_count
        or manifest_summary.scored_count != summary.scored_count
        or manifest_summary.passed_count != summary.passed_count
        or manifest_summary.failed_count != summary.failed_count
        or manifest_summary.error_count != summary.error_count
        or manifest_summary.loss_reason != summary.loss_reason
    ):
        raise EvaluationExecutionError(
            "Evaluation and manifest summaries differ"
        )
    if completeness == EvaluationResultCompleteness.COMPLETE:
        if summary.error_count != 0 or summary.loss_reason is not None:
            raise EvaluationExecutionError(
                "Complete evaluation cannot declare result loss"
            )
    elif summary.error_count == 0 or _safe_error_code(
        summary.loss_reason
    ) != summary.loss_reason:
        raise EvaluationExecutionError(
            "Partial evaluation requires a safe loss reason"
        )
    if summary.score is not None and not 0 <= summary.score <= 1:
        raise EvaluationExecutionError(
            "Evaluation score is outside [0, 1]"
        )
    if (
        _SAFE_PROVIDER_REF.fullmatch(
            manifest_summary.provider_dataset_ref
        )
        is None
        or _SAFE_PROVIDER_REF.fullmatch(
            manifest_summary.provider_experiment_ref
        )
        is None
        or _SAFE_SCHEMA_NAME.fullmatch(manifest_summary.schema_name)
        is None
        or _SAFE_SCHEMA_NAME.fullmatch(manifest_summary.schema_version)
        is None
        or _SHA256.fullmatch(manifest_summary.content_digest) is None
    ):
        raise EvaluationExecutionError(
            "Evaluation result manifest identity is invalid"
        )
    experiment = await db.get(Experiment, evaluation.experiment_id)
    if experiment is None:
        raise EvaluationHubNotFoundError("Experiment not found")
    dataset = await db.scalar(
        select(EvaluationDataset)
        .join(
            EvaluationDatasetVersion,
            EvaluationDatasetVersion.dataset_id == EvaluationDataset.id,
        )
        .where(
            EvaluationDatasetVersion.id == experiment.dataset_version_id
        )
    )
    if (
        dataset is None
        or manifest_summary.provider != experiment.provider.value
        or manifest_summary.provider_dataset_ref
        != dataset.provider_dataset_ref
        or manifest_summary.provider_experiment_ref
        != summary.provider_evaluation_ref
    ):
        raise EvaluationExecutionError(
            "Evaluation result manifest does not match pinned providers"
        )
    latest_manifest_version = await db.scalar(
        select(func.max(EvaluationResultManifest.version)).where(
            EvaluationResultManifest.evaluation_id == evaluation.id
        )
    )
    manifest = EvaluationResultManifest(
        public_id=f"erm_{uuid.uuid4().hex}",
        namespace_id=evaluation.namespace_id,
        evaluation_id=evaluation.id,
        version=int(latest_manifest_version or 0) + 1,
        provider=experiment.provider,
        provider_dataset_ref=manifest_summary.provider_dataset_ref,
        provider_experiment_ref=manifest_summary.provider_experiment_ref,
        schema_name=manifest_summary.schema_name,
        schema_version=manifest_summary.schema_version,
        content_digest=manifest_summary.content_digest,
        score=summary.score,
        expected_count=manifest_summary.expected_count,
        processed_count=manifest_summary.processed_count,
        scored_count=manifest_summary.scored_count,
        passed_count=manifest_summary.passed_count,
        failed_count=manifest_summary.failed_count,
        error_count=manifest_summary.error_count,
        completeness=completeness,
        loss_reason=manifest_summary.loss_reason,
        created_at=current,
    )
    db.add(manifest)
    await db.flush()
    await enqueue_domain_event(
        db,
        build_evaluation_result_manifest_registered(
            manifest,
            evaluation_public_id=evaluation.public_id,
        ),
        guaranteed_new=True,
    )
    evaluation.status = (
        EvaluationStatus.COMPLETED
        if completeness == EvaluationResultCompleteness.COMPLETE
        else EvaluationStatus.PARTIAL
    )
    evaluation.provider_evaluation_ref = summary.provider_evaluation_ref
    evaluation.score = summary.score
    evaluation.total_count = summary.total_count
    evaluation.processed_count = summary.processed_count
    evaluation.scored_count = summary.scored_count
    evaluation.passed_count = summary.passed_count
    evaluation.failed_count = summary.failed_count
    evaluation.error_count = summary.error_count
    evaluation.result_completeness = completeness
    evaluation.error_code = None
    evaluation.ended_at = current
    evaluation.lease_owner = None
    evaluation.lease_expires_at = None
    await _finish_experiment_if_terminal(
        db,
        experiment_id=evaluation.experiment_id,
        now=current,
    )
    await audit(
        db,
        username="evaluation-worker",
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
            "result_completeness": completeness.value,
            "result_manifest_public_id": manifest.public_id,
            "attempt_count": evaluation.attempt_count,
        },
    )
    await audit(
        db,
        username="evaluation-worker",
        action="evaluation_result_manifest.registered",
        resource_type="evaluation_result_manifest",
        resource_id=manifest.id,
        namespace_id=manifest.namespace_id,
        details={
            "public_id": manifest.public_id,
            "evaluation_public_id": evaluation.public_id,
            "version": manifest.version,
            "provider": manifest.provider.value,
            "provider_dataset_ref": manifest.provider_dataset_ref,
            "provider_experiment_ref": manifest.provider_experiment_ref,
            "schema_name": manifest.schema_name,
            "schema_version": manifest.schema_version,
            "content_digest": manifest.content_digest,
            "expected_count": manifest.expected_count,
            "processed_count": manifest.processed_count,
            "scored_count": manifest.scored_count,
            "error_count": manifest.error_count,
            "completeness": manifest.completeness.value,
            "loss_reason": manifest.loss_reason,
        },
    )
    await db.flush()
    return evaluation.status


async def fail_evaluation(
    db: AsyncSession,
    *,
    evaluation_public_id: str,
    worker_id: str,
    error_code: str | None,
    max_attempts: int,
    base_retry_seconds: int,
    max_retry_seconds: int,
    now: datetime | None = None,
) -> EvaluationStatus | None:
    current = now or _utcnow()
    evaluation = (
        await db.execute(
            select(Evaluation)
            .where(Evaluation.public_id == evaluation_public_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        evaluation is None
        or evaluation.status != EvaluationStatus.RUNNING
        or evaluation.lease_owner != worker_id
    ):
        return None
    if evaluation.cancel_requested_at is not None:
        await _cancel_locked(db, evaluation=evaluation, now=current)
        await db.flush()
        return EvaluationStatus.CANCELLED

    evaluation.error_code = _safe_error_code(error_code)
    evaluation.lease_owner = None
    evaluation.lease_expires_at = None
    if evaluation.attempt_count >= max_attempts:
        evaluation.status = EvaluationStatus.FAILED
        evaluation.ended_at = current
        await _finish_experiment_if_terminal(
            db,
            experiment_id=evaluation.experiment_id,
            now=current,
        )
        action = "evaluation.failed"
    else:
        delay = min(
            base_retry_seconds
            * (2 ** max(evaluation.attempt_count - 1, 0)),
            max_retry_seconds,
        )
        evaluation.status = EvaluationStatus.PENDING
        evaluation.available_at = current + timedelta(seconds=delay)
        action = "evaluation.retry_scheduled"
    await audit(
        db,
        username="evaluation-worker",
        action=action,
        resource_type="evaluation",
        resource_id=evaluation.id,
        namespace_id=evaluation.namespace_id,
        details={
            "public_id": evaluation.public_id,
            "error_code": evaluation.error_code,
            "attempt_count": evaluation.attempt_count,
        },
    )
    await db.flush()
    return evaluation.status


async def request_evaluation_cancel(
    db: AsyncSession,
    *,
    evaluation: Evaluation,
    actor: User,
    now: datetime | None = None,
) -> Evaluation:
    current = now or _utcnow()
    row = (
        await db.execute(
            select(Evaluation)
            .where(Evaluation.id == evaluation.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise EvaluationHubNotFoundError("Evaluation not found")
    if row.status == EvaluationStatus.CANCELLED:
        return row
    if row.status in (
        EvaluationStatus.COMPLETED,
        EvaluationStatus.PARTIAL,
        EvaluationStatus.FAILED,
    ):
        raise EvaluationHubStateError(
            "Terminal evaluation cannot be cancelled"
        )
    row.cancel_requested_at = row.cancel_requested_at or current
    if row.status == EvaluationStatus.PENDING or (
        row.status == EvaluationStatus.RUNNING
        and row.lease_owner is None
    ):
        await _cancel_locked(db, evaluation=row, now=current)
    await audit(
        db,
        user=actor,
        action="evaluation.cancel_requested",
        resource_type="evaluation",
        resource_id=row.id,
        namespace_id=row.namespace_id,
        details={
            "public_id": row.public_id,
            "status": row.status.value,
        },
    )
    await db.flush()
    return row


async def retry_failed_evaluation(
    db: AsyncSession,
    *,
    evaluation: Evaluation,
    actor: User,
    now: datetime | None = None,
) -> Evaluation:
    current = now or _utcnow()
    row = (
        await db.execute(
            select(Evaluation)
            .where(Evaluation.id == evaluation.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise EvaluationHubNotFoundError("Evaluation not found")
    if row.status not in (
        EvaluationStatus.FAILED,
        EvaluationStatus.PARTIAL,
    ):
        raise EvaluationHubStateError(
            "Only failed or partial evaluations can be retried"
        )
    previous_attempts = row.attempt_count
    row.status = EvaluationStatus.PENDING
    row.available_at = current
    row.attempt_count = 0
    row.error_code = None
    row.provider_evaluation_ref = None
    row.score = None
    row.total_count = 0
    row.processed_count = 0
    row.scored_count = 0
    row.passed_count = 0
    row.failed_count = 0
    row.error_count = 0
    row.result_completeness = None
    row.ended_at = None
    row.cancel_requested_at = None
    row.lease_owner = None
    row.lease_expires_at = None
    experiment = await db.get(Experiment, row.experiment_id)
    if experiment is not None:
        experiment.status = ExperimentStatus.RUNNING
        experiment.ended_at = None
    await audit(
        db,
        user=actor,
        action="evaluation.retried",
        resource_type="evaluation",
        resource_id=row.id,
        namespace_id=row.namespace_id,
        details={
            "public_id": row.public_id,
            "previous_attempts": previous_attempts,
        },
    )
    await db.flush()
    return row


async def list_evaluation_result_manifests(
    db: AsyncSession,
    *,
    evaluation_id: int,
    limit: int = 100,
) -> list[EvaluationResultManifest]:
    if limit < 1 or limit > 200:
        raise EvaluationExecutionError("limit must be between 1 and 200")
    return list(
        (
            await db.scalars(
                select(EvaluationResultManifest)
                .where(
                    EvaluationResultManifest.evaluation_id == evaluation_id
                )
                .order_by(
                    EvaluationResultManifest.version.desc(),
                    EvaluationResultManifest.id.desc(),
                )
                .limit(limit)
            )
        ).all()
    )


async def get_latest_evaluation_result_manifest(
    db: AsyncSession,
    *,
    evaluation_id: int,
) -> EvaluationResultManifest | None:
    return (
        await db.execute(
            select(EvaluationResultManifest)
            .where(EvaluationResultManifest.evaluation_id == evaluation_id)
            .order_by(
                EvaluationResultManifest.version.desc(),
                EvaluationResultManifest.id.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def list_due_evaluation_ids(
    db: AsyncSession,
    *,
    limit: int,
    now: datetime | None = None,
) -> list[str]:
    current = now or _utcnow()
    if limit < 1 or limit > 500:
        raise EvaluationExecutionError("limit must be between 1 and 500")
    return list(
        (
            await db.scalars(
                select(Evaluation.public_id)
                .where(
                    or_(
                        (
                            Evaluation.status == EvaluationStatus.PENDING
                        )
                        & (Evaluation.available_at <= current),
                        (
                            Evaluation.status == EvaluationStatus.RUNNING
                        )
                        & (Evaluation.lease_expires_at <= current),
                    )
                )
                .order_by(
                    func.coalesce(
                        Evaluation.available_at,
                        Evaluation.lease_expires_at,
                    ),
                    Evaluation.id,
                )
                .limit(limit)
            )
        ).all()
    )
