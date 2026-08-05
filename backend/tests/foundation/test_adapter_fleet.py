"""RT-08 dynamic DD-C0 negotiation, heartbeat drift and Fleet projection."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.v2.endpoints.executions import (
    get_reporter_execution_identity,
)
from app.api.v2.router import api_router
from app.core.database import get_db
from app.core.security import hash_password
from app.models.control_plane import (
    ReporterCredential,
    RuntimeCapabilitySnapshot,
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
)
from app.models.fleet import (
    AdapterConfigDrift,
    AdapterHandshake,
    AdapterHandshakeStatus,
    AdapterHeartbeatRecord,
    AdapterProfile,
)
from app.models.execution import TrustLevel, TrustSource
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.telemetry import TelemetrySink, TelemetrySinkStatus
from app.models.user import SystemRole, User
from app.schemas.fleet import AdapterDescriptor, AdapterHeartbeat
from app.services.adapters.generic import GenericRuntimeAdapter
from app.services.adapters.openclaw import OpenClawAdapter
from app.services.fleet_service import (
    get_fleet_summary,
    negotiate_adapter_handshake,
    record_adapter_heartbeat,
)
from app.services.reporter_identity_service import (
    EXECUTION_WRITE_SCOPE,
    ReporterExecutionIdentity,
)


def _nonce(seed: str) -> str:
    return (seed * 22)[:22]


async def _seed(async_session, suffix: str = "main"):
    owner = User(
        username=f"fleet-{suffix}",
        email=f"fleet-{suffix}@example.test",
        hashed_password="unused",
        system_role=SystemRole.USER,
    )
    async_session.add(owner)
    await async_session.flush()
    namespace = Namespace(
        name=f"fleet-{suffix}",
        owner_id=owner.id,
    )
    async_session.add(namespace)
    await async_session.flush()
    async_session.add(
        NamespaceMember(
            namespace_id=namespace.id,
            user_id=owner.id,
            role=NamespaceRole.ADMIN,
        )
    )
    runtime = RuntimeInstance(
        namespace_id=namespace.id,
        provider=RuntimeProvider.CUSTOM,
        name=f"fleet-runtime-{suffix}",
        deploy_type=RuntimeDeployType.PRIVATE,
    )
    async_session.add(runtime)
    await async_session.flush()
    credential = ReporterCredential(
        runtime_id=runtime.id,
        user_id=owner.id,
        device_id=f"fleet-device-{suffix}",
        name=f"fleet-reporter-{suffix}",
        token_prefix=f"fleet{suffix}"[:16],
        token_hash=hash_password("fleet-secret"),
        scopes=[EXECUTION_WRITE_SCOPE],
    )
    async_session.add(credential)
    await async_session.flush()
    identity = ReporterExecutionIdentity(
        namespace_id=namespace.id,
        runtime_id=runtime.id,
        credential_id=credential.id,
        actor_user_id=owner.id,
        trust_level=TrustLevel.CHANNEL_AUTHENTICATED,
        trust_source=TrustSource.REPORTER,
    )
    return owner, namespace, runtime, credential, identity


def _descriptor(
    *,
    profile: AdapterProfile,
    capabilities: list[str],
    nonce: str,
    claimed: str,
    instance_id: str = "fleet-instance",
    boot_id: str = "fleet-boot",
    adapter_version: str = "1.0.0",
    config_fingerprint: str | None = "a" * 64,
) -> AdapterDescriptor:
    return AdapterDescriptor.model_validate(
        {
            "adapter_id": profile.value,
            "adapter_version": adapter_version,
            "profile": profile.value,
            "source_schema": "agent-run",
            "source_schema_version": "2026-07",
            "capabilities": capabilities,
            "content_capture_modes": ["metadata_only"],
            "instance_id": instance_id,
            "boot_id": boot_id,
            "client_nonce": nonce,
            "client_time": datetime.now(timezone.utc).isoformat(),
            "claimed_capability_level": claimed,
            "config_fingerprint": config_fingerprint,
        }
    )


@asynccontextmanager
async def _client(db, identity):
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v2")

    async def override_db():
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    async def override_identity():
        return identity

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[
        get_reporter_execution_identity
    ] = override_identity
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        yield client


async def test_conf_c0_openclaw_negotiates_dynamic_c3_without_internal_ids(
    async_session,
) -> None:
    _, _, runtime, _, identity = await _seed(async_session)
    descriptor = _descriptor(
        profile=AdapterProfile.OPENCLAW_REPORTER,
        capabilities=[
            "session_control",
            "run_control",
            "otel_trace_correlation",
            "durable_replay",
            "vendor_unknown",
        ],
        nonce=_nonce("A"),
        claimed="DD-C3",
    )
    result = await negotiate_adapter_handshake(
        async_session,
        identity=identity,
        descriptor=descriptor,
    )

    assert result.conflict is False
    assert result.response.runtime_public_id == runtime.public_id
    assert result.response.runtime_public_id.startswith("rt_")
    assert str(runtime.id) != result.response.runtime_public_id
    assert result.response.certified_capability_level == "DD-C3"
    assert result.response.rejected_capabilities == ["vendor_unknown"]
    assert "namespace_id" not in result.response.model_dump()
    assert "runtime_id" not in result.response.model_dump()
    assert runtime.capabilities is not None
    assert (
        await async_session.scalar(
            select(func.count(RuntimeCapabilitySnapshot.id))
        )
        == 1
    )


async def test_native_hermes_profile_certifies_metadata_only_c1(
    async_session,
) -> None:
    _, _, runtime, _, identity = await _seed(async_session, "hermes")
    result = await negotiate_adapter_handshake(
        async_session,
        identity=identity,
        descriptor=_descriptor(
            profile=AdapterProfile.HERMES_REPORTER,
            capabilities=["session_control", "run_control"],
            nonce=_nonce("R"),
            claimed="DD-C3",
            instance_id="hermes-runtime",
            adapter_version="hermes-pilot-0.2.0",
        ),
    )

    assert result.response.runtime_public_id == runtime.public_id
    assert result.response.profile == AdapterProfile.HERMES_REPORTER
    assert result.response.certified_capability_level == "DD-C1"
    assert result.response.accepted_capabilities == [
        "run_control",
        "session_control",
    ]
    assert result.response.rejected_capabilities == []


def test_conf_c0_rejects_unsupported_protocol_and_forged_identity() -> None:
    base = _descriptor(
        profile=AdapterProfile.OPENCLAW_REPORTER,
        capabilities=["session_control"],
        nonce=_nonce("I"),
        claimed="DD-C0",
    ).model_dump(mode="json")
    with pytest.raises(ValidationError):
        AdapterDescriptor.model_validate(
            {**base, "protocol_version": "9.9"}
        )
    with pytest.raises(ValidationError):
        AdapterDescriptor.model_validate(
            {**base, "namespace_id": 999, "runtime_id": 999}
        )


async def test_conf_c0_nonce_replay_is_idempotent_and_changed_descriptor_conflicts(
    async_session,
) -> None:
    _, _, _, _, identity = await _seed(async_session)
    descriptor = _descriptor(
        profile=AdapterProfile.OPENCLAW_REPORTER,
        capabilities=["session_control", "run_control"],
        nonce=_nonce("B"),
        claimed="DD-C1",
    )
    first = await negotiate_adapter_handshake(
        async_session,
        identity=identity,
        descriptor=descriptor,
    )
    replay = await negotiate_adapter_handshake(
        async_session,
        identity=identity,
        descriptor=descriptor,
    )
    conflict = await negotiate_adapter_handshake(
        async_session,
        identity=identity,
        descriptor=descriptor.model_copy(
            update={"adapter_version": "2.0.0"}
        ),
    )

    assert replay.replayed is True
    assert replay.handshake.id == first.handshake.id
    assert conflict.conflict is True
    assert conflict.handshake.status == AdapterHandshakeStatus.DEGRADED
    assert conflict.handshake.config_drift == (
        AdapterConfigDrift.CAPABILITY_CHANGED
    )
    assert (
        await async_session.scalar(
            select(func.count(AdapterHandshake.id))
        )
        == 1
    )
    assert (
        await async_session.scalar(
            select(func.count(RuntimeCapabilitySnapshot.id))
        )
        == 1
    )


async def test_generic_capability_requires_registered_live_sink(
    async_session,
) -> None:
    _, namespace, _, _, identity = await _seed(async_session)
    requested = ["otel_trace_correlation", "metadata_projection"]
    no_sink = await negotiate_adapter_handshake(
        async_session,
        identity=identity,
        descriptor=_descriptor(
            profile=AdapterProfile.GENERIC_OTLP_BRIDGE,
            capabilities=requested,
            nonce=_nonce("C"),
            claimed="DD-C2",
        ),
    )
    assert no_sink.response.accepted_capabilities == []
    assert no_sink.response.rejected_capabilities == sorted(requested)
    assert no_sink.response.certified_capability_level == "DD-C0"
    assert no_sink.handshake.status == AdapterHandshakeStatus.DEGRADED

    async_session.add(
        TelemetrySink(
            public_id="tsk_" + "c" * 32,
            namespace_id=namespace.id,
            provider="custom",
            name="fleet-generic",
            endpoint="https://telemetry.example.test",
            credential_ref="secret://fleet/generic",
            status=TelemetrySinkStatus.ACTIVE,
        )
    )
    await async_session.flush()
    with_sink = await negotiate_adapter_handshake(
        async_session,
        identity=identity,
        descriptor=_descriptor(
            profile=AdapterProfile.GENERIC_OTLP_BRIDGE,
            capabilities=requested,
            nonce=_nonce("D"),
            claimed="DD-C2",
        ),
    )
    assert with_sink.response.accepted_capabilities == sorted(requested)
    assert with_sink.response.certified_capability_level == "DD-C2"
    assert no_sink.handshake.status == AdapterHandshakeStatus.SUPERSEDED


async def test_pack_profile_certifies_ddc3_only_for_complete_transfer_loop(
    async_session,
) -> None:
    _, _, _, _, identity = await _seed(async_session)
    result = await negotiate_adapter_handshake(
        async_session,
        identity=identity,
        descriptor=_descriptor(
            profile=AdapterProfile.PACK_ATIF_IMPORT,
            capabilities=[
                "artifact_import",
                "partial_loss",
                "atif_export",
                "resumable_upload",
                "durable_batch_ack",
                "evaluation_replay",
                "otel_trace_correlation",
            ],
            nonce=_nonce("E"),
            claimed="DD-C3",
        ),
    )

    assert result.response.certified_capability_level == "DD-C3"
    assert result.response.accepted_capabilities == [
        "artifact_import",
        "atif_export",
        "durable_batch_ack",
        "evaluation_replay",
        "otel_trace_correlation",
        "partial_loss",
        "resumable_upload",
    ]
    assert result.response.rejected_capabilities == []


async def test_heartbeat_detects_boot_config_and_capability_drift(
    async_session,
) -> None:
    _, namespace, _, _, identity = await _seed(async_session)
    descriptor = _descriptor(
        profile=AdapterProfile.OPENCLAW_REPORTER,
        capabilities=["session_control", "run_control"],
        nonce=_nonce("F"),
        claimed="DD-C1",
    )
    result = await negotiate_adapter_handshake(
        async_session,
        identity=identity,
        descriptor=descriptor,
    )
    healthy = await record_adapter_heartbeat(
        async_session,
        identity=identity,
        heartbeat=AdapterHeartbeat(
            handshake_id=result.handshake.public_id,
            boot_id=descriptor.boot_id,
            status="ok",
            accepted_capabilities=["run_control", "session_control"],
            config_fingerprint=descriptor.config_fingerprint,
            collector_status="not_applicable",
        ),
    )
    assert healthy.status == "ACTIVE"
    assert healthy.config_drift == AdapterConfigDrift.NONE

    drifted = await record_adapter_heartbeat(
        async_session,
        identity=identity,
        heartbeat=AdapterHeartbeat(
            handshake_id=result.handshake.public_id,
            boot_id="new-boot",
            status="ok",
            accepted_capabilities=["run_control", "session_control"],
            config_fingerprint=descriptor.config_fingerprint,
        ),
    )
    assert drifted.status == "DEGRADED"
    assert drifted.config_drift == AdapterConfigDrift.BOOT_CHANGED
    assert drifted.rehandshake_required is True

    fleet = await get_fleet_summary(
        async_session,
        namespace_id=namespace.id,
    )
    assert fleet.runtime_count == 1
    assert fleet.drifted_count == 1
    assert fleet.runtimes[0].heartbeat_state == "HEALTHY"
    assert fleet.runtimes[0].config_drift == AdapterConfigDrift.BOOT_CHANGED
    assert [
        item.config_drift
        for item in fleet.runtimes[0].heartbeat_history
    ] == [
        AdapterConfigDrift.BOOT_CHANGED,
        AdapterConfigDrift.NONE,
    ]
    assert (
        await async_session.scalar(
            select(func.count(AdapterHeartbeatRecord.id))
        )
        == 2
    )


async def test_fleet_never_reports_unhandshaken_runtime_as_available(
    async_session,
) -> None:
    _, namespace, runtime, _, _ = await _seed(async_session)
    fleet = await get_fleet_summary(
        async_session,
        namespace_id=namespace.id,
    )

    row = fleet.runtimes[0]
    assert row.runtime_public_id == runtime.public_id
    assert row.handshake_status == "NONE"
    assert row.heartbeat_state == "NEVER"
    assert row.certified_capability_level is None
    assert row.accepted_capabilities == []


async def test_expired_handshake_requires_renegotiation(
    async_session,
) -> None:
    _, _, _, _, identity = await _seed(async_session)
    descriptor = _descriptor(
        profile=AdapterProfile.OPENCLAW_REPORTER,
        capabilities=["session_control", "run_control"],
        nonce=_nonce("G"),
        claimed="DD-C1",
    )
    result = await negotiate_adapter_handshake(
        async_session,
        identity=identity,
        descriptor=descriptor,
    )
    result.handshake.handshaken_at = (
        datetime.now(timezone.utc) - timedelta(days=2)
    )
    result.handshake.expires_at = (
        datetime.now(timezone.utc) - timedelta(days=1)
    )
    heartbeat = await record_adapter_heartbeat(
        async_session,
        identity=identity,
        heartbeat=AdapterHeartbeat(
            handshake_id=result.handshake.public_id,
            boot_id=descriptor.boot_id,
            accepted_capabilities=["session_control", "run_control"],
        ),
    )
    assert heartbeat.status == "EXPIRED"
    assert heartbeat.rehandshake_required is True
    reused_expired = await negotiate_adapter_handshake(
        async_session,
        identity=identity,
        descriptor=descriptor,
    )
    assert reused_expired.conflict is True
    assert reused_expired.handshake.status == (
        AdapterHandshakeStatus.EXPIRED
    )
    replacement = await negotiate_adapter_handshake(
        async_session,
        identity=identity,
        descriptor=descriptor.model_copy(
            update={"client_nonce": _nonce("J")}
        ),
    )
    assert replacement.handshake.public_id != result.handshake.public_id
    assert replacement.handshake.status == AdapterHandshakeStatus.ACTIVE


async def test_generic_legacy_adapter_no_longer_claims_false_ok(
    async_session,
) -> None:
    _, _, runtime, _, _ = await _seed(async_session)
    result = await GenericRuntimeAdapter(runtime).test_connection()
    assert result.status == "degraded"
    assert result.details["reason_code"] == "dynamic_handshake_required"
    assert "base_url" not in result.details


async def test_openclaw_legacy_adapter_requires_real_ingestion_channel(
    async_session,
) -> None:
    _, _, runtime, _, _ = await _seed(async_session, "openclaw-legacy")
    runtime.provider = RuntimeProvider.OPENCLAW
    runtime.metadata_json = {
        "sample_collection": {"assets": [{"id": "must-not-ingest"}]},
        "backup_payload": {"assets": [{"id": "must-not-ingest-either"}]},
    }
    adapter = OpenClawAdapter(runtime)

    connection = await adapter.test_connection()
    capabilities = await adapter.list_capabilities()

    assert connection.status == "degraded"
    assert connection.details == {"reason_code": "dynamic_handshake_required"}
    assert capabilities.asset_sync == "reporter_required"
    assert capabilities.backup_import is True
    assert capabilities.backup_create == "provider_export_required"
    with pytest.raises(RuntimeError, match="Pull collection is not supported"):
        await adapter.collect(None)  # type: ignore[arg-type]


async def test_handshake_and_heartbeat_api_persist_safe_conflict(
    async_session,
) -> None:
    _, _, runtime, _, identity = await _seed(async_session)
    descriptor = _descriptor(
        profile=AdapterProfile.OPENCLAW_REPORTER,
        capabilities=["session_control", "run_control"],
        nonce=_nonce("H"),
        claimed="DD-C1",
    )
    body = descriptor.model_dump(mode="json")
    async with _client(async_session, identity) as client:
        created = await client.post(
            "/api/v2/reporter/handshakes",
            json=body,
        )
        assert created.status_code == 201
        response = created.json()
        assert response["runtime_public_id"] == runtime.public_id
        assert "runtime_id" not in response
        assert "namespace_id" not in response

        heartbeat = await client.post(
            "/api/v2/reporter/heartbeats",
            json={
                "handshake_id": response["handshake_id"],
                "boot_id": descriptor.boot_id,
                "status": "ok",
                "accepted_capabilities": [
                    "session_control",
                    "run_control",
                ],
                "config_fingerprint": "a" * 64,
                "collector_status": "not_applicable",
            },
        )
        assert heartbeat.status_code == 200
        assert heartbeat.json()["status"] == "ACTIVE"

        changed = dict(body)
        changed["adapter_version"] = "2.0.0"
        conflict = await client.post(
            "/api/v2/reporter/handshakes",
            json=changed,
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"

    row = (
        await async_session.execute(select(AdapterHandshake))
    ).scalar_one()
    assert row.status == AdapterHandshakeStatus.DEGRADED
    assert row.config_drift == AdapterConfigDrift.CAPABILITY_CHANGED


@pytest.mark.mysql
async def test_fleet_history_projection_runs_on_real_mysql(
    async_session_mysql,
) -> None:
    _, namespace, _, _, identity = await _seed(
        async_session_mysql,
        "real-mysql",
    )
    descriptor = _descriptor(
        profile=AdapterProfile.OPENCLAW_REPORTER,
        capabilities=["session_control", "run_control"],
        nonce=_nonce("M"),
        claimed="DD-C1",
    )
    handshake = await negotiate_adapter_handshake(
        async_session_mysql,
        identity=identity,
        descriptor=descriptor,
    )
    await record_adapter_heartbeat(
        async_session_mysql,
        identity=identity,
        heartbeat=AdapterHeartbeat(
            handshake_id=handshake.handshake.public_id,
            boot_id=descriptor.boot_id,
            accepted_capabilities=[
                "session_control",
                "run_control",
            ],
            collector_status="not_applicable",
        ),
    )

    fleet = await get_fleet_summary(
        async_session_mysql,
        namespace_id=namespace.id,
    )
    assert fleet.healthy_count == 1
    assert len(fleet.runtimes[0].heartbeat_history) == 1
