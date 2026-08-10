from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.control_plane import CredentialRecord
from app.models.execution import AgentRun, AgentSession
from app.models.telemetry import (
    AgentRunArtifact,
    TelemetrySink,
    TelemetrySinkStatus,
    TraceBackendRef,
    TraceBackendRefStatus,
)
from app.models.user import SystemRole, User
from app.schemas.telemetry import (
    AgentRunArtifactCreate,
    TelemetrySinkCreate,
    TelemetrySinkUpdate,
    validate_trace_url,
)
from app.services.audit_service import audit
from app.services.outbox_event_service import (
    build_trace_artifact_stored,
    enqueue_domain_event,
)
from app.services.telemetry_ports import (
    TelemetrySinkPort,
    TraceConfirmationRequest,
)
from app.services.tenant_write_service import require_active_namespace


_SAFE_ERROR_CODE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")


class TelemetryError(ValueError):
    pass


class TelemetryNotFoundError(TelemetryError):
    pass


class TelemetryTenantMismatchError(TelemetryError):
    pass


class TelemetryReferenceError(TelemetryError):
    pass


class TelemetryConflictError(TelemetryError):
    pass


class TelemetryStateError(TelemetryError):
    pass


def _artifact_public_id() -> str:
    return f"art_{uuid.uuid4().hex}"


def _sink_public_id() -> str:
    return f"tsk_{uuid.uuid4().hex}"


async def _run_for_namespace(
    db: AsyncSession,
    *,
    run_public_id: str,
    namespace_id: int,
) -> AgentRun:
    run = (
        await db.execute(
            select(AgentRun).where(AgentRun.public_id == run_public_id)
        )
    ).scalar_one_or_none()
    if run is None:
        raise TelemetryNotFoundError("AgentRun not found")
    if run.namespace_id != namespace_id:
        raise TelemetryTenantMismatchError(
            "AgentRun must belong to the requested Namespace"
        )
    return run


async def _validate_credential_ownership(
    db: AsyncSession,
    *,
    credential_ref: str,
    actor: User,
) -> None:
    if not credential_ref.startswith("db:"):
        return
    record_id = int(credential_ref.removeprefix("db:"))
    record = await db.get(CredentialRecord, record_id)
    if record is None:
        raise TelemetryReferenceError("credential_ref does not exist")
    if (
        actor.system_role != SystemRole.ADMIN
        and record.created_by != actor.id
    ):
        raise TelemetryReferenceError(
            "credential_ref is not owned by the current user"
        )


async def register_agent_run_artifact(
    db: AsyncSession,
    *,
    namespace_id: int,
    run_public_id: str,
    request: AgentRunArtifactCreate,
    actor: User,
) -> AgentRunArtifact:
    """Register immutable object metadata without reading or parsing the object."""

    await require_active_namespace(db, namespace_id)
    run = await _run_for_namespace(
        db,
        run_public_id=run_public_id,
        namespace_id=namespace_id,
    )
    existing = (
        await db.execute(
            select(AgentRunArtifact).where(
                AgentRunArtifact.namespace_id == namespace_id,
                AgentRunArtifact.agent_run_id == run.id,
                AgentRunArtifact.kind == request.kind,
                AgentRunArtifact.sha256 == request.sha256,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        replay_fields = (
            existing.schema_name == request.schema_name
            and existing.schema_version == request.schema_version
            and existing.object_uri == request.object_uri
            and existing.size_bytes == request.size_bytes
            and existing.sensitivity == request.sensitivity
            and existing.redaction_policy_version
            == request.redaction_policy_version
            and existing.completeness == request.completeness
        )
        if replay_fields:
            return existing
        raise TelemetryConflictError(
            "artifact identity is already registered with different metadata"
        )

    artifact = AgentRunArtifact(
        public_id=_artifact_public_id(),
        namespace_id=namespace_id,
        agent_run_id=run.id,
        kind=request.kind,
        schema_name=request.schema_name,
        schema_version=request.schema_version,
        object_uri=request.object_uri,
        sha256=request.sha256,
        size_bytes=request.size_bytes,
        sensitivity=request.sensitivity,
        redaction_policy_version=request.redaction_policy_version,
        completeness=request.completeness,
    )
    db.add(artifact)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="agent_run_artifact.registered",
        resource_type="agent_run_artifact",
        resource_id=artifact.id,
        namespace_id=namespace_id,
        details={
            "public_id": artifact.public_id,
            "run_public_id": run.public_id,
            "kind": artifact.kind.value,
            "schema_name": artifact.schema_name,
            "schema_version": artifact.schema_version,
            "sha256": artifact.sha256,
            "size_bytes": artifact.size_bytes,
        },
    )
    await enqueue_domain_event(
        db,
        build_trace_artifact_stored(
            artifact,
            run_public_id=run.public_id,
        ),
    )
    return artifact


async def list_agent_run_artifacts(
    db: AsyncSession,
    *,
    namespace_id: int,
    run_public_id: str,
    limit: int = 100,
) -> list[AgentRunArtifact]:
    run = await _run_for_namespace(
        db,
        run_public_id=run_public_id,
        namespace_id=namespace_id,
    )
    return list(
        (
            await db.execute(
                select(AgentRunArtifact)
                .where(
                    AgentRunArtifact.namespace_id == namespace_id,
                    AgentRunArtifact.agent_run_id == run.id,
                )
                .order_by(
                    AgentRunArtifact.created_at.desc(),
                    AgentRunArtifact.id.desc(),
                )
                .limit(limit)
            )
        ).scalars()
    )


async def create_telemetry_sink(
    db: AsyncSession,
    *,
    request: TelemetrySinkCreate,
    actor: User,
) -> TelemetrySink:
    await require_active_namespace(db, request.namespace_id)
    await _validate_credential_ownership(
        db,
        credential_ref=request.credential_ref,
        actor=actor,
    )
    duplicate = (
        await db.execute(
            select(TelemetrySink.id).where(
                TelemetrySink.namespace_id == request.namespace_id,
                TelemetrySink.name == request.name,
            )
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise TelemetryConflictError(
            "TelemetrySink name is already used in this Namespace"
        )

    sink = TelemetrySink(
        public_id=_sink_public_id(),
        namespace_id=request.namespace_id,
        provider=request.provider,
        name=request.name,
        endpoint=request.endpoint,
        project_ref=request.project_ref,
        credential_ref=request.credential_ref,
        status=TelemetrySinkStatus.ACTIVE,
        config_json=request.config.model_dump(mode="json"),
    )
    db.add(sink)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="telemetry_sink.created",
        resource_type="telemetry_sink",
        resource_id=sink.id,
        namespace_id=sink.namespace_id,
        details={
            "public_id": sink.public_id,
            "provider": sink.provider,
            "name": sink.name,
        },
    )
    return sink


async def get_telemetry_sink(
    db: AsyncSession,
    *,
    public_id: str,
    namespace_id: int | None = None,
) -> TelemetrySink:
    sink = (
        await db.execute(
            select(TelemetrySink).where(TelemetrySink.public_id == public_id)
        )
    ).scalar_one_or_none()
    if sink is None:
        raise TelemetryNotFoundError("TelemetrySink not found")
    if namespace_id is not None and sink.namespace_id != namespace_id:
        raise TelemetryTenantMismatchError("TelemetrySink not found")
    return sink


async def list_telemetry_sinks(
    db: AsyncSession,
    *,
    namespace_id: int,
    include_disabled: bool = True,
    limit: int = 100,
) -> list[TelemetrySink]:
    statement = select(TelemetrySink).where(
        TelemetrySink.namespace_id == namespace_id
    )
    if not include_disabled:
        statement = statement.where(
            TelemetrySink.status != TelemetrySinkStatus.DISABLED
        )
    return list(
        (
            await db.execute(
                statement.order_by(
                    TelemetrySink.name,
                    TelemetrySink.id,
                ).limit(limit)
            )
        ).scalars()
    )


async def update_telemetry_sink(
    db: AsyncSession,
    *,
    sink: TelemetrySink,
    request: TelemetrySinkUpdate,
    actor: User,
) -> TelemetrySink:
    if sink.status == TelemetrySinkStatus.DISABLED:
        raise TelemetryStateError("Disabled TelemetrySink configuration is immutable")
    changes = request.model_dump(exclude_unset=True)
    if request.credential_ref is not None:
        await _validate_credential_ownership(
            db,
            credential_ref=request.credential_ref,
            actor=actor,
        )
    changed_fields: list[str] = []
    for field in ("endpoint", "project_ref", "credential_ref"):
        if field in changes and getattr(sink, field) != changes[field]:
            setattr(sink, field, changes[field])
            changed_fields.append(field)
    if request.config is not None:
        config_json = request.config.model_dump(mode="json")
        if sink.config_json != config_json:
            sink.config_json = config_json
            changed_fields.append("config")
    if changed_fields:
        await db.flush()
        await audit(
            db,
            user=actor,
            action="telemetry_sink.updated",
            resource_type="telemetry_sink",
            resource_id=sink.id,
            namespace_id=sink.namespace_id,
            details={
                "public_id": sink.public_id,
                "changed_fields": sorted(changed_fields),
            },
        )
    return sink


async def disable_telemetry_sink(
    db: AsyncSession,
    *,
    sink: TelemetrySink,
    actor: User,
) -> TelemetrySink:
    if sink.status == TelemetrySinkStatus.DISABLED:
        return sink
    sink.status = TelemetrySinkStatus.DISABLED
    await db.flush()
    await audit(
        db,
        user=actor,
        action="telemetry_sink.disabled",
        resource_type="telemetry_sink",
        resource_id=sink.id,
        namespace_id=sink.namespace_id,
        details={"public_id": sink.public_id},
    )
    return sink


def _safe_error_code(value: str | None) -> str:
    candidate = (value or "").strip().lower()
    if _SAFE_ERROR_CODE.fullmatch(candidate):
        return candidate
    return "provider_error"


async def confirm_trace_backend_ref(
    db: AsyncSession,
    *,
    namespace_id: int,
    run_public_id: str,
    sink_public_id: str,
    port: TelemetrySinkPort,
    actor: User | None = None,
) -> TraceBackendRef:
    """Confirm a trace after Run persistence; provider failures are contained."""

    await require_active_namespace(db, namespace_id)
    run = await _run_for_namespace(
        db,
        run_public_id=run_public_id,
        namespace_id=namespace_id,
    )
    sink = await get_telemetry_sink(
        db,
        public_id=sink_public_id,
        namespace_id=namespace_id,
    )
    if sink.status != TelemetrySinkStatus.ACTIVE:
        raise TelemetryStateError("TelemetrySink is not active")

    external_session_id: str | None = None
    if run.session_id is not None:
        external_session_id = (
            await db.execute(
                select(AgentSession.external_session_id).where(
                    AgentSession.id == run.session_id
                )
            )
        ).scalar_one_or_none()
    source_trace_id = run.otel_trace_id or run.public_id

    status = TraceBackendRefStatus.ERROR
    trace_url: str | None = None
    last_error_code: str | None = "provider_error"
    confirmed_at: datetime | None = None
    confirmed_external_trace_id = source_trace_id
    confirmed_external_session_id = external_session_id
    try:
        confirmation = await port.confirm_trace(
            TraceConfirmationRequest(
                run_public_id=run.public_id,
                source_trace_id=source_trace_id,
                source_session_id=external_session_id,
                started_at=run.started_at,
                ended_at=run.ended_at,
            )
        )
        confirmed_external_trace_id = confirmation.external_trace_id
        confirmed_external_session_id = confirmation.external_session_id
        if confirmation.confirmed:
            if confirmation.trace_url is not None:
                trace_url = validate_trace_url(confirmation.trace_url)
            status = TraceBackendRefStatus.CONFIRMED
            last_error_code = None
            confirmed_at = datetime.now(timezone.utc)
        else:
            last_error_code = _safe_error_code(confirmation.error_code)
    except Exception:
        # Do not persist provider exception strings: they may contain response
        # bodies, tokens, or other secrets. The committed AgentRun is untouched.
        last_error_code = "provider_error"

    reference = (
        await db.execute(
            select(TraceBackendRef).where(
                TraceBackendRef.agent_run_id == run.id,
                TraceBackendRef.telemetry_sink_id == sink.id,
            )
        )
    ).scalar_one_or_none()
    if reference is None:
        reference = TraceBackendRef(
            namespace_id=namespace_id,
            agent_run_id=run.id,
            telemetry_sink_id=sink.id,
            external_trace_id=confirmed_external_trace_id,
        )
        db.add(reference)
    reference.external_trace_id = confirmed_external_trace_id
    reference.external_session_id = confirmed_external_session_id
    reference.trace_url = trace_url
    reference.status = status
    reference.last_confirmed_at = confirmed_at
    reference.last_error_code = last_error_code
    await db.flush()
    await audit(
        db,
        user=actor,
        action=(
            "trace_backend_ref.confirmed"
            if status == TraceBackendRefStatus.CONFIRMED
            else "trace_backend_ref.error"
        ),
        resource_type="trace_backend_ref",
        resource_id=reference.id,
        namespace_id=namespace_id,
        details={
            "run_public_id": run.public_id,
            "sink_public_id": sink.public_id,
            "status": reference.status.value,
            "error_code": reference.last_error_code,
        },
    )
    return reference
