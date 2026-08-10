"""FND-060/061/063/064/066-069 transactional Outbox contracts."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.v2.endpoints.outbox import outbox_health, retry_outbox_event
from app.core.deps import require_admin
from app.models.control_plane import (
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
)
from app.models.execution import (
    AgentRun,
    AgentRunStatus,
    AgentSession,
    AgentSessionStatus,
    TrustLevel,
    TrustSource,
)
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.outbox import OutboxEvent, OutboxEventStatus
from app.models.telemetry import AgentRunArtifact
from app.models.user import SystemRole, User
from app.schemas.execution import (
    RunCompleteEnvelope,
    RunStartEnvelope,
    SessionCompleteEnvelope,
    SessionStartEnvelope,
)
from app.schemas.outbox import OutboxRetryRequest
from app.schemas.telemetry import AgentRunArtifactCreate
from app.services import execution_service, telemetry_service
from app.services.execution_service import (
    complete_run,
    complete_session,
    start_run,
    start_session,
)
from app.services.outbox_dispatcher_service import (
    OutboxStateError,
    acknowledge_outbox_event,
    fail_outbox_event,
    get_outbox_health,
    lease_outbox_events,
    retry_failed_outbox_event,
)
from app.services.outbox_event_service import (
    AGENT_RUN_COMPLETED,
    AGENT_RUN_REGISTERED,
    AGENT_RUN_TRUST_DEGRADED,
    AGENT_SESSION_COMPLETED,
    AGENT_SESSION_STARTED,
    TRACE_ARTIFACT_STORED,
    OutboxPayloadError,
    build_agent_run_trust_degraded,
    build_release_receipt_recorded,
    enqueue_domain_event,
    serialize_event_payload,
)
from app.services.telemetry_service import register_agent_run_artifact


NOW = datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc)


async def _scope(db, suffix: str = "outbox"):
    owner = User(
        username=f"{suffix}-owner",
        email=f"{suffix}-owner@example.test",
        hashed_password="unused",
        system_role=SystemRole.USER,
    )
    admin = User(
        username=f"{suffix}-admin",
        email=f"{suffix}-admin@example.test",
        hashed_password="unused",
        system_role=SystemRole.ADMIN,
    )
    db.add_all([owner, admin])
    await db.flush()
    namespace = Namespace(name=f"{suffix}-namespace", owner_id=owner.id)
    db.add(namespace)
    await db.flush()
    db.add(
        NamespaceMember(
            namespace_id=namespace.id,
            user_id=owner.id,
            role=NamespaceRole.ADMIN,
        )
    )
    runtime = RuntimeInstance(
        namespace_id=namespace.id,
        provider=RuntimeProvider.CUSTOM,
        name=f"{suffix}-runtime",
        deploy_type=RuntimeDeployType.PRIVATE,
    )
    db.add(runtime)
    await db.flush()
    return owner, admin, namespace, runtime


def _session_start(suffix: str = "one") -> SessionStartEnvelope:
    return SessionStartEnvelope(
        external_session_id=f"session-{suffix}",
        started_at=NOW,
        sensitivity="RESTRICTED",
        content_capture_mode="metadata_only",
    )


def _run_start(suffix: str = "one") -> RunStartEnvelope:
    return RunStartEnvelope(
        external_run_id=f"run-{suffix}",
        started_at=NOW,
        source_schema="duckdock-run-envelope",
        source_schema_version="1.0",
        content_capture_mode="metadata_only",
    )


def _artifact() -> AgentRunArtifactCreate:
    return AgentRunArtifactCreate(
        kind="TRAJECTORY",
        schema_name="atif",
        schema_version="1.0",
        object_uri="runs/2026/07/28/trajectory.json",
        sha256="c" * 64,
        size_bytes=1024,
        sensitivity="restricted",
        completeness="COMPLETE",
    )


def _pending_row(namespace_id: int, suffix: str) -> OutboxEvent:
    return OutboxEvent(
        event_id=str(uuid.uuid4()),
        namespace_id=namespace_id,
        aggregate_type="AgentRun",
        aggregate_public_id=f"run_{suffix:0<32}"[:36],
        event_type=AGENT_RUN_REGISTERED,
        schema_version="1.0",
        payload_json={
            "namespace_id": namespace_id,
            "run_public_id": f"run_{suffix:0<32}"[:36],
        },
        idempotency_key=f"AgentRun:{suffix}:registered",
        status=OutboxEventStatus.PENDING,
        occurred_at=NOW,
        available_at=NOW,
        attempt_count=0,
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"prompt": "secret"},
        {"authorization": "Bearer secret"},
        {"namespace_id": 1, "run_public_id": {"nested": "raw"}},
        {"namespace_id": 1},
    ],
)
def test_event_serializer_rejects_sensitive_nested_and_incomplete_payloads(
    payload: dict,
) -> None:
    with pytest.raises(OutboxPayloadError):
        serialize_event_payload(AGENT_RUN_REGISTERED, payload)


def test_release_receipt_event_rejects_missing_dispatch_relationship() -> None:
    receipt = SimpleNamespace(promotion=None, rollback=None)

    with pytest.raises(
        OutboxPayloadError,
        match="neither promotion nor rollback",
    ):
        build_release_receipt_recorded(receipt)


async def test_lifecycle_and_artifact_mutations_emit_one_low_sensitive_event(
    async_session,
) -> None:
    owner, _, namespace, runtime = await _scope(async_session)
    session = await start_session(
        async_session,
        namespace_id=namespace.id,
        runtime_id=runtime.id,
        idempotency_key="session-start-outbox",
        envelope=_session_start(),
        actor_user_id=owner.id,
    )
    run = await start_run(
        async_session,
        namespace_id=namespace.id,
        runtime_id=runtime.id,
        idempotency_key="run-start-outbox",
        envelope=_run_start(),
        trust_level=TrustLevel.CHANNEL_AUTHENTICATED,
        trust_source=TrustSource.REPORTER,
    )
    await complete_run(
        async_session,
        run.public_id,
        idempotency_key="run-complete-outbox",
        envelope=RunCompleteEnvelope(
            status=AgentRunStatus.SUCCEEDED,
            ended_at=NOW + timedelta(seconds=1),
            duration_ms=1000,
        ),
    )
    await complete_session(
        async_session,
        session.public_id,
        idempotency_key="session-complete-outbox",
        envelope=SessionCompleteEnvelope(
            status=AgentSessionStatus.ENDED,
            ended_at=NOW + timedelta(seconds=2),
            run_count=0,
            error_count=0,
        ),
    )
    artifact = await register_agent_run_artifact(
        async_session,
        namespace_id=namespace.id,
        run_public_id=run.public_id,
        request=_artifact(),
        actor=owner,
    )
    assert artifact.public_id.startswith("art_")

    rows = list(
        (
            await async_session.execute(
                select(OutboxEvent).order_by(OutboxEvent.id)
            )
        ).scalars()
    )
    assert [row.event_type for row in rows] == [
        AGENT_SESSION_STARTED,
        AGENT_RUN_REGISTERED,
        AGENT_RUN_COMPLETED,
        AGENT_SESSION_COMPLETED,
        TRACE_ARTIFACT_STORED,
    ]
    serialized = str([row.payload_json for row in rows]).lower()
    assert "object_uri" not in serialized
    assert "prompt" not in serialized
    assert "tool_arguments" not in serialized

    await start_session(
        async_session,
        namespace_id=namespace.id,
        runtime_id=runtime.id,
        idempotency_key="session-start-outbox",
        envelope=_session_start(),
        actor_user_id=owner.id,
    )
    await complete_run(
        async_session,
        run.public_id,
        idempotency_key="run-complete-outbox",
        envelope=RunCompleteEnvelope(
            status=AgentRunStatus.SUCCEEDED,
            ended_at=NOW + timedelta(seconds=1),
            duration_ms=1000,
        ),
    )
    await register_agent_run_artifact(
        async_session,
        namespace_id=namespace.id,
        run_public_id=run.public_id,
        request=_artifact(),
        actor=owner,
    )
    assert int(
        await async_session.scalar(select(func.count(OutboxEvent.id))) or 0
    ) == 5


async def test_session_run_and_artifact_roll_back_when_outbox_insert_fails(
    async_session,
    monkeypatch,
) -> None:
    owner, _, namespace, runtime = await _scope(async_session, "atomic")
    owner_id = owner.id
    namespace_id = namespace.id
    runtime_id = runtime.id
    await async_session.commit()

    async def fail_enqueue(*_args, **_kwargs):
        raise RuntimeError("injected outbox insert failure")

    real_enqueue = enqueue_domain_event
    monkeypatch.setattr(execution_service, "enqueue_domain_event", fail_enqueue)
    with pytest.raises(RuntimeError, match="injected"):
        await start_session(
            async_session,
            namespace_id=namespace_id,
            runtime_id=runtime_id,
            idempotency_key="atomic-session",
            envelope=_session_start("atomic"),
            actor_user_id=owner_id,
        )
    await async_session.rollback()
    assert int(
        await async_session.scalar(select(func.count(AgentSession.id))) or 0
    ) == 0

    with pytest.raises(RuntimeError, match="injected"):
        await start_run(
            async_session,
            namespace_id=namespace_id,
            runtime_id=runtime_id,
            idempotency_key="atomic-run",
            envelope=_run_start("atomic"),
            trust_level=TrustLevel.CHANNEL_AUTHENTICATED,
            trust_source=TrustSource.REPORTER,
        )
    await async_session.rollback()
    assert int(await async_session.scalar(select(func.count(AgentRun.id))) or 0) == 0

    monkeypatch.setattr(execution_service, "enqueue_domain_event", real_enqueue)
    run = await start_run(
        async_session,
        namespace_id=namespace_id,
        runtime_id=runtime_id,
        idempotency_key="artifact-parent-run",
        envelope=_run_start("artifact-parent"),
        trust_level=TrustLevel.CHANNEL_AUTHENTICATED,
        trust_source=TrustSource.REPORTER,
    )
    await async_session.commit()
    owner = await async_session.get(User, owner_id)
    assert owner is not None
    monkeypatch.setattr(telemetry_service, "enqueue_domain_event", fail_enqueue)
    with pytest.raises(RuntimeError, match="injected"):
        await register_agent_run_artifact(
            async_session,
            namespace_id=namespace_id,
            run_public_id=run.public_id,
            request=_artifact(),
            actor=owner,
        )
    await async_session.rollback()
    assert int(
        await async_session.scalar(select(func.count(AgentRunArtifact.id))) or 0
    ) == 0
    trace_events = int(
        await async_session.scalar(
            select(func.count(OutboxEvent.id)).where(
                OutboxEvent.event_type == TRACE_ARTIFACT_STORED
            )
        )
        or 0
    )
    assert trace_events == 0


async def test_completion_transitions_roll_back_when_outbox_insert_fails(
    async_session,
    monkeypatch,
) -> None:
    owner, _, namespace, runtime = await _scope(async_session, "completion-atomic")
    session = await start_session(
        async_session,
        namespace_id=namespace.id,
        runtime_id=runtime.id,
        idempotency_key="completion-session-start",
        envelope=_session_start("completion"),
        actor_user_id=owner.id,
    )
    run_envelope = _run_start("completion").model_copy(
        update={"session_public_id": session.public_id}
    )
    run = await start_run(
        async_session,
        namespace_id=namespace.id,
        runtime_id=runtime.id,
        idempotency_key="completion-run-start",
        envelope=run_envelope,
        trust_level=TrustLevel.CHANNEL_AUTHENTICATED,
        trust_source=TrustSource.REPORTER,
    )
    session_public_id = session.public_id
    run_public_id = run.public_id
    await async_session.commit()

    async def fail_enqueue(*_args, **_kwargs):
        raise RuntimeError("injected completion outbox failure")

    monkeypatch.setattr(execution_service, "enqueue_domain_event", fail_enqueue)
    with pytest.raises(RuntimeError, match="completion outbox"):
        await complete_run(
            async_session,
            run_public_id,
            idempotency_key="completion-run-complete",
            envelope=RunCompleteEnvelope(
                status=AgentRunStatus.SUCCEEDED,
                ended_at=NOW + timedelta(seconds=1),
                duration_ms=1000,
            ),
        )
    await async_session.rollback()
    persisted_run = (
        await async_session.execute(
            select(AgentRun).where(AgentRun.public_id == run_public_id)
        )
    ).scalar_one()
    assert persisted_run.status == AgentRunStatus.STARTED

    with pytest.raises(RuntimeError, match="completion outbox"):
        await complete_session(
            async_session,
            session_public_id,
            idempotency_key="completion-session-complete",
            envelope=SessionCompleteEnvelope(
                status=AgentSessionStatus.ENDED,
                ended_at=NOW + timedelta(seconds=2),
                run_count=1,
                error_count=0,
            ),
        )
    await async_session.rollback()
    persisted_session = (
        await async_session.execute(
            select(AgentSession).where(
                AgentSession.public_id == session_public_id
            )
        )
    ).scalar_one()
    assert persisted_session.status == AgentSessionStatus.OPEN
    completion_events = int(
        await async_session.scalar(
            select(func.count(OutboxEvent.id)).where(
                OutboxEvent.event_type.in_(
                    (AGENT_RUN_COMPLETED, AGENT_SESSION_COMPLETED)
                )
            )
        )
        or 0
    )
    assert completion_events == 0


async def test_retry_backoff_exhaustion_health_and_admin_retry(
    async_session,
) -> None:
    owner, admin, namespace, _ = await _scope(async_session, "retry")
    row = _pending_row(namespace.id, "retry")
    async_session.add(row)
    await async_session.flush()

    leased = await lease_outbox_events(
        async_session,
        worker_id="worker-a",
        batch_size=1,
        lease_seconds=10,
        now=NOW,
    )
    assert [item.event_id for item in leased] == [row.event_id]
    failed = await fail_outbox_event(
        async_session,
        event_id=row.event_id,
        worker_id="worker-a",
        error_code="HTTP 500 token=secret",
        error="Authorization: Bearer secret",
        max_attempts=3,
        base_retry_seconds=5,
        max_retry_seconds=60,
        now=NOW,
    )
    assert failed is not None
    assert failed.status == OutboxEventStatus.PENDING
    assert failed.available_at == NOW + timedelta(seconds=5)
    assert failed.last_error_code == "publish_failed"
    assert "secret" not in (failed.last_error or "")

    for attempt, current in (
        (2, NOW + timedelta(seconds=5)),
        (3, NOW + timedelta(seconds=15)),
    ):
        await lease_outbox_events(
            async_session,
            worker_id="worker-a",
            batch_size=1,
            lease_seconds=10,
            now=current,
        )
        failed = await fail_outbox_event(
            async_session,
            event_id=row.event_id,
            worker_id="worker-a",
            error_code="timeout",
            error=None,
            max_attempts=3,
            base_retry_seconds=5,
            max_retry_seconds=60,
            now=current,
        )
        assert failed is not None
        assert failed.attempt_count == attempt
    assert failed.status == OutboxEventStatus.FAILED

    health = await get_outbox_health(
        async_session,
        now=NOW + timedelta(seconds=16),
    )
    assert health.failed_count == 1
    assert health.pending_count == 0

    with pytest.raises(HTTPException):
        await require_admin(owner)
    assert await require_admin(admin) is admin
    retried = await retry_outbox_event(
        row.event_id,
        OutboxRetryRequest(reason="provider recovered"),
        async_session,
        admin,
    )
    assert retried.status == OutboxEventStatus.PENDING
    assert retried.attempt_count == 0
    api_health = await outbox_health(async_session, admin)
    assert api_health.pending_count == 1

    with pytest.raises(OutboxStateError):
        await retry_failed_outbox_event(
            async_session,
            event_id=row.event_id,
            actor=admin,
            reason="invalid duplicate retry",
        )


async def test_crash_after_publish_reuses_event_id_for_consumer_dedupe(
    async_session,
) -> None:
    _, _, namespace, _ = await _scope(async_session, "crash")
    row = _pending_row(namespace.id, "crash")
    async_session.add(row)
    await async_session.commit()
    session_factory = async_sessionmaker(
        async_session.bind,
        expire_on_commit=False,
    )

    deliveries: list[str] = []
    effects: set[str] = set()

    async def publish(event_id: str) -> None:
        deliveries.append(event_id)
        effects.add(event_id)

    async with session_factory() as db:
        first = await lease_outbox_events(
            db,
            worker_id="worker-crashed",
            batch_size=1,
            lease_seconds=10,
            now=NOW,
        )
        await db.commit()
    await publish(first[0].event_id)
    # Simulate process death before acknowledge; the lease later expires.
    async with session_factory() as db:
        second = await lease_outbox_events(
            db,
            worker_id="worker-recovered",
            batch_size=1,
            lease_seconds=10,
            now=NOW + timedelta(seconds=11),
        )
        await db.commit()
    await publish(second[0].event_id)
    async with session_factory() as db:
        acknowledged = await acknowledge_outbox_event(
            db,
            event_id=second[0].event_id,
            worker_id="worker-recovered",
            now=NOW + timedelta(seconds=12),
        )
        await db.commit()
    assert acknowledged is True
    assert deliveries == [row.event_id, row.event_id]
    assert effects == {row.event_id}


async def test_trust_degraded_builder_is_allowlisted(async_session) -> None:
    _, _, namespace, runtime = await _scope(async_session, "trust")
    run = AgentRun(
        public_id=f"run_{'d' * 32}",
        namespace_id=namespace.id,
        runtime_id=runtime.id,
        external_run_id="trust-run",
        attempt=1,
        status=AgentRunStatus.STARTED,
        trust_level=TrustLevel.UNVERIFIED,
        trust_source=TrustSource.IMPORT,
        source_schema="duckdock-run-envelope",
        source_schema_version="1.0",
        normalizer_version="canonical-json-v1",
        content_capture_mode="metadata_only",
        started_at=NOW,
        start_idempotency_key="trust-run-start",
        start_envelope_sha256="d" * 64,
    )
    async_session.add(run)
    await async_session.flush()
    event = build_agent_run_trust_degraded(
        run,
        reason_code="attestation_rejected",
    )
    row = await enqueue_domain_event(async_session, event)
    assert row.event_type == AGENT_RUN_TRUST_DEGRADED
    assert row.payload_json["reason_code"] == "attestation_rejected"


@pytest.mark.mysql
async def test_mysql_skip_locked_dispatchers_claim_disjoint_batches(
    async_session_mysql,
) -> None:
    _, _, namespace, _ = await _scope(async_session_mysql, "mysql-outbox")
    async_session_mysql.add_all(
        [_pending_row(namespace.id, f"mysql-{index}") for index in range(10)]
    )
    await async_session_mysql.commit()
    session_factory = async_sessionmaker(
        async_session_mysql.bind,
        expire_on_commit=False,
    )

    async def claim(worker_id: str) -> set[str]:
        async with session_factory() as db:
            rows = await lease_outbox_events(
                db,
                worker_id=worker_id,
                batch_size=5,
                lease_seconds=30,
                now=NOW,
            )
            await db.commit()
            return {row.event_id for row in rows}

    first, second = await asyncio.gather(
        claim("mysql-worker-a"),
        claim("mysql-worker-b"),
    )
    assert first.isdisjoint(second)
    assert len(first | second) == 10
