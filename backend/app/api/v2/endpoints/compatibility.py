from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path

from app.core.deps import AdminUser, DB
from app.schemas.compatibility import (
    ReconciliationHealthOut,
    V1V2ReconciliationOut,
)
from app.services.compatibility_reconciliation_service import (
    CompatibilityWorkTraceNotFoundError,
    get_reconciliation_health,
    get_work_trace_reconciliation,
    reconcile_work_trace_now,
)


router = APIRouter(
    prefix="/reconciliation",
    tags=["compatibility-reconciliation"],
)


@router.get(
    "/health",
    response_model=ReconciliationHealthOut,
)
async def reconciliation_health(
    db: DB,
    current_user: AdminUser,
):
    del current_user
    return await get_reconciliation_health(db)


@router.get(
    "/work-traces/{work_trace_id}",
    response_model=V1V2ReconciliationOut,
)
async def get_work_trace_reconciliation_endpoint(
    db: DB,
    current_user: AdminUser,
    work_trace_id: int = Path(ge=1),
):
    del current_user
    try:
        return await get_work_trace_reconciliation(
            db,
            work_trace_id=work_trace_id,
        )
    except CompatibilityWorkTraceNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="WorkTrace reconciliation not found",
        ) from exc


@router.post(
    "/work-traces/{work_trace_id}",
    response_model=V1V2ReconciliationOut,
)
async def reconcile_work_trace_endpoint(
    db: DB,
    current_user: AdminUser,
    work_trace_id: int = Path(ge=1),
):
    del current_user
    try:
        return await reconcile_work_trace_now(
            db,
            work_trace_id=work_trace_id,
        )
    except CompatibilityWorkTraceNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="Structured Report reconciliation source not found",
        ) from exc
