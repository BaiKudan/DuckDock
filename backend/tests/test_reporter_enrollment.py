from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from fastapi import HTTPException

from app.api.v1.endpoints.control_plane import (
    enroll_reporter_endpoint,
    list_reporter_credentials,
    reporter_heartbeat_endpoint,
    revoke_reporter_credential,
    rotate_reporter_credential,
    test_runtime as runtime_self_check,
)
from app.core.security import hash_password
from app.models.audit import AuditLog
from app.models.control_plane import (
    ReporterCredential,
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
    RuntimeReportToken,
    RuntimeStatus,
)
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.user import SystemRole, User
from app.schemas.control_plane import ReporterCredentialRevoke, ReporterCredentialRotate, ReporterEnrollmentCreate, ReporterHeartbeat
from app.services.report_upload_service import authenticate_runtime_report_token


async def _user(session) -> User:
    user = User(
        username="employee",
        email="employee@example.com",
        full_name="Employee One",
        hashed_password=hash_password("password"),
        system_role=SystemRole.USER,
    )
    session.add(user)
    await session.flush()
    return user


async def _namespace(session, user: User, suffix: str = "default") -> Namespace:
    namespace = Namespace(name=f"reporter-{user.username}-{suffix}", owner_id=user.id)
    session.add(namespace)
    await session.flush()
    session.add(
        NamespaceMember(
            namespace_id=namespace.id,
            user_id=user.id,
            role=NamespaceRole.ADMIN,
        )
    )
    await session.flush()
    return namespace


async def test_self_service_reporter_enrollment_issues_long_lived_reporter_credential(async_session):
    user = await _user(async_session)
    namespace = await _namespace(async_session, user)

    out = await enroll_reporter_endpoint(
        ReporterEnrollmentCreate(
            namespace_id=namespace.id,
            runtime_name="Employee Hermes Mac mini",
            device_id="test-workstation",
            agent_kind="hermes",
            reporter_version="0.18.1",
            schedule_json={"cron": "0 * * * *"},
            metadata_json={"workspace": "local-validation"},
        ),
        async_session,
        user,
    )

    assert out.runtime.name == "Employee Hermes Mac mini"
    assert out.runtime.metadata_json["workspace"] == "local-validation"
    assert out.runtime.metadata_json["reporter"]["enrollment"]["mode"] == "self_service"
    assert out.runtime.metadata_json["reporter"]["enrollment"]["user_id"] == user.id
    assert out.runtime.metadata_json["reporter"]["enrollment"]["device_id"] == "test-workstation"
    assert out.credential.token.startswith("dkr_report_")
    assert out.credential.user_id == user.id
    assert out.credential.expires_at is None

    credential_row = (
        await async_session.execute(select(ReporterCredential).where(ReporterCredential.id == out.credential.id))
    ).scalar_one()
    assert credential_row.is_active is True
    assert credential_row.token_hash != out.credential.token
    assert credential_row.device_id == "test-workstation"
    assert credential_row.scopes == ["report.upload", "report.structured", "report.heartbeat"]

    auth = await authenticate_runtime_report_token(async_session, out.credential.token)
    assert auth.runtime.id == out.runtime.id
    assert auth.token.id == out.credential.id
    assert auth.source == "reporter_credential"

    audit_row = (await async_session.execute(select(AuditLog).where(AuditLog.action == "reporter.enrolled"))).scalar_one()
    assert audit_row.user_id == user.id
    assert audit_row.resource_id == out.runtime.id


async def test_legacy_runtime_report_token_still_authenticates(async_session):
    runtime = RuntimeInstance(provider=RuntimeProvider.CUSTOM, name="legacy", deploy_type=RuntimeDeployType.PRIVATE)
    async_session.add(runtime)
    await async_session.flush()
    legacy = RuntimeReportToken(
        runtime_id=runtime.id,
        name="legacy reporter token",
        token_prefix="abcd1234",
        token_hash=hash_password("legacy-secret"),
    )
    async_session.add(legacy)
    await async_session.flush()

    auth = await authenticate_runtime_report_token(async_session, "dkr_report_abcd1234_legacy-secret")

    assert auth.runtime.id == runtime.id
    assert auth.token.id == legacy.id
    assert auth.source == "runtime_report_token"


async def test_reporter_heartbeat_updates_runtime_liveness_without_admin_jwt(async_session):
    user = await _user(async_session)
    namespace = await _namespace(async_session, user)
    enrolled = await enroll_reporter_endpoint(
        ReporterEnrollmentCreate(
            namespace_id=namespace.id,
            runtime_name="Employee Hermes",
            device_id="test-workstation",
            agent_kind="hermes",
        ),
        async_session,
        user,
    )
    reporter = await authenticate_runtime_report_token(async_session, enrolled.credential.token)
    next_run = datetime(2030, 1, 1, 8, 0, tzinfo=timezone.utc)

    out = await reporter_heartbeat_endpoint(
        ReporterHeartbeat(
            device_id="test-workstation",
            reporter_version="0.18.2",
            agent_version="Hermes Agent v0.18.1",
            status="degraded",
            next_run_at=next_run,
            schedule_json={"interval": "hourly"},
            capabilities_json={"report_pack": "duckdock-pack-v1"},
            metadata_json={"last_error": "model key missing"},
        ),
        async_session,
        reporter,
    )

    assert out.runtime_id == enrolled.runtime.id
    assert out.token_id == enrolled.credential.id
    assert out.credential_source == "reporter_credential"
    assert out.status == "degraded"
    assert out.upload_recommended is True
    assert reporter.runtime.status == RuntimeStatus.DEGRADED
    assert reporter.token.last_used_at is not None
    assert reporter.token.last_heartbeat_at is not None

    heartbeat = reporter.runtime.metadata_json["reporter"]["heartbeat"]
    assert heartbeat["device_id"] == "test-workstation"
    assert heartbeat["reporter_version"] == "0.18.2"
    assert heartbeat["next_run_at"] == next_run.isoformat()
    assert heartbeat["capabilities_json"] == {"report_pack": "duckdock-pack-v1"}
    assert reporter.token.heartbeat_json == heartbeat


@pytest.mark.parametrize("tenant_state", ["missing", "inactive"])
async def test_reporter_heartbeat_rejects_runtime_without_active_namespace(
    async_session,
    tenant_state: str,
):
    user = await _user(async_session)
    namespace = await _namespace(async_session, user, suffix=tenant_state)
    enrolled = await enroll_reporter_endpoint(
        ReporterEnrollmentCreate(
            namespace_id=namespace.id,
            runtime_name=f"Invalid tenant reporter {tenant_state}",
            device_id=f"test-workstation-{tenant_state}",
            agent_kind="hermes",
        ),
        async_session,
        user,
    )
    reporter = await authenticate_runtime_report_token(
        async_session,
        enrolled.credential.token,
    )
    if tenant_state == "missing":
        reporter.runtime.namespace_id = None
    else:
        namespace.deleted_at = datetime.now(timezone.utc)

    original_metadata = reporter.runtime.metadata_json
    original_status = reporter.runtime.status

    with pytest.raises(HTTPException) as exc_info:
        await reporter_heartbeat_endpoint(
            ReporterHeartbeat(status="degraded"),
            async_session,
            reporter,
        )

    assert exc_info.value.status_code == 422
    assert reporter.runtime.metadata_json == original_metadata
    assert reporter.runtime.status == original_status
    assert reporter.token.last_heartbeat_at is None
    assert reporter.token.heartbeat_json is None


async def test_runtime_self_check_accepts_self_service_reporter_credential(async_session):
    user = await _user(async_session)
    namespace = await _namespace(async_session, user)
    enrolled = await enroll_reporter_endpoint(
        ReporterEnrollmentCreate(
            namespace_id=namespace.id,
            runtime_name="Employee Hermes",
            device_id="test-workstation",
            agent_kind="hermes",
        ),
        async_session,
        user,
    )

    out = await runtime_self_check(enrolled.runtime.id, async_session, user)

    assert out.status == "waiting"
    assert "1 个上报凭证就绪" in out.message


async def test_reporter_credential_rotation_revokes_old_token_and_returns_new_secret(async_session):
    user = await _user(async_session)
    namespace = await _namespace(async_session, user)
    enrolled = await enroll_reporter_endpoint(
        ReporterEnrollmentCreate(
            namespace_id=namespace.id,
            runtime_name="Employee Hermes",
            device_id="test-workstation",
            agent_kind="hermes",
        ),
        async_session,
        user,
    )

    rotated = await rotate_reporter_credential(
        enrolled.credential.id,
        ReporterCredentialRotate(reason="device reprovision"),
        async_session,
        user,
    )

    assert rotated.id != enrolled.credential.id
    assert rotated.token.startswith("dkr_report_")
    assert rotated.device_id == "test-workstation"
    assert rotated.rotated_from_id == enrolled.credential.id

    old = (
        await async_session.execute(select(ReporterCredential).where(ReporterCredential.id == enrolled.credential.id))
    ).scalar_one()
    assert old.is_active is False
    assert old.revoked_reason == "device reprovision"

    with pytest_raises_401():
        await authenticate_runtime_report_token(async_session, enrolled.credential.token)
    new_auth = await authenticate_runtime_report_token(async_session, rotated.token)
    assert new_auth.token.id == rotated.id


async def test_reporter_credential_revoke_blocks_future_auth(async_session):
    user = await _user(async_session)
    namespace = await _namespace(async_session, user)
    enrolled = await enroll_reporter_endpoint(
        ReporterEnrollmentCreate(
            namespace_id=namespace.id,
            runtime_name="Employee Hermes",
            device_id="test-workstation",
            agent_kind="hermes",
        ),
        async_session,
        user,
    )

    revoked = await revoke_reporter_credential(
        enrolled.credential.id,
        ReporterCredentialRevoke(reason="lost laptop"),
        async_session,
        user,
    )

    assert revoked.is_active is False
    assert revoked.revoked_reason == "lost laptop"
    with pytest_raises_401():
        await authenticate_runtime_report_token(async_session, enrolled.credential.token)


async def test_user_lists_only_own_reporter_credentials(async_session):
    user = await _user(async_session)
    other = User(
        username="other",
        email="other@example.com",
        full_name="Other User",
        hashed_password=hash_password("password"),
        system_role=SystemRole.USER,
    )
    async_session.add(other)
    await async_session.flush()
    mine_namespace = await _namespace(async_session, user, "mine")
    other_namespace = await _namespace(async_session, other, "other")
    mine = await enroll_reporter_endpoint(
        ReporterEnrollmentCreate(
            namespace_id=mine_namespace.id,
            runtime_name="Mine",
            device_id="mine",
            agent_kind="hermes",
        ),
        async_session,
        user,
    )
    await enroll_reporter_endpoint(
        ReporterEnrollmentCreate(
            namespace_id=other_namespace.id,
            runtime_name="Other",
            device_id="other",
            agent_kind="hermes",
        ),
        async_session,
        other,
    )

    rows = await list_reporter_credentials(async_session, user)
    assert [row.id for row in rows] == [mine.credential.id]


class pytest_raises_401:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
        assert exc_type is HTTPException
        assert exc.status_code == 401
        return True
