from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException
from sqlalchemy import select

from app.models.control_plane import (
    AIAsset,
    AssetOwnership,
    AssetType,
    HandoverCase,
    OwnerType,
    ReporterCredential,
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
    RuntimeReportToken,
    WorkloadIdentityKind,
)
from app.models.iam import (
    DirectoryLifecycleAction,
    EmploymentStatus,
    IdentityLink,
    Role,
    RoleBinding,
    RoleScope,
    SSOProviderConfig,
    SSOProviderType,
    UserHandoverProfile,
)
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.outbox import OutboxEvent
from app.models.package_registry import PackageSigningKeyStatus
from app.models.user import AuthSource, SystemRole, User
from app.schemas.identity_security import (
    DirectoryCredentialCreate,
    WorkloadIdentityCreate,
)
from app.schemas.package_registry import PackageSigningKeyCreate, PackageSigningKeyRotate
from app.services.identity_security_service import (
    IdentitySecurityConflictError,
    IdentitySecurityCredentialError,
    apply_scim_user_lifecycle,
    authenticate_directory_credential,
    create_directory_credential,
    create_workload_identity,
    revoke_directory_credential,
    revoke_workload_identity,
    rotate_workload_identity,
)
from app.services.package_registry_service import create_signing_key, rotate_signing_key
from app.services.report_upload_service import authenticate_runtime_report_token


def test_identity_security_openapi_surface_is_explicit():
    from app.main import app

    paths = app.openapi()["paths"]
    for path in (
        "/api/v2/identity/directory-credentials",
        "/api/v2/identity/directory-credentials/{public_id}/revoke",
        "/api/v2/identity/directory-events",
        "/api/v2/scim/v2/Users/{external_subject}",
        "/api/v2/identity/workload-identities",
        "/api/v2/identity/workload-identities/{public_id}/rotate",
        "/api/v2/identity/workload-identities/{public_id}/revoke",
        "/api/v2/package-signing-keys/{public_id}/rotate",
    ):
        assert path in paths


async def _user(db, label: str, *, admin: bool = False) -> User:
    suffix = uuid4().hex[:8]
    row = User(
        username=f"{label}-{suffix}",
        email=f"{label}-{suffix}@example.test",
        hashed_password="unused",
        system_role=SystemRole.ADMIN if admin else SystemRole.USER,
    )
    db.add(row)
    await db.flush()
    return row


async def _identity_seed(db):
    actor = await _user(db, "identity-admin", admin=True)
    subject = await _user(db, "directory-subject")
    receiver = await _user(db, "handover-receiver")
    namespace = Namespace(name=f"identity-{uuid4().hex[:12]}", owner_id=actor.id)
    provider = SSOProviderConfig(
        provider_type=SSOProviderType.OIDC,
        name=f"staging-directory-{uuid4().hex[:8]}",
        enabled=True,
        issuer_url="https://directory.example.test",
    )
    db.add_all([namespace, provider])
    await db.flush()
    db.add_all(
        [
            NamespaceMember(
                namespace_id=namespace.id,
                user_id=actor.id,
                role=NamespaceRole.ADMIN,
            ),
            NamespaceMember(
                namespace_id=namespace.id,
                user_id=subject.id,
                role=NamespaceRole.DEVELOPER,
            ),
            NamespaceMember(
                namespace_id=namespace.id,
                user_id=receiver.id,
                role=NamespaceRole.DEVELOPER,
            ),
        ]
    )
    link = IdentityLink(
        user_id=subject.id,
        provider_id=provider.id,
        source=AuthSource.OIDC,
        issuer=provider.issuer_url,
        external_subject="directory-user-42",
        external_uid="employee-42",
        username=subject.username,
        email=subject.email,
        is_active=True,
    )
    profile = UserHandoverProfile(
        user_id=subject.id,
        manager_user_id=actor.id,
        handover_receiver_user_id=receiver.id,
        employment_status=EmploymentStatus.ACTIVE,
    )
    runtime = RuntimeInstance(
        namespace_id=namespace.id,
        provider=RuntimeProvider.CUSTOM,
        name="Hermes identity test",
        deploy_type=RuntimeDeployType.ON_PREM,
    )
    role = Role(
        key=f"identity-role-{uuid4().hex[:8]}",
        name="Identity test role",
        scope=RoleScope.NAMESPACE,
    )
    db.add_all([link, profile, runtime, role])
    await db.flush()
    db.add(
        RoleBinding(
            role_id=role.id,
            user_id=subject.id,
            namespace_id=namespace.id,
            granted_by=actor.id,
        )
    )
    asset = AIAsset(
        namespace_id=namespace.id,
        asset_type=AssetType.AGENT,
        name="Hermes directory-owned Agent",
        source_provider=RuntimeProvider.CUSTOM,
        source_runtime_id=runtime.id,
    )
    db.add(asset)
    await db.flush()
    db.add(
        AssetOwnership(
            namespace_id=namespace.id,
            asset_id=asset.id,
            owner_type=OwnerType.BUSINESS_OWNER,
            user_id=subject.id,
            is_primary=True,
        )
    )
    return actor, subject, receiver, namespace, provider, link, profile, runtime


@pytest.mark.asyncio
async def test_directory_credential_is_provider_bound_one_time_secret_and_revocable(
    async_session,
):
    actor, _subject, _receiver, _namespace, provider, *_ = await _identity_seed(
        async_session
    )
    row, token = await create_directory_credential(
        async_session,
        request=DirectoryCredentialCreate(
            provider_id=provider.id,
            name="staging SCIM",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
        actor=actor,
    )
    assert token.startswith("dkr_scim_")
    assert token not in row.token_hash
    authenticated = await authenticate_directory_credential(async_session, token)
    assert authenticated.id == row.id
    await revoke_directory_credential(
        async_session,
        public_id=row.public_id,
        reason="staging exercise complete",
        actor=actor,
    )
    with pytest.raises(IdentitySecurityCredentialError):
        await authenticate_directory_credential(async_session, token)


@pytest.mark.asyncio
async def test_scim_disable_revokes_access_and_triggers_handover_atomically(async_session):
    actor, subject, receiver, namespace, provider, link, profile, runtime = (
        await _identity_seed(async_session)
    )
    reporter = ReporterCredential(
        runtime_id=runtime.id,
        user_id=subject.id,
        device_id="subject-mac",
        name="subject reporter",
        token_prefix="abc12345",
        token_hash="hash",
        scopes=["execution.write"],
        principal_kind=WorkloadIdentityKind.DEVICE,
    )
    legacy = RuntimeReportToken(
        runtime_id=runtime.id,
        name="legacy subject token",
        token_prefix="legacy42",
        token_hash="hash",
        created_by=subject.id,
    )
    async_session.add_all([reporter, legacy])
    credential, _token = await create_directory_credential(
        async_session,
        request=DirectoryCredentialCreate(
            provider_id=provider.id, name="SCIM offboarding"
        ),
        actor=actor,
    )
    payload = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "active", "value": False}],
    }

    event = await apply_scim_user_lifecycle(
        async_session,
        credential=credential,
        external_subject=link.external_subject,
        external_event_id="evt-disable-42",
        active=False,
        payload=payload,
    )
    replay = await apply_scim_user_lifecycle(
        async_session,
        credential=credential,
        external_subject=link.external_subject,
        external_event_id="evt-disable-42",
        active=False,
        payload=payload,
    )

    assert replay.id == event.id
    assert event.action == DirectoryLifecycleAction.DISABLE
    assert event.credentials_revoked == 1
    assert event.runtime_tokens_revoked == 1
    assert event.memberships_removed == 1
    assert event.role_bindings_removed == 1
    assert len(event.handover_case_ids_json) == 1
    assert subject.is_active is False
    assert link.is_active is False and link.disabled_at is not None
    assert profile.employment_status == EmploymentStatus.OFFBOARDED
    assert reporter.is_active is False and reporter.revoked_at is not None
    assert legacy.is_active is False
    subject_membership = await async_session.scalar(
        select(NamespaceMember).where(
            NamespaceMember.namespace_id == namespace.id,
            NamespaceMember.user_id == subject.id,
        )
    )
    assert subject_membership is None
    case = await async_session.get(HandoverCase, event.handover_case_ids_json[0])
    assert case.receiver_user_id == receiver.id
    assert case.fallback_owner_user_id in {receiver.id, actor.id}
    assert case.summary_json["runtime_ids"] == [runtime.id]
    outbox = list((await async_session.scalars(select(OutboxEvent))).all())
    directory_events = [row for row in outbox if row.event_type == "DirectoryUserDisabled"]
    assert len(directory_events) == 1
    encoded = str(directory_events[0].payload_json).lower()
    assert subject.email.lower() not in encoded
    assert link.external_subject.lower() not in encoded
    assert "abc12345" not in encoded
    assert "legacy42" not in encoded
    assert "'hash'" not in encoded

    with pytest.raises(IdentitySecurityConflictError):
        await apply_scim_user_lifecycle(
            async_session,
            credential=credential,
            external_subject=link.external_subject,
            external_event_id="evt-disable-42",
            active=True,
            payload={**payload, "different": True},
        )


@pytest.mark.asyncio
async def test_workload_identity_rotation_preserves_scope_and_rejects_old_token(async_session):
    actor, _subject, _receiver, namespace, _provider, _link, _profile, runtime = (
        await _identity_seed(async_session)
    )
    identity, old_token = await create_workload_identity(
        async_session,
        request=WorkloadIdentityCreate(
            namespace_id=namespace.id,
            runtime_id=runtime.id,
            principal_kind=WorkloadIdentityKind.SERVICE,
            name="Hermes execution service",
            device_id="hermes-service-1",
            scopes=["execution.write", "report.heartbeat"],
        ),
        actor=actor,
    )
    resolved = await authenticate_runtime_report_token(async_session, old_token)
    assert resolved.token.id == identity.id

    successor, new_token, namespace_id = await rotate_workload_identity(
        async_session,
        public_id=identity.public_id,
        reason="scheduled key rotation",
        expires_at=None,
        actor=actor,
    )
    assert namespace_id == namespace.id
    assert successor.rotated_from_id == identity.id
    assert successor.generation == 2
    assert successor.scopes == identity.scopes
    with pytest.raises(HTTPException):
        await authenticate_runtime_report_token(async_session, old_token)
    assert (await authenticate_runtime_report_token(async_session, new_token)).token.id == successor.id

    revoked, _ = await revoke_workload_identity(
        async_session,
        public_id=successor.public_id,
        reason="service retired",
        actor=actor,
    )
    assert revoked.is_active is False
    event_types = set((await async_session.scalars(select(OutboxEvent.event_type))).all())
    assert {
        "WorkloadIdentityIssued",
        "WorkloadIdentityRotated",
        "WorkloadIdentityRevoked",
    } <= event_types


def _public_pem() -> str:
    return (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )


@pytest.mark.asyncio
async def test_package_signing_key_rotation_is_atomic_and_preserves_lineage(async_session):
    actor = await _user(async_session, "rotation-admin", admin=True)
    namespace = Namespace(name=f"rotation-{uuid4().hex[:12]}", owner_id=actor.id)
    async_session.add(namespace)
    await async_session.flush()
    previous = await create_signing_key(
        async_session,
        request=PackageSigningKeyCreate(
            namespace_id=namespace.id,
            key_id="signing-2026-q3",
            public_key_pem=_public_pem(),
        ),
        actor=actor,
    )
    successor = await rotate_signing_key(
        async_session,
        public_id=previous.public_id,
        request=PackageSigningKeyRotate(
            key_id="signing-2026-q4",
            public_key_pem=_public_pem(),
        ),
        actor=actor,
    )
    assert previous.status == PackageSigningKeyStatus.REVOKED
    assert successor.status == PackageSigningKeyStatus.ACTIVE
    assert successor.rotated_from_id == previous.id
    assert successor.rotation_sequence == 2
    assert previous.public_key_fingerprint != successor.public_key_fingerprint
    event_types = set((await async_session.scalars(select(OutboxEvent.event_type))).all())
    assert "PackageSigningKeyRotated" in event_types
    assert "PackageSigningKeyRevoked" in event_types
    assert "PackageSigningKeyRegistered" in event_types
