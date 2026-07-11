"""WorkTrace 默认遮蔽 + reveal 流程测试(specs/001 T032 · FR-008 · 宪法原则 V)。

断言:敏感级 trace 默认只给摘要(metadata_json 遮蔽);reveal 需 reason(≥5 字)且
仅限本人 / worktrace.content.read / 系统 admin;每次 reveal 进审计;产物清单不泄漏存储路径。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from app.api.v1.endpoints.control_plane import (
    get_work_trace,
    list_work_traces,
    reveal_work_trace,
)
from app.models.audit import AuditLog
from app.models.control_plane import (
    ArtifactType,
    Sensitivity,
    TraceType,
    WorkArtifact,
    WorkTrace,
)
from app.models.iam import Role, RoleBinding
from app.models.user import SystemRole, User
from app.schemas.control_plane import WorkTraceRevealRequest
from app.services.iam_service import ensure_builtin_rbac

SECRET_META = {"session_log": "internal-secret-payload"}


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


async def _seed_trace(session, *, title, sensitivity, actor_user_id=None, metadata=None):
    trace = WorkTrace(
        title=title,
        trace_type=next(iter(TraceType)),
        sensitivity=sensitivity,
        actor_user_id=actor_user_id,
        metadata_json=metadata if metadata is not None else dict(SECRET_META),
    )
    session.add(trace)
    await session.flush()
    return trace


async def test_sensitive_trace_metadata_redacted_by_default(async_session):
    viewer = await _make_user(async_session, username="viewer-r1")
    # FR-002:敏感 trace 只有本人可见,故让 viewer 持有它——本测试聚焦 FR-008「可见但遮蔽 metadata」。
    restricted = await _seed_trace(
        async_session, title="敏感会话", sensitivity=Sensitivity.RESTRICTED, actor_user_id=viewer.id
    )
    internal = await _seed_trace(async_session, title="普通会话", sensitivity=Sensitivity.INTERNAL)

    listed = {out.title: out for out in await list_work_traces(async_session, viewer)}
    assert listed["敏感会话"].metadata_json is None  # FR-008 默认遮蔽(本人也只给摘要)
    assert listed["普通会话"].metadata_json == SECRET_META

    assert (await get_work_trace(restricted.id, async_session, viewer)).metadata_json is None
    assert (await get_work_trace(internal.id, async_session, viewer)).metadata_json == SECRET_META


async def test_outsider_cannot_reveal(async_session):
    outsider = await _make_user(async_session, username="outsider-r2")
    trace = await _seed_trace(async_session, title="t-r2", sensitivity=Sensitivity.CONFIDENTIAL)

    with pytest.raises(HTTPException) as exc:
        await reveal_work_trace(
            trace.id, WorkTraceRevealRequest(reason="只是好奇想看看"), async_session, outsider
        )
    assert exc.value.status_code == 403
    # FR-003:越权揭示尝试必须留痕
    denied = (
        await async_session.execute(
            select(AuditLog).where(AuditLog.action == "worktrace.content.denied")
        )
    ).scalar_one()
    assert denied.resource_id == trace.id
    assert denied.details["access"] == "reveal"


async def test_actor_reveals_own_trace_and_audit_records_reason(async_session):
    actor = await _make_user(async_session, username="actor-r3")
    trace = await _seed_trace(
        async_session, title="t-r3", sensitivity=Sensitivity.RESTRICTED, actor_user_id=actor.id
    )

    detail = await reveal_work_trace(
        trace.id, WorkTraceRevealRequest(reason="核对自己名下交接证据"), async_session, actor
    )

    assert detail.metadata_json == SECRET_META  # 全量返回
    log = (
        await async_session.execute(
            select(AuditLog).where(AuditLog.action == "worktrace.content.revealed")
        )
    ).scalar_one()
    assert log.resource_id == trace.id
    assert log.details["reason"] == "核对自己名下交接证据"
    assert log.details["sensitivity"] == "restricted"


async def test_content_reader_binding_reveals_with_artifacts_and_no_storage_path_leak(async_session):
    auditor = await _make_user(async_session, username="auditor-r4")
    await ensure_builtin_rbac(async_session)
    role = (
        await async_session.execute(select(Role).where(Role.key == "enterprise-admin"))
    ).scalar_one()  # 含 worktrace.content.read
    async_session.add(RoleBinding(role_id=role.id, user_id=auditor.id, namespace_id=None))
    await async_session.flush()

    trace = await _seed_trace(async_session, title="t-r4", sensitivity=Sensitivity.CONFIDENTIAL)
    async_session.add(
        WorkArtifact(
            trace_id=trace.id,
            artifact_type=next(iter(ArtifactType)),
            name="weekly-report.md",
            object_uri="minio://duckdock/reports/secret-path.md",
            size_bytes=128,
        )
    )
    await async_session.flush()

    detail = await reveal_work_trace(
        trace.id, WorkTraceRevealRequest(reason="安全审计抽查"), async_session, auditor
    )

    assert detail.metadata_json == SECRET_META
    assert len(detail.artifacts) == 1
    assert detail.artifacts[0].name == "weekly-report.md"
    # 产物索引不得泄漏内部存储路径(object_uri 不在 Out schema)
    assert "secret-path" not in detail.model_dump_json()


def test_reveal_reason_is_mandatory_and_min_length():
    with pytest.raises(ValidationError):
        WorkTraceRevealRequest(reason="短")
