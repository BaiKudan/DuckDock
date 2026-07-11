"""governance_service.get_or_create_namespace_governance 并发安全测试。

覆盖 get-or-create 的竞态：两个调用都未命中 SELECT 而都尝试 INSERT 时，唯一约束
（uq_namespace_governance_policy_namespace）会抛 IntegrityError。修复应捕获该错误、回滚保存点后
重新 SELECT 返回已存在行（对应 CORR-06）。纯逻辑 + 内存 SQLite。
"""
from __future__ import annotations

from sqlalchemy import func, insert, select

from app.core.security import hash_password
from app.models.governance import NamespaceGovernancePolicy
from app.models.namespace import Namespace
from app.models.user import User
from app.services import governance_service
from app.services.governance_service import (
    default_policy_payload,
    get_or_create_namespace_governance,
)


async def _seed_namespace(session, *, name: str = "team-a") -> Namespace:
    owner = User(
        username=f"owner-{name}",
        email=f"owner-{name}@example.com",
        hashed_password=hash_password("x"),
    )
    session.add(owner)
    await session.flush()
    namespace = Namespace(name=name, owner_id=owner.id)
    session.add(namespace)
    await session.flush()
    return namespace


async def _count_policies(session, namespace_id: int) -> int:
    return (
        await session.execute(
            select(func.count())
            .select_from(NamespaceGovernancePolicy)
            .where(NamespaceGovernancePolicy.namespace_id == namespace_id)
        )
    ).scalar_one()


async def test_get_or_create_returns_existing_row_under_race(async_session, monkeypatch):
    """竞态窗口：本次 SELECT 漏掉了并发写入的行，随后 flush 撞唯一约束——应回退到已存在行而非抛错。"""
    namespace = await _seed_namespace(async_session)

    # 模拟并发对手：另一个事务已提交该 namespace 的策略行（用 raw INSERT 绕过 identity map，
    # 这样它不会出现在本会话的 ORM 缓存里），但本次调用的首个 SELECT 没看到它。
    await async_session.execute(
        insert(NamespaceGovernancePolicy.__table__).values(**default_policy_payload(namespace.id))
    )
    await async_session.commit()

    real_select = governance_service._select_namespace_governance
    calls = {"n": 0}

    async def select_missing_first(db, *, namespace_id):
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # 竞态：首个 SELECT 错过并发插入的行
        return await real_select(db, namespace_id=namespace_id)

    monkeypatch.setattr(governance_service, "_select_namespace_governance", select_missing_first)

    result = await get_or_create_namespace_governance(async_session, namespace_id=namespace.id)

    assert result.namespace_id == namespace.id
    assert await _count_policies(async_session, namespace.id) == 1


async def test_get_or_create_creates_exactly_one_row(async_session):
    """空库下首次调用应创建恰好一行；重复调用复用该行而不再新增。"""
    namespace = await _seed_namespace(async_session, name="team-b")

    first = await get_or_create_namespace_governance(async_session, namespace_id=namespace.id)
    second = await get_or_create_namespace_governance(async_session, namespace_id=namespace.id)

    assert first.id == second.id
    assert await _count_policies(async_session, namespace.id) == 1
