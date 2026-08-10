from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, status

from app.core.deps import (
    CurrentUser,
    DB,
    require_namespace_member,
    require_namespace_writer,
)
from app.core.time import ensure_utc
from app.models.release import (
    ReleaseCandidateEvaluationBinding,
    ReleaseCandidateEvaluationReview,
)
from app.schemas.release import (
    ReleaseCandidateEvaluationBindingCreate,
    ReleaseCandidateEvaluationBindingOut,
    ReleaseCandidateEvaluationReviewCreate,
    ReleaseCandidateEvaluationReviewOut,
    ReleaseCandidateGateDecisionOut,
    ReleaseCandidateSelectorIn,
    ReleaseGateEvidenceOut,
)
from app.services.release_candidate_evaluation_service import (
    ReleaseCandidateEvaluationConflictError,
    ReleaseCandidateEvaluationError,
    ReleaseCandidateEvaluationNotFoundError,
    ReleaseCandidateEvaluationStateError,
    create_release_candidate_evaluation_binding,
    create_release_candidate_evaluation_review,
    evaluate_release_candidate_gate,
    get_release_candidate_evaluation_binding,
    list_release_candidate_evaluation_bindings,
    list_release_candidate_evaluation_reviews,
    load_release_candidate_evaluation_binding_view,
)
from app.services.release_evidence_ports import ReleaseEvidenceSelector


binding_router = APIRouter(
    prefix="/release-evidence",
    tags=["release-governance"],
)
gate_router = APIRouter(
    prefix="/release-gates",
    tags=["release-governance"],
)
ReleaseEvidenceIdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
]


def _review_out(
    review: ReleaseCandidateEvaluationReview,
    *,
    binding_public_id: str,
) -> ReleaseCandidateEvaluationReviewOut:
    return ReleaseCandidateEvaluationReviewOut(
        public_id=review.public_id,
        namespace_id=review.namespace_id,
        binding_public_id=binding_public_id,
        decision=review.decision,
        comment=review.comment,
        schema_name=review.schema_name,
        schema_version=review.schema_version,
        review_digest=review.review_digest,
        reviewed_by_user_id=review.reviewed_by_user_id,
        created_at=ensure_utc(review.created_at),
    )


def _http_error(exc: ReleaseCandidateEvaluationError) -> HTTPException:
    if isinstance(exc, ReleaseCandidateEvaluationNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(
        exc,
        (
            ReleaseCandidateEvaluationConflictError,
            ReleaseCandidateEvaluationStateError,
        ),
    ):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(
        status_code=422,
        detail="Release evaluation evidence was rejected",
    )


async def _binding_out(
    db: DB,
    binding: ReleaseCandidateEvaluationBinding,
) -> ReleaseCandidateEvaluationBindingOut:
    view = await load_release_candidate_evaluation_binding_view(
        db,
        binding=binding,
    )
    comparison = view.comparison_view.comparison
    return ReleaseCandidateEvaluationBindingOut(
        public_id=binding.public_id,
        namespace_id=binding.namespace_id,
        release_candidate_ref=binding.release_candidate_ref,
        deployment_public_id=binding.deployment_public_id,
        deployment_revision=binding.deployment_revision,
        deployment_configuration_digest=(
            binding.deployment_configuration_digest
        ),
        evaluation_comparison_public_id=comparison.public_id,
        comparison_outcome=comparison.outcome.value,
        comparison_reproducibility_digest=(
            comparison.reproducibility_digest
        ),
        candidate_evaluation_public_id=(
            view.comparison_view.candidate.evaluation.public_id
        ),
        candidate_manifest_public_id=(
            view.comparison_view.candidate.manifest.public_id
        ),
        schema_name=binding.schema_name,
        schema_version=binding.schema_version,
        binding_digest=binding.binding_digest,
        created_by_user_id=binding.created_by_user_id,
        created_at=ensure_utc(binding.created_at),
        review=(
            _review_out(
                view.review,
                binding_public_id=binding.public_id,
            )
            if view.review is not None
            else None
        ),
    )


def _selector(body: ReleaseCandidateSelectorIn) -> ReleaseEvidenceSelector:
    return ReleaseEvidenceSelector(
        namespace_id=body.namespace_id,
        release_candidate_ref=body.release_candidate_ref,
        deployment_public_id=body.deployment_public_id,
        deployment_revision=body.deployment_revision,
    )


@binding_router.post(
    "/bindings",
    operation_id="bindReleaseCandidateEvaluation",
    response_model=ReleaseCandidateEvaluationBindingOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_release_evaluation_binding(
    body: ReleaseCandidateEvaluationBindingCreate,
    idempotency_key: ReleaseEvidenceIdempotencyKey,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_writer(current_user, body.namespace_id, db)
    try:
        binding = await create_release_candidate_evaluation_binding(
            db,
            request=body,
            idempotency_key=idempotency_key,
            actor=current_user,
        )
        output = await _binding_out(db, binding)
        await db.commit()
        return output
    except ReleaseCandidateEvaluationError as exc:
        raise _http_error(exc) from exc


@binding_router.get(
    "/bindings",
    operation_id="listReleaseCandidateEvaluationBindings",
    response_model=list[ReleaseCandidateEvaluationBindingOut],
)
async def list_release_evaluation_bindings(
    namespace_id: Annotated[int, Query(gt=0)],
    db: DB,
    current_user: CurrentUser,
    release_candidate_ref: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=255,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,254}$",
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    bindings = await list_release_candidate_evaluation_bindings(
        db,
        namespace_id=namespace_id,
        release_candidate_ref=release_candidate_ref,
        limit=limit,
    )
    return [await _binding_out(db, binding) for binding in bindings]


@binding_router.get(
    "/bindings/{public_id}",
    operation_id="getReleaseCandidateEvaluationBinding",
    response_model=ReleaseCandidateEvaluationBindingOut,
)
async def get_release_evaluation_binding(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    try:
        binding = await get_release_candidate_evaluation_binding(
            db,
            public_id=public_id,
        )
        await require_namespace_member(
            current_user,
            binding.namespace_id,
            db,
        )
        return await _binding_out(db, binding)
    except ReleaseCandidateEvaluationNotFoundError as exc:
        raise _http_error(exc) from exc
    except HTTPException as exc:
        if exc.status_code == 403:
            raise HTTPException(
                status_code=404,
                detail="Release evaluation binding was not found",
            ) from exc
        raise


@binding_router.post(
    "/reviews",
    operation_id="reviewReleaseCandidateEvaluation",
    response_model=ReleaseCandidateEvaluationReviewOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_release_evaluation_review(
    body: ReleaseCandidateEvaluationReviewCreate,
    idempotency_key: ReleaseEvidenceIdempotencyKey,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_writer(current_user, body.namespace_id, db)
    try:
        review = await create_release_candidate_evaluation_review(
            db,
            request=body,
            idempotency_key=idempotency_key,
            actor=current_user,
        )
        binding = await db.get(
            ReleaseCandidateEvaluationBinding,
            review.binding_id,
        )
        if binding is None:
            raise ReleaseCandidateEvaluationStateError(
                "Release evaluation review binding is missing"
            )
        output = _review_out(
            review,
            binding_public_id=binding.public_id,
        )
        await db.commit()
        return output
    except ReleaseCandidateEvaluationError as exc:
        raise _http_error(exc) from exc


@binding_router.get(
    "/reviews",
    operation_id="listReleaseCandidateEvaluationReviews",
    response_model=list[ReleaseCandidateEvaluationReviewOut],
)
async def list_release_evaluation_reviews(
    namespace_id: Annotated[int, Query(gt=0)],
    db: DB,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    reviews = await list_release_candidate_evaluation_reviews(
        db,
        namespace_id=namespace_id,
        limit=limit,
    )
    output: list[ReleaseCandidateEvaluationReviewOut] = []
    for review in reviews:
        binding = await db.get(
            ReleaseCandidateEvaluationBinding,
            review.binding_id,
        )
        if binding is None:
            raise _http_error(
                ReleaseCandidateEvaluationStateError(
                    "Release evaluation review binding is missing"
                )
            )
        output.append(
            _review_out(
                review,
                binding_public_id=binding.public_id,
            )
        )
    return output


@gate_router.post(
    "/evaluations",
    operation_id="evaluateReleaseCandidateGate",
    response_model=ReleaseCandidateGateDecisionOut,
)
async def evaluate_candidate_release_gate(
    body: ReleaseCandidateSelectorIn,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_writer(current_user, body.namespace_id, db)
    try:
        decision = await evaluate_release_candidate_gate(
            db,
            selector=_selector(body),
            actor=current_user,
        )
        output = ReleaseCandidateGateDecisionOut(
            schema_name="duckdock-candidate-release-gate",
            schema_version="1.0",
            namespace_id=body.namespace_id,
            release_candidate_ref=body.release_candidate_ref,
            deployment_public_id=body.deployment_public_id,
            deployment_revision=body.deployment_revision,
            outcome=decision.outcome,
            reason_codes=list(decision.reason_codes),
            evidence_count=len(decision.evidence),
            evidence=[
                ReleaseGateEvidenceOut(
                    evidence_id=item.evidence_id,
                    evidence_kind=item.evidence_kind,
                    verdict=item.verdict,
                    evidence_digest=item.artifact_sha256,
                    observed_at=ensure_utc(item.observed_at),
                )
                for item in decision.evidence
            ],
            decision_digest=decision.decision_digest,
        )
        await db.commit()
        return output
    except ReleaseCandidateEvaluationError as exc:
        raise _http_error(exc) from exc
