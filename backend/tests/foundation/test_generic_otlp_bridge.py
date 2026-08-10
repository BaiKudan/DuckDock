"""DD-C2 / OTL-001..009 Generic OTLP bridge conformance."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI
from sqlalchemy import func, select

from app.api.v2.router import api_router
from app.core.database import get_db
from app.core.security import hash_password
from app.models.audit import AuditLog
from app.models.control_plane import (
    ReporterCredential,
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
)
from app.models.execution import (
    AgentRun,
    AgentRunStatus,
    TrustLevel,
    TrustSource,
)
from app.models.namespace import Namespace
from app.models.outbox import OutboxEvent
from app.models.telemetry import (
    GenericTraceProjectionStatus,
    TelemetrySink,
    TelemetrySinkStatus,
    TraceBackendRef,
    TraceBackendRefStatus,
)
from app.models.user import SystemRole, User
from app.services.generic_otlp_service import (
    GENERIC_OTLP_NORMALIZER_VERSION,
    ingest_generic_otlp_json,
    parse_generic_otlp_json,
)
from app.services.reporter_identity_service import ReporterExecutionIdentity
from app.services.reporter_identity_service import EXECUTION_WRITE_SCOPE


TRACE_ID = "0123456789abcdef0123456789abcdef"
ROOT_SPAN_ID = "0123456789abcdef"
CHILD_SPAN_ID = "1111111111111111"
START_NANO = "1785398400000000000"
END_NANO = "1785398401000000000"
LATE_END_NANO = "1785398410000000000"


def _string_attr(key: str, value: str) -> dict:
    return {"key": key, "value": {"stringValue": value}}


def _span(
    *,
    trace_id: str = TRACE_ID,
    span_id: str = ROOT_SPAN_ID,
    parent_span_id: str | None = None,
    external_run_id: str | None = "generic-run-001",
    attributes: list[dict] | None = None,
    start_nano: str = START_NANO,
    end_nano: str = END_NANO,
) -> dict:
    span_attributes = list(attributes or [])
    if external_run_id is not None:
        span_attributes.append(
            _string_attr("duckdock.external_run_id", external_run_id)
        )
    payload = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": "untrusted-free-form-name",
        "kind": 2,
        "startTimeUnixNano": start_nano,
        "endTimeUnixNano": end_nano,
        "attributes": span_attributes,
        "status": {"code": 1},
    }
    if parent_span_id is not None:
        payload["parentSpanId"] = parent_span_id
    return payload


def _payload(
    spans: list[dict],
    *,
    resource_attributes: list[dict] | None = None,
) -> dict:
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": list(resource_attributes or []),
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "generic"},
                        "spans": spans,
                    }
                ],
            }
        ]
    }


async def _seed(async_session):
    owner = User(
        username="generic-otlp-owner",
        email="generic-otlp-owner@example.test",
        hashed_password="unused",
        system_role=SystemRole.USER,
    )
    async_session.add(owner)
    await async_session.flush()
    namespace = Namespace(name="generic-otlp", owner_id=owner.id)
    async_session.add(namespace)
    await async_session.flush()
    runtime = RuntimeInstance(
        namespace_id=namespace.id,
        provider=RuntimeProvider.CUSTOM,
        name="generic-otlp-runtime",
        deploy_type=RuntimeDeployType.PRIVATE,
    )
    async_session.add(runtime)
    await async_session.flush()
    sink = TelemetrySink(
        public_id="tsk_" + "a" * 32,
        namespace_id=namespace.id,
        provider="custom",
        name="generic-otlp-sink",
        endpoint="https://telemetry.example.test",
        credential_ref="secret://duckdock/generic-otlp",
        status=TelemetrySinkStatus.ACTIVE,
    )
    async_session.add(sink)
    await async_session.flush()
    identity = ReporterExecutionIdentity(
        namespace_id=namespace.id,
        runtime_id=runtime.id,
        credential_id=1,
        actor_user_id=owner.id,
        trust_level=TrustLevel.CHANNEL_AUTHENTICATED,
        trust_source=TrustSource.REPORTER,
    )
    return namespace, runtime, sink, identity


async def _ingest(async_session, payload: dict):
    namespace, runtime, sink, identity = await _seed(async_session)
    result = await ingest_generic_otlp_json(
        async_session,
        identity=identity,
        sink=sink,
        payload=payload,
    )
    return namespace, runtime, sink, identity, result


@asynccontextmanager
async def _client(db):
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v2")

    async def override_db():
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    app.dependency_overrides[get_db] = override_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        yield client


async def test_otl001_single_root_creates_one_correlated_terminal_run(
    async_session,
) -> None:
    namespace, runtime, sink, _, result = await _ingest(
        async_session,
        _payload([_span()]),
    )

    assert result.rejected_spans == 0
    assert len(result.projections) == 1
    projection = result.projections[0]
    assert projection.status == GenericTraceProjectionStatus.MAPPED
    assert projection.namespace_id == namespace.id
    assert projection.runtime_id == runtime.id
    assert projection.normalizer_version == GENERIC_OTLP_NORMALIZER_VERSION
    run = await async_session.get(AgentRun, projection.agent_run_id)
    assert run is not None
    assert run.external_run_id == "generic-run-001"
    assert run.otel_trace_id == TRACE_ID
    assert run.root_span_id == ROOT_SPAN_ID
    assert run.status == AgentRunStatus.SUCCEEDED
    assert run.trust_source == TrustSource.COLLECTOR
    assert run.normalizer_version == GENERIC_OTLP_NORMALIZER_VERSION
    reference = (
        await async_session.execute(
            select(TraceBackendRef).where(
                TraceBackendRef.telemetry_sink_id == sink.id
            )
        )
    ).scalar_one()
    assert reference.agent_run_id == run.id
    assert reference.external_trace_id == TRACE_ID
    assert reference.status == TraceBackendRefStatus.PENDING


async def test_otl002_forged_governance_attributes_never_choose_identity(
    async_session,
) -> None:
    namespace, runtime, _, _, result = await _ingest(
        async_session,
        _payload(
            [_span()],
            resource_attributes=[
                _string_attr("duckdock.namespace.public_id", "forged-ns"),
                _string_attr("duckdock.runtime.public_id", "forged-runtime"),
                _string_attr("duckdock.trust.level", "PRODUCER_ATTESTED"),
            ],
        ),
    )
    projection = result.projections[0]
    run = await async_session.get(AgentRun, projection.agent_run_id)
    assert projection.namespace_id == namespace.id
    assert projection.runtime_id == runtime.id
    assert run is not None
    assert run.trust_level == TrustLevel.CHANNEL_AUTHENTICATED
    assert run.trust_source == TrustSource.COLLECTOR


async def test_otl003_two_roots_are_quarantined_without_run_or_reference(
    async_session,
) -> None:
    _, _, _, _, result = await _ingest(
        async_session,
        _payload(
            [
                _span(span_id=ROOT_SPAN_ID),
                _span(span_id="2222222222222222"),
            ]
        ),
    )
    projection = result.projections[0]
    assert result.rejected_spans == 2
    assert projection.status == GenericTraceProjectionStatus.QUARANTINED
    assert projection.reason_code == "ambiguous_root_spans"
    assert projection.agent_run_id is None
    assert (
        await async_session.scalar(select(func.count(AgentRun.id)))
    ) == 0
    assert (
        await async_session.scalar(select(func.count(TraceBackendRef.id)))
    ) == 0


async def test_otl004_late_child_updates_projection_only(
    async_session,
) -> None:
    _, _, sink, identity = await _seed(async_session)
    first = await ingest_generic_otlp_json(
        async_session,
        identity=identity,
        sink=sink,
        payload=_payload([_span()]),
    )
    projection = first.projections[0]
    run = await async_session.get(AgentRun, projection.agent_run_id)
    assert run is not None
    terminal_snapshot = (
        run.status,
        run.ended_at,
        run.completion_envelope_sha256,
    )
    late = await ingest_generic_otlp_json(
        async_session,
        identity=identity,
        sink=sink,
        payload=_payload(
            [
                _span(
                    span_id=CHILD_SPAN_ID,
                    parent_span_id=ROOT_SPAN_ID,
                    external_run_id="generic-run-001",
                    start_nano=END_NANO,
                    end_nano=LATE_END_NANO,
                )
            ]
        ),
    )
    assert late.projections[0].id == projection.id
    assert late.projections[0].status == GenericTraceProjectionStatus.MAPPED
    await async_session.refresh(run)
    assert (
        run.status,
        run.ended_at,
        run.completion_envelope_sha256,
    ) == terminal_snapshot
    assert late.projections[0].last_observed_at == datetime.fromtimestamp(
        int(LATE_END_NANO) / 1_000_000_000,
        tz=timezone.utc,
    )


async def test_otl005_duplicate_export_is_idempotent(
    async_session,
) -> None:
    _, _, sink, identity = await _seed(async_session)
    payload = _payload([_span()])
    first = await ingest_generic_otlp_json(
        async_session,
        identity=identity,
        sink=sink,
        payload=payload,
    )
    second = await ingest_generic_otlp_json(
        async_session,
        identity=identity,
        sink=sink,
        payload=payload,
    )
    assert second.projections[0].id == first.projections[0].id
    assert await async_session.scalar(select(func.count(AgentRun.id))) == 1
    assert (
        await async_session.scalar(select(func.count(TraceBackendRef.id)))
    ) == 1
    assert await async_session.scalar(select(func.count(OutboxEvent.id))) == 2


async def test_otl006_and_otl009_content_is_quarantined_and_not_persisted(
    async_session,
) -> None:
    canary = "SHADOW_SECRET_CANARY_DO_NOT_EXPORT"
    _, _, _, _, result = await _ingest(
        async_session,
        _payload(
            [
                _span(
                    attributes=[
                        _string_attr(
                            "gen_ai.prompt",
                            f"raw prompt {canary}",
                        ),
                        _string_attr(
                            "tool.arguments",
                            "must-not-enter-control-plane",
                        ),
                    ]
                )
            ]
        ),
    )
    projection = result.projections[0]
    assert projection.status == GenericTraceProjectionStatus.QUARANTINED
    assert projection.reason_code == "secret_canary_detected"
    assert projection.external_run_id is None
    assert projection.root_span_id is None
    assert await async_session.scalar(select(func.count(AgentRun.id))) == 0

    persisted = [
        projection.__dict__,
        *(
            (
                await async_session.execute(select(AuditLog.details))
            ).scalars().all()
        ),
        *(
            (
                await async_session.execute(select(OutboxEvent.payload_json))
            ).scalars().all()
        ),
    ]
    assert canary not in json.dumps(persisted, default=str)
    assert "raw prompt" not in json.dumps(persisted, default=str)


async def test_otl007_provider_outage_isolated_as_pending_reference(
    async_session,
) -> None:
    _, _, _, _, result = await _ingest(
        async_session,
        _payload([_span()]),
    )
    projection = result.projections[0]
    run = await async_session.get(AgentRun, projection.agent_run_id)
    reference = (
        await async_session.execute(select(TraceBackendRef))
    ).scalar_one()
    assert run is not None
    assert run.status == AgentRunStatus.SUCCEEDED
    assert reference.status == TraceBackendRefStatus.PENDING
    assert reference.last_error_code is None


def test_otl008_invalid_span_identity_surfaces_partial_success_count() -> None:
    result = parse_generic_otlp_json(
        _payload(
            [
                _span(),
                _span(trace_id="INVALID", span_id="2222222222222222"),
            ]
        )
    )
    assert result.rejected_spans == 1
    assert result.rejection_reason == "invalid_trace_identity"
    assert len(result.candidates) == 1
    invalid_correlation = parse_generic_otlp_json(
        _payload([_span(external_run_id="contains spaces")])
    )
    assert invalid_correlation.candidates[0].quarantine_reason == (
        "invalid_external_run_id"
    )


async def test_otl008_http_endpoint_returns_standard_partial_success(
    async_session,
) -> None:
    namespace, runtime, sink, _ = await _seed(async_session)
    owner = await async_session.get(User, namespace.owner_id)
    assert owner is not None
    prefix = "genericotlp"
    secret = "generic-otlp-reporter-secret"
    credential = ReporterCredential(
        runtime_id=runtime.id,
        user_id=owner.id,
        device_id="generic-otel-collector",
        name="Generic OTLP Collector",
        token_prefix=prefix,
        token_hash=hash_password(secret),
        scopes=[EXECUTION_WRITE_SCOPE],
    )
    async_session.add(credential)
    await async_session.commit()
    token = f"dkr_report_{prefix}_{secret}"
    payload = _payload(
        [
            _span(),
            _span(trace_id="INVALID", span_id="2222222222222222"),
        ]
    )

    async with _client(async_session) as client:
        handshake = await client.post(
            "/api/v2/reporter/handshakes",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "adapter_id": "generic-otel-collector",
                "adapter_version": "0.157.0",
                "profile": "generic-otlp-bridge",
                "source_schema": "otlp-traces",
                "source_schema_version": "1.0",
                "capabilities": [
                    "otel_trace_correlation",
                    "metadata_projection",
                ],
                "content_capture_modes": ["metadata_only"],
                "instance_id": "generic-otel-collector",
                "boot_id": "generic-otel-boot",
                "client_nonce": "OTL008nonce00000000000",
                "client_time": datetime.now(timezone.utc).isoformat(),
                "claimed_capability_level": "DD-C2",
            },
        )
        assert handshake.status_code == 201, handshake.text
        assert (
            handshake.json()["certified_capability_level"] == "DD-C2"
        )
        response = await client.post(
            (
                "/api/v2/reporter/telemetry-sinks/"
                f"{sink.public_id}/v1/traces"
            ),
            headers={
                "Authorization": f"Bearer {token}",
                "DuckDock-Handshake-Id": handshake.json()[
                    "handshake_id"
                ],
            },
            json=payload,
        )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "partialSuccess": {
            "rejectedSpans": "1",
            "errorMessage": "invalid_trace_identity",
        }
    }


async def test_reused_trace_with_conflicting_run_id_is_quarantined(
    async_session,
) -> None:
    _, _, sink, identity = await _seed(async_session)
    first = await ingest_generic_otlp_json(
        async_session,
        identity=identity,
        sink=sink,
        payload=_payload([_span(external_run_id="first-run")]),
    )
    second = await ingest_generic_otlp_json(
        async_session,
        identity=identity,
        sink=sink,
        payload=_payload([_span(external_run_id="different-run")]),
    )
    assert second.projections[0].id == first.projections[0].id
    assert (
        second.projections[0].status
        == GenericTraceProjectionStatus.QUARANTINED
    )
    assert second.projections[0].reason_code == "trace_run_identity_conflict"
    assert await async_session.scalar(select(func.count(AgentRun.id))) == 1
