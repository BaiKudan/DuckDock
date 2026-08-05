from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import ensure_utc
from app.models.deployment import AgentDeployment, AgentDeploymentStatus
from app.models.evaluation import (
    EvaluationComparison,
    EvaluationComparisonOutcome,
)
from app.models.release import (
    ReleaseCandidateEvaluationBinding,
    ReleaseCandidateEvaluationReview,
    ReleaseCandidateReviewDecision,
)
from app.models.user import User
from app.schemas.release import (
    ReleaseCandidateEvaluationBindingCreate,
    ReleaseCandidateEvaluationReviewCreate,
)
from app.services.audit_service import audit
from app.services.evaluation_comparison_service import (
    EvaluationComparisonView,
    load_evaluation_comparison_view,
)
from app.services.outbox_event_service import (
    build_release_candidate_evaluation_bound,
    build_release_candidate_evaluation_reviewed,
    enqueue_domain_event,
)
from app.services.release_evidence_ports import (
    CandidateEvidenceReference,
    ReleaseEvidenceLookupPort,
    ReleaseEvidenceSelector,
)
from app.services.release_gate_service import (
    CandidateReleaseGateDecision,
    ReleaseGateService,
)
from app.services.tenant_write_service import require_active_namespace


_SAFE_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_BINDING_SCHEMA_NAME = "duckdock-release-candidate-evaluation-binding"
_BINDING_SCHEMA_VERSION = "1.0"
_GATE_SCHEMA_NAME = "duckdock-candidate-release-gate"
_GATE_SCHEMA_VERSION = "1.0"
_REVIEW_SCHEMA_NAME = "duckdock-release-candidate-evaluation-review"
_REVIEW_SCHEMA_VERSION = "1.0"


class ReleaseCandidateEvaluationError(ValueError):
    pass


class ReleaseCandidateEvaluationNotFoundError(
    ReleaseCandidateEvaluationError
):
    pass


class ReleaseCandidateEvaluationConflictError(
    ReleaseCandidateEvaluationError
):
    pass


class ReleaseCandidateEvaluationStateError(
    ReleaseCandidateEvaluationError
):
    pass


@dataclass(frozen=True, slots=True)
class ReleaseCandidateEvaluationBindingView:
    binding: ReleaseCandidateEvaluationBinding
    deployment: AgentDeployment
    comparison_view: EvaluationComparisonView
    review: ReleaseCandidateEvaluationReview | None


def _public_id() -> str:
    return f"rcb_{uuid.uuid4().hex}"


def _review_public_id() -> str:
    return f"rcr_{uuid.uuid4().hex}"


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


def selector_from_request(
    request: ReleaseCandidateEvaluationBindingCreate,
) -> ReleaseEvidenceSelector:
    try:
        return ReleaseEvidenceSelector(
            namespace_id=request.namespace_id,
            release_candidate_ref=request.release_candidate_ref,
            deployment_public_id=request.deployment_public_id,
            deployment_revision=request.deployment_revision,
        )
    except ValueError as exc:
        raise ReleaseCandidateEvaluationStateError(str(exc)) from exc


async def _load_exact_deployment(
    db: AsyncSession,
    *,
    selector: ReleaseEvidenceSelector,
) -> AgentDeployment:
    deployment = (
        await db.execute(
            select(AgentDeployment).where(
                AgentDeployment.namespace_id == selector.namespace_id,
                AgentDeployment.public_id
                == selector.deployment_public_id,
                AgentDeployment.revision
                == selector.deployment_revision,
            )
        )
    ).scalar_one_or_none()
    if deployment is None:
        raise ReleaseCandidateEvaluationNotFoundError(
            "Exact Deployment revision was not found"
        )
    if deployment.status in {
        AgentDeploymentStatus.FAILED,
        AgentDeploymentStatus.RETIRED,
    }:
        raise ReleaseCandidateEvaluationStateError(
            "Failed or retired Deployment cannot receive release evidence"
        )
    return deployment


async def _load_exact_comparison(
    db: AsyncSession,
    *,
    namespace_id: int,
    public_id: str,
) -> tuple[EvaluationComparison, EvaluationComparisonView]:
    comparison = (
        await db.execute(
            select(EvaluationComparison).where(
                EvaluationComparison.namespace_id == namespace_id,
                EvaluationComparison.public_id == public_id,
            )
        )
    ).scalar_one_or_none()
    if comparison is None:
        raise ReleaseCandidateEvaluationNotFoundError(
            "EvaluationComparison was not found"
        )
    return comparison, await load_evaluation_comparison_view(
        db,
        comparison=comparison,
    )


def _validate_candidate_target(
    *,
    selector: ReleaseEvidenceSelector,
    deployment: AgentDeployment,
    comparison_view: EvaluationComparisonView,
) -> None:
    candidate = comparison_view.candidate.experiment
    if candidate.target_ref != selector.release_candidate_ref:
        raise ReleaseCandidateEvaluationStateError(
            "Candidate Experiment target_ref does not match the "
            "release candidate"
        )
    if candidate.target_digest != deployment.configuration_digest:
        raise ReleaseCandidateEvaluationStateError(
            "Candidate Experiment target_digest does not match the "
            "Deployment configuration digest"
        )


def _binding_payload(
    *,
    selector: ReleaseEvidenceSelector,
    deployment: AgentDeployment,
    comparison_view: EvaluationComparisonView,
) -> dict:
    comparison = comparison_view.comparison
    return {
        "schema_name": _BINDING_SCHEMA_NAME,
        "schema_version": _BINDING_SCHEMA_VERSION,
        "namespace_id": selector.namespace_id,
        "release_candidate_ref": selector.release_candidate_ref,
        "deployment": {
            "public_id": deployment.public_id,
            "revision": deployment.revision,
            "configuration_digest": deployment.configuration_digest,
        },
        "evaluation_comparison": {
            "public_id": comparison.public_id,
            "outcome": comparison.outcome.value,
            "reproducibility_digest": (
                comparison.reproducibility_digest
            ),
            "candidate_evaluation_public_id": (
                comparison_view.candidate.evaluation.public_id
            ),
            "candidate_manifest_public_id": (
                comparison_view.candidate.manifest.public_id
            ),
        },
    }


async def create_release_candidate_evaluation_binding(
    db: AsyncSession,
    *,
    request: ReleaseCandidateEvaluationBindingCreate,
    idempotency_key: str,
    actor: User,
) -> ReleaseCandidateEvaluationBinding:
    if _SAFE_IDEMPOTENCY_KEY.fullmatch(idempotency_key) is None:
        raise ReleaseCandidateEvaluationStateError(
            "Release evidence idempotency key is invalid"
        )
    await require_active_namespace(db, request.namespace_id)
    selector = selector_from_request(request)
    deployment = await _load_exact_deployment(db, selector=selector)
    comparison, comparison_view = await _load_exact_comparison(
        db,
        namespace_id=request.namespace_id,
        public_id=request.evaluation_comparison_public_id,
    )
    _validate_candidate_target(
        selector=selector,
        deployment=deployment,
        comparison_view=comparison_view,
    )

    existing_by_key = await db.scalar(
        select(ReleaseCandidateEvaluationBinding).where(
            ReleaseCandidateEvaluationBinding.namespace_id
            == request.namespace_id,
            ReleaseCandidateEvaluationBinding.idempotency_key
            == idempotency_key,
        )
    )
    exact_ids = (deployment.id, comparison.id)
    if existing_by_key is not None:
        if (
            existing_by_key.release_candidate_ref
            == selector.release_candidate_ref
            and (
                existing_by_key.deployment_id,
                existing_by_key.evaluation_comparison_id,
            )
            == exact_ids
        ):
            return existing_by_key
        raise ReleaseCandidateEvaluationConflictError(
            "Release evidence idempotency key is already bound"
        )
    existing_exact = await db.scalar(
        select(ReleaseCandidateEvaluationBinding).where(
            ReleaseCandidateEvaluationBinding.namespace_id
            == request.namespace_id,
            ReleaseCandidateEvaluationBinding.release_candidate_ref
            == selector.release_candidate_ref,
            ReleaseCandidateEvaluationBinding.deployment_id
            == deployment.id,
            ReleaseCandidateEvaluationBinding.evaluation_comparison_id
            == comparison.id,
        )
    )
    if existing_exact is not None:
        return existing_exact

    binding = ReleaseCandidateEvaluationBinding(
        public_id=_public_id(),
        namespace_id=request.namespace_id,
        release_candidate_ref=selector.release_candidate_ref,
        deployment_id=deployment.id,
        deployment_public_id=deployment.public_id,
        deployment_revision=deployment.revision,
        deployment_configuration_digest=(
            deployment.configuration_digest
        ),
        evaluation_comparison_id=comparison.id,
        idempotency_key=idempotency_key,
        schema_name=_BINDING_SCHEMA_NAME,
        schema_version=_BINDING_SCHEMA_VERSION,
        binding_digest=_digest(
            _binding_payload(
                selector=selector,
                deployment=deployment,
                comparison_view=comparison_view,
            )
        ),
        created_by_user_id=actor.id,
        created_at=_utcnow_mysql_safe(),
    )
    db.add(binding)
    await db.flush()
    await enqueue_domain_event(
        db,
        build_release_candidate_evaluation_bound(
            binding,
            evaluation_comparison_public_id=comparison.public_id,
            comparison_outcome=comparison.outcome.value,
            comparison_reproducibility_digest=(
                comparison.reproducibility_digest
            ),
        ),
        guaranteed_new=True,
    )
    await audit(
        db,
        user=actor,
        action="release_candidate.evaluation_bound",
        resource_type="release_candidate_evaluation_binding",
        resource_id=binding.id,
        namespace_id=binding.namespace_id,
        details={
            "public_id": binding.public_id,
            "release_candidate_ref": binding.release_candidate_ref,
            "deployment_public_id": binding.deployment_public_id,
            "deployment_revision": binding.deployment_revision,
            "evaluation_comparison_public_id": comparison.public_id,
            "comparison_outcome": comparison.outcome.value,
            "binding_digest": binding.binding_digest,
        },
    )
    return binding


async def get_release_candidate_evaluation_binding(
    db: AsyncSession,
    *,
    public_id: str,
    namespace_id: int | None = None,
) -> ReleaseCandidateEvaluationBinding:
    query = select(ReleaseCandidateEvaluationBinding).where(
        ReleaseCandidateEvaluationBinding.public_id == public_id
    )
    if namespace_id is not None:
        query = query.where(
            ReleaseCandidateEvaluationBinding.namespace_id
            == namespace_id
        )
    binding = (await db.execute(query)).scalar_one_or_none()
    if binding is None:
        raise ReleaseCandidateEvaluationNotFoundError(
            "Release evaluation binding was not found"
        )
    return binding


async def list_release_candidate_evaluation_bindings(
    db: AsyncSession,
    *,
    namespace_id: int,
    release_candidate_ref: str | None = None,
    limit: int = 100,
) -> list[ReleaseCandidateEvaluationBinding]:
    query = (
        select(ReleaseCandidateEvaluationBinding)
        .where(
            ReleaseCandidateEvaluationBinding.namespace_id
            == namespace_id
        )
        .order_by(
            ReleaseCandidateEvaluationBinding.created_at.desc(),
            ReleaseCandidateEvaluationBinding.id.desc(),
        )
        .limit(limit)
    )
    if release_candidate_ref is not None:
        query = query.where(
            ReleaseCandidateEvaluationBinding.release_candidate_ref
            == release_candidate_ref
        )
    return list((await db.scalars(query)).all())


async def load_release_candidate_evaluation_binding_view(
    db: AsyncSession,
    *,
    binding: ReleaseCandidateEvaluationBinding,
) -> ReleaseCandidateEvaluationBindingView:
    deployment = await db.get(AgentDeployment, binding.deployment_id)
    comparison = await db.get(
        EvaluationComparison,
        binding.evaluation_comparison_id,
    )
    if deployment is None or comparison is None:
        raise ReleaseCandidateEvaluationStateError(
            "Release evaluation binding reference is missing"
        )
    if (
        deployment.public_id != binding.deployment_public_id
        or deployment.revision != binding.deployment_revision
        or deployment.configuration_digest
        != binding.deployment_configuration_digest
    ):
        raise ReleaseCandidateEvaluationStateError(
            "Bound Deployment no longer matches its immutable snapshot"
        )
    review = await db.scalar(
        select(ReleaseCandidateEvaluationReview).where(
            ReleaseCandidateEvaluationReview.binding_id == binding.id
        )
    )
    return ReleaseCandidateEvaluationBindingView(
        binding=binding,
        deployment=deployment,
        comparison_view=await load_evaluation_comparison_view(
            db,
            comparison=comparison,
        ),
        review=review,
    )


async def create_release_candidate_evaluation_review(
    db: AsyncSession,
    *,
    request: ReleaseCandidateEvaluationReviewCreate,
    idempotency_key: str,
    actor: User,
) -> ReleaseCandidateEvaluationReview:
    if _SAFE_IDEMPOTENCY_KEY.fullmatch(idempotency_key) is None:
        raise ReleaseCandidateEvaluationStateError(
            "Release review idempotency key is invalid"
        )
    await require_active_namespace(db, request.namespace_id)
    binding = await get_release_candidate_evaluation_binding(
        db,
        public_id=request.binding_public_id,
        namespace_id=request.namespace_id,
    )
    existing_by_key = await db.scalar(
        select(ReleaseCandidateEvaluationReview).where(
            ReleaseCandidateEvaluationReview.namespace_id
            == request.namespace_id,
            ReleaseCandidateEvaluationReview.idempotency_key
            == idempotency_key,
        )
    )
    if existing_by_key is not None:
        if (
            existing_by_key.binding_id == binding.id
            and existing_by_key.decision == request.decision
            and existing_by_key.comment == request.comment
        ):
            return existing_by_key
        raise ReleaseCandidateEvaluationConflictError(
            "Release review idempotency key is already bound"
        )
    existing_review = await db.scalar(
        select(ReleaseCandidateEvaluationReview).where(
            ReleaseCandidateEvaluationReview.binding_id == binding.id
        )
    )
    if existing_review is not None:
        if (
            existing_review.decision == request.decision
            and existing_review.comment == request.comment
        ):
            return existing_review
        raise ReleaseCandidateEvaluationConflictError(
            "Release evaluation binding already has a final review"
        )
    payload = {
        "schema_name": _REVIEW_SCHEMA_NAME,
        "schema_version": _REVIEW_SCHEMA_VERSION,
        "namespace_id": request.namespace_id,
        "binding_public_id": binding.public_id,
        "binding_digest": binding.binding_digest,
        "decision": request.decision.value,
        "comment": request.comment,
        "reviewed_by_user_id": actor.id,
    }
    review = ReleaseCandidateEvaluationReview(
        public_id=_review_public_id(),
        namespace_id=request.namespace_id,
        binding_id=binding.id,
        decision=request.decision,
        comment=request.comment,
        idempotency_key=idempotency_key,
        schema_name=_REVIEW_SCHEMA_NAME,
        schema_version=_REVIEW_SCHEMA_VERSION,
        review_digest=_digest(payload),
        reviewed_by_user_id=actor.id,
        created_at=_utcnow_mysql_safe(),
    )
    db.add(review)
    await db.flush()
    await enqueue_domain_event(
        db,
        build_release_candidate_evaluation_reviewed(
            review,
            binding=binding,
        ),
        guaranteed_new=True,
    )
    await audit(
        db,
        user=actor,
        action="release_candidate.evaluation_reviewed",
        resource_type="release_candidate_evaluation_review",
        resource_id=review.id,
        namespace_id=review.namespace_id,
        details={
            "public_id": review.public_id,
            "binding_public_id": binding.public_id,
            "release_candidate_ref": binding.release_candidate_ref,
            "deployment_public_id": binding.deployment_public_id,
            "deployment_revision": binding.deployment_revision,
            "decision": review.decision.value,
            "review_digest": review.review_digest,
        },
    )
    return review


async def list_release_candidate_evaluation_reviews(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int = 100,
) -> list[ReleaseCandidateEvaluationReview]:
    return list(
        (
            await db.scalars(
                select(ReleaseCandidateEvaluationReview)
                .where(
                    ReleaseCandidateEvaluationReview.namespace_id
                    == namespace_id
                )
                .order_by(
                    ReleaseCandidateEvaluationReview.created_at.desc(),
                    ReleaseCandidateEvaluationReview.id.desc(),
                )
                .limit(limit)
            )
        ).all()
    )


class DatabaseReleaseEvidenceLookupPort(ReleaseEvidenceLookupPort):
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def lookup(
        self,
        selector: ReleaseEvidenceSelector,
    ) -> tuple[CandidateEvidenceReference, ...]:
        bindings = (
            await self._db.scalars(
                select(ReleaseCandidateEvaluationBinding)
                .where(
                    ReleaseCandidateEvaluationBinding.namespace_id
                    == selector.namespace_id,
                    ReleaseCandidateEvaluationBinding.release_candidate_ref
                    == selector.release_candidate_ref,
                    ReleaseCandidateEvaluationBinding.deployment_public_id
                    == selector.deployment_public_id,
                    ReleaseCandidateEvaluationBinding.deployment_revision
                    == selector.deployment_revision,
                )
                .order_by(
                    ReleaseCandidateEvaluationBinding.created_at,
                    ReleaseCandidateEvaluationBinding.public_id,
                )
            )
        ).all()
        if not bindings:
            return ()
        comparison_ids = {
            binding.evaluation_comparison_id for binding in bindings
        }
        comparisons = (
            await self._db.scalars(
                select(EvaluationComparison).where(
                    EvaluationComparison.id.in_(comparison_ids)
                )
            )
        ).all()
        comparison_by_id = {
            comparison.id: comparison for comparison in comparisons
        }
        if len(comparison_by_id) != len(comparison_ids):
            raise ReleaseCandidateEvaluationStateError(
                "Bound EvaluationComparison is missing"
            )
        verdict_by_outcome = {
            EvaluationComparisonOutcome.PASS: "pass",
            EvaluationComparisonOutcome.REGRESSION: "fail",
            EvaluationComparisonOutcome.INCONCLUSIVE: "inconclusive",
        }
        reviews = (
            await self._db.scalars(
                select(ReleaseCandidateEvaluationReview).where(
                    ReleaseCandidateEvaluationReview.binding_id.in_(
                        [binding.id for binding in bindings]
                    )
                )
            )
        ).all()
        review_by_binding_id = {
            review.binding_id: review for review in reviews
        }
        evidence: list[CandidateEvidenceReference] = []
        for binding in bindings:
            evidence.append(
                CandidateEvidenceReference(
                    selector=selector,
                    evidence_id=binding.public_id,
                    evidence_kind="evaluation_comparison",
                    verdict=verdict_by_outcome[
                        comparison_by_id[
                            binding.evaluation_comparison_id
                        ].outcome
                    ],
                    artifact_sha256=binding.binding_digest,
                    observed_at=ensure_utc(binding.created_at),
                )
            )
            review = review_by_binding_id.get(binding.id)
            if review is not None:
                evidence.append(
                    CandidateEvidenceReference(
                        selector=selector,
                        evidence_id=review.public_id,
                        evidence_kind="manual_review",
                        verdict=(
                            "pass"
                            if review.decision
                            == ReleaseCandidateReviewDecision.APPROVED
                            else "fail"
                        ),
                        artifact_sha256=review.review_digest,
                        observed_at=ensure_utc(review.created_at),
                    )
                )
        return tuple(evidence)


async def evaluate_release_candidate_gate(
    db: AsyncSession,
    *,
    selector: ReleaseEvidenceSelector,
    actor: User,
) -> CandidateReleaseGateDecision:
    await require_active_namespace(db, selector.namespace_id)
    # Resolve the exact Deployment before evaluating so a syntactically valid
    # but nonexistent selector cannot be mistaken for ordinary missing evidence.
    deployment = await _load_exact_deployment(db, selector=selector)
    decision = await ReleaseGateService(
        evidence_lookup=DatabaseReleaseEvidenceLookupPort(db)
    ).evaluate_candidate(selector)
    await audit(
        db,
        user=actor,
        action="release_candidate.gate_evaluated",
        resource_type="agent_deployment",
        resource_id=deployment.id,
        namespace_id=selector.namespace_id,
        details={
            "release_candidate_ref": selector.release_candidate_ref,
            "deployment_public_id": selector.deployment_public_id,
            "deployment_revision": selector.deployment_revision,
            "outcome": decision.outcome,
            "reason_codes": list(decision.reason_codes),
            "evidence_count": len(decision.evidence),
            "decision_digest": decision.decision_digest,
            "schema_name": _GATE_SCHEMA_NAME,
            "schema_version": _GATE_SCHEMA_VERSION,
        },
    )
    return decision
