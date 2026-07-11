"""Authorization / 越权（privilege-escalation）矩阵测试。

覆盖 app/core/deps.py 的命名空间角色门禁与系统管理员门禁，以及 iam_service 的权限解析。
对应 constitution 原则 IV/V 与 specs/002-test-harness 的越权矩阵。全部跑在内存 SQLite 上。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core.deps import (
    get_namespace_role,
    require_admin,
    require_namespace_admin,
    require_namespace_member,
    require_namespace_writer,
)
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.user import SystemRole, User
from app.services.iam_service import has_permission


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


async def _add_member(session, *, namespace, user, role):
    session.add(NamespaceMember(namespace_id=namespace.id, user_id=user.id, role=role))
    await session.flush()


async def test_readonly_member_can_read_but_not_write(async_session):
    owner = await _make_user(async_session, username="owner1")
    ns = await _make_namespace(async_session, owner=owner, name="team-a")
    reader = await _make_user(async_session, username="reader1")
    await _add_member(async_session, namespace=ns, user=reader, role=NamespaceRole.READONLY)

    assert await get_namespace_role(reader, ns.id, async_session) == NamespaceRole.READONLY

    with pytest.raises(HTTPException) as exc:
        await require_namespace_writer(reader, ns.id, async_session)
    assert exc.value.status_code == 403


async def test_developer_member_can_write_but_is_not_admin(async_session):
    owner = await _make_user(async_session, username="owner2")
    ns = await _make_namespace(async_session, owner=owner, name="team-b")
    dev = await _make_user(async_session, username="dev2")
    await _add_member(async_session, namespace=ns, user=dev, role=NamespaceRole.DEVELOPER)

    assert await require_namespace_writer(dev, ns.id, async_session) == NamespaceRole.DEVELOPER

    with pytest.raises(HTTPException) as exc:
        await require_namespace_admin(dev, ns.id, async_session)
    assert exc.value.status_code == 403


async def test_non_member_is_denied(async_session):
    owner = await _make_user(async_session, username="owner3")
    ns = await _make_namespace(async_session, owner=owner, name="team-c")
    outsider = await _make_user(async_session, username="outsider3")

    assert await get_namespace_role(outsider, ns.id, async_session) is None
    with pytest.raises(HTTPException) as exc:
        await require_namespace_member(outsider, ns.id, async_session)
    assert exc.value.status_code == 403


async def test_system_admin_bypasses_namespace_gates(async_session):
    admin = await _make_user(async_session, username="root4", system_role=SystemRole.ADMIN)
    owner = await _make_user(async_session, username="owner4")
    ns = await _make_namespace(async_session, owner=owner, name="team-d")

    # 系统 admin 不是成员，但仍取得 namespace admin（effective_permissions 对系统 admin 全开）。
    assert await require_namespace_admin(admin, ns.id, async_session) == NamespaceRole.ADMIN


async def test_require_admin_gate(async_session):
    plain = await _make_user(async_session, username="plain5")
    with pytest.raises(HTTPException) as exc:
        await require_admin(plain)
    assert exc.value.status_code == 403

    admin = await _make_user(async_session, username="root5", system_role=SystemRole.ADMIN)
    assert await require_admin(admin) is admin


async def test_permission_resolution_for_readonly_member(async_session):
    owner = await _make_user(async_session, username="owner6")
    ns = await _make_namespace(async_session, owner=owner, name="team-e")
    reader = await _make_user(async_session, username="reader6")
    await _add_member(async_session, namespace=ns, user=reader, role=NamespaceRole.READONLY)

    assert await has_permission(async_session, user=reader, permission_key="namespace.read", namespace_id=ns.id) is True
    assert await has_permission(async_session, user=reader, permission_key="namespace.write", namespace_id=ns.id) is False
