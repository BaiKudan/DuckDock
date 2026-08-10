from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.core.deps import (
    CurrentUser,
    DB,
    require_namespace_member,
    require_namespace_writer,
)
from app.models.telemetry import AgentRunArtifact, TelemetrySink
from app.schemas.telemetry import (
    AgentRunArtifactCreate,
    AgentRunArtifactOut,
    TelemetrySinkCreate,
    TelemetrySinkOut,
    TelemetrySinkUpdate,
)
from app.services.telemetry_service import (
    TelemetryConflictError,
    TelemetryError,
    TelemetryNotFoundError,
    TelemetryReferenceError,
    TelemetryStateError,
    TelemetryTenantMismatchError,
    create_telemetry_sink,
    disable_telemetry_sink,
    get_telemetry_sink,
    list_agent_run_artifacts,
    list_telemetry_sinks,
    register_agent_run_artifact,
    update_telemetry_sink,
)


sink_router = APIRouter(prefix="/telemetry-sinks", tags=["telemetry-sinks"])
artifact_router = APIRouter(prefix="/agent-runs", tags=["agent-run-artifacts"])


def _telemetry_http_exception(exc: TelemetryError) -> HTTPException:
    if isinstance(exc, (TelemetryNotFoundError, TelemetryTenantMismatchError)):
        return HTTPException(status_code=404, detail="Telemetry resource not found")
    if isinstance(exc, (TelemetryConflictError, TelemetryStateError)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, TelemetryReferenceError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=422, detail="Telemetry write rejected")


def _artifact_out(
    artifact: AgentRunArtifact,
    *,
    run_public_id: str,
) -> AgentRunArtifactOut:
    return AgentRunArtifactOut(
        public_id=artifact.public_id,
        namespace_id=artifact.namespace_id,
        run_public_id=run_public_id,
        kind=artifact.kind,
        schema_name=artifact.schema_name,
        schema_version=artifact.schema_version,
        object_uri=artifact.object_uri,
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
        sensitivity=artifact.sensitivity,
        redaction_policy_version=artifact.redaction_policy_version,
        completeness=artifact.completeness,
        created_at=artifact.created_at,
    )


async def _visible_sink(
    public_id: str,
    db: DB,
    current_user,
) -> TelemetrySink:
    try:
        sink = await get_telemetry_sink(db, public_id=public_id)
        await require_namespace_member(current_user, sink.namespace_id, db)
    except TelemetryError as exc:
        raise _telemetry_http_exception(exc) from exc
    except HTTPException as exc:
        if exc.status_code == 403:
            raise HTTPException(
                status_code=404,
                detail="Telemetry resource not found",
            ) from exc
        raise
    return sink


@sink_router.post(
    "",
    response_model=TelemetrySinkOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_sink(
    body: TelemetrySinkCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_writer(current_user, body.namespace_id, db)
    try:
        return await create_telemetry_sink(
            db,
            request=body,
            actor=current_user,
        )
    except TelemetryError as exc:
        raise _telemetry_http_exception(exc) from exc


@sink_router.get("", response_model=list[TelemetrySinkOut])
async def list_sinks(
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    include_disabled: bool = True,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    return await list_telemetry_sinks(
        db,
        namespace_id=namespace_id,
        include_disabled=include_disabled,
        limit=limit,
    )


@sink_router.get("/{public_id}", response_model=TelemetrySinkOut)
async def get_sink(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    return await _visible_sink(public_id, db, current_user)


@sink_router.patch("/{public_id}", response_model=TelemetrySinkOut)
async def patch_sink(
    public_id: str,
    body: TelemetrySinkUpdate,
    db: DB,
    current_user: CurrentUser,
):
    sink = await _visible_sink(public_id, db, current_user)
    await require_namespace_writer(current_user, sink.namespace_id, db)
    try:
        return await update_telemetry_sink(
            db,
            sink=sink,
            request=body,
            actor=current_user,
        )
    except TelemetryError as exc:
        raise _telemetry_http_exception(exc) from exc


@sink_router.post("/{public_id}/disable", response_model=TelemetrySinkOut)
async def disable_sink(
    public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    sink = await _visible_sink(public_id, db, current_user)
    await require_namespace_writer(current_user, sink.namespace_id, db)
    return await disable_telemetry_sink(
        db,
        sink=sink,
        actor=current_user,
    )


@artifact_router.post(
    "/{run_public_id}/artifacts",
    response_model=AgentRunArtifactOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_run_artifact(
    run_public_id: str,
    namespace_id: Annotated[int, Query(ge=1)],
    body: AgentRunArtifactCreate,
    db: DB,
    current_user: CurrentUser,
):
    await require_namespace_writer(current_user, namespace_id, db)
    try:
        artifact = await register_agent_run_artifact(
            db,
            namespace_id=namespace_id,
            run_public_id=run_public_id,
            request=body,
            actor=current_user,
        )
    except TelemetryError as exc:
        raise _telemetry_http_exception(exc) from exc
    return _artifact_out(artifact, run_public_id=run_public_id)


@artifact_router.get(
    "/{run_public_id}/artifacts",
    response_model=list[AgentRunArtifactOut],
)
async def list_run_artifacts(
    run_public_id: str,
    namespace_id: Annotated[int, Query(ge=1)],
    db: DB,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    await require_namespace_member(current_user, namespace_id, db)
    try:
        artifacts = await list_agent_run_artifacts(
            db,
            namespace_id=namespace_id,
            run_public_id=run_public_id,
            limit=limit,
        )
    except TelemetryError as exc:
        raise _telemetry_http_exception(exc) from exc
    return [
        _artifact_out(artifact, run_public_id=run_public_id)
        for artifact in artifacts
    ]
