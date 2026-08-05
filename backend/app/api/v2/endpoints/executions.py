from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Annotated, Any, TypeVar

from fastapi import (
    APIRouter,
    Body,
    Depends,
    Header,
    HTTPException,
    Query,
    Response,
    status,
)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ValidationError
from starlette.responses import JSONResponse

from app.core.config import settings
from app.core.deps import CurrentUser, DB, require_namespace_member
from app.models.execution import AgentRunStatus
from app.schemas.execution import (
    AgentRunListOut,
    AgentRunOut,
    ReporterSessionOut,
    RunCompleteEnvelope,
    RunStartEnvelope,
    SessionCompleteEnvelope,
    SessionStartEnvelope,
)
from app.services.execution_query_service import (
    AgentRunProjection,
    AgentSessionProjection,
    ExecutionQueryError,
    get_run_projection,
    get_session_projection,
    list_run_projections,
)
from app.services.execution_security_audit_service import (
    FORGED_GOVERNANCE_FIELDS,
    persist_execution_rejection_audit,
)
from app.services.execution_service import (
    EnvelopeConflictError,
    ExecutionError,
    ExecutionNotFoundError,
    ExecutionReferenceError,
    ExecutionStateError,
    ExecutionTenantMismatchError,
    complete_run,
    complete_session,
    start_run,
    start_session,
)
from app.services.reporter_identity_service import (
    ReporterExecutionIdentity,
    ReporterIdentityError,
    authenticate_reporter_execution_credential,
)


reporter_router = APIRouter(prefix="/reporter", tags=["reporter-execution"])
management_router = APIRouter(prefix="/agent-runs", tags=["agent-runs"])
reporter_bearer = HTTPBearer()
EnvelopeT = TypeVar("EnvelopeT", bound=BaseModel)
IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9._:-]+$"
MAX_EXECUTION_ENVELOPE_BYTES = 65_536
EXECUTION_START_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {
        "description": "Reporter Runtime is outside the rollout allowlist.",
    },
    503: {
        "description": (
            "New execution starts are disabled during rollout or backout."
        ),
        "headers": {
            "Retry-After": {
                "schema": {"type": "integer", "minimum": 1},
            }
        },
    },
}
IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=IDEMPOTENCY_KEY_PATTERN,
    ),
]


async def get_reporter_execution_identity(
    db: DB,
    credentials: HTTPAuthorizationCredentials = Depends(reporter_bearer),
) -> ReporterExecutionIdentity:
    try:
        return await authenticate_reporter_execution_credential(
            db,
            credentials.credentials,
        )
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            raise
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Reporter credential is not authorized for execution ingestion",
        ) from exc
    except ReporterIdentityError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Reporter credential is not authorized for execution ingestion",
        ) from exc


ReporterIdentity = Annotated[
    ReporterExecutionIdentity,
    Depends(get_reporter_execution_identity),
]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _optional_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return _utc(value)


def _session_out(projection: AgentSessionProjection) -> ReporterSessionOut:
    session = projection.session
    return ReporterSessionOut(
        session_public_id=session.public_id,
        external_session_id=session.external_session_id,
        namespace_id=str(session.namespace_id),
        runtime_instance_id=str(session.runtime_id),
        deployment_public_id=projection.deployment_public_id,
        work_trace_id=str(session.work_trace_id)
        if session.work_trace_id is not None
        else None,
        status=session.status,
        sensitivity=session.sensitivity.name,
        started_at=_utc(session.started_at),
        ended_at=_optional_utc(session.ended_at),
        run_count=session.run_count,
        error_count=session.error_count,
        content_capture_mode=session.content_capture_mode,
        metadata=session.metadata_json,
    )


def _run_out(projection: AgentRunProjection) -> AgentRunOut:
    run = projection.run
    return AgentRunOut(
        run_public_id=run.public_id,
        external_run_id=run.external_run_id,
        namespace_id=str(run.namespace_id),
        runtime_instance_id=str(run.runtime_id),
        session_public_id=projection.session_public_id,
        deployment_public_id=projection.deployment_public_id,
        work_trace_id=str(run.work_trace_id)
        if run.work_trace_id is not None
        else None,
        status=run.status,
        trust_level=run.trust_level,
        trust_source=run.trust_source,
        source_schema=run.source_schema,
        source_schema_version=run.source_schema_version,
        normalizer_version=run.normalizer_version,
        otel_trace_id=run.otel_trace_id,
        root_span_id=run.root_span_id,
        attempt=run.attempt,
        started_at=_utc(run.started_at),
        ended_at=_optional_utc(run.ended_at),
        duration_ms=run.duration_ms,
        step_count=run.step_count,
        model_call_count=run.model_call_count,
        tool_call_count=run.tool_call_count,
        input_token_count=run.input_token_count,
        output_token_count=run.output_token_count,
        error_type=run.error_type,
        content_capture_mode=run.content_capture_mode,
        metadata=run.metadata_json,
    )


def _require_execution_start_enabled(
    identity: ReporterExecutionIdentity,
) -> None:
    if not settings.AGENT_EXECUTION_INGESTION_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent execution ingestion is temporarily disabled",
            headers={"Retry-After": "30"},
        )
    runtime_allowlist = settings.AGENT_EXECUTION_RUNTIME_ALLOWLIST
    if (
        runtime_allowlist
        and identity.runtime_id not in runtime_allowlist
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Reporter runtime is not enabled for execution ingestion",
        )


def _validation_details(exc: ValidationError) -> list[dict[str, object]]:
    """Return useful Pydantic errors without echoing rejected request content."""

    return [
        {
            "loc": list(error["loc"]),
            "msg": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors(include_url=False)
    ]


async def _validate_envelope(
    db: DB,
    identity: ReporterExecutionIdentity,
    *,
    body: dict[str, Any],
    model: type[EnvelopeT],
    operation: str,
) -> EnvelopeT:
    envelope_size = len(
        json.dumps(
            body,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    if envelope_size > MAX_EXECUTION_ENVELOPE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Execution envelope exceeds 64 KiB",
        )
    rejected_fields = FORGED_GOVERNANCE_FIELDS.intersection(body)
    if rejected_fields:
        requested_trust_level = body.get("trust_level")
        await persist_execution_rejection_audit(
            db,
            identity=identity,
            action="agent_execution.identity_forgery_rejected",
            operation=operation,
            rejected_fields=rejected_fields,
            reason_code="client_governance_identity",
        )
        if isinstance(requested_trust_level, str):
            await persist_execution_rejection_audit(
                db,
                identity=identity,
                action="agent_run.trust_degraded",
                operation=operation,
                rejected_fields={"trust_level"},
                reason_code="unverified_client_trust_claim",
                requested_trust_level=requested_trust_level,
            )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Reporter envelopes cannot declare governance identity or trust",
        )
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_validation_details(exc),
        ) from exc


async def _execution_http_exception(
    db: DB,
    identity: ReporterExecutionIdentity,
    *,
    exc: ExecutionError,
    operation: str,
    resource_public_id: str | None = None,
) -> HTTPException:
    if isinstance(exc, ExecutionTenantMismatchError):
        action = "agent_execution.identity_forgery_rejected"
        reason_code = "cross_tenant_reference"
        response_status = status.HTTP_404_NOT_FOUND
        detail = "Execution resource not found"
    elif isinstance(exc, ExecutionNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Execution resource not found",
        )
    elif isinstance(exc, ExecutionReferenceError):
        action = "agent_execution.reference_rejected"
        reason_code = "invalid_reference"
        response_status = status.HTTP_404_NOT_FOUND
        detail = "Execution resource not found"
    elif isinstance(exc, ExecutionStateError):
        action = "agent_execution.invalid_transition"
        reason_code = "invalid_lifecycle_state"
        response_status = status.HTTP_409_CONFLICT
        detail = str(exc)
    elif isinstance(exc, EnvelopeConflictError):
        action = "agent_execution.idempotency_conflict"
        reason_code = "envelope_conflict"
        response_status = status.HTTP_409_CONFLICT
        detail = str(exc)
    else:
        action = "agent_execution.write_rejected"
        reason_code = "invalid_execution_write"
        response_status = status.HTTP_422_UNPROCESSABLE_CONTENT
        detail = "Execution write rejected"
    await persist_execution_rejection_audit(
        db,
        identity=identity,
        action=action,
        operation=operation,
        resource_public_id=resource_public_id,
        reason_code=reason_code,
    )
    return HTTPException(status_code=response_status, detail=detail)


@reporter_router.post(
    "/sessions",
    response_model=ReporterSessionOut,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
    responses=EXECUTION_START_RESPONSES,
)
async def start_reporter_session(
    response: Response,
    body: Annotated[dict[str, Any], Body()],
    idempotency_key: IdempotencyKey,
    db: DB,
    identity: ReporterIdentity,
):
    _require_execution_start_enabled(identity)
    envelope = await _validate_envelope(
        db,
        identity,
        body=body,
        model=SessionStartEnvelope,
        operation="session.start",
    )
    try:
        session = await start_session(
            db,
            namespace_id=identity.namespace_id,
            runtime_id=identity.runtime_id,
            idempotency_key=idempotency_key,
            envelope=envelope,
            actor_user_id=identity.actor_user_id,
        )
    except ExecutionError as exc:
        raise await _execution_http_exception(
            db,
            identity,
            exc=exc,
            operation="session.start",
        ) from exc
    response.headers["Location"] = f"/api/v2/reporter/sessions/{session.public_id}"
    return _session_out(await get_session_projection(db, session))


@reporter_router.post(
    "/sessions/{session_public_id}/complete",
    response_model=ReporterSessionOut,
    response_model_exclude_none=True,
)
async def complete_reporter_session(
    session_public_id: str,
    body: Annotated[dict[str, Any], Body()],
    idempotency_key: IdempotencyKey,
    db: DB,
    identity: ReporterIdentity,
):
    envelope = await _validate_envelope(
        db,
        identity,
        body=body,
        model=SessionCompleteEnvelope,
        operation="session.complete",
    )
    try:
        session = await complete_session(
            db,
            session_public_id,
            idempotency_key=idempotency_key,
            envelope=envelope,
            namespace_id=identity.namespace_id,
            runtime_id=identity.runtime_id,
        )
    except ExecutionError as exc:
        raise await _execution_http_exception(
            db,
            identity,
            exc=exc,
            operation="session.complete",
            resource_public_id=session_public_id,
        ) from exc
    return _session_out(await get_session_projection(db, session))


@reporter_router.post(
    "/runs",
    response_model=AgentRunOut,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
    responses=EXECUTION_START_RESPONSES,
)
async def start_reporter_run(
    response: Response,
    body: Annotated[dict[str, Any], Body()],
    idempotency_key: IdempotencyKey,
    db: DB,
    identity: ReporterIdentity,
):
    _require_execution_start_enabled(identity)
    envelope = await _validate_envelope(
        db,
        identity,
        body=body,
        model=RunStartEnvelope,
        operation="run.start",
    )
    try:
        run = await start_run(
            db,
            namespace_id=identity.namespace_id,
            runtime_id=identity.runtime_id,
            idempotency_key=idempotency_key,
            envelope=envelope,
            trust_level=identity.trust_level,
            trust_source=identity.trust_source,
            tenant_scope_validated=True,
        )
    except ExecutionError as exc:
        raise await _execution_http_exception(
            db,
            identity,
            exc=exc,
            operation="run.start",
        ) from exc
    response.headers["Location"] = f"/api/v2/agent-runs/{run.public_id}"
    projection: AgentRunProjection | None
    if run.session_id is None and run.deployment_id is None:
        projection = AgentRunProjection(
            run=run,
            session_public_id=None,
            deployment_public_id=None,
        )
    else:
        projection = await get_run_projection(
            db,
            public_id=run.public_id,
            namespace_id=identity.namespace_id,
        )
    if projection is None:
        raise HTTPException(
            status_code=500,
            detail="AgentRun projection failed",
        )
    return _run_out(projection)


@reporter_router.post(
    "/runs/{run_public_id}/complete",
    response_model=AgentRunOut,
    response_model_exclude_none=True,
)
async def complete_reporter_run(
    run_public_id: str,
    body: Annotated[dict[str, Any], Body()],
    idempotency_key: IdempotencyKey,
    db: DB,
    identity: ReporterIdentity,
):
    envelope = await _validate_envelope(
        db,
        identity,
        body=body,
        model=RunCompleteEnvelope,
        operation="run.complete",
    )
    try:
        run = await complete_run(
            db,
            run_public_id,
            idempotency_key=idempotency_key,
            envelope=envelope,
            namespace_id=identity.namespace_id,
            runtime_id=identity.runtime_id,
        )
    except ExecutionError as exc:
        raise await _execution_http_exception(
            db,
            identity,
            exc=exc,
            operation="run.complete",
            resource_public_id=run_public_id,
        ) from exc
    projection = await get_run_projection(
        db,
        public_id=run.public_id,
        namespace_id=identity.namespace_id,
    )
    if projection is None:
        raise HTTPException(status_code=500, detail="AgentRun projection failed")
    return _run_out(projection)


@management_router.get(
    "",
    response_model=AgentRunListOut,
    response_model_exclude_none=True,
)
async def list_agent_runs(
    namespace_id: Annotated[int, Query(ge=1)],
    started_after: datetime,
    started_before: datetime,
    db: DB,
    current_user: CurrentUser,
    runtime_instance_id: Annotated[int | None, Query(ge=1)] = None,
    deployment_public_id: Annotated[
        str | None,
        Query(min_length=1, max_length=36),
    ] = None,
    run_status: Annotated[AgentRunStatus | None, Query(alias="status")] = None,
    cursor: Annotated[str | None, Query(min_length=1, max_length=2000)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    await require_namespace_member(current_user, namespace_id, db)
    try:
        page = await list_run_projections(
            db,
            namespace_id=namespace_id,
            runtime_id=runtime_instance_id,
            deployment_public_id=deployment_public_id,
            status=run_status,
            started_after=started_after,
            started_before=started_before,
            cursor=cursor,
            limit=limit,
        )
    except ExecutionQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    payload = AgentRunListOut(
        items=[_run_out(item) for item in page.items],
        next_cursor=page.next_cursor,
    )
    return JSONResponse(
        content={
            "items": [
                item.model_dump(mode="json", exclude_none=True)
                for item in payload.items
            ],
            "next_cursor": payload.next_cursor,
        }
    )


@management_router.get(
    "/{run_public_id}",
    response_model=AgentRunOut,
    response_model_exclude_none=True,
)
async def get_agent_run(
    run_public_id: str,
    db: DB,
    current_user: CurrentUser,
):
    projection = await get_run_projection(db, public_id=run_public_id)
    if projection is None:
        raise HTTPException(status_code=404, detail="AgentRun not found")
    try:
        await require_namespace_member(
            current_user,
            projection.run.namespace_id,
            db,
        )
    except HTTPException as exc:
        if exc.status_code == 403:
            raise HTTPException(
                status_code=404,
                detail="AgentRun not found",
            ) from exc
        raise
    return _run_out(projection)
