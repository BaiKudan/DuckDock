from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.contracts.openapi_v2 import openapi_v2_contract_digest
from app.core.deps import AdminUser, DB
from app.schemas.ga_readiness import GAReadinessOut
from app.schemas.operations import (
    OpsIncidentAction,
    OpsIncidentOut,
    OpsOverviewOut,
    OpsRecoveryDrillCreate,
    OpsRecoveryDrillOut,
    OpsSLOEvaluationCreate,
    OpsSLOEvaluationOut,
)
from app.services.operations_service import (
    OperationsConflictError,
    OperationsNotFoundError,
    OperationsStateError,
    acknowledge_ops_incident,
    evaluate_operations_slo,
    get_operations_overview,
    list_ops_incidents,
    list_recovery_drills,
    list_slo_evaluations,
    record_recovery_drill,
    resolve_ops_incident,
)
from app.services.ga_readiness_service import get_ga_readiness


router = APIRouter(prefix="/operations", tags=["operations"])


def _http_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, OperationsNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (OperationsConflictError, OperationsStateError)):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/ga-readiness", response_model=GAReadinessOut)
async def ga_readiness(request: Request, db: DB, current_user: AdminUser):
    del current_user
    return await get_ga_readiness(
        db,
        openapi_contract_digest=openapi_v2_contract_digest(request.app),
    )


@router.get("/overview", response_model=OpsOverviewOut)
async def operations_overview(db: DB, current_user: AdminUser):
    del current_user
    return await get_operations_overview(db)


@router.post(
    "/slo-evaluations",
    response_model=OpsSLOEvaluationOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_slo_evaluation(
    body: OpsSLOEvaluationCreate, db: DB, current_user: AdminUser
):
    try:
        row = await evaluate_operations_slo(
            db,
            idempotency_key=body.idempotency_key,
            window_minutes=body.window_minutes,
            actor=current_user,
        )
        await db.commit()
        await db.refresh(row)
        return row
    except OperationsConflictError as exc:
        raise _http_exception(exc) from exc


@router.get("/slo-evaluations", response_model=list[OpsSLOEvaluationOut])
async def get_slo_evaluations(
    db: DB,
    current_user: AdminUser,
    limit: int = Query(default=50, ge=1, le=200),
):
    del current_user
    return await list_slo_evaluations(db, limit=limit)


@router.get("/incidents", response_model=list[OpsIncidentOut])
async def get_operations_incidents(
    db: DB,
    current_user: AdminUser,
    limit: int = Query(default=100, ge=1, le=200),
):
    del current_user
    return await list_ops_incidents(db, limit=limit)


@router.post("/incidents/{public_id}/acknowledge", response_model=OpsIncidentOut)
async def acknowledge_operations_incident(
    public_id: str, body: OpsIncidentAction, db: DB, current_user: AdminUser
):
    try:
        row = await acknowledge_ops_incident(
            db, public_id=public_id, actor=current_user, note=body.note
        )
        await db.commit()
        await db.refresh(row)
        return row
    except (OperationsNotFoundError, OperationsStateError) as exc:
        raise _http_exception(exc) from exc


@router.post("/incidents/{public_id}/resolve", response_model=OpsIncidentOut)
async def resolve_operations_incident(
    public_id: str, body: OpsIncidentAction, db: DB, current_user: AdminUser
):
    try:
        row = await resolve_ops_incident(
            db, public_id=public_id, actor=current_user, note=body.note
        )
        await db.commit()
        await db.refresh(row)
        return row
    except (OperationsNotFoundError, OperationsStateError) as exc:
        raise _http_exception(exc) from exc


@router.post(
    "/recovery-drills",
    response_model=OpsRecoveryDrillOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_recovery_drill(
    body: OpsRecoveryDrillCreate, db: DB, current_user: AdminUser
):
    try:
        row = await record_recovery_drill(db, request=body, actor=current_user)
        await db.commit()
        await db.refresh(row)
        return row
    except OperationsConflictError as exc:
        raise _http_exception(exc) from exc


@router.get("/recovery-drills", response_model=list[OpsRecoveryDrillOut])
async def get_recovery_drills(
    db: DB,
    current_user: AdminUser,
    limit: int = Query(default=50, ge=1, le=200),
):
    del current_user
    return await list_recovery_drills(db, limit=limit)
