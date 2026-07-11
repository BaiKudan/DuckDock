"""控制平面权限收口测试(specs/003 方案 A · FR-002 / 001 T014)。

两层防护:
1. 闸门单测——新 require_* 依赖对无授权用户拒 403,对 enterprise-admin 绑定/系统 admin 放行;
2. 接线锁——用签名内省断言关键端点声明了正确的权限门,防止回退成 CurrentUser。
"""
from __future__ import annotations

import typing

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.v1.endpoints import control_plane as cp
from app.core.deps import (
    require_asset_reader,
    require_evidence_reader,
    require_handover_manager,
    require_handover_reader,
    require_runtime_manager,
    require_runtime_reader,
    require_worktrace_reader,
)
from app.models.iam import Role, RoleBinding
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.user import SystemRole, User
from app.services.iam_service import ensure_builtin_rbac

READER_GATES = [
    require_runtime_reader,
    require_asset_reader,
    require_worktrace_reader,
    require_evidence_reader,
    require_handover_reader,
]


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


async def _bind_role(session, *, user, role_key):
    await ensure_builtin_rbac(session)
    role = (await session.execute(select(Role).where(Role.key == role_key))).scalar_one()
    session.add(RoleBinding(role_id=role.id, user_id=user.id, namespace_id=None))
    await session.flush()


async def test_plain_user_denied_on_all_control_plane_gates(async_session):
    plain = await _make_user(async_session, username="plain-cp")
    for gate in [*READER_GATES, require_runtime_manager, require_handover_manager]:
        with pytest.raises(HTTPException) as exc:
            await gate(plain, async_session)
        assert exc.value.status_code == 403, gate.__name__


async def test_enterprise_admin_binding_passes_reader_and_manager_gates(async_session):
    user = await _make_user(async_session, username="ops-cp")
    await _bind_role(async_session, user=user, role_key="enterprise-admin")
    for gate in [*READER_GATES, require_runtime_manager, require_handover_manager]:
        assert await gate(user, async_session) is user, gate.__name__


async def test_system_admin_bypasses_manager_gate(async_session):
    admin = await _make_user(async_session, username="root-cp", system_role=SystemRole.ADMIN)
    assert await require_handover_manager(admin, async_session) is admin


async def test_namespace_role_does_not_grant_system_scope(async_session):
    """命名空间角色(skill 团队空间)不得泄漏成控制平面系统权限。"""
    owner = await _make_user(async_session, username="owner-cp")
    ns = Namespace(name="team-cp", owner_id=owner.id)
    async_session.add(ns)
    await async_session.flush()
    dev = await _make_user(async_session, username="dev-cp")
    async_session.add(NamespaceMember(namespace_id=ns.id, user_id=dev.id, role=NamespaceRole.ADMIN))
    await async_session.flush()

    with pytest.raises(HTTPException) as exc:
        await require_asset_reader(dev, async_session)
    assert exc.value.status_code == 403


def _gate_name(endpoint) -> str:
    hints = typing.get_type_hints(endpoint, include_extras=True)
    return hints["current_user"].__metadata__[0].dependency.__name__


def test_endpoints_declare_expected_permission_gates():
    """接线锁:防止有人把权限门改回 CurrentUser(specs/003 FR-002 回归防护)。"""
    expected = {
        cp.list_runtimes: "require_runtime_reader",
        cp.create_runtime: "require_runtime_manager",
        cp.test_runtime: "require_runtime_manager",  # Push-only:上报链路自检(T080)
        cp.list_assets: "require_asset_reader",
        cp.create_asset: "require_asset_manager",
        cp.list_work_traces: "require_worktrace_reader",
        cp.list_evidence: "require_evidence_reader",
        cp.issue_evidence_download_link: "require_evidence_reader",  # 敏感级在端点体内叠加 evidence.sensitive.read
        cp.list_handovers: "require_handover_reader",
        cp.create_handover: "require_handover_manager",
        cp.upload_handover_evidence: "require_handover_manager",
        cp.execute_handover: "require_handover_manager",
        cp.complete_execution_action: "require_handover_manager",
        cp.verify_handover: "get_current_user",  # 接收人本人/handover.manage 在端点体内校验
        # 刻意保持自我范围(self-scoped)的端点:
        cp.my_ai_workspace: "get_current_user",
        cp.submit_asset_feedback: "get_current_user",
        cp.decide_approval: "get_current_user",  # 审批人身份在端点体内校验
    }
    for endpoint, gate in expected.items():
        assert _gate_name(endpoint) == gate, f"{endpoint.__name__} 应使用 {gate}"
