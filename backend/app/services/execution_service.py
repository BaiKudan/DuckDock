from __future__ import annotations

import uuid
from datetime import timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.control_plane import RuntimeInstance, Sensitivity, WorkTrace
from app.models.deployment import AgentDeployment, AgentDeploymentStatus
from app.models.execution import (
    AgentRun,
    AgentRunStatus,
    AgentSession,
    AgentSessionStatus,
    TrustLevel,
    TrustSource,
)
from app.schemas.execution import (
    RunCompleteEnvelope,
    RunStartEnvelope,
    SessionCompleteEnvelope,
    SessionStartEnvelope,
)
from app.services.audit_service import audit
from app.services.envelope_canonicalization_service import (
    CANONICALIZER_VERSION,
    canonical_sha256,
)
from app.services.outbox_event_service import (
    build_agent_run_completed,
    build_agent_run_registered,
    build_agent_session_completed,
    build_agent_session_started,
    enqueue_domain_event,
)
from app.services.tenant_write_service import require_active_namespace


class ExecutionError(ValueError):
    pass


class ExecutionNotFoundError(ExecutionError):
    pass


class ExecutionReferenceError(ExecutionError):
    pass


class ExecutionTenantMismatchError(ExecutionReferenceError):
    pass


class ExecutionStateError(ExecutionError):
    pass


class EnvelopeConflictError(ExecutionError):
    pass


def _session_public_id() -> str:
    return f"ses_{uuid.uuid4().hex}"


def _run_public_id() -> str:
    return f"run_{uuid.uuid4().hex}"


def _is_unique_violation(exc: IntegrityError) -> bool:
    args = getattr(exc.orig, "args", ())
    if args and args[0] == 1062:
        return True
    return "unique constraint failed" in str(exc.orig).lower()


def _uses_mysql(db: AsyncSession) -> bool:
    return db.get_bind().dialect.name == "mysql"


async def _mysql_insert_or_get_run(
    db: AsyncSession,
    candidate: AgentRun,
) -> tuple[AgentRun, bool]:
    """Atomically claim a Run identity without a shared Runtime row lock."""

    statement = mysql_insert(AgentRun).values(
        public_id=candidate.public_id,
        namespace_id=candidate.namespace_id,
        session_id=candidate.session_id,
        runtime_id=candidate.runtime_id,
        deployment_id=candidate.deployment_id,
        work_trace_id=candidate.work_trace_id,
        external_run_id=candidate.external_run_id,
        otel_trace_id=candidate.otel_trace_id,
        root_span_id=candidate.root_span_id,
        attempt=candidate.attempt,
        status=candidate.status,
        trust_level=candidate.trust_level,
        trust_source=candidate.trust_source,
        source_schema=candidate.source_schema,
        source_schema_version=candidate.source_schema_version,
        normalizer_version=candidate.normalizer_version,
        content_capture_mode=candidate.content_capture_mode,
        started_at=candidate.started_at,
        metadata_json=candidate.metadata_json,
        start_idempotency_key=candidate.start_idempotency_key,
        start_envelope_sha256=candidate.start_envelope_sha256,
    )
    statement = statement.on_duplicate_key_update(
        id=func.last_insert_id(AgentRun.id)
    )
    result = await db.execute(statement)
    row_id = int(getattr(result, "lastrowid", 0) or 0)
    if row_id <= 0:
        row_id = int(
            await db.scalar(select(func.last_insert_id())) or 0
        )
    stored = (
        await db.execute(
            select(AgentRun)
            .where(AgentRun.id == row_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if stored is None:
        raise ExecutionStateError(
            "run identity claim did not return a stored row"
        )
    return stored, stored.public_id == candidate.public_id


async def _require_runtime(
    db: AsyncSession,
    *,
    namespace_id: int,
    runtime_id: int,
) -> RuntimeInstance:
    await require_active_namespace(db, namespace_id)
    runtime = (
        await db.execute(
            select(RuntimeInstance).where(
                RuntimeInstance.id == runtime_id
            )
        )
    ).scalar_one_or_none()
    if runtime is None:
        raise ExecutionReferenceError("runtime was not found")
    if runtime.namespace_id != namespace_id:
        raise ExecutionTenantMismatchError(
            "runtime must belong to the requested Namespace"
        )
    return runtime


async def _deployment(
    db: AsyncSession,
    *,
    public_id: str | None,
    namespace_id: int,
    runtime_id: int,
) -> AgentDeployment | None:
    if public_id is None:
        return None
    deployment = (
        await db.execute(
            select(AgentDeployment)
            .where(AgentDeployment.public_id == public_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if deployment is None:
        raise ExecutionReferenceError("deployment was not found")
    if deployment.namespace_id != namespace_id:
        raise ExecutionTenantMismatchError(
            "deployment must belong to the requested Namespace"
        )
    if deployment.runtime_id != runtime_id:
        raise ExecutionTenantMismatchError(
            "deployment must belong to the requested runtime"
        )
    if deployment.status != AgentDeploymentStatus.ACTIVE:
        raise ExecutionStateError("deployment must be ACTIVE")
    return deployment


async def _work_trace(
    db: AsyncSession,
    *,
    work_trace_id: int | None,
    namespace_id: int,
    runtime_id: int,
) -> WorkTrace | None:
    if work_trace_id is None:
        return None
    work_trace = await db.get(WorkTrace, work_trace_id)
    if work_trace is None:
        raise ExecutionReferenceError("WorkTrace was not found")
    if work_trace.namespace_id != namespace_id:
        raise ExecutionTenantMismatchError(
            "WorkTrace must belong to the requested Namespace"
        )
    if work_trace.runtime_id is not None and work_trace.runtime_id != runtime_id:
        raise ExecutionTenantMismatchError(
            "WorkTrace must belong to the requested runtime"
        )
    return work_trace


async def start_session(
    db: AsyncSession,
    *,
    namespace_id: int,
    runtime_id: int,
    idempotency_key: str,
    envelope: SessionStartEnvelope,
    actor_user_id: int | None = None,
) -> AgentSession:
    await _require_runtime(
        db,
        namespace_id=namespace_id,
        runtime_id=runtime_id,
    )
    envelope_hash = canonical_sha256(envelope)
    existing = (
        await db.execute(
            select(AgentSession)
            .where(
                AgentSession.namespace_id == namespace_id,
                AgentSession.runtime_id == runtime_id,
                AgentSession.start_idempotency_key == idempotency_key,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.start_envelope_sha256 == envelope_hash:
            return existing
        raise EnvelopeConflictError(
            "session idempotency key was already used with a different envelope"
        )

    external_existing = (
        await db.execute(
            select(AgentSession)
            .where(
                AgentSession.namespace_id == namespace_id,
                AgentSession.runtime_id == runtime_id,
                AgentSession.external_session_id == envelope.external_session_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if external_existing is not None:
        raise EnvelopeConflictError("external_session_id is already registered")

    deployment = await _deployment(
        db,
        public_id=envelope.deployment_public_id,
        namespace_id=namespace_id,
        runtime_id=runtime_id,
    )
    work_trace = await _work_trace(
        db,
        work_trace_id=envelope.work_trace_id,
        namespace_id=namespace_id,
        runtime_id=runtime_id,
    )
    session = AgentSession(
        public_id=_session_public_id(),
        namespace_id=namespace_id,
        runtime_id=runtime_id,
        deployment_id=deployment.id if deployment is not None else None,
        work_trace_id=work_trace.id if work_trace is not None else None,
        external_session_id=envelope.external_session_id,
        actor_user_id=actor_user_id,
        status=AgentSessionStatus.OPEN,
        content_capture_mode=envelope.content_capture_mode,
        sensitivity=Sensitivity[envelope.sensitivity],
        started_at=envelope.started_at.astimezone(timezone.utc),
        metadata_json=envelope.metadata,
        start_idempotency_key=idempotency_key,
        start_envelope_sha256=envelope_hash,
    )
    try:
        db.add(session)
        await db.flush()
    except IntegrityError as exc:
        if _is_unique_violation(exc):
            raise EnvelopeConflictError(
                "session identity or idempotency key is already registered"
            ) from exc
        raise
    await audit(
        db,
        username="system:execution",
        action="agent_session.started",
        resource_type="agent_session",
        resource_id=session.id,
        namespace_id=namespace_id,
        details={
            "public_id": session.public_id,
            "runtime_id": runtime_id,
            "content_capture_mode": session.content_capture_mode.value,
        },
    )
    await enqueue_domain_event(db, build_agent_session_started(session))
    await db.flush()
    return session


async def complete_session(
    db: AsyncSession,
    public_id: str,
    *,
    idempotency_key: str,
    envelope: SessionCompleteEnvelope,
    namespace_id: int | None = None,
    runtime_id: int | None = None,
) -> AgentSession:
    predicates = [AgentSession.public_id == public_id]
    if namespace_id is not None:
        predicates.append(AgentSession.namespace_id == namespace_id)
    if runtime_id is not None:
        predicates.append(AgentSession.runtime_id == runtime_id)
    session = (
        await db.execute(
            select(AgentSession)
            .where(*predicates)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if session is None:
        raise ExecutionNotFoundError("session was not found")
    envelope_hash = canonical_sha256(envelope)
    if session.status != AgentSessionStatus.OPEN:
        if (
            session.completion_idempotency_key == idempotency_key
            and session.completion_envelope_sha256 == envelope_hash
        ):
            return session
        raise EnvelopeConflictError("session is already terminal")
    ended_at = envelope.ended_at.astimezone(timezone.utc)
    started_at = session.started_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    if ended_at < started_at:
        raise ExecutionStateError("session ended_at is before started_at")

    run_count = int(
        await db.scalar(
            select(func.count(AgentRun.id)).where(AgentRun.session_id == session.id)
        )
        or 0
    )
    error_count = int(
        await db.scalar(
            select(func.count(AgentRun.id)).where(
                AgentRun.session_id == session.id,
                AgentRun.status.in_(
                    (
                        AgentRunStatus.FAILED,
                        AgentRunStatus.CANCELLED,
                        AgentRunStatus.TIMED_OUT,
                    )
                ),
            )
        )
        or 0
    )
    if envelope.run_count is not None and envelope.run_count != run_count:
        raise ExecutionStateError("session run_count does not match stored runs")
    if envelope.error_count is not None and envelope.error_count != error_count:
        raise ExecutionStateError(
            "session error_count does not match stored terminal runs"
        )
    session.status = envelope.status
    session.ended_at = ended_at
    session.run_count = run_count
    session.error_count = error_count
    session.completion_idempotency_key = idempotency_key
    session.completion_envelope_sha256 = envelope_hash
    await audit(
        db,
        username="system:execution",
        action="agent_session.completed",
        resource_type="agent_session",
        resource_id=session.id,
        namespace_id=session.namespace_id,
        details={
            "public_id": session.public_id,
            "status": session.status.value,
            "run_count": run_count,
            "error_count": error_count,
        },
    )
    await enqueue_domain_event(db, build_agent_session_completed(session))
    await db.flush()
    return session


async def _run_session(
    db: AsyncSession,
    *,
    public_id: str | None,
    namespace_id: int,
    runtime_id: int,
) -> AgentSession | None:
    if public_id is None:
        return None
    session = (
        await db.execute(
            select(AgentSession)
            .where(AgentSession.public_id == public_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if session is None:
        raise ExecutionReferenceError("session was not found")
    if session.namespace_id != namespace_id:
        raise ExecutionTenantMismatchError(
            "session must belong to the requested Namespace"
        )
    if session.runtime_id != runtime_id:
        raise ExecutionTenantMismatchError(
            "session must belong to the requested runtime"
        )
    if session.status != AgentSessionStatus.OPEN:
        raise ExecutionStateError("session must be OPEN")
    return session


async def start_run(
    db: AsyncSession,
    *,
    namespace_id: int,
    runtime_id: int,
    idempotency_key: str,
    envelope: RunStartEnvelope,
    trust_level: TrustLevel,
    trust_source: TrustSource,
    normalizer_version: str = CANONICALIZER_VERSION,
    tenant_scope_validated: bool = False,
) -> AgentRun:
    if not tenant_scope_validated:
        await _require_runtime(
            db,
            namespace_id=namespace_id,
            runtime_id=runtime_id,
        )
    envelope_hash = canonical_sha256(envelope)
    use_mysql_identity_claim = _uses_mysql(db)
    if not use_mysql_identity_claim:
        existing = (
            await db.execute(
                select(AgentRun)
                .where(
                    AgentRun.namespace_id == namespace_id,
                    AgentRun.runtime_id == runtime_id,
                    AgentRun.start_idempotency_key
                    == idempotency_key,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.start_envelope_sha256 == envelope_hash:
                return existing
            raise EnvelopeConflictError(
                "run idempotency key was already used with a different envelope"
            )

        external_existing = (
            await db.execute(
                select(AgentRun)
                .where(
                    AgentRun.namespace_id == namespace_id,
                    AgentRun.runtime_id == runtime_id,
                    AgentRun.external_run_id
                    == envelope.external_run_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if external_existing is not None:
            raise EnvelopeConflictError(
                "external_run_id is already registered"
            )
        if envelope.otel_trace_id is not None:
            trace_existing = (
                await db.execute(
                    select(AgentRun)
                    .where(
                        AgentRun.namespace_id == namespace_id,
                        AgentRun.otel_trace_id
                        == envelope.otel_trace_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if trace_existing is not None:
                raise EnvelopeConflictError(
                    "otel_trace_id is already registered"
                )

    session = await _run_session(
        db,
        public_id=envelope.session_public_id,
        namespace_id=namespace_id,
        runtime_id=runtime_id,
    )
    deployment = await _deployment(
        db,
        public_id=envelope.deployment_public_id,
        namespace_id=namespace_id,
        runtime_id=runtime_id,
    )
    if (
        session is not None
        and session.deployment_id is not None
        and deployment is not None
        and session.deployment_id != deployment.id
    ):
        raise ExecutionReferenceError(
            "run deployment must match the Session deployment"
        )
    if deployment is None and session is not None and session.deployment_id is not None:
        deployment = await db.get(AgentDeployment, session.deployment_id)
    work_trace = await _work_trace(
        db,
        work_trace_id=envelope.work_trace_id,
        namespace_id=namespace_id,
        runtime_id=runtime_id,
    )
    run = AgentRun(
        public_id=_run_public_id(),
        namespace_id=namespace_id,
        session_id=session.id if session is not None else None,
        runtime_id=runtime_id,
        deployment_id=deployment.id if deployment is not None else None,
        work_trace_id=work_trace.id if work_trace is not None else None,
        external_run_id=envelope.external_run_id,
        otel_trace_id=envelope.otel_trace_id,
        root_span_id=envelope.root_span_id,
        attempt=envelope.attempt,
        status=AgentRunStatus.STARTED,
        trust_level=trust_level,
        trust_source=trust_source,
        source_schema=envelope.source_schema,
        source_schema_version=envelope.source_schema_version,
        normalizer_version=normalizer_version,
        content_capture_mode=envelope.content_capture_mode,
        started_at=envelope.started_at.astimezone(timezone.utc),
        metadata_json=envelope.metadata,
        start_idempotency_key=idempotency_key,
        start_envelope_sha256=envelope_hash,
    )
    if use_mysql_identity_claim:
        stored, created = await _mysql_insert_or_get_run(db, run)
        if not created:
            same_idempotency_identity = (
                stored.namespace_id == namespace_id
                and stored.runtime_id == runtime_id
                and stored.start_idempotency_key == idempotency_key
            )
            if (
                same_idempotency_identity
                and stored.start_envelope_sha256 == envelope_hash
            ):
                return stored
            if same_idempotency_identity:
                raise EnvelopeConflictError(
                    "run idempotency key was already used with a different envelope"
                )
            raise EnvelopeConflictError(
                "run identity, trace or idempotency key is already registered"
            )
        run = stored
    else:
        try:
            db.add(run)
            await db.flush()
        except IntegrityError as exc:
            if _is_unique_violation(exc):
                raise EnvelopeConflictError(
                    "run identity, trace or idempotency key is already registered"
                ) from exc
            raise
    await audit(
        db,
        username="system:execution",
        action="agent_run.started",
        resource_type="agent_run",
        resource_id=run.id,
        namespace_id=namespace_id,
        details={
            "public_id": run.public_id,
            "runtime_id": runtime_id,
            "trust_level": run.trust_level.value,
            "trust_source": run.trust_source.value,
            "content_capture_mode": run.content_capture_mode.value,
        },
    )
    await enqueue_domain_event(
        db,
        build_agent_run_registered(
            run,
            session_public_id=session.public_id
            if session is not None
            else None,
        ),
        guaranteed_new=True,
    )
    await db.flush()
    return run


async def complete_run(
    db: AsyncSession,
    public_id: str,
    *,
    idempotency_key: str,
    envelope: RunCompleteEnvelope,
    namespace_id: int | None = None,
    runtime_id: int | None = None,
) -> AgentRun:
    predicates = [AgentRun.public_id == public_id]
    if namespace_id is not None:
        predicates.append(AgentRun.namespace_id == namespace_id)
    if runtime_id is not None:
        predicates.append(AgentRun.runtime_id == runtime_id)
    run = (
        await db.execute(
            select(AgentRun)
            .where(*predicates)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if run is None:
        raise ExecutionNotFoundError("run was not found")
    envelope_hash = canonical_sha256(envelope)
    if run.status != AgentRunStatus.STARTED:
        if (
            run.completion_idempotency_key == idempotency_key
            and run.completion_envelope_sha256 == envelope_hash
        ):
            return run
        raise EnvelopeConflictError("run is already terminal")

    ended_at = envelope.ended_at.astimezone(timezone.utc)
    started_at = run.started_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    if ended_at < started_at:
        raise ExecutionStateError("run ended_at is before started_at")
    duration_ms = int((ended_at - started_at).total_seconds() * 1000)
    if (
        envelope.duration_ms is not None
        and envelope.duration_ms != duration_ms
    ):
        raise ExecutionStateError(
            "duration_ms does not match started_at/ended_at"
        )

    if envelope.otel_trace_id is not None:
        if run.otel_trace_id is not None and run.otel_trace_id != envelope.otel_trace_id:
            raise EnvelopeConflictError("completion otel_trace_id conflicts with start")
        run.otel_trace_id = envelope.otel_trace_id
    if envelope.root_span_id is not None:
        if run.root_span_id is not None and run.root_span_id != envelope.root_span_id:
            raise EnvelopeConflictError("completion root_span_id conflicts with start")
        run.root_span_id = envelope.root_span_id
    run.status = envelope.status
    run.ended_at = ended_at
    run.duration_ms = duration_ms
    run.step_count = envelope.step_count
    run.model_call_count = envelope.model_call_count
    run.tool_call_count = envelope.tool_call_count
    run.input_token_count = envelope.input_token_count
    run.output_token_count = envelope.output_token_count
    run.error_type = envelope.error_type
    run.metadata_json = envelope.metadata
    run.completion_idempotency_key = idempotency_key
    run.completion_envelope_sha256 = envelope_hash
    await audit(
        db,
        username="system:execution",
        action="agent_run.completed",
        resource_type="agent_run",
        resource_id=run.id,
        namespace_id=run.namespace_id,
        details={
            "public_id": run.public_id,
            "status": run.status.value,
            "duration_ms": duration_ms,
        },
    )
    await enqueue_domain_event(db, build_agent_run_completed(run))
    await db.flush()
    return run
