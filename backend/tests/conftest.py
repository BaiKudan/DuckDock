"""Shared pytest fixtures for the DuckDock backend test suite.

提供基于内存 SQLite 的异步会话夹具，未来的 unit/integration 测试可直接注入 `async_session`，
不必各自重复建表逻辑（对应 constitution 原则 III · specs/002-test-harness）。
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 - 注册所有 SQLAlchemy 模型以便 create_all
from app.core.database import Base


@pytest_asyncio.fixture
async def async_session():
    """Yield an AsyncSession backed by a fresh in-memory SQLite database."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    # DM-03: SQLite ignores FK constraints (incl. ondelete=SET NULL) unless
    # PRAGMA foreign_keys is enabled per-connection, so the in-memory test DB
    # would silently diverge from MySQL. Enable it so FK behavior is exercised.
    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_fk(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


def _is_destructive_test_mysql_url_allowed(mysql_url: str) -> bool:
    if os.getenv("ALLOW_DESTRUCTIVE_TEST_MYSQL") == "1":
        return True
    database = (make_url(mysql_url).database or "").lower()
    return "test" in database


@pytest_asyncio.fixture
async def async_session_mysql():
    """Yield an AsyncSession backed by TEST_MYSQL_URL, or skip when unavailable."""
    mysql_url = os.getenv("TEST_MYSQL_URL")
    if not mysql_url:
        pytest.skip("TEST_MYSQL_URL is not configured")
    if not _is_destructive_test_mysql_url_allowed(mysql_url):
        pytest.skip("TEST_MYSQL_URL must point to a test database or set ALLOW_DESTRUCTIVE_TEST_MYSQL=1")

    engine = create_async_engine(mysql_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
