"""Shared pytest fixtures for the DuckDock backend test suite.

提供基于内存 SQLite 的异步会话夹具，未来的 unit/integration 测试可直接注入 `async_session`，
不必各自重复建表逻辑（对应 constitution 原则 III · specs/002-test-harness）。
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy import MetaData, event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 - 注册所有 SQLAlchemy 模型以便 create_all
from app.core.database import Base


FOUNDATION_TENANT_TABLES = (
    "runtime_instances",
    "ai_assets",
    "runtime_bindings",
    "work_traces",
    "evidence_items",
)
FOUNDATION_CONTRACT_OBJECTS = {
    "ix_runtime_instances_namespace_status_updated_at",
    "ix_ai_assets_namespace_status_last_seen_at",
    "uq_runtime_bindings_namespace_runtime_asset_environment",
    "ix_work_traces_namespace_started_at",
    "ix_evidence_items_namespace_created_at",
}


def _foundation_expand_test_metadata() -> MetaData:
    """Clone the deployed 0028 shape for legacy backfill/remediation tests.

    Production model metadata represents the post-contract schema. The shared
    fixtures intentionally retain nullable Foundation tenant keys so the
    expand/backfill compatibility suite can still construct historical rows.
    Revision 0029's migration tests exercise the contracted schema itself.
    """

    metadata = MetaData(naming_convention=Base.metadata.naming_convention)
    for table in Base.metadata.sorted_tables:
        table.to_metadata(metadata)
    for table_name in FOUNDATION_TENANT_TABLES:
        metadata.tables[table_name].c.namespace_id.nullable = True
    for table in metadata.tables.values():
        contract_indexes = {
            index
            for index in table.indexes
            if index.name in FOUNDATION_CONTRACT_OBJECTS
        }
        contract_constraints = {
            constraint
            for constraint in table.constraints
            if constraint.name in FOUNDATION_CONTRACT_OBJECTS
        }
        table.indexes.difference_update(contract_indexes)
        table.constraints.difference_update(contract_constraints)
    return metadata


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

    test_metadata = _foundation_expand_test_metadata()
    async with engine.begin() as conn:
        await conn.run_sync(test_metadata.create_all)
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
    test_metadata = _foundation_expand_test_metadata()
    async with engine.begin() as conn:
        await conn.run_sync(test_metadata.drop_all)
        await conn.run_sync(test_metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(test_metadata.drop_all)
    await engine.dispose()
