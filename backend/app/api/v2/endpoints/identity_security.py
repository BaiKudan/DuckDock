from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, status
from sqlalchemy import select

from app.core.deps import (
    CurrentUser,
    DB,
    SsoManagerUser,
    require_namespace_admin,
    require_namespace_member,
)
from app.models.control_plane import WorkloadIdentityKind
from app.models.iam import DirectoryLifecycleEvent
from app.schemas.identity_security import (
    DirectoryCredentialCreate,
    DirectoryCredentialCreatedOut,
    DirectoryCredentialOut,
    DirectoryCredentialRevoke,
    DirectoryLifecycleEventOut,
    ScimPatchRequest,
    ScimUserLifecycleOut,
    WorkloadIdentityCreate,
    WorkloadIdentityCreatedOut,
    WorkloadIdentityOut,
    WorkloadIdentityRevoke,
    WorkloadIdentityRotate,
)
from app.services.identity_security_service import (
    IdentitySecurityConflictError,
    IdentitySecurityCredentialError,
    IdentitySecurityError,
    IdentitySecurityNotFoundError,
    IdentitySecurityStateError,
    apply_scim_user_lifecycle,
    authenticate_directory_credential,
    create_directory_credential,
    create_workload_identity,
    get_workload_identity,
    list_directory_credentials,
    list_workload_identities,
    revoke_directory_credential,
    revoke_workload_identity,
    rotate_workload_identity,
)


directory_router = APIRouter(prefix="/identity", tags=["identity-security"])
scim_router = APIRouter(prefix="/scim/v2", tags=["scim-2"])
workload_router = APIRouter(
    prefix="/identity/workload-identities", tags=["identity-security"]
)


def _http_exception(exc: IdentitySecurityError) -> HTTPException:
    if isinstance(exc, IdentitySecurityCredentialError):
        return HTTPException(
            status_code=401,
            detail="Invalid or expired directory credential",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if isinstance(exc, IdentitySecurityNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, IdentitySecurityConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, IdentitySecurityStateError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=422, detail="identity operation rejected")


@directory_router.post(
    "/directory-credentials",
    response_model=DirectoryCredentialCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def issue_directory_credential(
    body: DirectoryCredentialCreate,
    db: DB,
    current_user: SsoManagerUser,
):
    try:
        row, token = await create_directory_credential(
            db, request=body, actor=current_user
        )
        await db.commit()
        return DirectoryCredentialCreatedOut(
            **DirectoryCredentialOut.model_validate(row).model_dump(), token=token
        )
    except IdentitySecurityError as exc:
        raise _http_exception(exc) from exc


@directory_router.get(
    "/directory-credentials", response_model=list[DirectoryCredentialOut]
)
async def get_directory_credentials(
    db: DB,
    current_user: SsoManagerUser,
    provider_id: int | None = Query(default=None, ge=1),
):
    return await list_directory_credentials(db, provider_id=provider_id)


@directory_router.post(
    "/directory-credentials/{public_id}/revoke",
    response_model=DirectoryCredentialOut,
)
async def disable_directory_credential(
    public_id: str,
    body: DirectoryCredentialRevoke,
    db: DB,
    current_user: SsoManagerUser,
):
    try:
        return await revoke_directory_credential(
            db,
            public_id=public_id,
            reason=body.reason,
            actor=current_user,
        )
    except IdentitySecurityError as exc:
        raise _http_exception(exc) from exc


@directory_router.get(
    "/directory-events", response_model=list[DirectoryLifecycleEventOut]
)
async def get_directory_events(
    db: DB,
    current_user: SsoManagerUser,
    provider_id: int | None = Query(default=None, ge=1),
    user_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=100, ge=1, le=500),
):
    stmt = select(DirectoryLifecycleEvent).order_by(
        DirectoryLifecycleEvent.occurred_at.desc(),
        DirectoryLifecycleEvent.id.desc(),
    )
    if provider_id is not None:
        stmt = stmt.where(DirectoryLifecycleEvent.provider_id == provider_id)
    if user_id is not None:
        stmt = stmt.where(DirectoryLifecycleEvent.user_id == user_id)
    return list((await db.scalars(stmt.limit(limit))).all())


def _scim_active(body: ScimPatchRequest) -> bool:
    values: list[bool] = []
    for operation in body.operations:
        path = (operation.path or "").strip().lower()
        if path == "active" and isinstance(operation.value, bool):
            values.append(operation.value)
        elif not path and isinstance(operation.value, dict):
            value = operation.value.get("active")
            if isinstance(value, bool):
                values.append(value)
    if not values or len(set(values)) != 1:
        raise IdentitySecurityStateError(
            "SCIM PATCH must contain one unambiguous active replacement"
        )
    return values[0]


@scim_router.patch("/Users/{external_subject}", response_model=ScimUserLifecycleOut)
async def patch_scim_user(
    external_subject: str,
    body: ScimPatchRequest,
    db: DB,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    external_event_id: Annotated[
        str | None, Header(alias="X-DuckDock-Event-Id")
    ] = None,
):
    try:
        if authorization is None or not authorization.startswith("Bearer "):
            raise IdentitySecurityCredentialError("directory credential is required")
        credential = await authenticate_directory_credential(
            db, authorization.removeprefix("Bearer ").strip()
        )
        active = _scim_active(body)
        event = await apply_scim_user_lifecycle(
            db,
            credential=credential,
            external_subject=external_subject,
            external_event_id=external_event_id or "",
            active=active,
            payload=body.model_dump(mode="json", by_alias=True),
        )
        return ScimUserLifecycleOut(
            id=external_subject,
            active=active,
            meta={
                "resourceType": "User",
                "version": event.outcome_digest,
            },
            duckdock_event=DirectoryLifecycleEventOut.model_validate(event),
        )
    except IdentitySecurityError as exc:
        raise _http_exception(exc) from exc


@workload_router.post(
    "",
    response_model=WorkloadIdentityCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def issue_workload_identity(
    body: WorkloadIdentityCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_admin(current_user, body.namespace_id, db)
    try:
        row, token = await create_workload_identity(
            db, request=body, actor=current_user
        )
        await db.commit()
        return WorkloadIdentityCreatedOut(
            **WorkloadIdentityOut.model_validate(row).model_dump(), token=token
        )
    except IdentitySecurityError as exc:
        raise _http_exception(exc) from exc


@workload_router.get("", response_model=list[WorkloadIdentityOut])
async def get_workload_identities(
    namespace_id: int,
    db: DB,
    current_user: CurrentUser,
    runtime_id: int | None = Query(default=None, ge=1),
    principal_kind: WorkloadIdentityKind | None = None,
):
    await require_namespace_member(current_user, namespace_id, db)
    return await list_workload_identities(
        db,
        namespace_id=namespace_id,
        runtime_id=runtime_id,
        principal_kind=principal_kind,
    )


@workload_router.post(
    "/{public_id}/rotate", response_model=WorkloadIdentityCreatedOut
)
async def rotate_identity(
    public_id: str,
    body: WorkloadIdentityRotate,
    db: DB,
    current_user: CurrentUser,
):
    try:
        current, runtime = await get_workload_identity(db, public_id=public_id)
        await require_namespace_admin(current_user, runtime.namespace_id, db)
        successor, token, _ = await rotate_workload_identity(
            db,
            public_id=current.public_id,
            reason=body.reason,
            expires_at=body.expires_at,
            actor=current_user,
        )
        await db.commit()
        return WorkloadIdentityCreatedOut(
            **WorkloadIdentityOut.model_validate(successor).model_dump(), token=token
        )
    except IdentitySecurityError as exc:
        raise _http_exception(exc) from exc


@workload_router.post("/{public_id}/revoke", response_model=WorkloadIdentityOut)
async def revoke_identity(
    public_id: str,
    body: WorkloadIdentityRevoke,
    db: DB,
    current_user: CurrentUser,
):
    try:
        current, runtime = await get_workload_identity(db, public_id=public_id)
        await require_namespace_admin(current_user, runtime.namespace_id, db)
        row, _ = await revoke_workload_identity(
            db,
            public_id=current.public_id,
            reason=body.reason,
            actor=current_user,
        )
        return row
    except IdentitySecurityError as exc:
        raise _http_exception(exc) from exc
