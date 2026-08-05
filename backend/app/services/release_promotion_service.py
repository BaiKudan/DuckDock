from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.time import ensure_utc
from app.models.deployment import AgentDeploymentStatus
from app.models.execution import AgentRun, AgentRunStatus, TrustLevel
from app.models.release_control import (
    ReleaseApprovalDecision,
    ReleaseCandidate,
    ReleaseCandidateApproval,
    ReleaseCanaryEvaluation,
    ReleaseCanaryOutcome,
    ReleaseDeploymentReceipt,
    ReleaseEnvironment,
    ReleaseEnvironmentKind,
    ReleaseEnvironmentRelease,
    ReleaseEnvironmentReleaseStatus,
    ReleaseEnvironmentStatus,
    ReleaseExceptionReviewDecision,
    ReleasePolicyDecision,
    ReleasePolicyEnforcementOutcome,
    ReleasePolicyException,
    ReleasePolicyExceptionReview,
    ReleasePolicyRawOutcome,
    ReleasePolicyRuleVerdict,
    ReleasePromotion,
    ReleasePromotionStatus,
    ReleasePromotionStrategy,
    ReleaseReceiptKind,
    ReleaseReceiptStatus,
    ReleaseRollback,
    ReleaseRollbackStatus,
)
from app.models.user import User
from app.schemas.release_control import (
    ReleaseCandidateApprovalCreate,
    ReleaseCandidateApprovalOut,
    ReleaseCanaryConfig,
    ReleaseCanaryEvaluationCreate,
    ReleaseCanaryEvaluationOut,
    ReleaseDeploymentReceiptCreate,
    ReleaseDeploymentReceiptOut,
    ReleaseEnvironmentReleaseOut,
    ReleasePolicyExceptionCreate,
    ReleasePolicyExceptionOut,
    ReleasePolicyExceptionReviewCreate,
    ReleasePolicyExceptionReviewOut,
    ReleasePromotionCreate,
    ReleasePromotionOut,
    ReleaseRollbackCreate,
    ReleaseRollbackOut,
    RuntimeReceiptReportedStatus,
)
from app.services.audit_service import audit
from app.services.outbox_event_service import (
    build_release_candidate_approval_recorded,
    build_release_canary_evaluated,
    build_release_environment_activated,
    build_release_policy_exception_requested,
    build_release_policy_exception_reviewed,
    build_release_promotion_dispatched,
    build_release_receipt_recorded,
    build_release_rollback_dispatched,
    enqueue_domain_event,
)
from app.services.release_control_service import (
    ReleaseControlConflictError,
    ReleaseControlNotFoundError,
    ReleaseControlReferenceError,
    ReleaseControlStateError,
    ReleaseControlTenantMismatchError,
    get_release_candidate,
    get_release_policy_decision,
)
from app.services.reporter_identity_service import ReporterExecutionIdentity


MAX_EXCEPTION_LIFETIME = timedelta(days=30)
TERMINAL_RUN_STATUSES = {
    AgentRunStatus.SUCCEEDED,
    AgentRunStatus.FAILED,
    AgentRunStatus.CANCELLED,
    AgentRunStatus.TIMED_OUT,
}


def _public_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _is_unique_violation(exc: IntegrityError) -> bool:
    args = getattr(exc.orig, "args", ())
    return bool(args and args[0] == 1062) or "unique constraint failed" in str(exc.orig).lower()


def _comment_digest(comment: str | None) -> str | None:
    return hashlib.sha256(comment.encode("utf-8")).hexdigest() if comment is not None else None


def _promotion_options():
    return (
        selectinload(ReleasePromotion.candidate),
        selectinload(ReleasePromotion.policy_decision),
        selectinload(ReleasePromotion.source_environment),
        selectinload(ReleasePromotion.target_environment),
    )


def _exception_options():
    return (
        selectinload(ReleasePolicyException.candidate),
        selectinload(ReleasePolicyException.policy_decision),
        selectinload(ReleasePolicyException.review),
    )


def _rollback_options():
    return (
        selectinload(ReleaseRollback.promotion),
        selectinload(ReleaseRollback.environment),
        selectinload(ReleaseRollback.source_candidate),
        selectinload(ReleaseRollback.target_environment_release).selectinload(
            ReleaseEnvironmentRelease.candidate
        ),
    )


async def create_release_candidate_approval(
    db: AsyncSession,
    *,
    candidate_public_id: str,
    request: ReleaseCandidateApprovalCreate,
    actor: User,
) -> ReleaseCandidateApproval:
    candidate = await get_release_candidate(db, candidate_public_id)
    decision = await get_release_policy_decision(db, request.policy_decision_public_id)
    if candidate.namespace_id != request.namespace_id:
        raise ReleaseControlTenantMismatchError("ReleaseCandidate belongs to another Namespace")
    if decision.namespace_id != request.namespace_id or decision.candidate_id != candidate.id:
        raise ReleaseControlTenantMismatchError("PolicyDecision does not belong to this candidate")
    if candidate.target_environment.protected and actor.id == candidate.created_by_user_id:
        raise ReleaseControlStateError("protected Environment approval requires four-eyes review")
    document = {
        "candidate_public_id": candidate.public_id,
        "candidate_digest": candidate.candidate_digest,
        "policy_decision_public_id": decision.public_id,
        "decision_digest": decision.decision_digest,
        "decision": request.decision.value,
        "role": request.role.value,
        "comment_digest": _comment_digest(request.comment),
        "reviewed_by_user_id": actor.id,
    }
    approval_digest = _digest(document)
    replay = await db.scalar(
        select(ReleaseCandidateApproval)
        .options(
            selectinload(ReleaseCandidateApproval.candidate),
            selectinload(ReleaseCandidateApproval.policy_decision),
        )
        .where(
            ReleaseCandidateApproval.namespace_id == request.namespace_id,
            ReleaseCandidateApproval.idempotency_key == request.idempotency_key,
        )
    )
    if replay is not None:
        if replay.approval_digest == approval_digest:
            return replay
        raise ReleaseControlConflictError("idempotency key is bound to another candidate approval")
    row = ReleaseCandidateApproval(
        public_id=_public_id("rcapp"),
        namespace_id=request.namespace_id,
        candidate_id=candidate.id,
        policy_decision_id=decision.id,
        decision=request.decision,
        role=request.role,
        comment=request.comment,
        idempotency_key=request.idempotency_key,
        approval_digest=approval_digest,
        reviewed_by_user_id=actor.id,
    )
    row.candidate = candidate
    row.policy_decision = decision
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        if _is_unique_violation(exc):
            raise ReleaseControlConflictError(
                "reviewer already decided this candidate/policy decision"
            ) from exc
        raise
    await audit(
        db,
        user=actor,
        action="release.candidate_approval.recorded",
        resource_type="release_candidate_approval",
        resource_id=row.id,
        namespace_id=row.namespace_id,
        details={
            "public_id": row.public_id,
            "candidate_public_id": candidate.public_id,
            "policy_decision_public_id": decision.public_id,
            "decision": row.decision.value,
            "role": row.role.value,
            "approval_digest": row.approval_digest,
            "reviewed_by_user_id": row.reviewed_by_user_id,
        },
    )
    await enqueue_domain_event(db, build_release_candidate_approval_recorded(row), guaranteed_new=True)
    return row


async def list_release_candidate_approvals(
    db: AsyncSession,
    *,
    namespace_id: int,
    candidate_public_id: str,
) -> list[ReleaseCandidateApproval]:
    candidate = await get_release_candidate(db, candidate_public_id)
    if candidate.namespace_id != namespace_id:
        raise ReleaseControlTenantMismatchError("ReleaseCandidate belongs to another Namespace")
    return list(
        (
            await db.scalars(
                select(ReleaseCandidateApproval)
                .options(
                    selectinload(ReleaseCandidateApproval.candidate),
                    selectinload(ReleaseCandidateApproval.policy_decision),
                )
                .where(ReleaseCandidateApproval.candidate_id == candidate.id)
                .order_by(ReleaseCandidateApproval.created_at, ReleaseCandidateApproval.id)
            )
        ).all()
    )


def release_candidate_approval_out(row: ReleaseCandidateApproval) -> ReleaseCandidateApprovalOut:
    return ReleaseCandidateApprovalOut(
        public_id=row.public_id,
        namespace_id=row.namespace_id,
        candidate_public_id=row.candidate.public_id,
        policy_decision_public_id=row.policy_decision.public_id,
        decision=row.decision,
        role=row.role,
        comment=row.comment,
        approval_digest=row.approval_digest,
        reviewed_by_user_id=row.reviewed_by_user_id,
        created_at=row.created_at,
    )


async def create_release_policy_exception(
    db: AsyncSession,
    *,
    request: ReleasePolicyExceptionCreate,
    actor: User,
) -> ReleasePolicyException:
    decision = await get_release_policy_decision(db, request.policy_decision_public_id)
    if decision.namespace_id != request.namespace_id:
        raise ReleaseControlTenantMismatchError("PolicyDecision belongs to another Namespace")
    if decision.raw_outcome != ReleasePolicyRawOutcome.FAIL:
        raise ReleaseControlStateError("Policy exception requires a failed raw decision")
    failed_rule_ids = {
        result.rule_id
        for result in decision.rule_results
        if result.verdict == ReleasePolicyRuleVerdict.FAIL
    }
    requested_rule_ids = set(request.waived_rule_ids)
    if not requested_rule_ids.issubset(failed_rule_ids):
        raise ReleaseControlReferenceError("exception can waive only failed rules on the exact decision")
    now = datetime.now(timezone.utc)
    expires_at = ensure_utc(request.expires_at)
    if expires_at <= now or expires_at - now > MAX_EXCEPTION_LIFETIME:
        raise ReleaseControlReferenceError("exception expiry must be within the next 30 days")
    document = {
        "candidate_public_id": decision.candidate.public_id,
        "policy_decision_public_id": decision.public_id,
        "decision_digest": decision.decision_digest,
        "waived_rule_ids": request.waived_rule_ids,
        "reason_digest": hashlib.sha256(request.reason.encode("utf-8")).hexdigest(),
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        "requested_by_user_id": actor.id,
    }
    exception_digest = _digest(document)
    replay = await db.scalar(
        select(ReleasePolicyException)
        .options(*_exception_options())
        .where(
            ReleasePolicyException.namespace_id == request.namespace_id,
            ReleasePolicyException.idempotency_key == request.idempotency_key,
        )
    )
    if replay is not None:
        if replay.exception_digest == exception_digest:
            return replay
        raise ReleaseControlConflictError("idempotency key is bound to another Policy exception")
    row = ReleasePolicyException(
        public_id=_public_id("rpex"),
        namespace_id=request.namespace_id,
        candidate_id=decision.candidate_id,
        policy_decision_id=decision.id,
        waived_rule_ids_json=request.waived_rule_ids,
        waived_rule_count=len(request.waived_rule_ids),
        reason=request.reason,
        expires_at=expires_at,
        idempotency_key=request.idempotency_key,
        exception_digest=exception_digest,
        requested_by_user_id=actor.id,
    )
    row.candidate = decision.candidate
    row.policy_decision = decision
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        if _is_unique_violation(exc):
            raise ReleaseControlConflictError("Policy exception idempotency conflict") from exc
        raise
    await audit(
        db,
        user=actor,
        action="release.policy_exception.requested",
        resource_type="release_policy_exception",
        resource_id=row.id,
        namespace_id=row.namespace_id,
        details={
            "public_id": row.public_id,
            "candidate_public_id": row.candidate.public_id,
            "policy_decision_public_id": row.policy_decision.public_id,
            "waived_rule_ids_csv": ",".join(row.waived_rule_ids_json),
            "waived_rule_count": row.waived_rule_count,
            "expires_at": expires_at.isoformat(),
            "exception_digest": row.exception_digest,
        },
    )
    await enqueue_domain_event(db, build_release_policy_exception_requested(row), guaranteed_new=True)
    return row


async def get_release_policy_exception(db: AsyncSession, public_id: str) -> ReleasePolicyException:
    row = await db.scalar(
        select(ReleasePolicyException)
        .options(*_exception_options())
        .where(ReleasePolicyException.public_id == public_id)
    )
    if row is None:
        raise ReleaseControlNotFoundError("Release Policy exception was not found")
    return row


async def list_release_policy_exceptions(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int = 100,
) -> list[ReleasePolicyException]:
    return list(
        (
            await db.scalars(
                select(ReleasePolicyException)
                .options(*_exception_options())
                .where(ReleasePolicyException.namespace_id == namespace_id)
                .order_by(
                    ReleasePolicyException.created_at.desc(),
                    ReleasePolicyException.id.desc(),
                )
                .limit(limit)
            )
        ).all()
    )


async def review_release_policy_exception(
    db: AsyncSession,
    *,
    exception_public_id: str,
    request: ReleasePolicyExceptionReviewCreate,
    actor: User,
) -> ReleasePolicyExceptionReview:
    exception = await get_release_policy_exception(db, exception_public_id)
    if exception.namespace_id != request.namespace_id:
        raise ReleaseControlTenantMismatchError("Policy exception belongs to another Namespace")
    if actor.id == exception.requested_by_user_id:
        raise ReleaseControlStateError("Policy exception review requires a different operator")
    document = {
        "exception_public_id": exception.public_id,
        "exception_digest": exception.exception_digest,
        "decision": request.decision.value,
        "comment_digest": _comment_digest(request.comment),
        "reviewed_by_user_id": actor.id,
    }
    review_digest = _digest(document)
    replay = await db.scalar(
        select(ReleasePolicyExceptionReview)
        .options(selectinload(ReleasePolicyExceptionReview.exception))
        .where(
            ReleasePolicyExceptionReview.namespace_id == request.namespace_id,
            ReleasePolicyExceptionReview.idempotency_key == request.idempotency_key,
        )
    )
    if replay is not None:
        if replay.review_digest == review_digest:
            return replay
        raise ReleaseControlConflictError("idempotency key is bound to another exception review")
    if exception.review is not None:
        raise ReleaseControlConflictError("Policy exception already has an immutable review")
    row = ReleasePolicyExceptionReview(
        public_id=_public_id("rpexr"),
        namespace_id=request.namespace_id,
        exception_id=exception.id,
        decision=request.decision,
        comment=request.comment,
        idempotency_key=request.idempotency_key,
        review_digest=review_digest,
        reviewed_by_user_id=actor.id,
    )
    row.exception = exception
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        if _is_unique_violation(exc):
            raise ReleaseControlConflictError("Policy exception already has a review") from exc
        raise
    await audit(
        db,
        user=actor,
        action="release.policy_exception.reviewed",
        resource_type="release_policy_exception_review",
        resource_id=row.id,
        namespace_id=row.namespace_id,
        details={
            "public_id": row.public_id,
            "exception_public_id": exception.public_id,
            "decision": row.decision.value,
            "review_digest": row.review_digest,
            "reviewed_by_user_id": row.reviewed_by_user_id,
        },
    )
    await enqueue_domain_event(
        db,
        build_release_policy_exception_reviewed(row),
        guaranteed_new=True,
    )
    return row


def _exception_effective(row: ReleasePolicyException, *, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    return (
        row.review is not None
        and row.review.decision == ReleaseExceptionReviewDecision.APPROVED
        and ensure_utc(row.expires_at) > now
    )


def release_policy_exception_out(row: ReleasePolicyException) -> ReleasePolicyExceptionOut:
    review = row.review
    return ReleasePolicyExceptionOut(
        public_id=row.public_id,
        namespace_id=row.namespace_id,
        candidate_public_id=row.candidate.public_id,
        policy_decision_public_id=row.policy_decision.public_id,
        waived_rule_ids=list(row.waived_rule_ids_json),
        waived_rule_count=row.waived_rule_count,
        reason=row.reason,
        expires_at=row.expires_at,
        exception_digest=row.exception_digest,
        requested_by_user_id=row.requested_by_user_id,
        created_at=row.created_at,
        effective=_exception_effective(row),
        review=(
            ReleasePolicyExceptionReviewOut(
                public_id=review.public_id,
                decision=review.decision,
                comment=review.comment,
                review_digest=review.review_digest,
                reviewed_by_user_id=review.reviewed_by_user_id,
                created_at=review.created_at,
            )
            if review is not None
            else None
        ),
    )


async def _active_environment_release(
    db: AsyncSession,
    *,
    environment_id: int,
    lock: bool = False,
) -> ReleaseEnvironmentRelease | None:
    statement = (
        select(ReleaseEnvironmentRelease)
        .options(
            selectinload(ReleaseEnvironmentRelease.environment),
            selectinload(ReleaseEnvironmentRelease.candidate),
            selectinload(ReleaseEnvironmentRelease.receipt),
            selectinload(ReleaseEnvironmentRelease.previous_release),
        )
        .where(
            ReleaseEnvironmentRelease.environment_id == environment_id,
            ReleaseEnvironmentRelease.status == ReleaseEnvironmentReleaseStatus.ACTIVE,
        )
    )
    if lock:
        statement = statement.with_for_update()
    rows = list((await db.scalars(statement)).all())
    if len(rows) > 1:
        raise ReleaseControlStateError("Environment has multiple ACTIVE release records")
    return rows[0] if rows else None


async def _effective_exception_digest(
    db: AsyncSession,
    *,
    decision: ReleasePolicyDecision,
) -> str | None:
    failed_rule_ids = {
        result.rule_id
        for result in decision.rule_results
        if result.verdict == ReleasePolicyRuleVerdict.FAIL
    }
    if not failed_rule_ids:
        return None
    now = datetime.now(timezone.utc)
    rows = list(
        (
            await db.scalars(
                select(ReleasePolicyException)
                .options(selectinload(ReleasePolicyException.review))
                .where(
                    ReleasePolicyException.policy_decision_id == decision.id,
                    ReleasePolicyException.expires_at > now,
                )
                .order_by(ReleasePolicyException.created_at, ReleasePolicyException.id)
            )
        ).all()
    )
    effective = [row for row in rows if _exception_effective(row, now=now)]
    covered = {
        rule_id
        for row in effective
        for rule_id in row.waived_rule_ids_json
    }
    if not failed_rule_ids.issubset(covered):
        return None
    return _digest(
        [
            {
                "exception_public_id": row.public_id,
                "exception_digest": row.exception_digest,
                "review_digest": row.review.review_digest if row.review is not None else None,
                "expires_at": ensure_utc(row.expires_at).isoformat().replace("+00:00", "Z"),
            }
            for row in effective
        ]
    )


async def _approval_snapshot(
    db: AsyncSession,
    *,
    candidate: ReleaseCandidate,
    decision: ReleasePolicyDecision,
) -> tuple[list[ReleaseCandidateApproval], str]:
    rows = list(
        (
            await db.scalars(
                select(ReleaseCandidateApproval)
                .options(
                    selectinload(ReleaseCandidateApproval.candidate),
                    selectinload(ReleaseCandidateApproval.policy_decision),
                )
                .where(
                    ReleaseCandidateApproval.candidate_id == candidate.id,
                    ReleaseCandidateApproval.policy_decision_id == decision.id,
                )
                .order_by(ReleaseCandidateApproval.created_at, ReleaseCandidateApproval.id)
            )
        ).all()
    )
    if any(row.decision == ReleaseApprovalDecision.REJECTED for row in rows):
        raise ReleaseControlStateError("candidate has an immutable rejected approval")
    approved = [row for row in rows if row.decision == ReleaseApprovalDecision.APPROVED]
    distinct_reviewers = {row.reviewed_by_user_id for row in approved}
    required = candidate.target_environment.minimum_approvals
    if len(distinct_reviewers) < required:
        raise ReleaseControlStateError(
            f"target Environment requires {required} distinct approvals; got {len(distinct_reviewers)}"
        )
    document = [
        {
            "approval_public_id": row.public_id,
            "approval_digest": row.approval_digest,
            "reviewed_by_user_id": row.reviewed_by_user_id,
            "role": row.role.value,
        }
        for row in approved
    ]
    return approved, _digest(document)


async def _predecessor_environment(
    db: AsyncSession,
    *,
    target: ReleaseEnvironment,
) -> ReleaseEnvironment | None:
    return await db.scalar(
        select(ReleaseEnvironment)
        .where(
            ReleaseEnvironment.namespace_id == target.namespace_id,
            ReleaseEnvironment.status == ReleaseEnvironmentStatus.ACTIVE,
            ReleaseEnvironment.promotion_order < target.promotion_order,
        )
        .order_by(ReleaseEnvironment.promotion_order.desc(), ReleaseEnvironment.id.desc())
        .limit(1)
    )


async def create_release_promotion(
    db: AsyncSession,
    *,
    request: ReleasePromotionCreate,
    actor: User,
) -> ReleasePromotion:
    candidate = await get_release_candidate(db, request.candidate_public_id)
    decision = await get_release_policy_decision(db, request.policy_decision_public_id)
    if candidate.namespace_id != request.namespace_id:
        raise ReleaseControlTenantMismatchError("ReleaseCandidate belongs to another Namespace")
    if decision.namespace_id != request.namespace_id or decision.candidate_id != candidate.id:
        raise ReleaseControlTenantMismatchError("PolicyDecision does not belong to this candidate")
    exception_digest: str | None = None
    if decision.enforcement_outcome == ReleasePolicyEnforcementOutcome.BLOCK:
        exception_digest = await _effective_exception_digest(db, decision=decision)
        if exception_digest is None:
            raise ReleaseControlStateError("enforced PolicyDecision blocks promotion")
    if (
        decision.enforcement_outcome == ReleasePolicyEnforcementOutcome.WARN
        and not request.acknowledge_warnings
    ):
        raise ReleaseControlStateError("WARN PolicyDecision requires explicit acknowledgement")
    _, approval_digest = await _approval_snapshot(db, candidate=candidate, decision=decision)

    predecessor = await _predecessor_environment(db, target=candidate.target_environment)
    if predecessor is None:
        if candidate.baseline_candidate is not None:
            raise ReleaseControlReferenceError("first Environment candidate must not specify a baseline")
    else:
        if candidate.baseline_candidate is None:
            raise ReleaseControlReferenceError("promotion requires a baseline candidate from the predecessor")
        if candidate.baseline_candidate.target_environment_id != predecessor.id:
            raise ReleaseControlReferenceError("baseline candidate is not from the immediate predecessor")
        predecessor_active = await _active_environment_release(db, environment_id=predecessor.id)
        if (
            predecessor_active is None
            or predecessor_active.candidate_id != candidate.baseline_candidate_id
        ):
            raise ReleaseControlStateError("baseline candidate is not the predecessor's ACTIVE release")
    if candidate.target_environment.kind == ReleaseEnvironmentKind.PRODUCTION:
        if predecessor is None or predecessor.kind != ReleaseEnvironmentKind.CANARY:
            raise ReleaseControlStateError("PRODUCTION promotion requires an immediate CANARY predecessor")
    if (
        candidate.target_environment.requires_canary
        and request.strategy != ReleasePromotionStrategy.CANARY
        and (predecessor is None or predecessor.kind != ReleaseEnvironmentKind.CANARY)
    ):
        raise ReleaseControlStateError("target Environment requires a canary")
    if request.strategy == ReleasePromotionStrategy.CANARY:
        if await _active_environment_release(
            db,
            environment_id=candidate.target_environment_id,
        ) is None:
            raise ReleaseControlStateError("CANARY promotion requires an existing rollback target")

    canary_document = request.canary.model_dump(mode="json") if request.canary is not None else None
    dispatch_public_id = _public_id("rdisp")
    document = {
        "candidate_public_id": candidate.public_id,
        "candidate_digest": candidate.candidate_digest,
        "policy_decision_public_id": decision.public_id,
        "decision_digest": decision.decision_digest,
        "source_environment_public_id": predecessor.public_id if predecessor is not None else None,
        "target_environment_public_id": candidate.target_environment.public_id,
        "strategy": request.strategy.value,
        "acknowledge_warnings": request.acknowledge_warnings,
        "canary": canary_document,
        "exception_digest": exception_digest,
        "approval_digest": approval_digest,
        "dispatch_public_id": dispatch_public_id,
    }
    dispatch_digest = _digest(document)
    replay = await db.scalar(
        select(ReleasePromotion)
        .options(*_promotion_options())
        .where(
            ReleasePromotion.namespace_id == request.namespace_id,
            ReleasePromotion.idempotency_key == request.idempotency_key,
        )
    )
    if replay is not None:
        # dispatch_public_id is server-generated and intentionally excluded
        # from replay reconstruction; compare every client/evidence field.
        replay_document = {
            **document,
            "dispatch_public_id": replay.dispatch_public_id,
        }
        if replay.dispatch_digest == _digest(replay_document):
            return replay
        raise ReleaseControlConflictError("idempotency key is bound to another promotion")
    row = ReleasePromotion(
        public_id=_public_id("rprom"),
        dispatch_public_id=dispatch_public_id,
        namespace_id=request.namespace_id,
        candidate_id=candidate.id,
        policy_decision_id=decision.id,
        source_environment_id=predecessor.id if predecessor is not None else None,
        target_environment_id=candidate.target_environment_id,
        strategy=request.strategy,
        status=ReleasePromotionStatus.DISPATCHED,
        acknowledge_warnings=request.acknowledge_warnings,
        canary_config_json=canary_document,
        exception_digest=exception_digest,
        approval_digest=approval_digest,
        dispatch_digest=dispatch_digest,
        idempotency_key=request.idempotency_key,
        requested_by_user_id=actor.id,
    )
    row.candidate = candidate
    row.policy_decision = decision
    row.source_environment = predecessor
    row.target_environment = candidate.target_environment
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        if _is_unique_violation(exc):
            raise ReleaseControlConflictError("promotion idempotency or dispatch conflict") from exc
        raise
    await audit(
        db,
        user=actor,
        action="release.promotion.dispatched",
        resource_type="release_promotion",
        resource_id=row.id,
        namespace_id=row.namespace_id,
        details={
            "public_id": row.public_id,
            "dispatch_public_id": row.dispatch_public_id,
            "candidate_public_id": candidate.public_id,
            "policy_decision_public_id": decision.public_id,
            "target_environment_public_id": candidate.target_environment.public_id,
            "strategy": row.strategy.value,
            "status": row.status.value,
            "exception_digest": row.exception_digest,
            "approval_digest": row.approval_digest,
            "dispatch_digest": row.dispatch_digest,
        },
    )
    await enqueue_domain_event(db, build_release_promotion_dispatched(row), guaranteed_new=True)
    return row


async def get_release_promotion(db: AsyncSession, public_id: str) -> ReleasePromotion:
    row = await db.scalar(
        select(ReleasePromotion)
        .options(*_promotion_options())
        .where(ReleasePromotion.public_id == public_id)
    )
    if row is None:
        raise ReleaseControlNotFoundError("Release promotion was not found")
    row.candidate = await get_release_candidate(db, row.candidate.public_id)
    return row


async def list_release_promotions(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int = 100,
) -> list[ReleasePromotion]:
    rows = list(
        (
            await db.scalars(
                select(ReleasePromotion)
                .options(*_promotion_options())
                .where(ReleasePromotion.namespace_id == namespace_id)
                .order_by(ReleasePromotion.created_at.desc(), ReleasePromotion.id.desc())
                .limit(limit)
            )
        ).all()
    )
    for row in rows:
        row.candidate = await get_release_candidate(db, row.candidate.public_id)
    return rows


def release_promotion_out(row: ReleasePromotion) -> ReleasePromotionOut:
    return ReleasePromotionOut(
        public_id=row.public_id,
        dispatch_public_id=row.dispatch_public_id,
        namespace_id=row.namespace_id,
        candidate_public_id=row.candidate.public_id,
        policy_decision_public_id=row.policy_decision.public_id,
        source_environment_public_id=(
            row.source_environment.public_id if row.source_environment is not None else None
        ),
        target_environment_public_id=row.target_environment.public_id,
        target_environment_name=row.target_environment.name,
        strategy=row.strategy,
        status=row.status,
        acknowledge_warnings=row.acknowledge_warnings,
        canary=(
            ReleaseCanaryConfig.model_validate(row.canary_config_json)
            if row.canary_config_json is not None
            else None
        ),
        exception_digest=row.exception_digest,
        approval_digest=row.approval_digest,
        dispatch_digest=row.dispatch_digest,
        requested_by_user_id=row.requested_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _activate_environment_release(
    db: AsyncSession,
    *,
    candidate: ReleaseCandidate,
    receipt: ReleaseDeploymentReceipt,
    promotion: ReleasePromotion | None,
    rollback: ReleaseRollback | None,
    actor: User | None = None,
    audit_user_id: int | None = None,
    audit_username: str | None = None,
) -> ReleaseEnvironmentRelease:
    if (promotion is None) == (rollback is None):
        raise ReleaseControlStateError("activation requires exactly one promotion or rollback")
    if promotion is not None:
        environment_id = promotion.target_environment_id
    else:
        assert rollback is not None
        environment_id = rollback.environment_id
    current = await _active_environment_release(db, environment_id=environment_id, lock=True)
    now = ensure_utc(receipt.occurred_at)
    if current is not None:
        current.status = (
            ReleaseEnvironmentReleaseStatus.ROLLED_BACK
            if rollback is not None
            else ReleaseEnvironmentReleaseStatus.SUPERSEDED
        )
        current.deactivated_at = now
        current_candidate = await get_release_candidate(db, current.candidate.public_id)
        if (
            current_candidate.id != candidate.id
            and current_candidate.deployment.status == AgentDeploymentStatus.ACTIVE
        ):
            current_candidate.deployment.status = AgentDeploymentStatus.RETIRED
            current_candidate.deployment.retired_at = now
    target_deployment = candidate.deployment
    if target_deployment.status not in {
        AgentDeploymentStatus.REGISTERED,
        AgentDeploymentStatus.ACTIVE,
        AgentDeploymentStatus.RETIRED,
    }:
        raise ReleaseControlStateError("target Deployment cannot be activated from its current state")
    target_deployment.status = AgentDeploymentStatus.ACTIVE
    target_deployment.activated_at = target_deployment.activated_at or now
    target_deployment.retired_at = None
    activation_document = {
        "environment_public_id": candidate.target_environment.public_id,
        "candidate_public_id": candidate.public_id,
        "candidate_digest": candidate.candidate_digest,
        "receipt_public_id": receipt.public_id,
        "receipt_digest": receipt.receipt_digest,
        "promotion_public_id": promotion.public_id if promotion is not None else None,
        "rollback_public_id": rollback.public_id if rollback is not None else None,
        "previous_release_public_id": current.public_id if current is not None else None,
    }
    row = ReleaseEnvironmentRelease(
        public_id=_public_id("renvr"),
        namespace_id=candidate.namespace_id,
        environment_id=environment_id,
        candidate_id=candidate.id,
        promotion_id=promotion.id if promotion is not None else None,
        rollback_id=rollback.id if rollback is not None else None,
        receipt_id=receipt.id,
        previous_release_id=current.id if current is not None else None,
        status=ReleaseEnvironmentReleaseStatus.ACTIVE,
        activation_digest=_digest(activation_document),
        activated_at=now,
    )
    row.environment = candidate.target_environment
    row.candidate = candidate
    row.promotion = promotion
    row.rollback = rollback
    row.receipt = receipt
    row.previous_release = current
    db.add(row)
    await db.flush()
    await audit(
        db,
        user=actor,
        user_id=audit_user_id,
        username=audit_username,
        action="release.environment.activated",
        resource_type="release_environment_release",
        resource_id=row.id,
        namespace_id=row.namespace_id,
        details={
            "public_id": row.public_id,
            "environment_public_id": row.environment.public_id,
            "candidate_public_id": row.candidate.public_id,
            "promotion_public_id": row.promotion.public_id if row.promotion is not None else None,
            "rollback_public_id": row.rollback.public_id if row.rollback is not None else None,
            "receipt_public_id": row.receipt.public_id,
            "previous_release_public_id": (
                row.previous_release.public_id if row.previous_release is not None else None
            ),
            "activation_digest": row.activation_digest,
        },
    )
    await enqueue_domain_event(db, build_release_environment_activated(row), guaranteed_new=True)
    return row


async def get_release_environment_release(
    db: AsyncSession,
    public_id: str,
) -> ReleaseEnvironmentRelease:
    row = await db.scalar(
        select(ReleaseEnvironmentRelease)
        .options(
            selectinload(ReleaseEnvironmentRelease.environment),
            selectinload(ReleaseEnvironmentRelease.candidate),
            selectinload(ReleaseEnvironmentRelease.promotion),
            selectinload(ReleaseEnvironmentRelease.rollback),
            selectinload(ReleaseEnvironmentRelease.receipt),
            selectinload(ReleaseEnvironmentRelease.previous_release),
        )
        .where(ReleaseEnvironmentRelease.public_id == public_id)
    )
    if row is None:
        raise ReleaseControlNotFoundError("Environment release was not found")
    return row


async def list_release_environment_releases(
    db: AsyncSession,
    *,
    namespace_id: int,
    environment_public_id: str | None = None,
    limit: int = 100,
) -> list[ReleaseEnvironmentRelease]:
    statement = (
        select(ReleaseEnvironmentRelease)
        .options(
            selectinload(ReleaseEnvironmentRelease.environment),
            selectinload(ReleaseEnvironmentRelease.candidate),
            selectinload(ReleaseEnvironmentRelease.promotion),
            selectinload(ReleaseEnvironmentRelease.rollback),
            selectinload(ReleaseEnvironmentRelease.receipt),
            selectinload(ReleaseEnvironmentRelease.previous_release),
        )
        .where(ReleaseEnvironmentRelease.namespace_id == namespace_id)
        .order_by(
            ReleaseEnvironmentRelease.activated_at.desc(),
            ReleaseEnvironmentRelease.id.desc(),
        )
        .limit(limit)
    )
    if environment_public_id is not None:
        statement = statement.join(
            ReleaseEnvironment,
            ReleaseEnvironment.id == ReleaseEnvironmentRelease.environment_id,
        ).where(ReleaseEnvironment.public_id == environment_public_id)
    return list((await db.scalars(statement)).all())


def release_environment_release_out(row: ReleaseEnvironmentRelease) -> ReleaseEnvironmentReleaseOut:
    return ReleaseEnvironmentReleaseOut(
        public_id=row.public_id,
        namespace_id=row.namespace_id,
        environment_public_id=row.environment.public_id,
        candidate_public_id=row.candidate.public_id,
        promotion_public_id=row.promotion.public_id if row.promotion is not None else None,
        rollback_public_id=row.rollback.public_id if row.rollback is not None else None,
        receipt_public_id=row.receipt.public_id,
        previous_release_public_id=(
            row.previous_release.public_id if row.previous_release is not None else None
        ),
        status=row.status,
        activation_digest=row.activation_digest,
        activated_at=row.activated_at,
        deactivated_at=row.deactivated_at,
    )


async def _load_dispatch(
    db: AsyncSession,
    *,
    request: ReleaseDeploymentReceiptCreate,
) -> tuple[ReleasePromotion | None, ReleaseRollback | None]:
    if request.kind == ReleaseReceiptKind.PROMOTION:
        promotion = await db.scalar(
            select(ReleasePromotion)
            .options(*_promotion_options())
            .where(ReleasePromotion.dispatch_public_id == request.dispatch_public_id)
            .with_for_update()
        )
        if promotion is None:
            raise ReleaseControlNotFoundError("promotion dispatch was not found")
        promotion.candidate = await get_release_candidate(db, promotion.candidate.public_id)
        return promotion, None
    rollback = await db.scalar(
        select(ReleaseRollback)
        .options(*_rollback_options())
        .where(ReleaseRollback.dispatch_public_id == request.dispatch_public_id)
        .with_for_update()
    )
    if rollback is None:
        raise ReleaseControlNotFoundError("rollback dispatch was not found")
    rollback.source_candidate = await get_release_candidate(db, rollback.source_candidate.public_id)
    rollback.target_environment_release.candidate = await get_release_candidate(
        db,
        rollback.target_environment_release.candidate.public_id,
    )
    return None, rollback


async def record_release_deployment_receipt(
    db: AsyncSession,
    *,
    request: ReleaseDeploymentReceiptCreate,
    identity: ReporterExecutionIdentity,
) -> ReleaseDeploymentReceipt:
    promotion, rollback = await _load_dispatch(db, request=request)
    if promotion is not None:
        candidate = promotion.candidate
    else:
        assert rollback is not None
        candidate = rollback.target_environment_release.candidate
    if candidate.namespace_id != identity.namespace_id:
        raise ReleaseControlTenantMismatchError("dispatch belongs to another Namespace")
    if candidate.deployment.runtime_id != identity.runtime_id:
        raise ReleaseControlTenantMismatchError("dispatch belongs to another Runtime")
    document = {
        "dispatch_public_id": request.dispatch_public_id,
        "kind": request.kind.value,
        "external_receipt_id": request.external_receipt_id,
        "reported_status": request.status.value,
        "observed_package_version_public_id": request.observed_package_version_public_id,
        "observed_deployment_revision": request.observed_deployment_revision,
        "observed_configuration_digest": request.observed_configuration_digest,
        "runtime_release_ref": request.runtime_release_ref,
        "error_code": request.error_code,
        "occurred_at": ensure_utc(request.occurred_at).isoformat().replace("+00:00", "Z"),
        "namespace_id": identity.namespace_id,
        "runtime_id": identity.runtime_id,
        "reporter_credential_id": identity.credential_id,
    }
    receipt_digest = _digest(document)
    replay = await db.scalar(
        select(ReleaseDeploymentReceipt)
        .options(
            selectinload(ReleaseDeploymentReceipt.promotion),
            selectinload(ReleaseDeploymentReceipt.rollback),
        )
        .where(
            ReleaseDeploymentReceipt.namespace_id == identity.namespace_id,
            ReleaseDeploymentReceipt.runtime_id == identity.runtime_id,
            ReleaseDeploymentReceipt.idempotency_key == request.idempotency_key,
        )
    )
    if replay is not None:
        if replay.receipt_digest == receipt_digest:
            return replay
        raise ReleaseControlConflictError("idempotency key is bound to another Runtime receipt")
    if promotion is not None and promotion.status != ReleasePromotionStatus.DISPATCHED:
        raise ReleaseControlStateError(f"promotion is in {promotion.status.value} state")
    if rollback is not None and rollback.status != ReleaseRollbackStatus.DISPATCHED:
        raise ReleaseControlStateError(f"rollback is in {rollback.status.value} state")
    exact_match = all(
        (
            request.observed_package_version_public_id == candidate.package_version.public_id,
            request.observed_deployment_revision == candidate.deployment.revision,
            request.observed_configuration_digest == candidate.deployment.configuration_digest,
        )
    )
    if request.status == RuntimeReceiptReportedStatus.FAILED:
        effective_status = ReleaseReceiptStatus.FAILED
        effective_error = request.error_code
    elif not exact_match:
        effective_status = ReleaseReceiptStatus.MISMATCH
        effective_error = "runtime_release_evidence_mismatch"
    else:
        effective_status = ReleaseReceiptStatus.APPLIED
        effective_error = None
    row = ReleaseDeploymentReceipt(
        public_id=_public_id("rrcpt"),
        namespace_id=identity.namespace_id,
        runtime_id=identity.runtime_id,
        reporter_credential_id=identity.credential_id,
        kind=request.kind,
        promotion_id=promotion.id if promotion is not None else None,
        rollback_id=rollback.id if rollback is not None else None,
        external_receipt_id=request.external_receipt_id,
        status=effective_status,
        observed_package_version_public_id=request.observed_package_version_public_id,
        observed_deployment_revision=request.observed_deployment_revision,
        observed_configuration_digest=request.observed_configuration_digest,
        runtime_release_ref=request.runtime_release_ref,
        error_code=effective_error,
        idempotency_key=request.idempotency_key,
        receipt_digest=receipt_digest,
        occurred_at=ensure_utc(request.occurred_at),
    )
    row.promotion = promotion
    row.rollback = rollback
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        if _is_unique_violation(exc):
            raise ReleaseControlConflictError("Runtime receipt identity already exists") from exc
        raise

    if promotion is not None:
        if effective_status == ReleaseReceiptStatus.APPLIED:
            candidate.deployment.status = AgentDeploymentStatus.ACTIVE
            candidate.deployment.activated_at = candidate.deployment.activated_at or row.occurred_at
            if promotion.strategy == ReleasePromotionStrategy.CANARY:
                promotion.status = ReleasePromotionStatus.OBSERVING
            else:
                promotion.status = ReleasePromotionStatus.SUCCEEDED
                await _activate_environment_release(
                    db,
                    candidate=candidate,
                    receipt=row,
                    promotion=promotion,
                    rollback=None,
                    audit_user_id=identity.actor_user_id,
                    audit_username=f"reporter-credential:{identity.credential_id}",
                )
        else:
            promotion.status = ReleasePromotionStatus.FAILED
            if candidate.deployment.status == AgentDeploymentStatus.REGISTERED:
                candidate.deployment.status = AgentDeploymentStatus.FAILED
    else:
        assert rollback is not None
        if effective_status == ReleaseReceiptStatus.APPLIED:
            rollback.status = ReleaseRollbackStatus.SUCCEEDED
            rollback.completed_at = row.occurred_at
            rollback.promotion.status = ReleasePromotionStatus.ROLLED_BACK
            source_deployment = rollback.source_candidate.deployment
            if source_deployment.status == AgentDeploymentStatus.ACTIVE:
                source_deployment.status = AgentDeploymentStatus.RETIRED
                source_deployment.retired_at = row.occurred_at
            await _activate_environment_release(
                db,
                candidate=candidate,
                receipt=row,
                promotion=None,
                rollback=rollback,
                audit_user_id=identity.actor_user_id,
                audit_username=f"reporter-credential:{identity.credential_id}",
            )
        else:
            rollback.status = ReleaseRollbackStatus.FAILED
            rollback.completed_at = row.occurred_at
            rollback.promotion.status = ReleasePromotionStatus.FAILED
    await audit(
        db,
        user_id=identity.actor_user_id,
        username=f"reporter-credential:{identity.credential_id}",
        action="release.runtime_receipt.recorded",
        resource_type="release_deployment_receipt",
        resource_id=row.id,
        namespace_id=row.namespace_id,
        details={
            "public_id": row.public_id,
            "dispatch_public_id": request.dispatch_public_id,
            "kind": row.kind.value,
            "runtime_id": row.runtime_id,
            "reporter_credential_id": row.reporter_credential_id,
            "status": row.status.value,
            "error_code": row.error_code,
            "observed_package_version_public_id": row.observed_package_version_public_id,
            "observed_deployment_revision": row.observed_deployment_revision,
            "observed_configuration_digest": row.observed_configuration_digest,
            "receipt_digest": row.receipt_digest,
        },
    )
    await enqueue_domain_event(db, build_release_receipt_recorded(row), guaranteed_new=True)
    await db.flush()
    return row


def release_deployment_receipt_out(row: ReleaseDeploymentReceipt) -> ReleaseDeploymentReceiptOut:
    if row.promotion is not None:
        dispatch_public_id = row.promotion.dispatch_public_id
    else:
        assert row.rollback is not None
        dispatch_public_id = row.rollback.dispatch_public_id
    return ReleaseDeploymentReceiptOut(
        public_id=row.public_id,
        namespace_id=row.namespace_id,
        runtime_id=row.runtime_id,
        reporter_credential_id=row.reporter_credential_id,
        kind=row.kind,
        dispatch_public_id=dispatch_public_id,
        external_receipt_id=row.external_receipt_id,
        status=row.status,
        observed_package_version_public_id=row.observed_package_version_public_id,
        observed_deployment_revision=row.observed_deployment_revision,
        observed_configuration_digest=row.observed_configuration_digest,
        runtime_release_ref=row.runtime_release_ref,
        error_code=row.error_code,
        receipt_digest=row.receipt_digest,
        occurred_at=row.occurred_at,
        created_at=row.created_at,
    )


async def list_release_deployment_receipts(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int = 100,
) -> list[ReleaseDeploymentReceipt]:
    return list(
        (
            await db.scalars(
                select(ReleaseDeploymentReceipt)
                .options(
                    selectinload(ReleaseDeploymentReceipt.promotion),
                    selectinload(ReleaseDeploymentReceipt.rollback),
                )
                .where(ReleaseDeploymentReceipt.namespace_id == namespace_id)
                .order_by(
                    ReleaseDeploymentReceipt.occurred_at.desc(),
                    ReleaseDeploymentReceipt.id.desc(),
                )
                .limit(limit)
            )
        ).all()
    )


async def _promotion_receipt(
    db: AsyncSession,
    promotion_id: int,
) -> ReleaseDeploymentReceipt:
    row = await db.scalar(
        select(ReleaseDeploymentReceipt)
        .options(selectinload(ReleaseDeploymentReceipt.promotion))
        .where(
            ReleaseDeploymentReceipt.promotion_id == promotion_id,
            ReleaseDeploymentReceipt.status == ReleaseReceiptStatus.APPLIED,
        )
        .order_by(ReleaseDeploymentReceipt.created_at.desc(), ReleaseDeploymentReceipt.id.desc())
        .limit(1)
    )
    if row is None:
        raise ReleaseControlStateError("promotion has no matching APPLIED Runtime receipt")
    return row


async def _create_rollback_dispatch(
    db: AsyncSession,
    *,
    promotion: ReleasePromotion,
    target_release: ReleaseEnvironmentRelease,
    reason_code: str,
    idempotency_key: str,
    actor: User,
) -> ReleaseRollback:
    source_candidate = promotion.candidate
    if target_release.candidate_id == source_candidate.id:
        raise ReleaseControlStateError("rollback target must be a different ReleaseCandidate")
    dispatch_public_id = _public_id("rrdisp")
    document = {
        "promotion_public_id": promotion.public_id,
        "source_candidate_public_id": source_candidate.public_id,
        "source_candidate_digest": source_candidate.candidate_digest,
        "target_environment_release_public_id": target_release.public_id,
        "target_candidate_public_id": target_release.candidate.public_id,
        "target_candidate_digest": target_release.candidate.candidate_digest,
        "reason_code": reason_code,
        "dispatch_public_id": dispatch_public_id,
    }
    dispatch_digest = _digest(document)
    replay = await db.scalar(
        select(ReleaseRollback)
        .options(*_rollback_options())
        .where(
            ReleaseRollback.namespace_id == promotion.namespace_id,
            ReleaseRollback.idempotency_key == idempotency_key,
        )
    )
    if replay is not None:
        replay_document = {**document, "dispatch_public_id": replay.dispatch_public_id}
        if replay.dispatch_digest == _digest(replay_document):
            return replay
        raise ReleaseControlConflictError("idempotency key is bound to another rollback")
    row = ReleaseRollback(
        public_id=_public_id("rrbk"),
        dispatch_public_id=dispatch_public_id,
        namespace_id=promotion.namespace_id,
        promotion_id=promotion.id,
        environment_id=promotion.target_environment_id,
        source_candidate_id=source_candidate.id,
        target_environment_release_id=target_release.id,
        status=ReleaseRollbackStatus.DISPATCHED,
        reason_code=reason_code,
        dispatch_digest=dispatch_digest,
        idempotency_key=idempotency_key,
        requested_by_user_id=actor.id,
    )
    row.promotion = promotion
    row.environment = promotion.target_environment
    row.source_candidate = source_candidate
    row.target_environment_release = target_release
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        if _is_unique_violation(exc):
            raise ReleaseControlConflictError("rollback idempotency or dispatch conflict") from exc
        raise
    promotion.status = ReleasePromotionStatus.ROLLBACK_REQUESTED
    await audit(
        db,
        user=actor,
        action="release.rollback.dispatched",
        resource_type="release_rollback",
        resource_id=row.id,
        namespace_id=row.namespace_id,
        details={
            "public_id": row.public_id,
            "dispatch_public_id": row.dispatch_public_id,
            "promotion_public_id": promotion.public_id,
            "source_candidate_public_id": source_candidate.public_id,
            "target_environment_release_public_id": target_release.public_id,
            "target_candidate_public_id": target_release.candidate.public_id,
            "reason_code": row.reason_code,
            "dispatch_digest": row.dispatch_digest,
        },
    )
    await enqueue_domain_event(db, build_release_rollback_dispatched(row), guaranteed_new=True)
    return row


async def request_release_rollback(
    db: AsyncSession,
    *,
    promotion_public_id: str,
    request: ReleaseRollbackCreate,
    actor: User,
) -> ReleaseRollback:
    promotion = await get_release_promotion(db, promotion_public_id)
    if promotion.namespace_id != request.namespace_id:
        raise ReleaseControlTenantMismatchError("promotion belongs to another Namespace")
    if promotion.status != ReleasePromotionStatus.SUCCEEDED:
        raise ReleaseControlStateError("operator rollback requires a SUCCEEDED promotion")
    current = await _active_environment_release(
        db,
        environment_id=promotion.target_environment_id,
        lock=True,
    )
    if current is None or current.candidate_id != promotion.candidate_id:
        raise ReleaseControlStateError("promotion candidate is not the ACTIVE Environment release")
    if current.previous_release is None:
        raise ReleaseControlStateError("Environment has no previous release to roll back to")
    target = await get_release_environment_release(db, current.previous_release.public_id)
    target.candidate = await get_release_candidate(db, target.candidate.public_id)
    return await _create_rollback_dispatch(
        db,
        promotion=promotion,
        target_release=target,
        reason_code=request.reason_code,
        idempotency_key=request.idempotency_key,
        actor=actor,
    )


async def get_release_rollback(db: AsyncSession, public_id: str) -> ReleaseRollback:
    row = await db.scalar(
        select(ReleaseRollback)
        .options(*_rollback_options())
        .where(ReleaseRollback.public_id == public_id)
    )
    if row is None:
        raise ReleaseControlNotFoundError("Release rollback was not found")
    row.source_candidate = await get_release_candidate(db, row.source_candidate.public_id)
    row.target_environment_release.candidate = await get_release_candidate(
        db,
        row.target_environment_release.candidate.public_id,
    )
    return row


async def list_release_rollbacks(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int = 100,
) -> list[ReleaseRollback]:
    rows = list(
        (
            await db.scalars(
                select(ReleaseRollback)
                .options(*_rollback_options())
                .where(ReleaseRollback.namespace_id == namespace_id)
                .order_by(ReleaseRollback.created_at.desc(), ReleaseRollback.id.desc())
                .limit(limit)
            )
        ).all()
    )
    for row in rows:
        row.source_candidate = await get_release_candidate(db, row.source_candidate.public_id)
        row.target_environment_release.candidate = await get_release_candidate(
            db,
            row.target_environment_release.candidate.public_id,
        )
    return rows


def release_rollback_out(row: ReleaseRollback) -> ReleaseRollbackOut:
    return ReleaseRollbackOut(
        public_id=row.public_id,
        dispatch_public_id=row.dispatch_public_id,
        namespace_id=row.namespace_id,
        promotion_public_id=row.promotion.public_id,
        environment_public_id=row.environment.public_id,
        source_candidate_public_id=row.source_candidate.public_id,
        target_environment_release_public_id=row.target_environment_release.public_id,
        target_candidate_public_id=row.target_environment_release.candidate.public_id,
        status=row.status,
        reason_code=row.reason_code,
        dispatch_digest=row.dispatch_digest,
        requested_by_user_id=row.requested_by_user_id,
        created_at=row.created_at,
        completed_at=row.completed_at,
    )


async def evaluate_release_canary(
    db: AsyncSession,
    *,
    promotion_public_id: str,
    request: ReleaseCanaryEvaluationCreate,
    actor: User,
) -> ReleaseCanaryEvaluation:
    promotion = await get_release_promotion(db, promotion_public_id)
    if promotion.namespace_id != request.namespace_id:
        raise ReleaseControlTenantMismatchError("promotion belongs to another Namespace")
    if promotion.status != ReleasePromotionStatus.OBSERVING:
        raise ReleaseControlStateError("canary evaluation requires an OBSERVING promotion")
    if promotion.strategy != ReleasePromotionStrategy.CANARY or promotion.canary_config_json is None:
        raise ReleaseControlStateError("promotion is not configured for canary analysis")
    config = ReleaseCanaryConfig.model_validate(promotion.canary_config_json)
    receipt = await _promotion_receipt(db, promotion.id)
    window_start = ensure_utc(receipt.occurred_at)
    configured_end = window_start + timedelta(seconds=config.observation_window_seconds)
    window_end = min(datetime.now(timezone.utc), configured_end)
    # The current MySQL columns persist DATETIME values at second precision.
    # Reject a sub-second window before flush instead of letting truncation
    # turn it into a database check-constraint failure and an HTTP 500.
    if window_end.replace(microsecond=0) <= window_start.replace(microsecond=0):
        raise ReleaseControlStateError(
            "canary observation window has not accumulated one second"
        )
    runs = list(
        (
            await db.scalars(
                select(AgentRun)
                .where(
                    AgentRun.namespace_id == promotion.namespace_id,
                    AgentRun.deployment_id == promotion.candidate.deployment_id,
                    AgentRun.status.in_(TERMINAL_RUN_STATUSES),
                    AgentRun.started_at >= window_start,
                    AgentRun.started_at <= window_end,
                )
                .order_by(AgentRun.started_at, AgentRun.public_id)
                .limit(10001)
            )
        ).all()
    )
    if len(runs) > 10000:
        raise ReleaseControlStateError("canary evidence exceeds the 10000 Run bound")
    completed_count = len(runs)
    failed_count = sum(run.status != AgentRunStatus.SUCCEEDED for run in runs)
    untrusted_count = sum(run.trust_level == TrustLevel.UNVERIFIED for run in runs)
    failure_rate = failed_count / completed_count if completed_count else 0.0
    untrusted_rate = untrusted_count / completed_count if completed_count else 0.0
    evidence_document = [
        {
            "run_public_id": run.public_id,
            "status": run.status.value,
            "trust_level": run.trust_level.value,
            "start_envelope_sha256": run.start_envelope_sha256,
            "completion_envelope_sha256": run.completion_envelope_sha256,
        }
        for run in runs
    ]
    evidence_digest = _digest(evidence_document)
    reasons: list[str] = []
    if completed_count < config.minimum_completed_runs:
        outcome = ReleaseCanaryOutcome.INCONCLUSIVE
        reasons.append("insufficient_completed_runs")
    else:
        if failure_rate > config.maximum_failure_rate:
            reasons.append("failure_rate_exceeded")
        if untrusted_rate > config.maximum_untrusted_rate:
            reasons.append("untrusted_rate_exceeded")
        outcome = ReleaseCanaryOutcome.FAIL if reasons else ReleaseCanaryOutcome.PASS
        if not reasons:
            reasons.append("canary_thresholds_passed")
    decision_document = {
        "promotion_public_id": promotion.public_id,
        "dispatch_digest": promotion.dispatch_digest,
        "canary_config": config.model_dump(mode="json"),
        "window_start": window_start.isoformat().replace("+00:00", "Z"),
        "completed_run_count": completed_count,
        "failed_run_count": failed_count,
        "untrusted_run_count": untrusted_count,
        "failure_rate": failure_rate,
        "untrusted_rate": untrusted_rate,
        "evidence_digest": evidence_digest,
        "outcome": outcome.value,
        "reason_codes": reasons,
    }
    decision_digest = _digest(decision_document)
    replay = await db.scalar(
        select(ReleaseCanaryEvaluation)
        .options(selectinload(ReleaseCanaryEvaluation.promotion))
        .where(
            ReleaseCanaryEvaluation.namespace_id == request.namespace_id,
            ReleaseCanaryEvaluation.idempotency_key == request.idempotency_key,
        )
    )
    if replay is not None:
        if replay.decision_digest == decision_digest:
            return replay
        raise ReleaseControlConflictError("idempotency key is bound to another canary snapshot")
    exact = await db.scalar(
        select(ReleaseCanaryEvaluation)
        .options(selectinload(ReleaseCanaryEvaluation.promotion))
        .where(
            ReleaseCanaryEvaluation.promotion_id == promotion.id,
            ReleaseCanaryEvaluation.evidence_digest == evidence_digest,
        )
    )
    if exact is not None:
        return exact
    row = ReleaseCanaryEvaluation(
        public_id=_public_id("rcan"),
        namespace_id=request.namespace_id,
        promotion_id=promotion.id,
        outcome=outcome,
        reason_codes_json=reasons,
        window_start=window_start,
        window_end=window_end,
        completed_run_count=completed_count,
        failed_run_count=failed_count,
        untrusted_run_count=untrusted_count,
        failure_rate=failure_rate,
        untrusted_rate=untrusted_rate,
        evidence_digest=evidence_digest,
        decision_digest=decision_digest,
        idempotency_key=request.idempotency_key,
        evaluated_by_user_id=actor.id,
    )
    row.promotion = promotion
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        if _is_unique_violation(exc):
            raise ReleaseControlConflictError("canary evidence or idempotency key already exists") from exc
        raise
    if outcome == ReleaseCanaryOutcome.PASS:
        promotion.status = ReleasePromotionStatus.SUCCEEDED
        await _activate_environment_release(
            db,
            candidate=promotion.candidate,
            receipt=receipt,
            promotion=promotion,
            rollback=None,
            actor=actor,
        )
    elif outcome == ReleaseCanaryOutcome.FAIL:
        target = await _active_environment_release(
            db,
            environment_id=promotion.target_environment_id,
            lock=True,
        )
        if target is None:
            raise ReleaseControlStateError("failed canary has no rollback target")
        target.candidate = await get_release_candidate(db, target.candidate.public_id)
        await _create_rollback_dispatch(
            db,
            promotion=promotion,
            target_release=target,
            reason_code="canary_policy_failed",
            idempotency_key=f"canary-rollback-{row.public_id}",
            actor=actor,
        )
    await audit(
        db,
        user=actor,
        action="release.canary.evaluated",
        resource_type="release_canary_evaluation",
        resource_id=row.id,
        namespace_id=row.namespace_id,
        details={
            "public_id": row.public_id,
            "promotion_public_id": promotion.public_id,
            "outcome": row.outcome.value,
            "reason_codes": row.reason_codes_json,
            "completed_run_count": row.completed_run_count,
            "failed_run_count": row.failed_run_count,
            "untrusted_run_count": row.untrusted_run_count,
            "failure_rate": row.failure_rate,
            "untrusted_rate": row.untrusted_rate,
            "evidence_digest": row.evidence_digest,
            "decision_digest": row.decision_digest,
        },
    )
    await enqueue_domain_event(db, build_release_canary_evaluated(row), guaranteed_new=True)
    await db.flush()
    return row


def release_canary_evaluation_out(row: ReleaseCanaryEvaluation) -> ReleaseCanaryEvaluationOut:
    return ReleaseCanaryEvaluationOut(
        public_id=row.public_id,
        namespace_id=row.namespace_id,
        promotion_public_id=row.promotion.public_id,
        outcome=row.outcome,
        reason_codes=list(row.reason_codes_json),
        window_start=row.window_start,
        window_end=row.window_end,
        completed_run_count=row.completed_run_count,
        failed_run_count=row.failed_run_count,
        untrusted_run_count=row.untrusted_run_count,
        failure_rate=row.failure_rate,
        untrusted_rate=row.untrusted_rate,
        evidence_digest=row.evidence_digest,
        decision_digest=row.decision_digest,
        evaluated_by_user_id=row.evaluated_by_user_id,
        created_at=row.created_at,
    )


async def list_release_canary_evaluations(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int = 100,
) -> list[ReleaseCanaryEvaluation]:
    return list(
        (
            await db.scalars(
                select(ReleaseCanaryEvaluation)
                .options(selectinload(ReleaseCanaryEvaluation.promotion))
                .where(ReleaseCanaryEvaluation.namespace_id == namespace_id)
                .order_by(
                    ReleaseCanaryEvaluation.created_at.desc(),
                    ReleaseCanaryEvaluation.id.desc(),
                )
                .limit(limit)
            )
        ).all()
    )
