"""RBAC 角色绑定（RoleBinding）解析测试 —— 命名空间作用域与系统级门禁。

补充 test_authz.py（基于 NamespaceMember 的 legacy 角色）之外的 RBAC 绑定路径：
验证角色绑定按命名空间作用域生效、不跨命名空间泄漏（越权防护），以及系统级 iam 门禁。
对应 constitution 原则 IV/V · specs/002-test-harness。

注：RoleBinding 过期（expires_at）的越权用例依赖 SQL 层 tz-aware 时间比较，在内存 SQLite 上
不稳定，留作 MySQL 集成测试层（参考 specs/002 FR-003）。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.deps import require_iam_admin
from app.models.iam import Permission, Role, RoleBinding, RolePermission, RoleScope
from app.models.namespace import Namespace
from app.models.user import SystemRole, User
from app.services.iam_service import ensure_builtin_rbac, has_permission


async def _make_user(session, *, username, system_role=SystemRole.USER):
    user = User(
        username=username,
        email=f"{username}@duckdock-ai.com",
        hashed_password="x",
        system_role=system_role,
    )
    session.add(user)
    await session.flush()
    return user


async def _make_namespace(session, *, owner, name):
    namespace = Namespace(name=name, owner_id=owner.id)
    session.add(namespace)
    await session.flush()
    return namespace


async def _role(session, key):
    return (await session.execute(select(Role).where(Role.key == key))).scalar_one()


async def test_role_binding_grants_scoped_permission(async_session):
    owner = await _make_user(async_session, username="owner1")
    ns = await _make_namespace(async_session, owner=owner, name="team-a")
    user = await _make_user(async_session, username="dev1")
    await ensure_builtin_rbac(async_session)
    dev_role = await _role(async_session, "namespace-developer")
    async_session.add(RoleBinding(role_id=dev_role.id, user_id=user.id, namespace_id=ns.id))
    await async_session.flush()

    assert await has_permission(async_session, user=user, permission_key="namespace.write", namespace_id=ns.id) is True
    # developer 绑定不含 namespace.admin —— 不能越权到管理权限
    assert await has_permission(async_session, user=user, permission_key="namespace.admin", namespace_id=ns.id) is False


async def test_role_binding_does_not_leak_across_namespaces(async_session):
    owner = await _make_user(async_session, username="owner2")
    ns_a = await _make_namespace(async_session, owner=owner, name="team-a")
    ns_b = await _make_namespace(async_session, owner=owner, name="team-b")
    user = await _make_user(async_session, username="dev2")
    await ensure_builtin_rbac(async_session)
    dev_role = await _role(async_session, "namespace-developer")
    async_session.add(RoleBinding(role_id=dev_role.id, user_id=user.id, namespace_id=ns_a.id))
    await async_session.flush()

    # 越权防护：在 ns_a 的绑定不得在 ns_b 生效
    assert await has_permission(async_session, user=user, permission_key="namespace.write", namespace_id=ns_a.id) is True
    assert await has_permission(async_session, user=user, permission_key="namespace.write", namespace_id=ns_b.id) is False


async def test_system_scope_binding_satisfies_iam_admin_gate(async_session):
    plain = await _make_user(async_session, username="plain3")

    # 无任何授权 → require_iam_admin 拒绝
    with pytest.raises(HTTPException) as exc:
        await require_iam_admin(plain, async_session)
    assert exc.value.status_code == 403

    # 绑定 enterprise-admin（系统作用域，namespace_id=None，含 iam.manage）→ 放行
    await ensure_builtin_rbac(async_session)
    admin_role = await _role(async_session, "enterprise-admin")
    async_session.add(RoleBinding(role_id=admin_role.id, user_id=plain.id, namespace_id=None))
    await async_session.flush()

    assert await require_iam_admin(plain, async_session) is plain


async def test_role_permission_revocation_ignores_stale_binding_cache(async_session):
    user = await _make_user(async_session, username="sensitive-reader")
    await ensure_builtin_rbac(async_session)
    permission = (
        await async_session.execute(select(Permission).where(Permission.key == "evidence.sensitive.read"))
    ).scalar_one()
    role = Role(
        key="custom-sensitive-reader",
        name="Custom Sensitive Reader",
        scope=RoleScope.SYSTEM,
        description="Temporary test role",
        is_system=False,
    )
    async_session.add(role)
    await async_session.flush()
    role_permission = RolePermission(role_id=role.id, permission_id=permission.id)
    async_session.add(role_permission)
    await async_session.flush()
    async_session.add(
        RoleBinding(
            role_id=role.id,
            user_id=user.id,
            namespace_id=None,
            permission_cache={"keys": [permission.key]},
        )
    )
    await async_session.flush()

    assert await has_permission(
        async_session,
        user=user,
        permission_key="evidence.sensitive.read",
    ) is True

    await async_session.delete(role_permission)
    await async_session.flush()
    async_session.expire(role, ["permissions"])

    assert await has_permission(
        async_session,
        user=user,
        permission_key="evidence.sensitive.read",
    ) is False
