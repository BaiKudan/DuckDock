from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.deps import AdminUser, DB
from app.schemas.component import ComponentActionResult, ComponentOut
from app.services.audit_service import audit
from app.services.component_service import ComponentCommandError, component_service

router = APIRouter(prefix="/components", tags=["components"])


@router.get("", response_model=list[ComponentOut])
async def list_components(current_user: AdminUser):
    return [component_service.get_langfuse_component()]


@router.get("/{component_key}", response_model=ComponentOut)
async def get_component(component_key: str, current_user: AdminUser):
    if component_key != "langfuse":
        raise HTTPException(status_code=404, detail="Component not found")
    return component_service.get_langfuse_component()


@router.post("/{component_key}/start", response_model=ComponentActionResult)
async def start_component(component_key: str, db: DB, current_user: AdminUser):
    if component_key != "langfuse":
        raise HTTPException(status_code=404, detail="Component not found")
    try:
        result = component_service.start_langfuse()
    except ComponentCommandError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await audit(
        db,
        user=current_user,
        action="component.started",
        resource_type="component",
        resource_id=None,
        details={"component": component_key, "output": result.output},
    )
    return result


@router.post("/{component_key}/stop", response_model=ComponentActionResult)
async def stop_component(component_key: str, db: DB, current_user: AdminUser):
    if component_key != "langfuse":
        raise HTTPException(status_code=404, detail="Component not found")
    try:
        result = component_service.stop_langfuse()
    except ComponentCommandError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await audit(
        db,
        user=current_user,
        action="component.stopped",
        resource_type="component",
        resource_id=None,
        details={"component": component_key, "output": result.output},
    )
    return result
