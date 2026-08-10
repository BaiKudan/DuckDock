"""FND-032/033/037/039 Session/Run lifecycle tests."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.audit import AuditLog
from app.models.control_plane import (
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
    Sensitivity,
)
from app.models.deployment import AgentDeployment, AgentDeploymentStatus
from app.models.execution import (
    AgentRun,
    AgentRunStatus,
    AgentSession,
    AgentSessionStatus,
    ContentCaptureMode,
    TrustLevel,
    TrustSource,
)
from app.models.namespace import Namespace
from app.models.user import SystemRole, User
from app.schemas.execution import (
    RunCompleteEnvelope,
    RunStartEnvelope,
    SessionCompleteEnvelope,
    SessionStartEnvelope,
)
from app.services.execution_service import (
    EnvelopeConflictError,
    ExecutionStateError,
    ExecutionTenantMismatchError,
    complete_run,
    complete_session,
    start_run,
    start_session,
)


NOW = datetime(2026, 7, 28, 0, 0, tzinfo=timezone.utc)


async def _scope(db, suffix: str = "default"):
    user = User(
        username=f"run-{suffix}",
        email=f"run-{suffix}@example.test",
        hashed_password="unused",
        system_role=SystemRole.USER,
    )
    db.add(user)
    await db.flush()
    namespace = Namespace(name=f"run-{suffix}", owner_id=user.id)
    db.add(namespace)
    await db.flush()
    runtime = RuntimeInstance(
        namespace_id=namespace.id,
        provider=RuntimeProvider.CUSTOM,
        name=f"run-runtime-{suffix}",
        deploy_type=RuntimeDeployType.PRIVATE,
    )
    db.add(runtime)
    await db.flush()
    deployment = AgentDeployment(
        public_id=f"dep_{suffix:0<32}"[:36],
        namespace_id=namespace.id,
        runtime_id=runtime.id,
        external_deployment_id=f"external-{suffix}",
        environment="test",
        revision="1",
        configuration_digest="a" * 64,
        status=AgentDeploymentStatus.ACTIVE,
        activated_at=NOW,
        created_by_user_id=user.id,
    )
    db.add(deployment)
    await db.flush()
    return user, namespace, runtime, deployment


def _session_start(
    *,
    deployment_public_id: str | None = None,
) -> SessionStartEnvelope:
    return SessionStartEnvelope(
        external_session_id="session-1",
        deployment_public_id=deployment_public_id,
        started_at=NOW,
        sensitivity="RESTRICTED",
        content_capture_mode=ContentCaptureMode.METADATA_ONLY,
        metadata={"service.name": "agent-loop"},
    )


def _run_start(
    *,
    session_public_id: str | None = None,
    deployment_public_id: str | None = None,
    external_run_id: str = "run-1",
) -> RunStartEnvelope:
    return RunStartEnvelope(
        external_run_id=external_run_id,
        session_public_id=session_public_id,
        deployment_public_id=deployment_public_id,
        started_at=NOW,
        source_schema="duckdock-run-envelope",
        source_schema_version="1.0",
        content_capture_mode=ContentCaptureMode.METADATA_ONLY,
        otel_trace_id="1" * 32,
        root_span_id="2" * 16,
        metadata={"operation.name": "agent.run"},
    )


async def test_session_open_end_and_identical_replay(async_session) -> None:
    user, namespace, runtime, deployment = await _scope(async_session, "session")
    envelope = _session_start(deployment_public_id=deployment.public_id)

    session = await start_session(
        async_session,
        namespace_id=namespace.id,
        runtime_id=runtime.id,
        idempotency_key="session-start-1",
        envelope=envelope,
        actor_user_id=user.id,
    )
    replay = await start_session(
        async_session,
        namespace_id=namespace.id,
        runtime_id=runtime.id,
        idempotency_key="session-start-1",
        envelope=envelope,
        actor_user_id=user.id,
    )
    assert replay.id == session.id
    assert session.status == AgentSessionStatus.OPEN
    assert session.sensitivity == Sensitivity.RESTRICTED

    completed = await complete_session(
        async_session,
        session.public_id,
        idempotency_key="session-complete-1",
        envelope=SessionCompleteEnvelope(
            ended_at=NOW + timedelta(seconds=5),
            status=AgentSessionStatus.ENDED,
            run_count=0,
            error_count=0,
        ),
    )
    replayed_completion = await complete_session(
        async_session,
        session.public_id,
        idempotency_key="session-complete-1",
        envelope=SessionCompleteEnvelope(
            ended_at=NOW + timedelta(seconds=5),
            status=AgentSessionStatus.ENDED,
            run_count=0,
            error_count=0,
        ),
    )
    assert replayed_completion.id == completed.id
    assert completed.status == AgentSessionStatus.ENDED


async def test_run_start_replay_and_conflicting_payload(async_session) -> None:
    _, namespace, runtime, deployment = await _scope(async_session, "start")
    envelope = _run_start(deployment_public_id=deployment.public_id)

    run = await start_run(
        async_session,
        namespace_id=namespace.id,
        runtime_id=runtime.id,
        idempotency_key="run-start-1",
        envelope=envelope,
        trust_level=TrustLevel.CHANNEL_AUTHENTICATED,
        trust_source=TrustSource.REPORTER,
    )
    replay = await start_run(
        async_session,
        namespace_id=namespace.id,
        runtime_id=runtime.id,
        idempotency_key="run-start-1",
        envelope=envelope,
        trust_level=TrustLevel.CHANNEL_AUTHENTICATED,
        trust_source=TrustSource.REPORTER,
    )
    assert replay.id == run.id

    with pytest.raises(EnvelopeConflictError, match="different envelope"):
        await start_run(
            async_session,
            namespace_id=namespace.id,
            runtime_id=runtime.id,
            idempotency_key="run-start-1",
            envelope=_run_start(
                deployment_public_id=deployment.public_id,
                external_run_id="run-different",
            ),
            trust_level=TrustLevel.CHANNEL_AUTHENTICATED,
            trust_source=TrustSource.REPORTER,
        )


@pytest.mark.parametrize(
    "terminal_status",
    [
        AgentRunStatus.SUCCEEDED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
        AgentRunStatus.TIMED_OUT,
    ],
)
async def test_run_legal_terminal_transitions_and_identical_completion_replay(
    async_session,
    terminal_status: AgentRunStatus,
) -> None:
    _, namespace, runtime, deployment = await _scope(
        async_session,
        f"terminal-{terminal_status.value.lower()}",
    )
    run = await start_run(
        async_session,
        namespace_id=namespace.id,
        runtime_id=runtime.id,
        idempotency_key=f"start-{terminal_status.value}",
        envelope=_run_start(
            deployment_public_id=deployment.public_id,
            external_run_id=f"run-{terminal_status.value.lower()}",
        ),
        trust_level=TrustLevel.CHANNEL_AUTHENTICATED,
        trust_source=TrustSource.REPORTER,
    )
    completion = RunCompleteEnvelope(
        ended_at=NOW + timedelta(seconds=2),
        status=terminal_status,
        duration_ms=2000,
        step_count=3,
        model_call_count=1,
        tool_call_count=1,
        input_token_count=10,
        output_token_count=5,
        error_type="classified_error"
        if terminal_status == AgentRunStatus.FAILED
        else None,
    )
    completed = await complete_run(
        async_session,
        run.public_id,
        idempotency_key=f"complete-{terminal_status.value}",
        envelope=completion,
    )
    replay = await complete_run(
        async_session,
        run.public_id,
        idempotency_key=f"complete-{terminal_status.value}",
        envelope=completion,
    )
    assert replay.id == completed.id
    assert completed.status == terminal_status
    assert completed.duration_ms == 2000

    with pytest.raises(EnvelopeConflictError, match="terminal"):
        await complete_run(
            async_session,
            run.public_id,
            idempotency_key="different-completion",
            envelope=completion.model_copy(
                update={"status": AgentRunStatus.CANCELLED}
            ),
        )


async def test_run_rejects_invalid_time_and_cross_tenant_links(async_session) -> None:
    _, first, runtime_first, deployment_first = await _scope(
        async_session,
        "tenant-first",
    )
    _, second, runtime_second, deployment_second = await _scope(
        async_session,
        "tenant-second",
    )

    with pytest.raises(ExecutionTenantMismatchError, match="deployment"):
        await start_run(
            async_session,
            namespace_id=first.id,
            runtime_id=runtime_first.id,
            idempotency_key="cross-deployment",
            envelope=_run_start(
                deployment_public_id=deployment_second.public_id,
            ),
            trust_level=TrustLevel.UNVERIFIED,
            trust_source=TrustSource.IMPORT,
        )

    with pytest.raises(ExecutionTenantMismatchError, match="runtime"):
        await start_run(
            async_session,
            namespace_id=first.id,
            runtime_id=runtime_second.id,
            idempotency_key="cross-runtime",
            envelope=_run_start(
                deployment_public_id=deployment_first.public_id,
            ),
            trust_level=TrustLevel.UNVERIFIED,
            trust_source=TrustSource.IMPORT,
        )

    run = await start_run(
        async_session,
        namespace_id=second.id,
        runtime_id=runtime_second.id,
        idempotency_key="invalid-time",
        envelope=_run_start(
            deployment_public_id=deployment_second.public_id,
            external_run_id="invalid-time",
        ),
        trust_level=TrustLevel.UNVERIFIED,
        trust_source=TrustSource.IMPORT,
    )
    with pytest.raises(ExecutionStateError, match="before"):
        await complete_run(
            async_session,
            run.public_id,
            idempotency_key="invalid-time-complete",
            envelope=RunCompleteEnvelope(
                ended_at=NOW - timedelta(seconds=1),
                status=AgentRunStatus.SUCCEEDED,
            ),
        )
    with pytest.raises(ExecutionStateError, match="duration_ms"):
        await complete_run(
            async_session,
            run.public_id,
            idempotency_key="invalid-duration",
            envelope=RunCompleteEnvelope(
                ended_at=NOW + timedelta(seconds=1),
                status=AgentRunStatus.SUCCEEDED,
                duration_ms=999,
            ),
        )


def test_session_and_run_models_have_tenant_scoped_constraints() -> None:
    session_uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in AgentSession.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("public_id",) in session_uniques
    assert ("namespace_id", "runtime_id", "external_session_id") in session_uniques
    assert ("namespace_id", "runtime_id", "start_idempotency_key") in session_uniques

    run_uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in AgentRun.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("public_id",) in run_uniques
    assert ("namespace_id", "runtime_id", "external_run_id") in run_uniques
    assert ("namespace_id", "runtime_id", "start_idempotency_key") in run_uniques
    assert ("namespace_id", "runtime_id", "completion_idempotency_key") in run_uniques
    assert ("namespace_id", "otel_trace_id") in run_uniques


@pytest.mark.mysql
async def test_mysql_concurrent_start_and_completion_are_idempotent(
    async_session_mysql,
) -> None:
    _, namespace, runtime, deployment = await _scope(
        async_session_mysql,
        "mysql-race",
    )
    namespace_id = namespace.id
    runtime_id = runtime.id
    deployment_public_id = deployment.public_id
    await async_session_mysql.commit()

    session_factory = async_sessionmaker(
        async_session_mysql.bind,
        expire_on_commit=False,
    )

    async def concurrent_start() -> str:
        async with session_factory() as db:
            run = await start_run(
                db,
                namespace_id=namespace_id,
                runtime_id=runtime_id,
                idempotency_key="mysql-start-race",
                envelope=_run_start(
                    deployment_public_id=deployment_public_id,
                    external_run_id="mysql-race",
                ),
                trust_level=TrustLevel.CHANNEL_AUTHENTICATED,
                trust_source=TrustSource.REPORTER,
            )
            await db.commit()
            return run.public_id

    public_ids = await asyncio.gather(concurrent_start(), concurrent_start())
    assert public_ids[0] == public_ids[1]

    completion = RunCompleteEnvelope(
        ended_at=NOW + timedelta(seconds=1),
        status=AgentRunStatus.SUCCEEDED,
        duration_ms=1000,
    )

    async def concurrent_complete() -> AgentRunStatus:
        async with session_factory() as db:
            run = await complete_run(
                db,
                public_ids[0],
                idempotency_key="mysql-complete-race",
                envelope=completion,
            )
            await db.commit()
            return run.status

    statuses = await asyncio.gather(
        concurrent_complete(),
        concurrent_complete(),
    )
    assert statuses == [
        AgentRunStatus.SUCCEEDED,
        AgentRunStatus.SUCCEEDED,
    ]
    rows = (
        await async_session_mysql.execute(
            select(AgentRun).where(AgentRun.public_id == public_ids[0])
        )
    ).scalars().all()
    assert len(rows) == 1

    audit_actions = (
        await async_session_mysql.execute(
            select(AuditLog.action).where(
                AuditLog.resource_type == "agent_run"
            )
        )
    ).scalars().all()
    assert audit_actions.count("agent_run.started") == 1
    assert audit_actions.count("agent_run.completed") == 1
