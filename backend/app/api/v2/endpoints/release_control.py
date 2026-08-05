from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from app.core.api_token_security import hash_reporter_token_secret
from app.core.deps import (
    CurrentUser,
    DB,
    require_namespace_admin,
    require_namespace_member,
    require_namespace_writer,
)
from app.models.control_plane import ReporterCredential, RuntimeInstance
from app.schemas.control_plane import (
    ReporterCredentialCreatedOut,
    ReporterCredentialOut,
    ReporterCredentialRevoke,
)
from app.schemas.release_control import (
    ReleaseCandidateCreate,
    ReleaseCandidateApprovalCreate,
    ReleaseCandidateApprovalOut,
    ReleaseCandidateOut,
    ReleaseCanaryEvaluationCreate,
    ReleaseCanaryEvaluationOut,
    ReleaseDeploymentReceiptCreate,
    ReleaseDeploymentReceiptOut,
    ReleaseEnvironmentCreate,
    ReleaseEnvironmentOut,
    ReleaseEnvironmentReleaseOut,
    ReleasePolicyCreate,
    ReleasePolicyDecisionOut,
    ReleasePolicyEvaluationCreate,
    ReleasePolicyExceptionCreate,
    ReleasePolicyExceptionOut,
    ReleasePolicyExceptionReviewCreate,
    ReleasePolicyOut,
    ReleasePolicyVersionCreate,
    ReleasePolicyVersionOut,
    ReleasePromotionCreate,
    ReleasePromotionOut,
    ReleaseRollbackCreate,
    ReleaseRollbackOut,
    ReleaseReceiptCredentialCreate,
)
from app.services.audit_service import audit
from app.services.release_control_service import (
    ReleaseControlConflictError,
    ReleaseControlNotFoundError,
    ReleaseControlReferenceError,
    ReleaseControlStateError,
    ReleaseControlTenantMismatchError,
    create_release_candidate,
    create_release_environment,
    create_release_policy,
    create_release_policy_version,
    evaluate_release_policy,
    get_release_candidate,
    get_release_environment,
    get_release_policy,
    get_release_policy_decision,
    get_release_policy_version,
    list_release_candidates,
    list_release_environments,
    list_release_policies,
    list_release_policy_decisions,
    release_candidate_out,
    release_policy_decision_out,
    release_policy_out,
    release_policy_version_out,
)
from app.services.release_promotion_service import (
    create_release_candidate_approval,
    create_release_policy_exception,
    create_release_promotion,
    evaluate_release_canary,
    get_release_environment_release,
    get_release_policy_exception,
    get_release_promotion,
    get_release_rollback,
    list_release_candidate_approvals,
    list_release_canary_evaluations,
    list_release_deployment_receipts,
    list_release_environment_releases,
    list_release_policy_exceptions,
    list_release_promotions,
    list_release_rollbacks,
    record_release_deployment_receipt,
    release_candidate_approval_out,
    release_canary_evaluation_out,
    release_deployment_receipt_out,
    release_environment_release_out,
    release_policy_exception_out,
    release_promotion_out,
    release_rollback_out,
    request_release_rollback,
    review_release_policy_exception,
)
from app.services.reporter_identity_service import (
    RELEASE_RECEIPT_SCOPE,
    ReporterExecutionIdentity,
    ReporterIdentityError,
    authenticate_reporter_release_credential,
)
from app.services.report_upload_service import generate_runtime_report_token


environment_router = APIRouter(prefix="/release-environments", tags=["release-control"])
policy_router = APIRouter(prefix="/release-policies", tags=["release-control"])
policy_version_router = APIRouter(prefix="/release-policy-versions", tags=["release-control"])
candidate_router = APIRouter(prefix="/release-candidates", tags=["release-control"])
decision_router = APIRouter(prefix="/release-policy-decisions", tags=["release-control"])
exception_router = APIRouter(prefix="/release-policy-exceptions", tags=["release-control"])
promotion_router = APIRouter(prefix="/release-promotions", tags=["release-control"])
environment_release_router = APIRouter(
    prefix="/release-environment-releases", tags=["release-control"]
)
rollback_router = APIRouter(prefix="/release-rollbacks", tags=["release-control"])
canary_router = APIRouter(prefix="/release-canary-evaluations", tags=["release-control"])
receipt_router = APIRouter(prefix="/release-deployment-receipts", tags=["release-control"])
receipt_credential_router = APIRouter(
    prefix="/release-receipt-credentials",
    tags=["release-control"],
)
reporter_receipt_router = APIRouter(prefix="/reporter", tags=["reporter-release"])
reporter_bearer = HTTPBearer()


async def get_reporter_release_identity(
    db: DB,
    credentials: HTTPAuthorizationCredentials = Depends(reporter_bearer),
) -> ReporterExecutionIdentity:
    try:
        return await authenticate_reporter_release_credential(db, credentials.credentials)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            raise
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Reporter credential is not authorized to return release receipts",
        ) from exc
    except ReporterIdentityError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Reporter credential is not authorized to return release receipts",
        ) from exc


ReporterReleaseIdentity = Annotated[
    ReporterExecutionIdentity,
    Depends(get_reporter_release_identity),
]


def _http_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, ReleaseControlNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ReleaseControlConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ReleaseControlTenantMismatchError):
        return HTTPException(status_code=404, detail="Release Control resource was not found")
    if isinstance(exc, (ReleaseControlReferenceError, ReleaseControlStateError)):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=422, detail="Release Control operation rejected")


async def _require_visible(current_user, namespace_id: int, db: DB) -> None:
    try:
        await require_namespace_member(current_user, namespace_id, db)
    except HTTPException as exc:
        if exc.status_code == 403:
            raise HTTPException(status_code=404, detail="Release Control resource was not found") from exc
        raise


@environment_router.post(
    "",
    response_model=ReleaseEnvironmentOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_release_environment_definition(
    body: ReleaseEnvironmentCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_admin(current_user, body.namespace_id, db)
    try:
        return await create_release_environment(db, request=body, actor=current_user)
    except (ReleaseControlConflictError, ReleaseControlReferenceError) as exc:
        raise _http_exception(exc) from exc


@environment_router.get("", response_model=list[ReleaseEnvironmentOut])
async def list_release_environment_definitions(
    namespace_id: int,
    db: DB,
    current_user: CurrentUser,
    include_retired: bool = False,
):
    await require_namespace_member(current_user, namespace_id, db)
    return await list_release_environments(
        db,
        namespace_id=namespace_id,
        include_retired=include_retired,
    )


@environment_router.get("/{public_id}", response_model=ReleaseEnvironmentOut)
async def get_release_environment_definition(public_id: str, db: DB, current_user: CurrentUser):
    try:
        row = await get_release_environment(db, public_id)
        await _require_visible(current_user, row.namespace_id, db)
        return row
    except ReleaseControlNotFoundError as exc:
        raise _http_exception(exc) from exc


@policy_router.post("", response_model=ReleasePolicyOut, status_code=status.HTTP_201_CREATED)
async def create_release_policy_definition(
    body: ReleasePolicyCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_admin(current_user, body.namespace_id, db)
    try:
        row = await create_release_policy(db, request=body, actor=current_user)
        row = await get_release_policy(db, row.public_id)
        return release_policy_out(row)
    except ReleaseControlConflictError as exc:
        raise _http_exception(exc) from exc


@policy_router.get("", response_model=list[ReleasePolicyOut])
async def list_release_policy_definitions(
    namespace_id: int,
    db: DB,
    current_user: CurrentUser,
    limit: int = Query(default=100, ge=1, le=200),
):
    await require_namespace_member(current_user, namespace_id, db)
    rows = await list_release_policies(db, namespace_id=namespace_id, limit=limit)
    return [release_policy_out(row) for row in rows]


@policy_router.get("/{public_id}", response_model=ReleasePolicyOut)
async def get_release_policy_definition(public_id: str, db: DB, current_user: CurrentUser):
    try:
        row = await get_release_policy(db, public_id)
        await _require_visible(current_user, row.namespace_id, db)
        return release_policy_out(row)
    except ReleaseControlNotFoundError as exc:
        raise _http_exception(exc) from exc


@policy_router.post(
    "/{public_id}/versions",
    response_model=ReleasePolicyVersionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_release_policy_definition_version(
    public_id: str,
    body: ReleasePolicyVersionCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_admin(current_user, body.namespace_id, db)
    try:
        row = await create_release_policy_version(
            db,
            policy_public_id=public_id,
            request=body,
            actor=current_user,
        )
        row = await get_release_policy_version(db, row.public_id)
        return release_policy_version_out(row)
    except (
        ReleaseControlConflictError,
        ReleaseControlNotFoundError,
        ReleaseControlReferenceError,
        ReleaseControlStateError,
        ReleaseControlTenantMismatchError,
    ) as exc:
        raise _http_exception(exc) from exc


@policy_version_router.get("/{public_id}", response_model=ReleasePolicyVersionOut)
async def get_release_policy_definition_version(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    try:
        row = await get_release_policy_version(db, public_id)
        await _require_visible(current_user, row.namespace_id, db)
        return release_policy_version_out(row)
    except ReleaseControlNotFoundError as exc:
        raise _http_exception(exc) from exc


@candidate_router.post(
    "",
    response_model=ReleaseCandidateOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_release_candidate_record(
    body: ReleaseCandidateCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_writer(current_user, body.namespace_id, db)
    try:
        row = await create_release_candidate(db, request=body, actor=current_user)
        return release_candidate_out(row)
    except (
        ReleaseControlConflictError,
        ReleaseControlNotFoundError,
        ReleaseControlReferenceError,
        ReleaseControlStateError,
        ReleaseControlTenantMismatchError,
    ) as exc:
        raise _http_exception(exc) from exc


@candidate_router.get("", response_model=list[ReleaseCandidateOut])
async def list_release_candidate_records(
    namespace_id: int,
    db: DB,
    current_user: CurrentUser,
    environment_public_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
):
    await require_namespace_member(current_user, namespace_id, db)
    rows = await list_release_candidates(
        db,
        namespace_id=namespace_id,
        environment_public_id=environment_public_id,
        limit=limit,
    )
    return [release_candidate_out(row) for row in rows]


@candidate_router.get("/{public_id}", response_model=ReleaseCandidateOut)
async def get_release_candidate_record(public_id: str, db: DB, current_user: CurrentUser):
    try:
        row = await get_release_candidate(db, public_id)
        await _require_visible(current_user, row.namespace_id, db)
        return release_candidate_out(row)
    except ReleaseControlNotFoundError as exc:
        raise _http_exception(exc) from exc


@candidate_router.post(
    "/{public_id}/policy-evaluations",
    response_model=ReleasePolicyDecisionOut,
    status_code=status.HTTP_201_CREATED,
)
async def evaluate_release_candidate_policy(
    public_id: str,
    body: ReleasePolicyEvaluationCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_writer(current_user, body.namespace_id, db)
    try:
        row = await evaluate_release_policy(
            db,
            candidate_public_id=public_id,
            request=body,
            actor=current_user,
        )
        return release_policy_decision_out(row)
    except (
        ReleaseControlConflictError,
        ReleaseControlNotFoundError,
        ReleaseControlReferenceError,
        ReleaseControlStateError,
        ReleaseControlTenantMismatchError,
    ) as exc:
        raise _http_exception(exc) from exc


@decision_router.get("", response_model=list[ReleasePolicyDecisionOut])
async def list_release_policy_decision_records(
    namespace_id: int,
    db: DB,
    current_user: CurrentUser,
    candidate_public_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
):
    await require_namespace_member(current_user, namespace_id, db)
    rows = await list_release_policy_decisions(
        db,
        namespace_id=namespace_id,
        candidate_public_id=candidate_public_id,
        limit=limit,
    )
    return [release_policy_decision_out(row) for row in rows]


@decision_router.get("/{public_id}", response_model=ReleasePolicyDecisionOut)
async def get_release_policy_decision_record(public_id: str, db: DB, current_user: CurrentUser):
    try:
        row = await get_release_policy_decision(db, public_id)
        await _require_visible(current_user, row.namespace_id, db)
        return release_policy_decision_out(row)
    except ReleaseControlNotFoundError as exc:
        raise _http_exception(exc) from exc


@candidate_router.post(
    "/{public_id}/approvals",
    response_model=ReleaseCandidateApprovalOut,
    status_code=status.HTTP_201_CREATED,
)
async def approve_release_candidate(
    public_id: str,
    body: ReleaseCandidateApprovalCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_admin(current_user, body.namespace_id, db)
    try:
        row = await create_release_candidate_approval(
            db,
            candidate_public_id=public_id,
            request=body,
            actor=current_user,
        )
        return release_candidate_approval_out(row)
    except (
        ReleaseControlConflictError,
        ReleaseControlNotFoundError,
        ReleaseControlReferenceError,
        ReleaseControlStateError,
        ReleaseControlTenantMismatchError,
    ) as exc:
        raise _http_exception(exc) from exc


@candidate_router.get(
    "/{public_id}/approvals",
    response_model=list[ReleaseCandidateApprovalOut],
)
async def list_release_candidate_approval_records(
    public_id: str,
    namespace_id: int,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_member(current_user, namespace_id, db)
    try:
        rows = await list_release_candidate_approvals(
            db,
            namespace_id=namespace_id,
            candidate_public_id=public_id,
        )
        return [release_candidate_approval_out(row) for row in rows]
    except (
        ReleaseControlNotFoundError,
        ReleaseControlTenantMismatchError,
    ) as exc:
        raise _http_exception(exc) from exc


@exception_router.post(
    "",
    response_model=ReleasePolicyExceptionOut,
    status_code=status.HTTP_201_CREATED,
)
async def request_release_policy_exception(
    body: ReleasePolicyExceptionCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_writer(current_user, body.namespace_id, db)
    try:
        row = await create_release_policy_exception(db, request=body, actor=current_user)
        return release_policy_exception_out(row)
    except (
        ReleaseControlConflictError,
        ReleaseControlNotFoundError,
        ReleaseControlReferenceError,
        ReleaseControlStateError,
        ReleaseControlTenantMismatchError,
    ) as exc:
        raise _http_exception(exc) from exc


@exception_router.get("", response_model=list[ReleasePolicyExceptionOut])
async def list_release_policy_exception_records(
    namespace_id: int,
    db: DB,
    current_user: CurrentUser,
    limit: int = Query(default=100, ge=1, le=200),
):
    await require_namespace_member(current_user, namespace_id, db)
    rows = await list_release_policy_exceptions(
        db,
        namespace_id=namespace_id,
        limit=limit,
    )
    return [release_policy_exception_out(row) for row in rows]


@exception_router.get("/{public_id}", response_model=ReleasePolicyExceptionOut)
async def get_release_policy_exception_record(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    try:
        row = await get_release_policy_exception(db, public_id)
        await _require_visible(current_user, row.namespace_id, db)
        return release_policy_exception_out(row)
    except ReleaseControlNotFoundError as exc:
        raise _http_exception(exc) from exc


@exception_router.post(
    "/{public_id}/review",
    response_model=ReleasePolicyExceptionOut,
    status_code=status.HTTP_201_CREATED,
)
async def review_release_policy_exception_record(
    public_id: str,
    body: ReleasePolicyExceptionReviewCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_admin(current_user, body.namespace_id, db)
    try:
        await review_release_policy_exception(
            db,
            exception_public_id=public_id,
            request=body,
            actor=current_user,
        )
        row = await get_release_policy_exception(db, public_id)
        return release_policy_exception_out(row)
    except (
        ReleaseControlConflictError,
        ReleaseControlNotFoundError,
        ReleaseControlStateError,
        ReleaseControlTenantMismatchError,
    ) as exc:
        raise _http_exception(exc) from exc


@promotion_router.post(
    "",
    response_model=ReleasePromotionOut,
    status_code=status.HTTP_201_CREATED,
)
async def dispatch_release_promotion(
    body: ReleasePromotionCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_writer(current_user, body.namespace_id, db)
    try:
        row = await create_release_promotion(db, request=body, actor=current_user)
        return release_promotion_out(row)
    except (
        ReleaseControlConflictError,
        ReleaseControlNotFoundError,
        ReleaseControlReferenceError,
        ReleaseControlStateError,
        ReleaseControlTenantMismatchError,
    ) as exc:
        raise _http_exception(exc) from exc


@promotion_router.get("", response_model=list[ReleasePromotionOut])
async def list_release_promotion_records(
    namespace_id: int,
    db: DB,
    current_user: CurrentUser,
    limit: int = Query(default=100, ge=1, le=200),
):
    await require_namespace_member(current_user, namespace_id, db)
    rows = await list_release_promotions(db, namespace_id=namespace_id, limit=limit)
    return [release_promotion_out(row) for row in rows]


@promotion_router.get("/{public_id}", response_model=ReleasePromotionOut)
async def get_release_promotion_record(public_id: str, db: DB, current_user: CurrentUser):
    try:
        row = await get_release_promotion(db, public_id)
        await _require_visible(current_user, row.namespace_id, db)
        return release_promotion_out(row)
    except ReleaseControlNotFoundError as exc:
        raise _http_exception(exc) from exc


@promotion_router.post(
    "/{public_id}/canary-evaluations",
    response_model=ReleaseCanaryEvaluationOut,
    status_code=status.HTTP_201_CREATED,
)
async def evaluate_release_promotion_canary(
    public_id: str,
    body: ReleaseCanaryEvaluationCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_writer(current_user, body.namespace_id, db)
    try:
        row = await evaluate_release_canary(
            db,
            promotion_public_id=public_id,
            request=body,
            actor=current_user,
        )
        return release_canary_evaluation_out(row)
    except (
        ReleaseControlConflictError,
        ReleaseControlNotFoundError,
        ReleaseControlStateError,
        ReleaseControlTenantMismatchError,
    ) as exc:
        raise _http_exception(exc) from exc


@promotion_router.post(
    "/{public_id}/rollback",
    response_model=ReleaseRollbackOut,
    status_code=status.HTTP_201_CREATED,
)
async def rollback_release_promotion(
    public_id: str,
    body: ReleaseRollbackCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_admin(current_user, body.namespace_id, db)
    try:
        row = await request_release_rollback(
            db,
            promotion_public_id=public_id,
            request=body,
            actor=current_user,
        )
        return release_rollback_out(row)
    except (
        ReleaseControlConflictError,
        ReleaseControlNotFoundError,
        ReleaseControlReferenceError,
        ReleaseControlStateError,
        ReleaseControlTenantMismatchError,
    ) as exc:
        raise _http_exception(exc) from exc


@rollback_router.get("/{public_id}", response_model=ReleaseRollbackOut)
async def get_release_rollback_record(public_id: str, db: DB, current_user: CurrentUser):
    try:
        row = await get_release_rollback(db, public_id)
        await _require_visible(current_user, row.namespace_id, db)
        return release_rollback_out(row)
    except ReleaseControlNotFoundError as exc:
        raise _http_exception(exc) from exc


@rollback_router.get("", response_model=list[ReleaseRollbackOut])
async def list_release_rollback_records(
    namespace_id: int,
    db: DB,
    current_user: CurrentUser,
    limit: int = Query(default=100, ge=1, le=200),
):
    await require_namespace_member(current_user, namespace_id, db)
    rows = await list_release_rollbacks(db, namespace_id=namespace_id, limit=limit)
    return [release_rollback_out(row) for row in rows]


@canary_router.get("", response_model=list[ReleaseCanaryEvaluationOut])
async def list_release_canary_evaluation_records(
    namespace_id: int,
    db: DB,
    current_user: CurrentUser,
    limit: int = Query(default=100, ge=1, le=200),
):
    await require_namespace_member(current_user, namespace_id, db)
    rows = await list_release_canary_evaluations(
        db,
        namespace_id=namespace_id,
        limit=limit,
    )
    return [release_canary_evaluation_out(row) for row in rows]


@environment_release_router.get("", response_model=list[ReleaseEnvironmentReleaseOut])
async def list_release_environment_release_records(
    namespace_id: int,
    db: DB,
    current_user: CurrentUser,
    environment_public_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
):
    await require_namespace_member(current_user, namespace_id, db)
    rows = await list_release_environment_releases(
        db,
        namespace_id=namespace_id,
        environment_public_id=environment_public_id,
        limit=limit,
    )
    return [release_environment_release_out(row) for row in rows]


@environment_release_router.get("/{public_id}", response_model=ReleaseEnvironmentReleaseOut)
async def get_release_environment_release_record(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    try:
        row = await get_release_environment_release(db, public_id)
        await _require_visible(current_user, row.namespace_id, db)
        return release_environment_release_out(row)
    except ReleaseControlNotFoundError as exc:
        raise _http_exception(exc) from exc


@receipt_router.get("", response_model=list[ReleaseDeploymentReceiptOut])
async def list_release_deployment_receipt_records(
    namespace_id: int,
    db: DB,
    current_user: CurrentUser,
    limit: int = Query(default=100, ge=1, le=200),
):
    await require_namespace_member(current_user, namespace_id, db)
    rows = await list_release_deployment_receipts(
        db,
        namespace_id=namespace_id,
        limit=limit,
    )
    return [release_deployment_receipt_out(row) for row in rows]


@receipt_credential_router.post(
    "",
    response_model=ReporterCredentialCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_release_receipt_credential(
    body: ReleaseReceiptCredentialCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_admin(current_user, body.namespace_id, db)
    runtime = await db.scalar(
        select(RuntimeInstance).where(
            RuntimeInstance.id == body.runtime_id,
            RuntimeInstance.namespace_id == body.namespace_id,
        )
    )
    if runtime is None:
        raise HTTPException(status_code=404, detail="Runtime was not found")
    if body.expires_at is not None and body.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=422, detail="credential expiry must be in the future")
    prefix, secret, token = generate_runtime_report_token()
    row = ReporterCredential(
        runtime_id=runtime.id,
        user_id=current_user.id,
        device_id=body.device_id,
        name=body.name,
        token_prefix=prefix,
        token_hash=hash_reporter_token_secret(secret),
        scopes=[RELEASE_RECEIPT_SCOPE],
        expires_at=body.expires_at,
        metadata_json={
            "purpose": "release_receipt",
            "issued_by_user_id": current_user.id,
        },
    )
    db.add(row)
    await db.flush()
    await audit(
        db,
        user=current_user,
        action="release.receipt_credential.created",
        resource_type="reporter_credential",
        resource_id=row.id,
        namespace_id=body.namespace_id,
        details={
            "runtime_id": row.runtime_id,
            "credential_id": row.id,
            "token_prefix": row.token_prefix,
            "scope": RELEASE_RECEIPT_SCOPE,
            "expires_at": row.expires_at.isoformat() if row.expires_at is not None else None,
        },
    )
    # The returned token is designed for immediate Runtime use. Make the
    # credential visible to a separate authentication transaction before the
    # plaintext secret leaves this response.
    await db.commit()
    await db.refresh(row)
    payload = ReporterCredentialOut.model_validate(row).model_dump()
    return ReporterCredentialCreatedOut(**payload, token=token)


@receipt_credential_router.get("", response_model=list[ReporterCredentialOut])
async def list_release_receipt_credentials(
    namespace_id: int,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_admin(current_user, namespace_id, db)
    rows = list(
        (
            await db.scalars(
                select(ReporterCredential)
                .join(RuntimeInstance, RuntimeInstance.id == ReporterCredential.runtime_id)
                .where(RuntimeInstance.namespace_id == namespace_id)
                .order_by(ReporterCredential.created_at.desc(), ReporterCredential.id.desc())
                .limit(200)
            )
        ).all()
    )
    return [
        row
        for row in rows
        if RELEASE_RECEIPT_SCOPE
        in {scope for scope in (row.scopes or []) if isinstance(scope, str)}
    ]


@receipt_credential_router.post(
    "/{credential_id}/revoke",
    response_model=ReporterCredentialOut,
)
async def revoke_release_receipt_credential(
    credential_id: int,
    body: ReporterCredentialRevoke,
    db: DB,
    current_user: CurrentUser,
):
    row = await db.scalar(
        select(ReporterCredential).where(ReporterCredential.id == credential_id)
    )
    if row is None or RELEASE_RECEIPT_SCOPE not in set(row.scopes or []):
        raise HTTPException(status_code=404, detail="Release receipt credential was not found")
    runtime = await db.scalar(select(RuntimeInstance).where(RuntimeInstance.id == row.runtime_id))
    if runtime is None:
        raise HTTPException(status_code=404, detail="Runtime was not found")
    await require_namespace_admin(current_user, runtime.namespace_id, db)
    now = datetime.now(timezone.utc)
    row.is_active = False
    row.revoked_at = row.revoked_at or now
    row.revoked_by = current_user.id
    row.revoked_reason = body.reason
    await audit(
        db,
        user=current_user,
        action="release.receipt_credential.revoked",
        resource_type="reporter_credential",
        resource_id=row.id,
        namespace_id=runtime.namespace_id,
        details={
            "runtime_id": row.runtime_id,
            "credential_id": row.id,
            "token_prefix": row.token_prefix,
            "scope": RELEASE_RECEIPT_SCOPE,
        },
    )
    return row


@reporter_receipt_router.post(
    "/promotion-receipts",
    response_model=ReleaseDeploymentReceiptOut,
    status_code=status.HTTP_201_CREATED,
)
async def record_reporter_release_receipt(
    body: ReleaseDeploymentReceiptCreate,
    db: DB,
    identity: ReporterReleaseIdentity,
):
    try:
        row = await record_release_deployment_receipt(
            db,
            request=body,
            identity=identity,
        )
        return release_deployment_receipt_out(row)
    except (
        ReleaseControlConflictError,
        ReleaseControlNotFoundError,
        ReleaseControlReferenceError,
        ReleaseControlStateError,
        ReleaseControlTenantMismatchError,
    ) as exc:
        raise _http_exception(exc) from exc
