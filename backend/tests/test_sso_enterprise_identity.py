from __future__ import annotations

from sqlalchemy import select

from app.api.v1.endpoints.iam import (
    create_sso_role_mapping,
    delete_sso_role_mapping,
    list_identity_links,
    list_sso_role_mappings,
    update_sso_role_mapping,
)
from app.api.v1.endpoints.auth import register
from app.models.iam import IdentityLink, Role, RoleBinding, SSOProviderConfig, SSOProviderType, SSORoleMapping
from app.models.user import AuthSource, SystemRole, User
from app.schemas.auth import RegisterRequest
from app.schemas.iam import SSORoleMappingCreate, SSORoleMappingUpdate
from app.services.iam_service import effective_permissions, ensure_builtin_rbac
from app.services.sso_service import upsert_external_user


async def _provider(session, *, name: str, provider_type: SSOProviderType = SSOProviderType.OIDC) -> SSOProviderConfig:
    provider = SSOProviderConfig(
        provider_type=provider_type,
        name=name,
        enabled=True,
        issuer_url=f"https://{name}.example.com" if provider_type == SSOProviderType.OIDC else None,
        ldap_server_url=f"ldaps://{name}.example.com" if provider_type == SSOProviderType.LDAP else None,
        attribute_mapping={
            "enterprise_uid": "employee_id",
            "external_uid": "employee_id",
        },
    )
    session.add(provider)
    await session.flush()
    return provider


async def _admin(session) -> User:
    user = User(
        username="iam-admin",
        email="iam-admin@example.com",
        hashed_password="x",
        system_role=SystemRole.ADMIN,
    )
    session.add(user)
    await session.flush()
    return user


class _NoopLimiter:
    def client_ip(self, _request) -> str:  # noqa: ANN001 - tiny endpoint test double
        return "127.0.0.1"

    async def precheck(self, _scope: str, _identity: str, _ip: str) -> None:
        return None

    async def register_failure(self, _scope: str, _identity: str, _ip: str) -> None:
        return None


async def test_local_register_assigns_duckdock_enterprise_uid(async_session):
    user = await register(
        RegisterRequest(
            username="local-employee",
            email="local-employee@example.com",
            password="DuckDock@2026",
            full_name="Local Employee",
        ),
        async_session,
        object(),  # endpoint uses the request only through the limiter test double
        _NoopLimiter(),
    )

    assert user.enterprise_uid is not None
    assert user.enterprise_uid.startswith("duid_")


async def test_sso_upsert_assigns_enterprise_uid_and_links_multiple_providers(async_session):
    oidc = await _provider(async_session, name="oidc-main")
    ldap = await _provider(async_session, name="ldap-main", provider_type=SSOProviderType.LDAP)

    user = await upsert_external_user(
        async_session,
        provider=oidc,
        source=AuthSource.OIDC,
        claims={
            "sub": "oidc-subject-1",
            "employee_id": "EMP-001",
            "email": "alice@example.com",
            "preferred_username": "alice",
            "name": "Alice Example",
        },
    )
    await async_session.flush()

    assert user.enterprise_uid == "EMP-001"
    assert user.external_subject == f"oidc:{oidc.id}:oidc-subject-1"

    same_user = await upsert_external_user(
        async_session,
        provider=ldap,
        source=AuthSource.LDAP,
        claims={
            "entry_dn": "uid=alice,ou=people,dc=example,dc=com",
            "employee_id": "EMP-001",
            "mail": "alice@example.com",
            "uid": "alice",
            "cn": "Alice Example",
        },
    )
    await async_session.flush()

    assert same_user.id == user.id
    links = (await async_session.execute(select(IdentityLink).where(IdentityLink.user_id == user.id))).scalars().all()
    assert {(link.provider_id, link.external_uid) for link in links} == {
        (oidc.id, "EMP-001"),
        (ldap.id, "EMP-001"),
    }
    listed = await list_identity_links(async_session, await _admin(async_session), user_id=user.id)
    assert {link.provider_id for link in listed} == {oidc.id, ldap.id}


async def test_sso_role_mapping_creates_and_revokes_managed_role_binding(async_session):
    await ensure_builtin_rbac(async_session)
    provider = await _provider(async_session, name="oidc-rbac")
    role = (await async_session.execute(select(Role).where(Role.key == "enterprise-admin"))).scalar_one()
    mapping = SSORoleMapping(
        provider_id=provider.id,
        claim_name="groups",
        claim_value="duckdock-admins",
        role_id=role.id,
        enabled=True,
    )
    async_session.add(mapping)
    await async_session.flush()

    user = await upsert_external_user(
        async_session,
        provider=provider,
        source=AuthSource.OIDC,
        claims={
            "sub": "admin-subject",
            "employee_id": "EMP-ADM",
            "email": "admin@example.com",
            "preferred_username": "admin",
            "groups": ["duckdock-admins"],
        },
    )
    await async_session.flush()

    permissions = await effective_permissions(async_session, user=user)
    assert "iam.manage" in permissions
    managed_bindings = (
        await async_session.execute(select(RoleBinding).where(RoleBinding.user_id == user.id))
    ).scalars().all()
    assert len(managed_bindings) == 1
    assert managed_bindings[0].permission_cache["managed_by"] == "sso_role_mapping"
    assert managed_bindings[0].permission_cache["mapping_id"] == mapping.id

    await upsert_external_user(
        async_session,
        provider=provider,
        source=AuthSource.OIDC,
        claims={
            "sub": "admin-subject",
            "employee_id": "EMP-ADM",
            "email": "admin@example.com",
            "preferred_username": "admin",
            "groups": [],
        },
    )
    await async_session.flush()

    managed_bindings = (
        await async_session.execute(select(RoleBinding).where(RoleBinding.user_id == user.id))
    ).scalars().all()
    assert managed_bindings == []


async def test_sso_role_mapping_does_not_overwrite_manual_role_binding(async_session):
    await ensure_builtin_rbac(async_session)
    provider = await _provider(async_session, name="oidc-manual")
    role = (await async_session.execute(select(Role).where(Role.key == "enterprise-admin"))).scalar_one()
    async_session.add(
        SSORoleMapping(
            provider_id=provider.id,
            claim_name="groups",
            claim_value="duckdock-admins",
            role_id=role.id,
            enabled=True,
        )
    )
    await async_session.flush()

    user = await upsert_external_user(
        async_session,
        provider=provider,
        source=AuthSource.OIDC,
        claims={
            "sub": "manual-subject",
            "employee_id": "EMP-MANUAL",
            "email": "manual@example.com",
            "preferred_username": "manual",
            "groups": [],
        },
    )
    manual_cache = {"keys": ["iam.manage"], "managed_by": "admin_manual"}
    async_session.add(RoleBinding(role_id=role.id, user_id=user.id, permission_cache=manual_cache))
    await async_session.flush()

    await upsert_external_user(
        async_session,
        provider=provider,
        source=AuthSource.OIDC,
        claims={
            "sub": "manual-subject",
            "employee_id": "EMP-MANUAL",
            "email": "manual@example.com",
            "preferred_username": "manual",
            "groups": ["duckdock-admins"],
        },
    )
    await async_session.flush()

    bindings = (await async_session.execute(select(RoleBinding).where(RoleBinding.user_id == user.id))).scalars().all()
    assert len(bindings) == 1
    assert bindings[0].permission_cache == manual_cache

    await upsert_external_user(
        async_session,
        provider=provider,
        source=AuthSource.OIDC,
        claims={
            "sub": "manual-subject",
            "employee_id": "EMP-MANUAL",
            "email": "manual@example.com",
            "preferred_username": "manual",
            "groups": [],
        },
    )
    await async_session.flush()

    bindings = (await async_session.execute(select(RoleBinding).where(RoleBinding.user_id == user.id))).scalars().all()
    assert len(bindings) == 1
    assert bindings[0].permission_cache == manual_cache


async def test_sso_role_mapping_management_endpoints(async_session):
    admin = await _admin(async_session)
    await ensure_builtin_rbac(async_session)
    provider = await _provider(async_session, name="oidc-api")
    role = (await async_session.execute(select(Role).where(Role.key == "enterprise-admin"))).scalar_one()

    created = await create_sso_role_mapping(
        SSORoleMappingCreate(
            provider_id=provider.id,
            claim_name="groups",
            claim_value="duckdock-ops",
            role_id=role.id,
            priority=50,
        ),
        async_session,
        admin,
    )
    assert created.provider_id == provider.id
    assert created.claim_value == "duckdock-ops"

    listed = await list_sso_role_mappings(async_session, admin, provider_id=provider.id)
    assert [row.id for row in listed] == [created.id]

    updated = await update_sso_role_mapping(
        created.id,
        SSORoleMappingUpdate(enabled=False, priority=80, description="paused by IdP cutover"),
        async_session,
        admin,
    )
    assert updated.enabled is False
    assert updated.priority == 80
    assert updated.description == "paused by IdP cutover"

    await delete_sso_role_mapping(created.id, async_session, admin)
    assert await list_sso_role_mappings(async_session, admin, provider_id=provider.id) == []
