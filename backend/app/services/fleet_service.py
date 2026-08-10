"""Dynamic adapter negotiation, heartbeat drift and Fleet read model."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.control_plane import (
    ReporterCredential,
    RuntimeCapabilitySnapshot,
    RuntimeInstance,
    RuntimeStatus,
)
from app.models.execution import AgentRun
from app.models.fleet import (
    AdapterConfigDrift,
    AdapterHandshake,
    AdapterHandshakeStatus,
    AdapterHeartbeatRecord,
    AdapterProfile,
)
from app.models.telemetry import (
    GenericTraceProjection,
    GenericTraceProjectionStatus,
    PackImport,
    PackImportStatus,
    TelemetrySink,
    TelemetrySinkStatus,
)
from app.schemas.fleet import (
    AdapterDescriptor,
    AdapterHandshakeOut,
    AdapterHeartbeat,
    AdapterHeartbeatOut,
    FleetHeartbeatOut,
    FleetRuntimeOut,
    FleetSummaryOut,
)
from app.services.artifact_service import artifact_service
from app.services.audit_service import audit
from app.services.envelope_canonicalization_service import canonical_sha256
from app.services.reporter_identity_service import ReporterExecutionIdentity


_PROFILE_CAPABILITIES: dict[AdapterProfile, set[str]] = {
    AdapterProfile.OPENCLAW_REPORTER: {
        "session_control",
        "run_control",
        "otel_trace_correlation",
        "durable_replay",
    },
    AdapterProfile.HERMES_REPORTER: {
        "session_control",
        "run_control",
        "otel_trace_correlation",
        "durable_replay",
    },
    AdapterProfile.GENERIC_OTLP_BRIDGE: {
        "otel_trace_correlation",
        "metadata_projection",
    },
    AdapterProfile.PACK_ATIF_IMPORT: {
        "artifact_import",
        "partial_loss",
        "atif_export",
        "resumable_upload",
        "durable_batch_ack",
        "evaluation_replay",
        "otel_trace_correlation",
    },
}
_LEVEL_RANK = {
    "DD-C0": 0,
    "DD-C1": 1,
    "DD-C2": 2,
    "DD-C3": 3,
}


@dataclass(frozen=True, slots=True)
class AdapterHandshakeResult:
    handshake: AdapterHandshake
    response: AdapterHandshakeOut
    replayed: bool = False
    conflict: bool = False


class FleetError(ValueError):
    pass


class FleetNotFoundError(FleetError):
    pass


class FleetHandshakeRequiredError(FleetError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _handshake_public_id() -> str:
    return f"hs_{uuid.uuid4().hex}"


async def require_active_adapter_handshake(
    db: AsyncSession,
    *,
    identity: ReporterExecutionIdentity,
    handshake_public_id: str,
    profile: AdapterProfile,
    required_capabilities: set[str],
) -> AdapterHandshake:
    """Bind a data-plane write to the exact negotiated credential/runtime."""

    handshake = (
        await db.execute(
            select(AdapterHandshake).where(
                AdapterHandshake.public_id == handshake_public_id,
                AdapterHandshake.namespace_id == identity.namespace_id,
                AdapterHandshake.runtime_id == identity.runtime_id,
                AdapterHandshake.reporter_credential_id
                == identity.credential_id,
                AdapterHandshake.profile == profile,
            )
        )
    ).scalar_one_or_none()
    if handshake is None:
        raise FleetHandshakeRequiredError(
            "adapter_handshake_not_found"
        )
    now = _now()
    if _utc(handshake.expires_at) <= now:
        handshake.status = AdapterHandshakeStatus.EXPIRED
        raise FleetHandshakeRequiredError("adapter_handshake_expired")
    if handshake.status != AdapterHandshakeStatus.ACTIVE:
        raise FleetHandshakeRequiredError(
            "adapter_handshake_not_active"
        )
    if not required_capabilities <= set(
        handshake.accepted_capabilities_json
    ):
        raise FleetHandshakeRequiredError(
            "adapter_capability_not_negotiated"
        )
    return handshake


async def _available_capabilities(
    db: AsyncSession,
    *,
    identity: ReporterExecutionIdentity,
    profile: AdapterProfile,
) -> set[str]:
    available = set(_PROFILE_CAPABILITIES[profile])
    if profile == AdapterProfile.GENERIC_OTLP_BRIDGE:
        active_sink = await db.scalar(
            select(func.count(TelemetrySink.id)).where(
                TelemetrySink.namespace_id == identity.namespace_id,
                TelemetrySink.status == TelemetrySinkStatus.ACTIVE,
            )
        )
        if not active_sink:
            available.clear()
    if (
        profile == AdapterProfile.PACK_ATIF_IMPORT
        and not artifact_service.is_configured()
    ):
        available.clear()
    return available


def _certified_level(
    *,
    profile: AdapterProfile,
    claimed: str,
    accepted: set[str],
) -> str:
    maximum = "DD-C0"
    if profile in {
        AdapterProfile.OPENCLAW_REPORTER,
        AdapterProfile.HERMES_REPORTER,
    }:
        if {"session_control", "run_control"} <= accepted:
            maximum = "DD-C1"
        if (
            maximum == "DD-C1"
            and "otel_trace_correlation" in accepted
        ):
            maximum = "DD-C2"
        if maximum == "DD-C2" and "durable_replay" in accepted:
            maximum = "DD-C3"
    elif profile == AdapterProfile.GENERIC_OTLP_BRIDGE:
        if {
            "otel_trace_correlation",
            "metadata_projection",
        } <= accepted:
            maximum = "DD-C2"
    elif (
        profile == AdapterProfile.PACK_ATIF_IMPORT
        and "artifact_import" in accepted
    ):
        maximum = (
            "DD-C3"
            if {
                "artifact_import",
                "partial_loss",
                "atif_export",
                "resumable_upload",
                "durable_batch_ack",
                "evaluation_replay",
                "otel_trace_correlation",
            }
            <= accepted
            else "DD-C1+ARTIFACT"
        )

    comparable = maximum.removesuffix("+ARTIFACT")
    if _LEVEL_RANK[claimed] < _LEVEL_RANK[comparable]:
        return claimed
    return maximum


def _handshake_response(
    handshake: AdapterHandshake,
    runtime: RuntimeInstance,
    *,
    clock_skew_degraded: bool,
    server_time: datetime,
) -> AdapterHandshakeOut:
    return AdapterHandshakeOut(
        handshake_id=handshake.public_id,
        protocol_version="1.0",
        runtime_public_id=runtime.public_id,
        profile=handshake.profile,
        accepted_capabilities=list(
            handshake.accepted_capabilities_json
        ),
        rejected_capabilities=list(
            handshake.rejected_capabilities_json
        ),
        claimed_capability_level=handshake.claimed_capability_level,
        certified_capability_level=(
            handshake.claimed_capability_level
            if handshake.status == AdapterHandshakeStatus.DEGRADED
            and handshake.config_drift
            == AdapterConfigDrift.CAPABILITY_CHANGED
            else _certified_level(
                profile=handshake.profile,
                claimed=handshake.claimed_capability_level,
                accepted=set(handshake.accepted_capabilities_json),
            )
        ),
        content_capture_mode="metadata_only",
        clock_skew_degraded=clock_skew_degraded,
        limits={
            "max_envelope_bytes": 65_536,
            "max_metadata_keys": 32,
            "max_metadata_depth": 1,
            "max_metadata_value_bytes": 2_000,
            "max_clock_skew_seconds": (
                settings.ADAPTER_MAX_CLOCK_SKEW_SECONDS
            ),
        },
        idempotency={
            "canonicalizer_version": "duckdock-canonical-json-v1",
            "minimum_replay_window_seconds": (
                settings.ADAPTER_HANDSHAKE_REPLAY_WINDOW_SECONDS
            ),
        },
        server_time=server_time,
        expires_at=handshake.expires_at,
    )


async def negotiate_adapter_handshake(
    db: AsyncSession,
    *,
    identity: ReporterExecutionIdentity,
    descriptor: AdapterDescriptor,
) -> AdapterHandshakeResult:
    now = _now()
    descriptor_sha256 = canonical_sha256(descriptor)
    nonce_sha256 = hashlib.sha256(
        descriptor.client_nonce.encode("ascii")
    ).hexdigest()
    runtime = await db.get(RuntimeInstance, identity.runtime_id)
    if (
        runtime is None
        or runtime.namespace_id != identity.namespace_id
    ):
        raise FleetNotFoundError("Runtime not found")
    credential = await db.get(
        ReporterCredential,
        identity.credential_id,
    )
    if (
        credential is None
        or credential.runtime_id != runtime.id
        or not credential.is_active
        or credential.revoked_at is not None
    ):
        raise FleetNotFoundError("Reporter credential not found")

    existing = (
        await db.execute(
            select(AdapterHandshake)
            .where(
                AdapterHandshake.reporter_credential_id
                == credential.id,
                AdapterHandshake.instance_id
                == descriptor.instance_id,
                AdapterHandshake.client_nonce_sha256 == nonce_sha256,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    clock_skew_degraded = (
        abs((_utc(descriptor.client_time) - now).total_seconds())
        > settings.ADAPTER_MAX_CLOCK_SKEW_SECONDS
    )
    if existing is not None:
        if _utc(existing.expires_at) <= now:
            existing.status = AdapterHandshakeStatus.EXPIRED
            await audit(
                db,
                user_id=identity.actor_user_id,
                action="adapter_handshake.expired_nonce_reused",
                resource_type="adapter_handshake",
                resource_id=existing.id,
                namespace_id=identity.namespace_id,
                details={
                    "handshake_id": existing.public_id,
                    "reason_code": "expired_nonce_reuse",
                },
            )
            return AdapterHandshakeResult(
                handshake=existing,
                response=_handshake_response(
                    existing,
                    runtime,
                    clock_skew_degraded=clock_skew_degraded,
                    server_time=now,
                ),
                conflict=True,
            )
        if existing.descriptor_sha256 != descriptor_sha256:
            existing.status = AdapterHandshakeStatus.DEGRADED
            existing.config_drift = (
                AdapterConfigDrift.CAPABILITY_CHANGED
            )
            await audit(
                db,
                user_id=identity.actor_user_id,
                action="adapter_handshake.idempotency_conflict",
                resource_type="adapter_handshake",
                resource_id=existing.id,
                namespace_id=identity.namespace_id,
                details={
                    "handshake_id": existing.public_id,
                    "reason_code": "nonce_descriptor_conflict",
                },
            )
            return AdapterHandshakeResult(
                handshake=existing,
                response=_handshake_response(
                    existing,
                    runtime,
                    clock_skew_degraded=clock_skew_degraded,
                    server_time=now,
                ),
                conflict=True,
            )
        return AdapterHandshakeResult(
            handshake=existing,
            response=_handshake_response(
                existing,
                runtime,
                clock_skew_degraded=clock_skew_degraded,
                server_time=now,
            ),
            replayed=True,
        )

    available = await _available_capabilities(
        db,
        identity=identity,
        profile=descriptor.profile,
    )
    requested = set(descriptor.capabilities)
    accepted = sorted(requested & available)
    rejected = sorted(requested - available)
    if clock_skew_degraded or not accepted:
        status = AdapterHandshakeStatus.DEGRADED
    else:
        status = AdapterHandshakeStatus.ACTIVE

    previous = list(
        (
            await db.execute(
                select(AdapterHandshake).where(
                    AdapterHandshake.reporter_credential_id
                    == credential.id,
                    AdapterHandshake.instance_id
                    == descriptor.instance_id,
                    AdapterHandshake.profile == descriptor.profile,
                    AdapterHandshake.status.in_(
                        [
                            AdapterHandshakeStatus.ACTIVE,
                            AdapterHandshakeStatus.DEGRADED,
                        ]
                    ),
                )
            )
        ).scalars()
    )
    for row in previous:
        row.status = AdapterHandshakeStatus.SUPERSEDED

    handshake = AdapterHandshake(
        public_id=_handshake_public_id(),
        namespace_id=identity.namespace_id,
        runtime_id=identity.runtime_id,
        reporter_credential_id=identity.credential_id,
        profile=descriptor.profile,
        adapter_id=descriptor.adapter_id,
        adapter_version=descriptor.adapter_version,
        protocol_version=descriptor.protocol_version,
        source_schema=descriptor.source_schema,
        source_schema_version=descriptor.source_schema_version,
        instance_id=descriptor.instance_id,
        boot_id=descriptor.boot_id,
        client_nonce_sha256=nonce_sha256,
        descriptor_sha256=descriptor_sha256,
        config_fingerprint=descriptor.config_fingerprint,
        claimed_capability_level=descriptor.claimed_capability_level,
        accepted_capabilities_json=accepted,
        rejected_capabilities_json=rejected,
        content_capture_mode="metadata_only",
        status=status,
        config_drift=AdapterConfigDrift.NONE,
        client_time=_utc(descriptor.client_time),
        handshaken_at=now,
        expires_at=now
        + timedelta(seconds=settings.ADAPTER_HANDSHAKE_TTL_SECONDS),
    )
    db.add(handshake)
    await db.flush()
    level = _certified_level(
        profile=descriptor.profile,
        claimed=descriptor.claimed_capability_level,
        accepted=set(accepted),
    )
    snapshot = RuntimeCapabilitySnapshot(
        runtime_id=runtime.id,
        provider=runtime.provider,
        adapter_name=descriptor.adapter_id,
        status=status.value.lower(),
        source="handshake",
        version=descriptor.adapter_version,
        capabilities_json={
            "handshake_id": handshake.public_id,
            "profile": descriptor.profile.value,
            "accepted_capabilities": accepted,
            "rejected_capabilities": rejected,
            "certified_capability_level": level,
            "content_capture_mode": "metadata_only",
        },
        collected_at=now,
    )
    db.add(snapshot)
    runtime.capabilities = snapshot.capabilities_json
    runtime.last_sync_at = now
    await audit(
        db,
        user_id=identity.actor_user_id,
        action="adapter_handshake.negotiated",
        resource_type="adapter_handshake",
        resource_id=handshake.id,
        namespace_id=identity.namespace_id,
        details={
            "handshake_id": handshake.public_id,
            "runtime_public_id": runtime.public_id,
            "profile": descriptor.profile.value,
            "adapter_id": descriptor.adapter_id,
            "adapter_version": descriptor.adapter_version,
            "accepted_capabilities": accepted,
            "rejected_capabilities": rejected,
            "certified_capability_level": level,
            "clock_skew_degraded": clock_skew_degraded,
        },
    )
    return AdapterHandshakeResult(
        handshake=handshake,
        response=_handshake_response(
            handshake,
            runtime,
            clock_skew_degraded=clock_skew_degraded,
            server_time=now,
        ),
    )


async def record_adapter_heartbeat(
    db: AsyncSession,
    *,
    identity: ReporterExecutionIdentity,
    heartbeat: AdapterHeartbeat,
) -> AdapterHeartbeatOut:
    row = (
        await db.execute(
            select(AdapterHandshake)
            .where(
                AdapterHandshake.public_id == heartbeat.handshake_id,
                AdapterHandshake.namespace_id == identity.namespace_id,
                AdapterHandshake.runtime_id == identity.runtime_id,
                AdapterHandshake.reporter_credential_id
                == identity.credential_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise FleetNotFoundError("Adapter handshake not found")
    now = _now()
    if _utc(row.expires_at) <= now:
        row.status = AdapterHandshakeStatus.EXPIRED
        db.add(
            AdapterHeartbeatRecord(
                adapter_handshake_id=row.id,
                status=AdapterHandshakeStatus.EXPIRED.value,
                config_drift=row.config_drift,
                collector_status=heartbeat.collector_status,
                collector_version=heartbeat.collector_version,
                observed_at=now,
            )
        )
        await db.flush()
        return AdapterHeartbeatOut(
            handshake_id=row.public_id,
            status="EXPIRED",
            config_drift=row.config_drift,
            rehandshake_required=True,
            last_heartbeat_at=now,
            expires_at=row.expires_at,
        )

    drift = AdapterConfigDrift.NONE
    if heartbeat.boot_id != row.boot_id:
        drift = AdapterConfigDrift.BOOT_CHANGED
    elif sorted(heartbeat.accepted_capabilities) != sorted(
        row.accepted_capabilities_json
    ):
        drift = AdapterConfigDrift.CAPABILITY_CHANGED
    elif (
        heartbeat.config_fingerprint is not None
        and row.config_fingerprint is not None
        and heartbeat.config_fingerprint != row.config_fingerprint
    ):
        drift = AdapterConfigDrift.CONFIG_CHANGED

    collector_degraded = (
        row.profile == AdapterProfile.GENERIC_OTLP_BRIDGE
        and heartbeat.collector_status != "healthy"
    )
    row.config_drift = drift
    row.heartbeat_status = heartbeat.status
    row.collector_status = heartbeat.collector_status
    row.collector_version = heartbeat.collector_version
    row.last_heartbeat_at = now
    row.status = (
        AdapterHandshakeStatus.DEGRADED
        if (
            heartbeat.status != "ok"
            or drift != AdapterConfigDrift.NONE
            or collector_degraded
        )
        else AdapterHandshakeStatus.ACTIVE
    )
    db.add(
        AdapterHeartbeatRecord(
            adapter_handshake_id=row.id,
            status=row.status.value,
            config_drift=drift,
            collector_status=heartbeat.collector_status,
            collector_version=heartbeat.collector_version,
            observed_at=now,
        )
    )
    credential = await db.get(
        ReporterCredential,
        identity.credential_id,
    )
    if credential is not None:
        credential.last_heartbeat_at = now
        credential.heartbeat_json = {
            "handshake_id": row.public_id,
            "status": heartbeat.status,
            "config_drift": drift.value,
            "collector_status": heartbeat.collector_status,
            "collector_version": heartbeat.collector_version,
            "last_seen_at": now.isoformat(),
        }
    runtime = await db.get(RuntimeInstance, identity.runtime_id)
    if runtime is not None:
        runtime.status = (
            RuntimeStatus.ACTIVE
            if row.status == AdapterHandshakeStatus.ACTIVE
            else RuntimeStatus.DEGRADED
        )
    await db.flush()
    return AdapterHeartbeatOut(
        handshake_id=row.public_id,
        status=row.status.value,  # type: ignore[arg-type]
        config_drift=drift,
        rehandshake_required=drift != AdapterConfigDrift.NONE,
        last_heartbeat_at=now,
        expires_at=row.expires_at,
    )


async def get_fleet_summary(
    db: AsyncSession,
    *,
    namespace_id: int,
) -> FleetSummaryOut:
    runtimes = list(
        (
            await db.execute(
                select(RuntimeInstance)
                .where(RuntimeInstance.namespace_id == namespace_id)
                .order_by(RuntimeInstance.created_at.desc())
                .limit(200)
            )
        ).scalars()
    )
    if not runtimes:
        return FleetSummaryOut(
            namespace_id=namespace_id,
            runtime_count=0,
            healthy_count=0,
            stale_count=0,
            degraded_count=0,
            drifted_count=0,
            runtimes=[],
        )
    runtime_ids = [runtime.id for runtime in runtimes]
    handshake_rows = list(
        (
            await db.execute(
                select(AdapterHandshake)
                .where(AdapterHandshake.runtime_id.in_(runtime_ids))
                .order_by(
                    AdapterHandshake.runtime_id,
                    AdapterHandshake.handshaken_at.desc(),
                    AdapterHandshake.id.desc(),
                )
            )
        ).scalars()
    )
    handshakes: dict[int, AdapterHandshake] = {}
    for row in handshake_rows:
        handshakes.setdefault(row.runtime_id, row)
    handshake_ids = [row.id for row in handshakes.values()]
    heartbeat_history_by_handshake: dict[
        int,
        list[AdapterHeartbeatRecord],
    ] = {}
    if handshake_ids:
        ranked_heartbeats = (
            select(
                AdapterHeartbeatRecord.id.label("heartbeat_id"),
                func.row_number()
                .over(
                    partition_by=(
                        AdapterHeartbeatRecord.adapter_handshake_id
                    ),
                    order_by=(
                        AdapterHeartbeatRecord.observed_at.desc(),
                        AdapterHeartbeatRecord.id.desc(),
                    ),
                )
                .label("history_rank"),
            )
            .where(
                AdapterHeartbeatRecord.adapter_handshake_id.in_(
                    handshake_ids
                )
            )
            .subquery()
        )
        heartbeat_rows = list(
            (
                await db.execute(
                    select(AdapterHeartbeatRecord)
                    .join(
                        ranked_heartbeats,
                        ranked_heartbeats.c.heartbeat_id
                        == AdapterHeartbeatRecord.id,
                    )
                    .where(ranked_heartbeats.c.history_rank <= 20)
                    .order_by(
                        AdapterHeartbeatRecord.adapter_handshake_id,
                        AdapterHeartbeatRecord.observed_at.desc(),
                        AdapterHeartbeatRecord.id.desc(),
                    )
                )
            ).scalars()
        )
        for heartbeat_record in heartbeat_rows:
            heartbeat_history_by_handshake.setdefault(
                heartbeat_record.adapter_handshake_id,
                [],
            ).append(heartbeat_record)
    sink_counts = (
        await db.execute(
            select(
                func.count(TelemetrySink.id),
                func.sum(
                    TelemetrySink.status == TelemetrySinkStatus.ACTIVE
                ),
            ).where(TelemetrySink.namespace_id == namespace_id)
        )
    ).one()
    latest_run_by_runtime = {
        runtime_id: started_at
        for runtime_id, started_at in (
            await db.execute(
                select(
                    AgentRun.runtime_id,
                    func.max(AgentRun.started_at),
                )
                .where(AgentRun.runtime_id.in_(runtime_ids))
                .group_by(AgentRun.runtime_id)
            )
        ).all()
    }
    pack_counts: dict[int, dict[PackImportStatus, int]] = {}
    for runtime_id, pack_status, count in (
        await db.execute(
            select(
                PackImport.runtime_id,
                PackImport.status,
                func.count(PackImport.id),
            )
            .where(PackImport.runtime_id.in_(runtime_ids))
            .group_by(PackImport.runtime_id, PackImport.status)
        )
    ).all():
        pack_counts.setdefault(runtime_id, {})[pack_status] = int(count)
    quarantined_trace_by_runtime = {
        runtime_id: int(count)
        for runtime_id, count in (
            await db.execute(
                select(
                    GenericTraceProjection.runtime_id,
                    func.count(GenericTraceProjection.id),
                )
                .where(
                    GenericTraceProjection.runtime_id.in_(runtime_ids),
                    GenericTraceProjection.status
                    == GenericTraceProjectionStatus.QUARANTINED,
                )
                .group_by(GenericTraceProjection.runtime_id)
            )
        ).all()
    }
    now = _now()
    projections: list[FleetRuntimeOut] = []
    for runtime in runtimes:
        handshake = handshakes.get(runtime.id)
        latest_run_at = latest_run_by_runtime.get(runtime.id)
        runtime_pack_counts = pack_counts.get(runtime.id, {})
        pending_pack_count = (
            runtime_pack_counts.get(
                PackImportStatus.PENDING_VALIDATION,
                0,
            )
            + runtime_pack_counts.get(PackImportStatus.VALIDATING, 0)
        )
        quarantined_packs = runtime_pack_counts.get(
            PackImportStatus.QUARANTINED,
            0,
        )
        quarantined_traces = quarantined_trace_by_runtime.get(
            runtime.id,
            0,
        )

        if handshake is None:
            handshake_status = "NONE"
            heartbeat_state = "NEVER"
            profile = None
            adapter_id = None
            adapter_version = None
            accepted: list[str] = []
            rejected: list[str] = []
            level = None
            last_heartbeat_at = None
            expires_at = None
            drift = None
            collector_status = None
            collector_version = None
            heartbeat_history: list[FleetHeartbeatOut] = []
        else:
            expires_at_utc = _utc(handshake.expires_at)
            if expires_at_utc <= now:
                handshake.status = AdapterHandshakeStatus.EXPIRED
            handshake_status = handshake.status.value
            profile = handshake.profile
            adapter_id = handshake.adapter_id
            adapter_version = handshake.adapter_version
            accepted = list(handshake.accepted_capabilities_json)
            rejected = list(handshake.rejected_capabilities_json)
            level = _certified_level(
                profile=handshake.profile,
                claimed=handshake.claimed_capability_level,
                accepted=set(accepted),
            )
            last_heartbeat_at = handshake.last_heartbeat_at
            expires_at = handshake.expires_at
            drift = handshake.config_drift
            collector_status = handshake.collector_status
            collector_version = handshake.collector_version
            heartbeat_rows = heartbeat_history_by_handshake.get(
                handshake.id,
                [],
            )
            heartbeat_history = [
                FleetHeartbeatOut(
                    status=item.status,
                    config_drift=item.config_drift,
                    collector_status=item.collector_status,
                    collector_version=item.collector_version,
                    observed_at=item.observed_at,
                )
                for item in heartbeat_rows
            ]
            if handshake.heartbeat_status == "error":
                heartbeat_state = "ERROR"
            elif last_heartbeat_at is None:
                heartbeat_state = "NEVER"
            elif (
                now - _utc(last_heartbeat_at)
                > timedelta(
                    seconds=settings.FLEET_HEARTBEAT_STALE_SECONDS
                )
            ):
                heartbeat_state = "STALE"
            else:
                heartbeat_state = "HEALTHY"
        projections.append(
            FleetRuntimeOut(
                runtime_public_id=runtime.public_id,
                provider=runtime.provider,
                name=runtime.name,
                runtime_status=runtime.status,
                profile=profile,
                adapter_id=adapter_id,
                adapter_version=adapter_version,
                certified_capability_level=level,
                accepted_capabilities=accepted,
                rejected_capabilities=rejected,
                handshake_status=handshake_status,  # type: ignore[arg-type]
                heartbeat_state=heartbeat_state,  # type: ignore[arg-type]
                last_heartbeat_at=last_heartbeat_at,
                handshake_expires_at=expires_at,
                config_drift=drift,
                collector_status=collector_status,
                collector_version=collector_version,
                heartbeat_history=heartbeat_history,
                namespace_telemetry_sink_count=int(
                    sink_counts[0] or 0
                ),
                namespace_active_telemetry_sink_count=int(
                    sink_counts[1] or 0
                ),
                latest_run_at=latest_run_at,
                pending_pack_import_count=pending_pack_count,
                quarantined_item_count=(
                    quarantined_packs + quarantined_traces
                ),
            )
        )
    return FleetSummaryOut(
        namespace_id=namespace_id,
        runtime_count=len(projections),
        healthy_count=sum(
            item.heartbeat_state == "HEALTHY" for item in projections
        ),
        stale_count=sum(
            item.heartbeat_state == "STALE" for item in projections
        ),
        degraded_count=sum(
            item.handshake_status in {"DEGRADED", "EXPIRED"}
            or item.heartbeat_state in {"ERROR", "NEVER"}
            for item in projections
        ),
        drifted_count=sum(
            item.config_drift not in {None, AdapterConfigDrift.NONE}
            for item in projections
        ),
        runtimes=projections,
    )
