from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse

from app.api.v2.endpoints.executions import (
    get_reporter_execution_identity,
)
from app.core.deps import CurrentUser, DB, require_namespace_member
from app.schemas.fleet import (
    AdapterDescriptor,
    AdapterHandshakeOut,
    AdapterHeartbeat,
    AdapterHeartbeatOut,
    FleetSummaryOut,
)
from app.services.fleet_service import (
    FleetNotFoundError,
    get_fleet_summary,
    negotiate_adapter_handshake,
    record_adapter_heartbeat,
)
from app.services.reporter_identity_service import ReporterExecutionIdentity


reporter_router = APIRouter(prefix="/reporter", tags=["adapter-fleet"])
management_router = APIRouter(prefix="/fleet", tags=["adapter-fleet"])
ReporterIdentity = Annotated[
    ReporterExecutionIdentity,
    Depends(get_reporter_execution_identity),
]


@reporter_router.post(
    "/handshakes",
    operation_id="negotiateAdapterHandshake",
    response_model=AdapterHandshakeOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {
            "description": (
                "Nonce was reused with a different validated descriptor."
            )
        }
    },
)
async def create_adapter_handshake(
    body: AdapterDescriptor,
    db: DB,
    identity: ReporterIdentity,
):
    try:
        result = await negotiate_adapter_handshake(
            db,
            identity=identity,
            descriptor=body,
        )
    except FleetNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Adapter Runtime not found",
        ) from exc
    if result.conflict:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            media_type="application/problem+json",
            content={
                "type": "about:blank",
                "title": "Idempotency conflict",
                "status": 409,
                "code": "IDEMPOTENCY_CONFLICT",
                "detail": (
                    "The handshake nonce is bound to another descriptor."
                ),
                "retryable": False,
                "details": {"operation": "adapter_handshake"},
            },
        )
    return result.response


@reporter_router.post(
    "/heartbeats",
    operation_id="recordAdapterHeartbeat",
    response_model=AdapterHeartbeatOut,
)
async def create_adapter_heartbeat(
    body: AdapterHeartbeat,
    db: DB,
    identity: ReporterIdentity,
):
    try:
        return await record_adapter_heartbeat(
            db,
            identity=identity,
            heartbeat=body,
        )
    except FleetNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Adapter handshake not found",
        ) from exc


@management_router.get(
    "/runtimes",
    operation_id="getFleetRuntimeSummary",
    response_model=FleetSummaryOut,
)
async def list_fleet_runtimes(
    namespace_id: Annotated[int, Query(gt=0)],
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_member(current_user, namespace_id, db)
    return await get_fleet_summary(db, namespace_id=namespace_id)
