from __future__ import annotations

import base64
import io
import json
import zipfile
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.models.control_plane import (
    AIAsset,
    AssetOwnership,
    AssetType,
    Criticality,
    EvidenceItem,
    EvidenceSourceType,
    ExecutionAction,
    ExecutionStatus,
    HandoverAction,
    HandoverCase,
    HandoverCaseType,
    HandoverItem,
    HandoverItemStatus,
    HandoverStatus,
    OwnerType,
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
)
from app.models.deployment import AgentDeployment, AgentDeploymentStatus
from app.models.handover import (
    HandoverAcceptanceDecision,
    HandoverObligationReceiptDecision,
    HandoverObligationSeverity,
    HandoverObligationType,
    HandoverReadinessOutcome,
)
from app.models.namespace import Namespace
from app.models.outbox import OutboxEvent
from app.models.package_registry import PackageSignatureAlgorithm
from app.models.user import SystemRole, User
from app.schemas.handover import (
    HandoverAcceptanceCreate,
    HandoverEvidenceSnapshotCreate,
    HandoverObligationReceiptCreate,
    HandoverSignedPackageCreate,
)
from app.schemas.package_registry import PackageSigningKeyCreate
from app.services import handover_evidence_service
from app.services.handover_evidence_service import (
    HandoverEvidenceReferenceError,
    HandoverEvidenceStateError,
    create_handover_acceptance,
    create_handover_signed_package,
    create_handover_snapshot,
    handover_signing_payload,
    handover_snapshot_out,
    record_handover_obligation_receipt,
)
from app.services.package_registry_service import create_signing_key
from sqlalchemy import select


def test_handover_v2_openapi_surface_is_explicit():
    from app.main import app

    paths = app.openapi()["paths"]
    for path in (
        "/api/v2/handover-evidence-snapshots",
        "/api/v2/handover-evidence-snapshots/{public_id}",
        "/api/v2/handover-obligations/{public_id}",
        "/api/v2/handover-obligations/{public_id}/receipts",
        "/api/v2/handover-acceptances",
        "/api/v2/handover-acceptances/{public_id}/signing-payload",
        "/api/v2/handover-signed-packages",
        "/api/v2/handover-signed-packages/{public_id}",
    ):
        assert path in paths


async def _user(session, name: str, *, admin: bool = False) -> User:
    user = User(
        username=f"{name}-{uuid4().hex[:8]}",
        email=f"{name}-{uuid4().hex[:8]}@duckdock.dev",
        hashed_password="x",
        system_role=SystemRole.ADMIN if admin else SystemRole.USER,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_case(session, *, failed_action: bool = False):
    actor = await _user(session, "handover-v2-admin", admin=True)
    subject = await _user(session, "handover-v2-subject")
    namespace = Namespace(name=f"handover-v2-{uuid4().hex[:12]}", owner_id=actor.id)
    session.add(namespace)
    await session.flush()
    runtime = RuntimeInstance(
        namespace_id=namespace.id,
        provider=RuntimeProvider.CUSTOM,
        name="Hermes local",
        deploy_type=RuntimeDeployType.ON_PREM,
    )
    session.add(runtime)
    await session.flush()
    asset = AIAsset(
        namespace_id=namespace.id,
        asset_type=AssetType.AGENT,
        name="Hermes governed Agent",
        source_provider=RuntimeProvider.CUSTOM,
        source_runtime_id=runtime.id,
        criticality=Criticality.MEDIUM,
        metadata_json={"runbook_url": "https://runbooks.duckdock.dev/hermes"},
    )
    session.add(asset)
    await session.flush()
    ownership = AssetOwnership(
        namespace_id=namespace.id,
        asset_id=asset.id,
        owner_type=OwnerType.BUSINESS_OWNER,
        user_id=subject.id,
        is_primary=True,
    )
    evidence = EvidenceItem(
        namespace_id=namespace.id,
        source_type=EvidenceSourceType.USER_CONFIRM,
        source_provider=RuntimeProvider.CUSTOM,
        summary="Handover readiness evidence",
        created_by=actor.id,
    )
    session.add_all([ownership, evidence])
    await session.flush()
    case = HandoverCase(
        namespace_id=namespace.id,
        case_type=HandoverCaseType.EMPLOYEE_OFFBOARDING,
        title="Hermes production Agent handover",
        subject_user_id=subject.id,
        receiver_user_id=actor.id,
        fallback_owner_user_id=actor.id,
        status=HandoverStatus.VERIFYING,
        created_by=actor.id,
    )
    session.add(case)
    await session.flush()
    item = HandoverItem(
        handover_case_id=case.id,
        asset_id=asset.id,
        recommended_action=HandoverAction.TRANSFER_OWNER,
        receiver_user_id=actor.id,
        evidence_id=evidence.id,
        status=HandoverItemStatus.DONE,
    )
    deployment = AgentDeployment(
        public_id=f"dep_{uuid4().hex}",
        namespace_id=namespace.id,
        runtime_id=runtime.id,
        agent_asset_id=asset.id,
        external_deployment_id=f"hermes-{uuid4().hex[:12]}",
        environment="production",
        revision="hermes-handover-v2",
        configuration_digest="a" * 64,
        status=AgentDeploymentStatus.ACTIVE,
        activated_at=datetime.now(timezone.utc),
        created_by_user_id=actor.id,
    )
    session.add_all([item, deployment])
    await session.flush()
    if failed_action:
        session.add(
            ExecutionAction(
                handover_case_id=case.id,
                handover_item_id=item.id,
                action_type=HandoverAction.TRANSFER_OWNER.value,
                provider=RuntimeProvider.CUSTOM,
                status=ExecutionStatus.FAILED,
                result_json={"note": "provider rejected transfer"},
            )
        )
        await session.flush()
    return actor, namespace, case, asset, evidence


async def _fulfil_blocking(session, snapshot, actor, evidence):
    for obligation in snapshot.obligations:
        if obligation.severity != HandoverObligationSeverity.BLOCKING:
            continue
        await record_handover_obligation_receipt(
            session,
            obligation_public_id=obligation.public_id,
            request=HandoverObligationReceiptCreate(
                decision=HandoverObligationReceiptDecision.FULFILLED,
                note=f"Resolved {obligation.obligation_key}",
                evidence_ids=[evidence.id] if obligation.requires_evidence else [],
                idempotency_key=f"resolve-{obligation.public_id}",
            ),
            actor=actor,
        )


async def test_snapshot_is_idempotent_deterministic_and_emits_typed_obligations(async_session):
    actor, namespace, case, _asset, _evidence = await _seed_case(async_session)
    request = HandoverEvidenceSnapshotCreate(
        handover_case_id=case.id,
        idempotency_key="handover-snapshot-v1",
    )

    first = await create_handover_snapshot(async_session, request=request, actor=actor)
    replay = await create_handover_snapshot(async_session, request=request, actor=actor)
    second = await create_handover_snapshot(
        async_session,
        request=HandoverEvidenceSnapshotCreate(
            handover_case_id=case.id,
            idempotency_key="handover-snapshot-v2",
        ),
        actor=actor,
    )

    assert replay.id == first.id
    assert first.sequence == 1
    assert second.sequence == 2
    assert first.snapshot_digest == second.snapshot_digest
    assert first.namespace_id == namespace.id
    assert first.readiness_outcome == HandoverReadinessOutcome.BLOCKED
    assert any(node["kind"] == "deployment" for node in first.nodes_json)
    assert any(node["kind"] == "asset" for node in first.nodes_json)
    assert first.nodes_json == sorted(first.nodes_json, key=lambda row: row["key"])
    assert first.edges_json == sorted(first.edges_json, key=lambda row: row["key"])
    assert any(
        obligation.obligation_type == HandoverObligationType.EVALUATION_BASELINE
        and obligation.severity == HandoverObligationSeverity.BLOCKING
        for obligation in first.obligations
    )
    rendered = handover_snapshot_out(first)
    assert rendered.current_readiness_outcome == HandoverReadinessOutcome.BLOCKED
    assert rendered.open_blocking_obligation_count >= 1


async def test_required_evidence_obligation_fails_closed_and_cross_tenant_is_rejected(async_session):
    actor, _namespace, case, _asset, _evidence = await _seed_case(async_session)
    snapshot = await create_handover_snapshot(
        async_session,
        request=HandoverEvidenceSnapshotCreate(
            handover_case_id=case.id,
            idempotency_key="handover-evidence-gate",
        ),
        actor=actor,
    )
    obligation = next(item for item in snapshot.obligations if item.requires_evidence)

    with pytest.raises(HandoverEvidenceReferenceError, match="evidence is required"):
        await record_handover_obligation_receipt(
            async_session,
            obligation_public_id=obligation.public_id,
            request=HandoverObligationReceiptCreate(
                decision=HandoverObligationReceiptDecision.FULFILLED,
                note="No evidence supplied",
                idempotency_key="missing-evidence",
            ),
            actor=actor,
        )

    other_namespace = Namespace(name=f"other-{uuid4().hex[:12]}", owner_id=actor.id)
    async_session.add(other_namespace)
    await async_session.flush()
    foreign_evidence = EvidenceItem(
        namespace_id=other_namespace.id,
        source_type=EvidenceSourceType.USER_CONFIRM,
        source_provider=RuntimeProvider.CUSTOM,
        summary="Foreign evidence",
        created_by=actor.id,
    )
    async_session.add(foreign_evidence)
    await async_session.flush()
    with pytest.raises(HandoverEvidenceReferenceError, match="belong to the Namespace"):
        await record_handover_obligation_receipt(
            async_session,
            obligation_public_id=obligation.public_id,
            request=HandoverObligationReceiptCreate(
                decision=HandoverObligationReceiptDecision.FULFILLED,
                note="Wrong tenant evidence",
                evidence_ids=[foreign_evidence.id],
                idempotency_key="foreign-evidence",
            ),
            actor=actor,
        )


async def test_failed_action_requires_explicit_acceptance_acknowledgement(async_session):
    actor, _namespace, case, _asset, evidence = await _seed_case(async_session, failed_action=True)
    snapshot = await create_handover_snapshot(
        async_session,
        request=HandoverEvidenceSnapshotCreate(
            handover_case_id=case.id,
            idempotency_key="handover-failed-action",
        ),
        actor=actor,
    )
    await _fulfil_blocking(async_session, snapshot, actor, evidence)

    with pytest.raises(HandoverEvidenceStateError, match="explicit acknowledgement"):
        await create_handover_acceptance(
            async_session,
            request=HandoverAcceptanceCreate(
                snapshot_public_id=snapshot.public_id,
                decision=HandoverAcceptanceDecision.ACCEPTED,
                comment="Accept without acknowledging failure",
                idempotency_key="accept-no-ack",
            ),
            actor=actor,
        )

    accepted = await create_handover_acceptance(
        async_session,
        request=HandoverAcceptanceCreate(
            snapshot_public_id=snapshot.public_id,
            decision=HandoverAcceptanceDecision.ACCEPTED,
            comment="Failure reviewed and accepted",
            acknowledges_failures=True,
            idempotency_key="accept-with-ack",
        ),
        actor=actor,
    )
    assert accepted.decision == HandoverAcceptanceDecision.ACCEPTED
    assert accepted.acknowledges_failures is True


async def test_external_ed25519_signed_package_round_trip(async_session, monkeypatch):
    actor, namespace, case, _asset, evidence = await _seed_case(async_session)
    snapshot = await create_handover_snapshot(
        async_session,
        request=HandoverEvidenceSnapshotCreate(
            handover_case_id=case.id,
            idempotency_key="handover-signed-snapshot",
        ),
        actor=actor,
    )
    await _fulfil_blocking(async_session, snapshot, actor, evidence)
    acceptance = await create_handover_acceptance(
        async_session,
        request=HandoverAcceptanceCreate(
            snapshot_public_id=snapshot.public_id,
            decision=HandoverAcceptanceDecision.ACCEPTED,
            comment="Receiver accepts exact snapshot",
            idempotency_key="handover-signed-acceptance",
        ),
        actor=actor,
    )
    private_key = Ed25519PrivateKey.generate()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    signing_key = await create_signing_key(
        async_session,
        request=PackageSigningKeyCreate(
            namespace_id=namespace.id,
            key_id=f"handover-{uuid4().hex[:12]}",
            algorithm=PackageSignatureAlgorithm.ED25519,
            public_key_pem=public_pem,
        ),
        actor=actor,
    )
    payload = await handover_signing_payload(
        async_session,
        acceptance_public_id=acceptance.public_id,
    )
    payload_bytes = base64.b64decode(payload.payload_base64)
    signature = base64.b64encode(private_key.sign(payload_bytes)).decode("ascii")
    stored: dict[str, bytes] = {}

    def _capture(*, object_key, body, content_type, metadata):
        stored[object_key] = body

    monkeypatch.setattr(handover_evidence_service, "_put_object", _capture)
    package = await create_handover_signed_package(
        async_session,
        request=HandoverSignedPackageCreate(
            acceptance_public_id=acceptance.public_id,
            signing_key_public_id=signing_key.public_id,
            signature=signature,
            idempotency_key="handover-signed-package",
        ),
        actor=actor,
    )

    assert package.manifest_digest == payload.manifest_digest
    assert package.archive_digest == package.evidence_item.sha256
    assert package.signing_key.public_key_fingerprint == signing_key.public_key_fingerprint
    key = package.evidence_item.object_uri.split(f"/{handover_evidence_service.artifact_service.bucket}/", 1)[1]
    archive = stored[key]
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        assert bundle.namelist() == ["manifest.json", "signature.json"]
        manifest_bytes = bundle.read("manifest.json")
        signature_document = json.loads(bundle.read("signature.json"))
    assert manifest_bytes == payload_bytes
    assert signature_document["manifest_digest"] == payload.manifest_digest
    private_key.public_key().verify(base64.b64decode(signature_document["signature"]), manifest_bytes)
    outbox_rows = list(
        (
            await async_session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type.in_(
                        {
                            "HandoverSnapshotCreated",
                            "HandoverObligationReceiptRecorded",
                            "HandoverAcceptanceRecorded",
                            "HandoverSignedPackageCreated",
                        }
                    )
                )
            )
        ).scalars().all()
    )
    assert {row.event_type for row in outbox_rows} == {
        "HandoverSnapshotCreated",
        "HandoverObligationReceiptRecorded",
        "HandoverAcceptanceRecorded",
        "HandoverSignedPackageCreated",
    }
    serialized = json.dumps([row.payload_json for row in outbox_rows], sort_keys=True)
    assert "Receiver accepts exact snapshot" not in serialized
    assert "Resolved " not in serialized
    assert signature not in serialized
