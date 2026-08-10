from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from app.core.deps import CurrentUser, DB, require_namespace_member, require_namespace_writer
from app.models.deployment import AgentDeployment, AgentDeploymentStatus
from app.schemas.deployment import AgentDeploymentCreate, AgentDeploymentOut
from app.services.deployment_service import (
    DeploymentImmutableError,
    DeploymentNotFoundError,
    DeploymentReferenceError,
    DeploymentStateError,
    DeploymentTenantMismatchError,
    DuplicateDeploymentError,
    activate_deployment,
    get_deployment,
    list_deployments,
    register_deployment,
    retire_deployment,
)


router = APIRouter(prefix="/deployments", tags=["agent-deployments"])


def _deployment_http_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, DeploymentNotFoundError):
        return HTTPException(status_code=404, detail="Deployment not found")
    if isinstance(exc, (DuplicateDeploymentError, DeploymentStateError)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, DeploymentTenantMismatchError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (DeploymentReferenceError, DeploymentImmutableError)):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=422, detail="Deployment write rejected")


async def _visible_deployment(
    public_id: str,
    db: DB,
    current_user,
) -> AgentDeployment:
    try:
        deployment = await get_deployment(db, public_id)
        await require_namespace_member(current_user, deployment.namespace_id, db)
    except DeploymentNotFoundError as exc:
        raise _deployment_http_exception(exc) from exc
    except HTTPException as exc:
        if exc.status_code == 403:
            raise HTTPException(status_code=404, detail="Deployment not found") from exc
        raise
    return deployment


@router.post(
    "",
    response_model=AgentDeploymentOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_deployment(
    body: AgentDeploymentCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_writer(current_user, body.namespace_id, db)
    try:
        return await register_deployment(db, request=body, actor=current_user)
    except (
        DuplicateDeploymentError,
        DeploymentReferenceError,
        DeploymentTenantMismatchError,
    ) as exc:
        raise _deployment_http_exception(exc) from exc


@router.get("", response_model=list[AgentDeploymentOut])
async def list_agent_deployments(
    namespace_id: int,
    db: DB,
    current_user: CurrentUser,
    runtime_id: int | None = None,
    environment: str | None = None,
    deployment_status: AgentDeploymentStatus | None = None,
    limit: int = Query(default=100, ge=1, le=200),
):
    await require_namespace_member(current_user, namespace_id, db)
    return await list_deployments(
        db,
        namespace_id=namespace_id,
        runtime_id=runtime_id,
        environment=environment,
        status=deployment_status,
        limit=limit,
    )


@router.get("/{public_id}", response_model=AgentDeploymentOut)
async def get_agent_deployment(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    return await _visible_deployment(public_id, db, current_user)


@router.post("/{public_id}/activate", response_model=AgentDeploymentOut)
async def activate_agent_deployment(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    deployment = await _visible_deployment(public_id, db, current_user)
    await require_namespace_writer(current_user, deployment.namespace_id, db)
    try:
        return await activate_deployment(db, deployment.id, actor=current_user)
    except (
        DeploymentNotFoundError,
        DeploymentReferenceError,
        DeploymentStateError,
    ) as exc:
        raise _deployment_http_exception(exc) from exc


@router.post("/{public_id}/retire", response_model=AgentDeploymentOut)
async def retire_agent_deployment(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    deployment = await _visible_deployment(public_id, db, current_user)
    await require_namespace_writer(current_user, deployment.namespace_id, db)
    try:
        return await retire_deployment(db, deployment.id, actor=current_user)
    except (DeploymentNotFoundError, DeploymentStateError) as exc:
        raise _deployment_http_exception(exc) from exc
