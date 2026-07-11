"""Sanity test for the shared async DB harness (specs/002-test-harness).

证明 conftest 的 `async_session` 夹具可建表、写入、查询——为后续 service/越权测试打底。
"""
from __future__ import annotations

from sqlalchemy import select

from app.models.user import SystemRole, User


async def test_shared_async_session_persists_and_queries(async_session):
    async_session.add(
        User(
            username="harness",
            email="harness@duckdock-ai.com",
            hashed_password="x",
            system_role=SystemRole.USER,
        )
    )
    await async_session.flush()

    loaded = (
        await async_session.execute(select(User).where(User.username == "harness"))
    ).scalar_one()
    assert loaded.email == "harness@duckdock-ai.com"
    assert loaded.system_role == SystemRole.USER
