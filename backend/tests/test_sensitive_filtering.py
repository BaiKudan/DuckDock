"""敏感内容 ownership 过滤(specs/003 FR-002 · 宪法原则 V 最小暴露)。

方案:**按归属排除**——敏感级行(worktrace CONFIDENTIAL/RESTRICTED、evidence SENSITIVE/RESTRICTED)
只有本人(actor_user_id / created_by == 调用者)可见;持提权键(worktrace.content.read /
evidence.sensitive.read)或系统 admin 不受限。非提权者**整行看不到**他人的敏感行(列表排除、
按 id 直取 404),含 actor/creator 为空者。非敏感行不受影响。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.v1.endpoints.control_plane import get_work_trace, list_evidence, list_work_traces
from app.models.audit import AuditLog
from app.models.control_plane import (
    EvidenceItem,
    EvidenceSourceType,
    EvidenceVisibility,
    RuntimeProvider,
    Sensitivity,
    TraceType,
    WorkTrace,
)
from app.models.iam import Role, RoleBinding
from app.models.user import SystemRole, User
from app.services.iam_service import ensure_builtin_rbac


async def _make_user(session, *, username, system_role=SystemRole.USER):
    user = User(username=username, email=f"{username}@duckdock-ai.com", hashed_password="x", system_role=system_role)
    session.add(user)
    await session.flush()
    return user


async def _bind_enterprise_admin(session, user):
    await ensure_builtin_rbac(session)
    role = (await session.execute(select(Role).where(Role.key == "enterprise-admin"))).scalar_one()
    session.add(RoleBinding(role_id=role.id, user_id=user.id, namespace_id=None))
    await session.flush()


async def _seed_trace(session, *, title, sensitivity, actor_user_id=None):
    trace = WorkTrace(
        title=title, trace_type=TraceType.SESSION, sensitivity=sensitivity, actor_user_id=actor_user_id
    )
    session.add(trace)
    await session.flush()
    return trace


async def _seed_evidence(session, *, summary, visibility, created_by=None):
    ev = EvidenceItem(
        source_type=EvidenceSourceType.BACKUP_PACKAGE,
        source_provider=RuntimeProvider.OPENCLAW,
        summary=summary,
        visibility=visibility,
        created_by=created_by,
    )
    session.add(ev)
    await session.flush()
    return ev


# ── work traces ─────────────────────────────────────────────────

async def test_plain_user_sees_own_and_nonsensitive_traces_only(async_session):
    me = await _make_user(async_session, username="wt-me")
    other = await _make_user(async_session, username="wt-other")
    await _seed_trace(async_session, title="own-secret", sensitivity=Sensitivity.RESTRICTED, actor_user_id=me.id)
    await _seed_trace(async_session, title="other-secret", sensitivity=Sensitivity.CONFIDENTIAL, actor_user_id=other.id)
    await _seed_trace(async_session, title="orphan-secret", sensitivity=Sensitivity.RESTRICTED, actor_user_id=None)
    await _seed_trace(async_session, title="public-internal", sensitivity=Sensitivity.INTERNAL, actor_user_id=other.id)

    titles = {t.title for t in await list_work_traces(async_session, me)}
    assert "own-secret" in titles          # 本人敏感 → 可见
    assert "public-internal" in titles     # 非敏感 → 可见(无论归属)
    assert "other-secret" not in titles    # 他人敏感 → 排除
    assert "orphan-secret" not in titles   # 无主敏感 → 排除


async def test_content_reader_and_admin_see_all_sensitive_traces(async_session):
    owner = await _make_user(async_session, username="wt-owner")
    await _seed_trace(async_session, title="s1", sensitivity=Sensitivity.RESTRICTED, actor_user_id=owner.id)
    await _seed_trace(async_session, title="s2", sensitivity=Sensitivity.CONFIDENTIAL, actor_user_id=None)

    auditor = await _make_user(async_session, username="wt-auditor")
    await _bind_enterprise_admin(async_session, auditor)  # 含 worktrace.content.read
    admin = await _make_user(async_session, username="wt-admin", system_role=SystemRole.ADMIN)

    assert {t.title for t in await list_work_traces(async_session, auditor)} >= {"s1", "s2"}
    assert {t.title for t in await list_work_traces(async_session, admin)} >= {"s1", "s2"}


async def test_get_by_id_hides_others_sensitive_trace_as_404(async_session):
    me = await _make_user(async_session, username="wt-byid-me")
    other = await _make_user(async_session, username="wt-byid-other")
    mine = await _seed_trace(async_session, title="mine", sensitivity=Sensitivity.RESTRICTED, actor_user_id=me.id)
    theirs = await _seed_trace(async_session, title="theirs", sensitivity=Sensitivity.RESTRICTED, actor_user_id=other.id)

    # 本人敏感按 id 可取(摘要,metadata 仍遮蔽)
    assert (await get_work_trace(mine.id, async_session, me)).id == mine.id
    # 他人敏感按 id → 404,不暴露存在性
    with pytest.raises(HTTPException) as exc:
        await get_work_trace(theirs.id, async_session, me)
    assert exc.value.status_code == 404


async def test_get_by_id_orphan_sensitive_404_and_denial_audited(async_session):
    """无主(actor 为空)敏感 trace 按 id 直取 → 404;且越权尝试留痕(FR-003)。"""
    me = await _make_user(async_session, username="wt-orphan-me")
    orphan = await _seed_trace(async_session, title="orphan", sensitivity=Sensitivity.RESTRICTED, actor_user_id=None)
    with pytest.raises(HTTPException) as exc:
        await get_work_trace(orphan.id, async_session, me)
    assert exc.value.status_code == 404

    denied = (
        await async_session.execute(
            select(AuditLog).where(AuditLog.action == "worktrace.content.denied")
        )
    ).scalar_one()
    assert denied.resource_id == orphan.id
    assert denied.details["access"] == "get_by_id"


async def test_admin_can_get_others_sensitive_trace_by_id(async_session):
    other = await _make_user(async_session, username="wt-byid-other2")
    admin = await _make_user(async_session, username="wt-byid-admin", system_role=SystemRole.ADMIN)
    theirs = await _seed_trace(async_session, title="t", sensitivity=Sensitivity.RESTRICTED, actor_user_id=other.id)
    assert (await get_work_trace(theirs.id, async_session, admin)).id == theirs.id


# ── evidence ────────────────────────────────────────────────────

async def test_plain_user_sees_own_and_normal_evidence_only(async_session):
    me = await _make_user(async_session, username="ev-me")
    other = await _make_user(async_session, username="ev-other")
    await _seed_evidence(async_session, summary="own-sensitive", visibility=EvidenceVisibility.SENSITIVE, created_by=me.id)
    await _seed_evidence(async_session, summary="other-sensitive", visibility=EvidenceVisibility.RESTRICTED, created_by=other.id)
    await _seed_evidence(async_session, summary="orphan-sensitive", visibility=EvidenceVisibility.SENSITIVE, created_by=None)
    await _seed_evidence(async_session, summary="normal", visibility=EvidenceVisibility.NORMAL, created_by=other.id)

    summaries = {e.summary for e in await list_evidence(async_session, me)}
    assert "own-sensitive" in summaries
    assert "normal" in summaries
    assert "other-sensitive" not in summaries
    assert "orphan-sensitive" not in summaries


async def test_sensitive_reader_and_admin_see_all_evidence(async_session):
    other = await _make_user(async_session, username="ev-owner")
    await _seed_evidence(async_session, summary="s1", visibility=EvidenceVisibility.SENSITIVE, created_by=other.id)
    await _seed_evidence(async_session, summary="s2", visibility=EvidenceVisibility.RESTRICTED, created_by=None)

    auditor = await _make_user(async_session, username="ev-auditor")
    await _bind_enterprise_admin(async_session, auditor)  # 含 evidence.sensitive.read
    admin = await _make_user(async_session, username="ev-admin", system_role=SystemRole.ADMIN)

    assert {e.summary for e in await list_evidence(async_session, auditor)} >= {"s1", "s2"}
    assert {e.summary for e in await list_evidence(async_session, admin)} >= {"s1", "s2"}
