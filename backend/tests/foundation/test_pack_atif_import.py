"""DD-C3 Pack/ATIF PAT-001..012 conformance coverage."""

from __future__ import annotations

import hashlib
import io
import json
import os
import warnings
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from app.api.v2.endpoints.executions import (
    get_reporter_execution_identity,
)
from app.api.v2.router import api_router
from app.core.database import get_db
from app.core.security import hash_password
from app.models.audit import AuditLog
from app.models.control_plane import (
    ReporterCredential,
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
    Sensitivity,
)
from app.models.execution import AgentRun, AgentRunStatus, TrustLevel, TrustSource
from app.models.fleet import AdapterProfile
from app.models.namespace import Namespace
from app.models.outbox import OutboxEvent
from app.models.telemetry import (
    AgentRunArtifact,
    AgentRunArtifactCompleteness,
    AgentRunArtifactKind,
    PackBatchReceipt,
    PackExport,
    PackImportStatus,
    PackUploadMode,
)
from app.models.user import SystemRole, User
from app.schemas.fleet import AdapterDescriptor
from app.schemas.pack_import import (
    EvaluationReplayCreate,
    PackBatchCreate,
    PackExportCreate,
    PackImportCreate,
    PackManifest,
    PackMultipartComplete,
)
from app.services.artifact_service import artifact_service
from app.services.pack_import_service import (
    complete_pack_multipart_upload,
    create_pack_multipart_part_url,
    create_pack_import,
    finalize_pack_import,
    list_pack_multipart_parts,
)
from app.services.pack_transfer_service import (
    create_pack_batch,
    create_pack_export,
    replay_evaluation_result,
)
from app.services.fleet_service import negotiate_adapter_handshake
from app.services.reporter_identity_service import (
    EXECUTION_WRITE_SCOPE,
    ReporterExecutionIdentity,
)


NOW = datetime(2026, 7, 30, tzinfo=timezone.utc)
TRACE_A = "0123456789abcdef0123456789abcdef"
TRACE_B = "fedcba9876543210fedcba9876543210"
CANARY = "PACK_SECRET_CANARY_DO_NOT_EXPORT"


class MemoryArtifactStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts: list[str] = []
        self.deletes: list[str] = []

    def presign(self, *, object_key: str, **_kwargs):
        return {
            "upload_url": f"http://minio.test/{object_key}",
            "expires_at": NOW,
            "expires_in": 900,
        }

    def presign_get(self, *, object_key: str, **_kwargs):
        return {
            "download_url": f"http://minio.test/{object_key}",
            "expires_at": NOW,
            "expires_in": 900,
        }

    async def head(self, object_key: str):
        if object_key not in self.objects:
            from app.services.artifact_service import ArtifactStorageError

            raise ArtifactStorageError("missing")
        return {"size_bytes": len(self.objects[object_key])}

    async def digest(self, object_key: str):
        payload = self.objects[object_key]
        return {
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }

    async def read(self, object_key: str, *, max_bytes=None):
        payload = self.objects[object_key]
        if max_bytes is not None and len(payload) > max_bytes:
            from app.services.artifact_service import ArtifactStorageError

            raise ArtifactStorageError("too large")
        return payload

    async def put(
        self,
        *,
        object_key: str,
        payload: bytes,
        **_kwargs,
    ):
        self.objects[object_key] = payload
        self.puts.append(object_key)
        return {"object_key": object_key, "size_bytes": len(payload)}

    async def delete(self, object_key: str):
        self.objects.pop(object_key, None)
        self.deletes.append(object_key)


@pytest.fixture
def artifact_store(monkeypatch) -> MemoryArtifactStore:
    store = MemoryArtifactStore()
    monkeypatch.setattr(
        artifact_service,
        "generate_presigned_put_url",
        store.presign,
    )
    monkeypatch.setattr(
        artifact_service,
        "generate_presigned_get_url",
        store.presign_get,
    )
    monkeypatch.setattr(artifact_service, "head_object_async", store.head)
    monkeypatch.setattr(artifact_service, "hash_object_async", store.digest)
    monkeypatch.setattr(
        artifact_service,
        "read_object_bytes_async",
        store.read,
    )
    monkeypatch.setattr(
        artifact_service,
        "put_object_bytes_async",
        store.put,
    )
    monkeypatch.setattr(
        artifact_service,
        "delete_object_async",
        store.delete,
    )
    return store


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


async def _seed(async_session):
    owner = User(
        username="pack-owner",
        email="pack-owner@example.test",
        hashed_password="unused",
        system_role=SystemRole.USER,
    )
    async_session.add(owner)
    await async_session.flush()
    namespace = Namespace(name="pack-import", owner_id=owner.id)
    async_session.add(namespace)
    await async_session.flush()
    runtime = RuntimeInstance(
        namespace_id=namespace.id,
        provider=RuntimeProvider.CUSTOM,
        name="pack-runtime",
        deploy_type=RuntimeDeployType.PRIVATE,
    )
    async_session.add(runtime)
    await async_session.flush()
    credential = ReporterCredential(
        runtime_id=runtime.id,
        user_id=owner.id,
        device_id="pack-device",
        name="pack-reporter",
        token_prefix="packtoken",
        token_hash=hash_password("pack-secret"),
        scopes=[EXECUTION_WRITE_SCOPE],
    )
    async_session.add(credential)
    await async_session.flush()
    run = AgentRun(
        public_id="run_" + "a" * 32,
        namespace_id=namespace.id,
        runtime_id=runtime.id,
        external_run_id="pack-run-a",
        otel_trace_id=TRACE_A,
        attempt=1,
        status=AgentRunStatus.STARTED,
        trust_level=TrustLevel.CHANNEL_AUTHENTICATED,
        trust_source=TrustSource.REPORTER,
        source_schema="openclaw-run",
        source_schema_version="1.0",
        normalizer_version="duckdock-openclaw-v1",
        started_at=NOW,
        start_idempotency_key="pack-run-start-a",
        start_envelope_sha256="a" * 64,
    )
    async_session.add(run)
    await async_session.flush()
    identity = ReporterExecutionIdentity(
        namespace_id=namespace.id,
        runtime_id=runtime.id,
        credential_id=credential.id,
        actor_user_id=owner.id,
        trust_level=TrustLevel.CHANNEL_AUTHENTICATED,
        trust_source=TrustSource.REPORTER,
    )
    return namespace, runtime, credential, run, identity


def _atif(*, version: str = "ATIF-v1.7", content=None) -> bytes:
    document = {
        "schema_version": version,
        "session_id": "pack-session",
        "agent": {"name": "fixture-agent", "version": "1.0"},
        "steps": [],
    }
    if content is not None:
        document["steps"] = [{"step_id": 1, "message": content}]
    return json.dumps(document, separators=(",", ":")).encode()


def _manifest(
    payload: bytes,
    *,
    pack_id: str = "pack-001",
    schema_version: str = "ATIF-v1.7",
    completeness: str = "COMPLETE",
    loss_reason: str | None = None,
    external_run_id: str = "pack-run-a",
    trace_id: str = TRACE_A,
    handshake_id: str | None = None,
) -> PackManifest:
    return PackManifest.model_validate(
        {
            "manifest_schema": "duckdock-pack",
            "manifest_version": "1.0",
            "pack_id": pack_id,
            "producer": {
                "adapter_id": "fixture-exporter",
                "adapter_version": "1.0.0",
                "instance_id": "fixture-instance",
                "handshake_id": handshake_id,
            },
            "created_at": NOW.isoformat(),
            "external_run_id": external_run_id,
            "otel_trace_id": trace_id,
            "content_capture_mode": "metadata_only",
            "payloads": [
                {
                    "path": "trajectories/trajectory.json",
                    "media_type": "application/atif+json",
                    "size_bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "schema_name": "ATIF",
                    "schema_version": schema_version,
                    "sensitivity": "restricted",
                    "completeness": completeness,
                    "loss_reason": loss_reason,
                }
            ],
        }
    )


def _pack(
    manifest: PackManifest,
    payload: bytes,
    *,
    extras: list[tuple[str, bytes]] | None = None,
    compression=zipfile.ZIP_DEFLATED,
) -> bytes:
    buffer = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(
                    manifest.model_dump(mode="json"),
                    separators=(",", ":"),
                ),
            )
            archive.writestr(manifest.payloads[0].path, payload)
            for path, content in extras or []:
                archive.writestr(path, content)
    return buffer.getvalue()


async def _create_and_upload(
    async_session,
    store: MemoryArtifactStore,
    identity: ReporterExecutionIdentity,
    manifest: PackManifest,
    pack: bytes,
):
    result = await create_pack_import(
        async_session,
        identity=identity,
        request=PackImportCreate(
            expected_pack_sha256=hashlib.sha256(pack).hexdigest(),
            expected_size_bytes=len(pack),
            manifest=manifest,
        ),
    )
    store.objects[result.pack_import.staging_object_key] = pack
    return result


async def test_pack_import_binds_negotiated_artifact_capability(
    async_session,
    artifact_store,
) -> None:
    _, _, _, _, identity = await _seed(async_session)
    handshake = await negotiate_adapter_handshake(
        async_session,
        identity=identity,
        descriptor=AdapterDescriptor.model_validate(
            {
                "adapter_id": "fixture-exporter",
                "adapter_version": "1.0.0",
                "profile": AdapterProfile.PACK_ATIF_IMPORT.value,
                "source_schema": "ATIF",
                "source_schema_version": "ATIF-v1.7",
                "capabilities": ["artifact_import", "partial_loss"],
                "content_capture_modes": ["metadata_only"],
                "instance_id": "fixture-instance",
                "boot_id": "fixture-boot",
                "client_nonce": "PACKbindnonce000000000",
                "client_time": datetime.now(timezone.utc).isoformat(),
                "claimed_capability_level": "DD-C1",
            }
        ),
    )
    assert handshake.response.certified_capability_level == (
        "DD-C1+ARTIFACT"
    )
    payload = _atif()
    manifest = _manifest(
        payload,
        pack_id="pack-handshake",
        handshake_id=handshake.handshake.public_id,
    )
    pack = _pack(manifest, payload)
    created = await _create_and_upload(
        async_session,
        artifact_store,
        identity,
        manifest,
        pack,
    )

    assert created.pack_import.adapter_handshake_id == (
        handshake.handshake.id
    )


async def test_pat001_valid_atif_creates_verified_artifact(
    async_session,
    artifact_store,
) -> None:
    _, _, _, run, identity = await _seed(async_session)
    payload = _atif()
    manifest = _manifest(payload)
    pack = _pack(manifest, payload)
    created = await _create_and_upload(
        async_session,
        artifact_store,
        identity,
        manifest,
        pack,
    )

    imported = await finalize_pack_import(
        async_session,
        identity=identity,
        public_id=created.pack_import.public_id,
    )

    assert imported.status == PackImportStatus.IMPORTED
    assert imported.agent_run_id == run.id
    assert imported.verified_payload_count == 1
    artifact = (
        await async_session.execute(select(AgentRunArtifact))
    ).scalar_one()
    assert artifact.sha256 == hashlib.sha256(payload).hexdigest()
    storage_prefix = f"s3://{artifact_service.bucket}/"
    assert artifact.object_uri.startswith(storage_prefix)
    assert artifact_store.objects[
        artifact.object_uri.removeprefix(storage_prefix)
    ] == payload


async def test_pat002_replay_reuses_import_artifact_and_event(
    async_session,
    artifact_store,
) -> None:
    await _seed(async_session)
    identity = ReporterExecutionIdentity(
        namespace_id=1,
        runtime_id=1,
        credential_id=1,
        actor_user_id=1,
        trust_level=TrustLevel.CHANNEL_AUTHENTICATED,
        trust_source=TrustSource.REPORTER,
    )
    payload = _atif()
    manifest = _manifest(payload, pack_id="pack-002")
    pack = _pack(manifest, payload)
    created = await _create_and_upload(
        async_session,
        artifact_store,
        identity,
        manifest,
        pack,
    )
    first = await finalize_pack_import(
        async_session,
        identity=identity,
        public_id=created.pack_import.public_id,
    )
    replay_create = await create_pack_import(
        async_session,
        identity=identity,
        request=PackImportCreate(
            expected_pack_sha256=hashlib.sha256(pack).hexdigest(),
            expected_size_bytes=len(pack),
            manifest=manifest,
        ),
    )
    second = await finalize_pack_import(
        async_session,
        identity=identity,
        public_id=first.public_id,
    )

    assert replay_create.replayed is True
    assert replay_create.pack_import.id == first.id == second.id
    assert (
        await async_session.scalar(
            select(func.count(AgentRunArtifact.id))
        )
        == 1
    )
    assert await async_session.scalar(select(func.count(OutboxEvent.id))) == 1


async def test_pat003_changed_checksum_quarantines_pack_id(
    async_session,
    artifact_store,
) -> None:
    _, _, _, _, identity = await _seed(async_session)
    payload = _atif()
    manifest = _manifest(payload, pack_id="pack-003")
    pack = _pack(manifest, payload)
    created = await _create_and_upload(
        async_session,
        artifact_store,
        identity,
        manifest,
        pack,
    )
    conflict = await create_pack_import(
        async_session,
        identity=identity,
        request=PackImportCreate(
            expected_pack_sha256="f" * 64,
            expected_size_bytes=len(pack),
            manifest=manifest,
        ),
    )

    assert conflict.conflict is True
    assert conflict.pack_import.id == created.pack_import.id
    assert conflict.pack_import.status == PackImportStatus.QUARANTINED
    assert conflict.pack_import.last_error_code == "pack_id_checksum_conflict"


async def test_pat004_unknown_atif_version_is_never_normalized(
    async_session,
    artifact_store,
) -> None:
    _, _, _, _, identity = await _seed(async_session)
    payload = _atif(version="ATIF-v9.9")
    manifest = _manifest(
        payload,
        pack_id="pack-004",
        schema_version="ATIF-v9.9",
    )
    pack = _pack(manifest, payload)
    created = await _create_and_upload(
        async_session,
        artifact_store,
        identity,
        manifest,
        pack,
    )
    result = await finalize_pack_import(
        async_session,
        identity=identity,
        public_id=created.pack_import.public_id,
    )

    assert result.status == PackImportStatus.REJECTED
    assert result.last_error_code == "atif_schema_unsupported"
    assert await async_session.scalar(
        select(func.count(AgentRunArtifact.id))
    ) == 0


async def test_pat005_bad_or_truncated_pack_never_completes(
    async_session,
    artifact_store,
) -> None:
    _, _, _, _, identity = await _seed(async_session)
    payload = _atif()
    manifest = _manifest(payload, pack_id="pack-005")
    pack = b"not-a-zip"
    created = await _create_and_upload(
        async_session,
        artifact_store,
        identity,
        manifest,
        pack,
    )
    result = await finalize_pack_import(
        async_session,
        identity=identity,
        public_id=created.pack_import.public_id,
    )

    assert result.status == PackImportStatus.REJECTED
    assert result.last_error_code == "invalid_zip_archive"
    assert result.verified_payload_count == 0


@pytest.mark.parametrize(
    "extras,expected",
    [
        ([("../escape.json", b"x")], "unsafe_archive_path"),
        ([("/absolute.json", b"x")], "unsafe_archive_path"),
        ([("manifest.json", b"{}")], "duplicate_archive_entry"),
    ],
)
async def test_pat006_dangerous_archive_entries_are_rejected(
    async_session,
    artifact_store,
    extras,
    expected,
) -> None:
    _, _, _, _, identity = await _seed(async_session)
    payload = _atif()
    manifest = _manifest(payload, pack_id=f"pack-006-{expected}")
    pack = _pack(manifest, payload, extras=extras)
    created = await _create_and_upload(
        async_session,
        artifact_store,
        identity,
        manifest,
        pack,
    )
    result = await finalize_pack_import(
        async_session,
        identity=identity,
        public_id=created.pack_import.public_id,
    )

    assert result.status == PackImportStatus.REJECTED
    assert result.last_error_code == expected


async def test_pat007_compression_bomb_is_rejected_before_decode(
    async_session,
    artifact_store,
) -> None:
    _, _, _, _, identity = await _seed(async_session)
    payload = b"0" * (1024 * 1024)
    manifest = _manifest(payload, pack_id="pack-007")
    pack = _pack(manifest, payload)
    created = await _create_and_upload(
        async_session,
        artifact_store,
        identity,
        manifest,
        pack,
    )
    result = await finalize_pack_import(
        async_session,
        identity=identity,
        public_id=created.pack_import.public_id,
    )

    assert result.status == PackImportStatus.REJECTED
    assert result.last_error_code == "compression_ratio_exceeded"


async def test_pat008_declared_loss_imports_partial_evidence(
    async_session,
    artifact_store,
) -> None:
    _, _, _, _, identity = await _seed(async_session)
    payload = _atif()
    manifest = _manifest(
        payload,
        pack_id="pack-008",
        completeness="PARTIAL",
        loss_reason="offline_buffer_discard",
    )
    pack = _pack(manifest, payload)
    created = await _create_and_upload(
        async_session,
        artifact_store,
        identity,
        manifest,
        pack,
    )
    result = await finalize_pack_import(
        async_session,
        identity=identity,
        public_id=created.pack_import.public_id,
    )

    assert result.status == PackImportStatus.IMPORTED_PARTIAL
    assert result.loss_reason == "offline_buffer_discard"
    artifact = (
        await async_session.execute(select(AgentRunArtifact))
    ).scalar_one()
    assert artifact.completeness == AgentRunArtifactCompleteness.PARTIAL


async def test_pat009_checksum_is_not_producer_attestation(
    async_session,
    artifact_store,
) -> None:
    _, _, _, _, identity = await _seed(async_session)
    payload = _atif()
    manifest = _manifest(payload, pack_id="pack-009")
    pack = _pack(manifest, payload)
    created = await _create_and_upload(
        async_session,
        artifact_store,
        identity,
        manifest,
        pack,
    )
    result = await finalize_pack_import(
        async_session,
        identity=identity,
        public_id=created.pack_import.public_id,
    )

    assert result.trust_level == "CHANNEL_AUTHENTICATED"
    assert result.trust_source == "IMPORT"


async def test_pat010_conflicting_run_and_trace_are_quarantined(
    async_session,
    artifact_store,
) -> None:
    namespace, runtime, _, _, identity = await _seed(async_session)
    second = AgentRun(
        public_id="run_" + "b" * 32,
        namespace_id=namespace.id,
        runtime_id=runtime.id,
        external_run_id="pack-run-b",
        otel_trace_id=TRACE_B,
        attempt=1,
        status=AgentRunStatus.STARTED,
        trust_level=TrustLevel.CHANNEL_AUTHENTICATED,
        trust_source=TrustSource.REPORTER,
        source_schema="openclaw-run",
        source_schema_version="1.0",
        normalizer_version="duckdock-openclaw-v1",
        started_at=NOW,
        start_idempotency_key="pack-run-start-b",
        start_envelope_sha256="b" * 64,
    )
    async_session.add(second)
    await async_session.flush()
    payload = _atif()
    manifest = _manifest(
        payload,
        pack_id="pack-010",
        external_run_id="pack-run-a",
        trace_id=TRACE_B,
    )
    pack = _pack(manifest, payload)
    created = await _create_and_upload(
        async_session,
        artifact_store,
        identity,
        manifest,
        pack,
    )
    result = await finalize_pack_import(
        async_session,
        identity=identity,
        public_id=created.pack_import.public_id,
    )

    assert result.status == PackImportStatus.QUARANTINED
    assert result.last_error_code == "run_trace_mapping_conflict"
    assert await async_session.scalar(
        select(func.count(AgentRunArtifact.id))
    ) == 0


async def test_pat011_content_without_namespace_policy_is_quarantined(
    async_session,
    artifact_store,
) -> None:
    _, _, _, _, identity = await _seed(async_session)
    payload = _atif(content="private user request")
    manifest = _manifest(payload, pack_id="pack-011")
    pack = _pack(manifest, payload)
    created = await _create_and_upload(
        async_session,
        artifact_store,
        identity,
        manifest,
        pack,
    )
    result = await finalize_pack_import(
        async_session,
        identity=identity,
        public_id=created.pack_import.public_id,
    )

    assert result.status == PackImportStatus.QUARANTINED
    assert result.last_error_code == "content_policy_required"
    assert not artifact_store.puts


async def test_pat012_canary_is_absent_from_final_objects_db_and_outbox(
    async_session,
    artifact_store,
) -> None:
    _, _, _, _, identity = await _seed(async_session)
    payload = _atif(content=CANARY)
    manifest = _manifest(payload, pack_id="pack-012")
    pack = _pack(manifest, payload)
    created = await _create_and_upload(
        async_session,
        artifact_store,
        identity,
        manifest,
        pack,
    )
    staging_key = created.pack_import.staging_object_key
    result = await finalize_pack_import(
        async_session,
        identity=identity,
        public_id=created.pack_import.public_id,
    )

    assert result.status == PackImportStatus.QUARANTINED
    assert result.last_error_code == "secret_canary_detected"
    assert staging_key in artifact_store.deletes
    assert all(
        CANARY.encode() not in value
        for value in artifact_store.objects.values()
    )
    persisted = {
        "imports": [
            row.__dict__
            for row in (
                await async_session.execute(
                    select(type(result))
                )
            ).scalars()
        ],
        "artifacts": [
            row.__dict__
            for row in (
                await async_session.execute(select(AgentRunArtifact))
            ).scalars()
        ],
        "outbox": [
            row.payload_json
            for row in (
                await async_session.execute(select(OutboxEvent))
            ).scalars()
        ],
        "audit": [
            row.details
            for row in (
                await async_session.execute(select(AuditLog))
            ).scalars()
        ],
    }
    assert CANARY not in json.dumps(persisted, default=str)


async def test_pack_import_api_exposes_idempotent_resource_and_conflict(
    async_session,
    artifact_store,
) -> None:
    _, _, _, _, identity = await _seed(async_session)
    payload = _atif()
    manifest = _manifest(payload, pack_id="pack-api")
    pack = _pack(manifest, payload)
    body = {
        "expected_pack_sha256": hashlib.sha256(pack).hexdigest(),
        "expected_size_bytes": len(pack),
        "manifest": manifest.model_dump(mode="json"),
    }

    async with _client(async_session, identity) as client:
        created = await client.post("/api/v2/reporter/pack-imports", json=body)
        assert created.status_code == 201
        created_body = created.json()
        assert created_body["status"] == "PENDING_VALIDATION"
        assert created_body["upload_url"].startswith("http://minio.test/")

        replay = await client.post("/api/v2/reporter/pack-imports", json=body)
        assert replay.status_code == 201
        assert replay.json()["public_id"] == created_body["public_id"]

        pending = await client.post(
            "/api/v2/reporter/pack-imports/"
            + created_body["public_id"]
            + "/finalize"
        )
        assert pending.status_code == 202
        assert pending.json()["last_error_code"] == "upload_pending"

        changed = dict(body)
        changed["expected_pack_sha256"] = "f" * 64
        conflict = await client.post(
            "/api/v2/reporter/pack-imports",
            json=changed,
        )
        assert conflict.status_code == 409
        assert conflict.headers["content-type"].startswith(
            "application/problem+json"
        )
        assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"

        detail = await client.get(
            "/api/v2/reporter/pack-imports/"
            + created_body["public_id"]
        )
        assert detail.status_code == 200
        assert detail.json()["status"] == "QUARANTINED"


async def test_interrupted_upload_expires_without_artifact_registration(
    async_session,
    artifact_store,
) -> None:
    _, _, _, _, identity = await _seed(async_session)
    payload = _atif()
    manifest = _manifest(payload, pack_id="pack-expired")
    pack = _pack(manifest, payload)
    created = await _create_and_upload(
        async_session,
        artifact_store,
        identity,
        manifest,
        pack,
    )
    created.pack_import.expires_at = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    result = await finalize_pack_import(
        async_session,
        identity=identity,
        public_id=created.pack_import.public_id,
    )

    assert result.status == PackImportStatus.REJECTED
    assert result.last_error_code == "upload_expired"
    assert created.pack_import.staging_object_key in artifact_store.deletes
    assert await async_session.scalar(
        select(func.count(AgentRunArtifact.id))
    ) == 0


@pytest.mark.skipif(
    os.getenv("RUN_MINIO_INTEGRATION") != "1",
    reason="set RUN_MINIO_INTEGRATION=1 for live MinIO verification",
)
async def test_pack_import_real_minio_put_verify_and_materialize(
    async_session,
) -> None:
    _, _, _, _, identity = await _seed(async_session)
    payload = _atif()
    manifest = _manifest(payload, pack_id="pack-minio-live")
    pack = _pack(manifest, payload)
    created = await create_pack_import(
        async_session,
        identity=identity,
        request=PackImportCreate(
            expected_pack_sha256=hashlib.sha256(pack).hexdigest(),
            expected_size_bytes=len(pack),
            manifest=manifest,
        ),
    )
    assert created.upload is not None
    staging_key = created.pack_import.staging_object_key
    final_key: str | None = None
    try:
        async with httpx.AsyncClient() as client:
            uploaded = await client.put(
                created.upload["upload_url"],
                content=pack,
                headers={"Content-Type": "application/zip"},
            )
        assert uploaded.status_code == 200
        result = await finalize_pack_import(
            async_session,
            identity=identity,
            public_id=created.pack_import.public_id,
        )
        assert result.status == PackImportStatus.IMPORTED
        artifact = (
            await async_session.execute(select(AgentRunArtifact))
        ).scalar_one()
        final_key = artifact.object_uri.removeprefix("s3://duckdock/")
        assert await artifact_service.read_object_bytes_async(
            final_key,
            max_bytes=1024 * 1024,
        ) == payload
    finally:
        await artifact_service.delete_object_async(staging_key)
        if final_key is not None:
            await artifact_service.delete_object_async(final_key)


async def test_conf_c3_006_multipart_resume_complete_and_verify(
    async_session,
    artifact_store,
    monkeypatch,
) -> None:
    _, _, _, _, identity = await _seed(async_session)
    payload = _atif()
    manifest = _manifest(payload, pack_id="pack-multipart")
    pack = _pack(manifest, payload)
    uploaded_parts: list[dict] = []

    monkeypatch.setattr(
        artifact_service,
        "initiate_multipart_upload",
        lambda **_kwargs: "upload-test-1",
    )
    monkeypatch.setattr(
        artifact_service,
        "generate_presigned_upload_part_url",
        lambda **kwargs: {
            "upload_url": (
                "http://minio.test/part/"
                + str(kwargs["part_number"])
            ),
            "expires_at": NOW,
            "expires_in": 900,
        },
    )
    monkeypatch.setattr(
        artifact_service,
        "list_multipart_parts",
        lambda **_kwargs: list(uploaded_parts),
    )

    def complete_upload(*, object_key, **_kwargs):
        artifact_store.objects[object_key] = pack

    monkeypatch.setattr(
        artifact_service,
        "complete_multipart_upload",
        complete_upload,
    )
    created = await create_pack_import(
        async_session,
        identity=identity,
        request=PackImportCreate(
            expected_pack_sha256=hashlib.sha256(pack).hexdigest(),
            expected_size_bytes=len(pack),
            upload_mode=PackUploadMode.MULTIPART,
            multipart_part_size_bytes=5 * 1024 * 1024,
            manifest=manifest,
        ),
    )
    assert created.upload is None
    assert created.pack_import.multipart_upload_id == "upload-test-1"
    _, signed = await create_pack_multipart_part_url(
        async_session,
        identity=identity,
        public_id=created.pack_import.public_id,
        part_number=1,
    )
    assert signed["upload_url"].endswith("/1")

    uploaded_parts.append(
        {
            "part_number": 1,
            "etag": '"part-one"',
            "size_bytes": len(pack),
        }
    )
    resumed = await list_pack_multipart_parts(
        async_session,
        identity=identity,
        public_id=created.pack_import.public_id,
    )
    assert resumed == uploaded_parts
    imported = await complete_pack_multipart_upload(
        async_session,
        identity=identity,
        public_id=created.pack_import.public_id,
        request=PackMultipartComplete.model_validate(
            {
                "parts": [
                    {"part_number": 1, "etag": '"part-one"'}
                ],
                "observed_pack_sha256": hashlib.sha256(pack).hexdigest(),
                "observed_size_bytes": len(pack),
            }
        ),
    )
    assert imported.status == PackImportStatus.IMPORTED
    assert imported.multipart_completed_at is not None


@pytest.mark.skipif(
    os.getenv("RUN_MINIO_INTEGRATION") != "1",
    reason="set RUN_MINIO_INTEGRATION=1 for live MinIO verification",
)
async def test_conf_c3_006_real_minio_multipart_resume_and_complete(
    async_session,
) -> None:
    _, _, _, _, identity = await _seed(async_session)
    payload = _atif()
    manifest = _manifest(payload, pack_id="pack-multipart-minio-live")
    pack = _pack(manifest, payload)
    created = await create_pack_import(
        async_session,
        identity=identity,
        request=PackImportCreate(
            expected_pack_sha256=hashlib.sha256(pack).hexdigest(),
            expected_size_bytes=len(pack),
            upload_mode=PackUploadMode.MULTIPART,
            multipart_part_size_bytes=5 * 1024 * 1024,
            manifest=manifest,
        ),
    )
    staging_key = created.pack_import.staging_object_key
    final_key: str | None = None
    try:
        _, signed = await create_pack_multipart_part_url(
            async_session,
            identity=identity,
            public_id=created.pack_import.public_id,
            part_number=1,
        )
        async with httpx.AsyncClient() as client:
            uploaded = await client.put(
                signed["upload_url"],
                content=pack,
            )
        assert uploaded.status_code == 200
        parts = await list_pack_multipart_parts(
            async_session,
            identity=identity,
            public_id=created.pack_import.public_id,
        )
        assert len(parts) == 1
        assert parts[0]["size_bytes"] == len(pack)
        result = await complete_pack_multipart_upload(
            async_session,
            identity=identity,
            public_id=created.pack_import.public_id,
            request=PackMultipartComplete.model_validate(
                {
                    "parts": [
                        {
                            "part_number": 1,
                            "etag": parts[0]["etag"],
                        }
                    ],
                    "observed_pack_sha256": hashlib.sha256(
                        pack
                    ).hexdigest(),
                    "observed_size_bytes": len(pack),
                }
            ),
        )
        assert result.status == PackImportStatus.IMPORTED
        artifact = (
            await async_session.execute(select(AgentRunArtifact))
        ).scalar_one()
        final_key = artifact.object_uri.removeprefix("s3://duckdock/")
        assert await artifact_service.read_object_bytes_async(
            final_key,
            max_bytes=1024 * 1024,
        ) == payload
    finally:
        await artifact_service.delete_object_async(staging_key)
        if final_key is not None:
            await artifact_service.delete_object_async(final_key)


async def test_conf_c3_002_batch_receipts_advance_only_contiguous_cursor(
    async_session,
    artifact_store,
) -> None:
    _, _, _, _, identity = await _seed(async_session)
    payload = _atif()

    def item(sequence: int, pack_id: str) -> dict:
        manifest = _manifest(payload, pack_id=pack_id)
        pack = _pack(manifest, payload)
        return {
            "sequence": sequence,
            "idempotency_key": f"queue-{sequence}",
            "pack_import": {
                "expected_pack_sha256": hashlib.sha256(pack).hexdigest(),
                "expected_size_bytes": len(pack),
                "manifest": manifest.model_dump(mode="json"),
            },
        }

    out_of_order = await create_pack_batch(
        async_session,
        identity=identity,
        request=PackBatchCreate.model_validate(
            {"stream_id": "adapter-queue", "items": [item(2, "batch-2")]}
        ),
    )
    assert out_of_order.ack_cursor == 0
    assert out_of_order.items[0].disposition == "accepted"

    fills_gap = await create_pack_batch(
        async_session,
        identity=identity,
        request=PackBatchCreate.model_validate(
            {"stream_id": "adapter-queue", "items": [item(1, "batch-1")]}
        ),
    )
    assert fills_gap.ack_cursor == 2
    replay = await create_pack_batch(
        async_session,
        identity=identity,
        request=PackBatchCreate.model_validate(
            {"stream_id": "adapter-queue", "items": [item(1, "batch-1")]}
        ),
    )
    assert replay.items[0].disposition == "replayed"
    assert (
        await async_session.scalar(
            select(func.count(PackBatchReceipt.id))
        )
        == 2
    )

    conflict_item = item(1, "batch-changed")
    conflict = await create_pack_batch(
        async_session,
        identity=identity,
        request=PackBatchCreate.model_validate(
            {"stream_id": "adapter-queue", "items": [conflict_item]}
        ),
    )
    assert conflict.ack_cursor == 2
    assert conflict.items[0].code == "IDEMPOTENCY_CONFLICT"


async def test_next003_atif_export_is_verified_and_idempotent(
    async_session,
    artifact_store,
) -> None:
    _, _, _, run, identity = await _seed(async_session)
    payload = _atif()
    manifest = _manifest(payload, pack_id="pack-export-source")
    pack = _pack(manifest, payload)
    created = await _create_and_upload(
        async_session,
        artifact_store,
        identity,
        manifest,
        pack,
    )
    await finalize_pack_import(
        async_session,
        identity=identity,
        public_id=created.pack_import.public_id,
    )
    request = PackExportCreate(
        run_public_id=run.public_id,
        idempotency_key="export-once",
    )
    first = await create_pack_export(
        async_session,
        identity=identity,
        request=request,
    )
    exported = artifact_store.objects[first.pack_export.object_key]
    assert hashlib.sha256(exported).hexdigest() == first.pack_export.sha256
    with zipfile.ZipFile(io.BytesIO(exported)) as archive:
        exported_manifest = json.loads(archive.read("manifest.json"))
        assert exported_manifest["run_public_id"] == run.public_id
        assert len(exported_manifest["payloads"]) == 1
        assert (
            archive.read(exported_manifest["payloads"][0]["path"])
            == payload
        )
    replay = await create_pack_export(
        async_session,
        identity=identity,
        request=request,
    )
    assert replay.replayed is True
    assert replay.pack_export.id == first.pack_export.id
    assert await async_session.scalar(select(func.count(PackExport.id))) == 1


async def test_next003_evaluation_replay_has_one_durable_outbox_event(
    async_session,
    artifact_store,
) -> None:
    namespace, _, _, run, identity = await _seed(async_session)
    evaluation_payload = b'{"schema_version":"1.0","status":"completed"}'
    evaluation_sha256 = hashlib.sha256(evaluation_payload).hexdigest()
    evaluation_key = "evaluations/result.json"
    artifact_store.objects[evaluation_key] = evaluation_payload
    artifact = AgentRunArtifact(
        public_id="art_" + "e" * 32,
        namespace_id=namespace.id,
        agent_run_id=run.id,
        kind=AgentRunArtifactKind.EVALUATION,
        schema_name="DuckDockEvaluationResult",
        schema_version="1.0",
        object_uri=f"s3://{artifact_service.bucket}/{evaluation_key}",
        sha256=evaluation_sha256,
        size_bytes=len(evaluation_payload),
        sensitivity=Sensitivity.RESTRICTED,
        completeness=AgentRunArtifactCompleteness.COMPLETE,
    )
    async_session.add(artifact)
    await async_session.flush()
    request = EvaluationReplayCreate(
        run_public_id=run.public_id,
        artifact_public_id=artifact.public_id,
        idempotency_key="eval-replay-once",
    )
    first, first_replayed = await replay_evaluation_result(
        async_session,
        identity=identity,
        request=request,
    )
    second, second_replayed = await replay_evaluation_result(
        async_session,
        identity=identity,
        request=request,
    )
    assert first_replayed is False
    assert second_replayed is True
    assert second.id == first.id
    assert first.event_type == "EvaluationResultReplayed"
    assert await async_session.scalar(select(func.count(OutboxEvent.id))) == 1
