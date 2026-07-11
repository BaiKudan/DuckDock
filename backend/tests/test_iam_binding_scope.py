"""RoleBinding 作用域校验测试 —— 角色 scope 必须与绑定目标匹配（ISO-002）。

create_binding 必须拒绝 scope 与目标 id 不一致的绑定：
- NAMESPACE 作用域角色 → 必须带 namespace_id 且不带 org_unit_id。
- ORG 作用域角色 → 必须带 org_unit_id 且不带 namespace_id。
- SYSTEM 作用域角色 → 两者都不能带（全局绑定）。
对应 constitution 原则 IV/V（最小权限 / 越权防护）。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.v1.endpoints.iam import create_binding
from app.models.iam import OrgUnit, OrgUnitType, Role
from app.models.namespace import Namespace
from app.models.user import SystemRole, User
from app.schemas.iam import RoleBindingCreate
from app.services.iam_service import ensure_builtin_rbac


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


async def _make_org_unit(session, *, name, code):
    org = OrgUnit(name=name, code=code, unit_type=OrgUnitType.DEPARTMENT, path=f"/{code}")
    session.add(org)
    await session.flush()
    return org


async def _role(session, key) -> Role:
    return (await session.execute(select(Role).where(Role.key == key))).scalar_one()


async def _binding(session, *, role_id, user_id, namespace_id=None, org_unit_id=None):
    body = RoleBindingCreate(
        role_id=role_id,
        user_id=user_id,
        namespace_id=namespace_id,
        org_unit_id=org_unit_id,
    )
    admin = await _make_user(session, username=f"admin-{role_id}-{namespace_id}-{org_unit_id}", system_role=SystemRole.ADMIN)
    return await create_binding(body, session, admin)


# --- NAMESPACE-scoped role -------------------------------------------------


async def test_namespace_role_requires_namespace_id(async_session):
    target = await _make_user(async_session, username="dev-ns1")
    await ensure_builtin_rbac(async_session)
    role = await _role(async_session, "namespace-developer")

    # 缺 namespace_id → 422
    with pytest.raises(HTTPException) as exc:
        await _binding(async_session, role_id=role.id, user_id=target.id)
    assert exc.value.status_code == 422


async def test_namespace_role_rejects_org_unit_id(async_session):
    owner = await _make_user(async_session, username="owner-ns2")
    ns = await _make_namespace(async_session, owner=owner, name="team-ns2")
    org = await _make_org_unit(async_session, name="Dept NS2", code="dept-ns2")
    target = await _make_user(async_session, username="dev-ns2")
    await ensure_builtin_rbac(async_session)
    role = await _role(async_session, "namespace-developer")

    # 同时带 org_unit_id（即便有 namespace_id）→ 422
    with pytest.raises(HTTPException) as exc:
        await _binding(async_session, role_id=role.id, user_id=target.id, namespace_id=ns.id, org_unit_id=org.id)
    assert exc.value.status_code == 422


async def test_namespace_role_with_namespace_id_ok(async_session):
    owner = await _make_user(async_session, username="owner-ns3")
    ns = await _make_namespace(async_session, owner=owner, name="team-ns3")
    target = await _make_user(async_session, username="dev-ns3")
    await ensure_builtin_rbac(async_session)
    role = await _role(async_session, "namespace-developer")

    out = await _binding(async_session, role_id=role.id, user_id=target.id, namespace_id=ns.id)
    assert out.namespace_id == ns.id
    assert out.org_unit_id is None


# --- ORG-scoped role -------------------------------------------------------


async def test_org_role_requires_org_unit_id(async_session):
    target = await _make_user(async_session, username="mgr-org1")
    await ensure_builtin_rbac(async_session)
    role = await _role(async_session, "org-manager")

    # 缺 org_unit_id → 422
    with pytest.raises(HTTPException) as exc:
        await _binding(async_session, role_id=role.id, user_id=target.id)
    assert exc.value.status_code == 422


async def test_org_role_rejects_namespace_id(async_session):
    owner = await _make_user(async_session, username="owner-org2")
    ns = await _make_namespace(async_session, owner=owner, name="team-org2")
    org = await _make_org_unit(async_session, name="Dept ORG2", code="dept-org2")
    target = await _make_user(async_session, username="mgr-org2")
    await ensure_builtin_rbac(async_session)
    role = await _role(async_session, "org-manager")

    # 同时带 namespace_id → 422
    with pytest.raises(HTTPException) as exc:
        await _binding(async_session, role_id=role.id, user_id=target.id, namespace_id=ns.id, org_unit_id=org.id)
    assert exc.value.status_code == 422


async def test_org_role_with_org_unit_id_ok(async_session):
    org = await _make_org_unit(async_session, name="Dept ORG3", code="dept-org3")
    target = await _make_user(async_session, username="mgr-org3")
    await ensure_builtin_rbac(async_session)
    role = await _role(async_session, "org-manager")

    out = await _binding(async_session, role_id=role.id, user_id=target.id, org_unit_id=org.id)
    assert out.org_unit_id == org.id
    assert out.namespace_id is None


# --- SYSTEM-scoped role ----------------------------------------------------


async def test_system_role_rejects_namespace_id(async_session):
    owner = await _make_user(async_session, username="owner-sys1")
    ns = await _make_namespace(async_session, owner=owner, name="team-sys1")
    target = await _make_user(async_session, username="adm-sys1")
    await ensure_builtin_rbac(async_session)
    role = await _role(async_session, "enterprise-admin")

    with pytest.raises(HTTPException) as exc:
        await _binding(async_session, role_id=role.id, user_id=target.id, namespace_id=ns.id)
    assert exc.value.status_code == 422


async def test_system_role_rejects_org_unit_id(async_session):
    org = await _make_org_unit(async_session, name="Dept SYS2", code="dept-sys2")
    target = await _make_user(async_session, username="adm-sys2")
    await ensure_builtin_rbac(async_session)
    role = await _role(async_session, "enterprise-admin")

    with pytest.raises(HTTPException) as exc:
        await _binding(async_session, role_id=role.id, user_id=target.id, org_unit_id=org.id)
    assert exc.value.status_code == 422


async def test_system_role_global_binding_ok(async_session):
    target = await _make_user(async_session, username="adm-sys3")
    await ensure_builtin_rbac(async_session)
    role = await _role(async_session, "enterprise-admin")

    out = await _binding(async_session, role_id=role.id, user_id=target.id)
    assert out.namespace_id is None
    assert out.org_unit_id is None
