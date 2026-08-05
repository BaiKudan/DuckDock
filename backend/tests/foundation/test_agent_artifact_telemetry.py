"""FND-050/051/055-058 artifact and provider-neutral telemetry contracts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from app.api.v2.endpoints.telemetry import (
    create_run_artifact,
    create_sink,
    disable_sink,
    get_sink,
    list_run_artifacts,
    list_sinks,
    patch_sink,
)
from app.models.audit import AuditLog
from app.models.control_plane import (
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
)
from app.models.execution import (
    AgentRun,
    AgentRunStatus,
    ContentCaptureMode,
    TrustLevel,
    TrustSource,
)
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.telemetry import (
    AgentRunArtifactCompleteness,
    AgentRunArtifactKind,
    TelemetrySinkStatus,
    TraceBackendRefStatus,
)
from app.models.user import SystemRole, User
from app.schemas.telemetry import (
    AgentRunArtifactCreate,
    TelemetrySinkCreate,
    TelemetrySinkUpdate,
)
from app.services.telemetry_ports import (
    NullAttestationVerifier,
    NullTelemetrySinkPort,
    NullTrajectoryCodec,
    TelemetryPortUnavailable,
    TraceConfirmation,
    TraceConfirmationRequest,
    TrajectoryCodecUnavailable,
)
from app.services.telemetry_service import (
    TelemetryConflictError,
    TelemetryTenantMismatchError,
    confirm_trace_backend_ref,
    create_telemetry_sink,
    register_agent_run_artifact,
)


NOW = datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc)


class InMemoryTelemetrySinkPort:
    """Test double kept outside the production application package."""

    def __init__(self, *, fail_with: str | None = None) -> None:
        self.fail_with = fail_with
        self.requests: list[TraceConfirmationRequest] = []

    async def confirm_trace(self, request: TraceConfirmationRequest) -> TraceConfirmation:
        self.requests.append(request)
        if self.fail_with is not None:
            raise TelemetryPortUnavailable(self.fail_with)
        return TraceConfirmation(
            confirmed=True,
            external_trace_id=request.source_trace_id,
            external_session_id=request.source_session_id,
            trace_url=f"https://telemetry.example.test/traces/{request.source_trace_id}",
        )


class InMemoryTrajectoryCodec:
    """Deterministic trajectory codec used only by this test module."""

    def encode(self, document: Mapping[str, Any]) -> bytes:
        return json.dumps(
            dict(document),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    def decode(self, payload: bytes) -> dict[str, Any]:
        decoded = json.loads(payload.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("trajectory document must be a JSON object")
        return decoded


class InMemoryAttestationVerifier:
    """Allow-list verifier used only by this test module."""

    def __init__(self, accepted_proofs: set[str] | None = None) -> None:
        self.accepted_proofs = accepted_proofs or set()

    async def verify(self, proof: dict[str, Any]) -> bool:
        proof_id = proof.get("proof_id")
        return isinstance(proof_id, str) and proof_id in self.accepted_proofs


async def _seed(db):
    owner = User(
        username="telemetry-owner",
        email="telemetry-owner@example.test",
        hashed_password="unused",
        system_role=SystemRole.USER,
    )
    outsider = User(
        username="telemetry-outsider",
        email="telemetry-outsider@example.test",
        hashed_password="unused",
        system_role=SystemRole.USER,
    )
    db.add_all([owner, outsider])
    await db.flush()
    first = Namespace(name="telemetry-first", owner_id=owner.id)
    second = Namespace(name="telemetry-second", owner_id=outsider.id)
    db.add_all([first, second])
    await db.flush()
    db.add_all(
        [
            NamespaceMember(
                namespace_id=first.id,
                user_id=owner.id,
                role=NamespaceRole.ADMIN,
            ),
            NamespaceMember(
                namespace_id=second.id,
                user_id=outsider.id,
                role=NamespaceRole.ADMIN,
            ),
        ]
    )
    first_runtime = RuntimeInstance(
        namespace_id=first.id,
        provider=RuntimeProvider.CUSTOM,
        name="telemetry-first-runtime",
        deploy_type=RuntimeDeployType.PRIVATE,
    )
    second_runtime = RuntimeInstance(
        namespace_id=second.id,
        provider=RuntimeProvider.CUSTOM,
        name="telemetry-second-runtime",
        deploy_type=RuntimeDeployType.PRIVATE,
    )
    db.add_all([first_runtime, second_runtime])
    await db.flush()
    first_run = _run(
        namespace_id=first.id,
        runtime_id=first_runtime.id,
        suffix="first",
    )
    second_run = _run(
        namespace_id=second.id,
        runtime_id=second_runtime.id,
        suffix="second",
    )
    db.add_all([first_run, second_run])
    await db.flush()
    return owner, outsider, first, second, first_run, second_run


def _run(*, namespace_id: int, runtime_id: int, suffix: str) -> AgentRun:
    return AgentRun(
        public_id=f"run_{suffix:0<32}"[:36],
        namespace_id=namespace_id,
        runtime_id=runtime_id,
        external_run_id=f"external-{suffix}",
        otel_trace_id=(suffix[0] * 32),
        attempt=1,
        status=AgentRunStatus.STARTED,
        trust_level=TrustLevel.CHANNEL_AUTHENTICATED,
        trust_source=TrustSource.REPORTER,
        source_schema="duckdock-run-envelope",
        source_schema_version="1.0",
        normalizer_version="duckdock-normalizer-v1",
        content_capture_mode=ContentCaptureMode.METADATA_ONLY,
        started_at=NOW,
        start_idempotency_key=f"start-{suffix}",
        start_envelope_sha256=("a" if suffix == "first" else "b") * 64,
    )


def _artifact_request(
    *,
    sha256: str = "c" * 64,
    size_bytes: int = 1024,
) -> AgentRunArtifactCreate:
    return AgentRunArtifactCreate(
        kind=AgentRunArtifactKind.TRAJECTORY,
        schema_name="atif",
        schema_version="1.0",
        object_uri="runs/2026/07/28/trajectory.json",
        sha256=sha256,
        size_bytes=size_bytes,
        completeness=AgentRunArtifactCompleteness.COMPLETE,
    )


def _sink_request(namespace_id: int) -> TelemetrySinkCreate:
    return TelemetrySinkCreate(
        namespace_id=namespace_id,
        provider="custom",
        name="primary-telemetry",
        endpoint="https://telemetry.example.test/v1",
        project_ref="duckdock-dev",
        credential_ref="secret://duckdock/telemetry",
        config={"protocol": "otlp_http", "timeout_ms": 5000},
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("object_uri", "https://evil.example.test/private.json"),
        ("object_uri", "file:///tmp/private.json"),
        ("object_uri", "file:/tmp/private.json"),
        ("object_uri", "../private.json"),
        ("object_uri", "runs/%2e%2e/private.json"),
        ("sha256", "ABC"),
        ("size_bytes", -1),
    ],
)
def test_artifact_schema_rejects_external_content_and_bad_integrity(
    field: str,
    value: object,
) -> None:
    body = _artifact_request().model_dump()
    body[field] = value
    with pytest.raises(ValidationError):
        AgentRunArtifactCreate.model_validate(body)


def test_artifact_and_sink_schemas_forbid_raw_sensitive_fields() -> None:
    with pytest.raises(ValidationError):
        AgentRunArtifactCreate.model_validate(
            {
                **_artifact_request().model_dump(),
                "prompt": "must-not-enter-mysql",
            }
        )
    sink = _sink_request(1).model_dump()
    sink["api_key"] = "plaintext-secret"
    with pytest.raises(ValidationError):
        TelemetrySinkCreate.model_validate(sink)
    sink = _sink_request(1).model_dump()
    sink["credential_ref"] = "plaintext-secret"
    with pytest.raises(ValidationError):
        TelemetrySinkCreate.model_validate(sink)
    sink = _sink_request(1).model_dump()
    sink["config"]["token"] = "plaintext-secret"
    with pytest.raises(ValidationError):
        TelemetrySinkCreate.model_validate(sink)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.0.0.1:4318",
        "http://localhost:4318",
        "https://user:secret@telemetry.example.test/v1",
        "https://telemetry.example.test/v1?token=secret",
    ],
)
def test_sink_endpoint_is_write_time_ssrf_safe(endpoint: str) -> None:
    body = _sink_request(1).model_dump()
    body["endpoint"] = endpoint
    with pytest.raises(ValidationError):
        TelemetrySinkCreate.model_validate(body)


async def test_artifact_registration_is_tenant_safe_immutable_and_idempotent(
    async_session,
) -> None:
    owner, _, first, second, first_run, _, = await _seed(async_session)
    request = _artifact_request()
    artifact = await register_agent_run_artifact(
        async_session,
        namespace_id=first.id,
        run_public_id=first_run.public_id,
        request=request,
        actor=owner,
    )
    replay = await register_agent_run_artifact(
        async_session,
        namespace_id=first.id,
        run_public_id=first_run.public_id,
        request=request,
        actor=owner,
    )
    assert replay.id == artifact.id
    assert artifact.public_id.startswith("art_")

    changed = request.model_copy(update={"size_bytes": 2048})
    with pytest.raises(TelemetryConflictError):
        await register_agent_run_artifact(
            async_session,
            namespace_id=first.id,
            run_public_id=first_run.public_id,
            request=changed,
            actor=owner,
        )
    with pytest.raises(TelemetryTenantMismatchError):
        await register_agent_run_artifact(
            async_session,
            namespace_id=second.id,
            run_public_id=first_run.public_id,
            request=_artifact_request(sha256="d" * 64),
            actor=owner,
        )

    audit_row = (
        await async_session.execute(
            select(AuditLog).where(
                AuditLog.action == "agent_run_artifact.registered"
            )
        )
    ).scalar_one()
    assert "object_uri" not in audit_row.details


async def test_port_adapters_round_trip_without_vendor_dependencies() -> None:
    request = TraceConfirmationRequest(
        run_public_id="run_test",
        source_trace_id="a" * 32,
    )
    null_result = await NullTelemetrySinkPort().confirm_trace(request)
    assert null_result.confirmed is False
    assert null_result.error_code == "telemetry_sink_not_configured"

    with pytest.raises(TrajectoryCodecUnavailable):
        NullTrajectoryCodec().encode({"steps": []})
    assert await NullAttestationVerifier().verify({"proof_id": "anything"}) is False

    codec = InMemoryTrajectoryCodec()
    payload = codec.encode({"steps": [{"name": "tool"}]})
    assert codec.decode(payload) == {"steps": [{"name": "tool"}]}
    verifier = InMemoryAttestationVerifier({"accepted"})
    assert await verifier.verify({"proof_id": "accepted"}) is True
    assert await verifier.verify({"proof_id": "rejected"}) is False

    sink = InMemoryTelemetrySinkPort()
    result = await sink.confirm_trace(request)
    assert result.confirmed is True
    assert sink.requests == [request]


async def test_provider_confirmation_and_failure_are_isolated_from_run(
    async_session,
) -> None:
    owner, _, first, _, first_run, _ = await _seed(async_session)
    sink = await create_telemetry_sink(
        async_session,
        request=_sink_request(first.id),
        actor=owner,
    )
    run_id = first_run.id
    await async_session.commit()

    confirmed = await confirm_trace_backend_ref(
        async_session,
        namespace_id=first.id,
        run_public_id=first_run.public_id,
        sink_public_id=sink.public_id,
        port=InMemoryTelemetrySinkPort(),
        actor=owner,
    )
    assert confirmed.status == TraceBackendRefStatus.CONFIRMED
    assert confirmed.trace_url is not None

    failing_sink = await create_telemetry_sink(
        async_session,
        request=_sink_request(first.id).model_copy(
            update={"name": "failing-telemetry"}
        ),
        actor=owner,
    )
    failed = await confirm_trace_backend_ref(
        async_session,
        namespace_id=first.id,
        run_public_id=first_run.public_id,
        sink_public_id=failing_sink.public_id,
        port=InMemoryTelemetrySinkPort(
            fail_with="Authorization: secret-must-not-persist"
        ),
        actor=owner,
    )
    assert failed.status == TraceBackendRefStatus.ERROR
    assert failed.last_error_code == "provider_error"
    assert "secret" not in str(failed.last_error_code)
    assert await async_session.get(AgentRun, run_id) is not None


async def test_management_api_is_namespace_scoped_and_disables_sink(
    async_session,
) -> None:
    owner, outsider, first, _, first_run, _ = await _seed(async_session)
    body = _sink_request(first.id)
    with pytest.raises(HTTPException) as denied:
        await create_sink(body, async_session, outsider)
    assert denied.value.status_code == 403

    created = await create_sink(body, async_session, owner)
    assert created.status == TelemetrySinkStatus.ACTIVE
    listed = await list_sinks(
        namespace_id=first.id,
        db=async_session,
        current_user=owner,
        include_disabled=True,
        limit=100,
    )
    assert [sink.public_id for sink in listed] == [created.public_id]
    with pytest.raises(HTTPException) as hidden:
        await get_sink(created.public_id, async_session, outsider)
    assert hidden.value.status_code == 404

    updated = await patch_sink(
        created.public_id,
        TelemetrySinkUpdate(
            endpoint="https://telemetry.example.test/v2",
        ),
        async_session,
        owner,
    )
    assert updated.endpoint.endswith("/v2")
    disabled = await disable_sink(created.public_id, async_session, owner)
    assert disabled.status == TelemetrySinkStatus.DISABLED

    artifact = await create_run_artifact(
        first_run.public_id,
        first.id,
        _artifact_request(),
        async_session,
        owner,
    )
    assert artifact.run_public_id == first_run.public_id
    artifacts = await list_run_artifacts(
        first_run.public_id,
        first.id,
        async_session,
        owner,
        100,
    )
    assert [item.public_id for item in artifacts] == [artifact.public_id]
