from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.deps import AdminUser, DB
from app.schemas.outbox import (
    OutboxEventOut,
    OutboxHealthOut,
    OutboxRetryRequest,
)
from app.services.outbox_dispatcher_service import (
    OutboxNotFoundError,
    OutboxStateError,
    get_outbox_health,
    retry_failed_outbox_event,
)


router = APIRouter(prefix="/outbox", tags=["outbox-operations"])


@router.get("/health", response_model=OutboxHealthOut)
async def outbox_health(
    db: DB,
    current_user: AdminUser,
):
    del current_user
    return await get_outbox_health(db)


@router.post("/{event_id}/retry", response_model=OutboxEventOut)
async def retry_outbox_event(
    event_id: str,
    body: OutboxRetryRequest,
    db: DB,
    current_user: AdminUser,
):
    try:
        return await retry_failed_outbox_event(
            db,
            event_id=event_id,
            actor=current_user,
            reason=body.reason,
        )
    except OutboxNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Outbox event not found") from exc
    except OutboxStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
