"""Red-first tests for the Foundation tenant contract revision."""

from __future__ import annotations

import asyncio
import importlib.util
import os
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, TypeVar

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.models.control_plane import (
    AIAsset,
    EvidenceItem,
    RuntimeBinding,
    RuntimeInstance,
    WorkTrace,
)


BACKEND_DIR = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    BACKEND_DIR
    / "alembic"
    / "versions"
    / "20260728_0029_foundation_tenant_contract.py"
)
PREVIOUS_REVISION = "20260720_0028"
TARGET_REVISION = "20260728_0029"
SYSTEM_MYSQL_DATABASES = {"information_schema", "mysql", "performance_schema", "sys"}
FOUNDATION_MODELS = (
    RuntimeInstance,
    AIAsset,
    RuntimeBinding,
    WorkTrace,
    EvidenceItem,
)
TARGET_TABLES = tuple(model.__table__.name for model in FOUNDATION_MODELS)
EXPECTED_INDEXES = {
    "runtime_instances": (
        "ix_runtime_instances_namespace_status_updated_at",
        ("namespace_id", "status", "updated_at"),
    ),
    "ai_assets": (
        "ix_ai_assets_namespace_status_last_seen_at",
        ("namespace_id", "status", "last_seen_at"),
    ),
    "work_traces": (
        "ix_work_traces_namespace_started_at",
        ("namespace_id", "started_at"),
    ),
    "evidence_items": (
        "ix_evidence_items_namespace_created_at",
        ("namespace_id", "created_at"),
    ),
}
BINDING_UNIQUE_NAME = "uq_runtime_bindings_namespace_runtime_asset_environment"
BINDING_UNIQUE_COLUMNS = (
    "namespace_id",
    "runtime_id",
    "asset_id",
    "environment",
)

T = TypeVar("T")


def _load_migration_module():
    assert MIGRATION_PATH.is_file(), (
        "missing Foundation tenant contract revision: "
        f"{MIGRATION_PATH.relative_to(BACKEND_DIR)}"
    )
    spec = importlib.util.spec_from_file_location(
        "foundation_tenant_contract_0029",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _column_names(item: sa.Index | sa.UniqueConstraint) -> tuple[str, ...]:
    return tuple(column.name for column in item.columns)


def test_foundation_models_are_contracted_and_tenant_indexed() -> None:
    for model in FOUNDATION_MODELS:
        table = model.__table__
        assert table.c.namespace_id.nullable is False, (
            f"{table.name}.namespace_id must be non-null after the contract phase"
        )

    for table_name, (index_name, columns) in EXPECTED_INDEXES.items():
        table = next(
            model.__table__
            for model in FOUNDATION_MODELS
            if model.__table__.name == table_name
        )
        assert any(
            index.name == index_name and _column_names(index) == columns
            for index in table.indexes
        ), f"{table_name} is missing tenant index {index_name}"

    binding_table = RuntimeBinding.__table__
    assert any(
        constraint.name == BINDING_UNIQUE_NAME
        and _column_names(constraint) == BINDING_UNIQUE_COLUMNS
        for constraint in binding_table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    ), "runtime_bindings must reject duplicate tenant/runtime/asset/environment keys"


class _OperationRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def alter_column(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("alter_column", args, kwargs))

    def create_index(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("create_index", args, kwargs))

    def create_unique_constraint(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("create_unique_constraint", args, kwargs))


def _zero_blockers() -> dict[str, dict[str, int]]:
    return {
        "null_counts": {table_name: 0 for table_name in TARGET_TABLES},
        "relationship_counts": {
            "runtime_binding_runtime_missing_or_mismatch": 0,
            "runtime_binding_asset_missing_or_mismatch": 0,
            "work_trace_runtime_missing_or_mismatch": 0,
            "work_trace_asset_missing_or_mismatch": 0,
            "evidence_work_trace_missing_or_mismatch": 0,
            "runtime_binding_tenant_key_duplicates": 0,
        },
    }


def test_revision_0029_refuses_blockers_before_any_ddl() -> None:
    module = _load_migration_module()
    assert module.revision == TARGET_REVISION
    assert module.down_revision == PREVIOUS_REVISION

    recorder = _OperationRecorder()
    module.op = recorder
    blockers = _zero_blockers()
    blockers["null_counts"]["ai_assets"] = 2
    blockers["relationship_counts"][
        "runtime_binding_asset_missing_or_mismatch"
    ] = 1
    module._contract_blockers = lambda: blockers

    with pytest.raises(
        RuntimeError,
        match=(
            r"(?s)(?=.*check_foundation_tenant_contract\.py)"
            r"(?=.*\"total_blockers\":3)"
        ),
    ):
        module.upgrade()

    assert recorder.calls == [], "unsafe contract migration must fail before DDL"


def test_revision_0029_contracts_columns_and_adds_tenant_indexes() -> None:
    module = _load_migration_module()
    recorder = _OperationRecorder()
    module.op = recorder
    module._contract_blockers = _zero_blockers
    module._is_namespace_nullable = lambda _table_name: True
    module._has_index = lambda _table_name, _columns: False
    module._has_binding_unique = lambda: False

    module.upgrade()

    alter_calls = [call for call in recorder.calls if call[0] == "alter_column"]
    assert len(alter_calls) == len(TARGET_TABLES)
    assert {
        (call[1][0], call[1][1])
        for call in alter_calls
    } == {(table_name, "namespace_id") for table_name in TARGET_TABLES}
    assert all(call[2]["nullable"] is False for call in alter_calls)

    index_calls = [call for call in recorder.calls if call[0] == "create_index"]
    assert {
        (call[1][0], call[1][1], tuple(call[1][2]))
        for call in index_calls
    } == {
        (index_name, table_name, columns)
        for table_name, (index_name, columns) in EXPECTED_INDEXES.items()
    }

    unique_calls = [
        call
        for call in recorder.calls
        if call[0] == "create_unique_constraint"
    ]
    assert len(unique_calls) == 1
    assert unique_calls[0][1] == (
        BINDING_UNIQUE_NAME,
        "runtime_bindings",
        list(BINDING_UNIQUE_COLUMNS),
    )


def _validated_mysql_url() -> str:
    mysql_url = os.getenv("TEST_MYSQL_URL")
    if not mysql_url:
        pytest.skip("TEST_MYSQL_URL is not configured")
    parsed = make_url(mysql_url)
    database = (parsed.database or "").strip().lower()
    assert parsed.get_backend_name() == "mysql"
    assert database and database not in SYSTEM_MYSQL_DATABASES
    assert "test" in database or os.getenv("ALLOW_DESTRUCTIVE_TEST_MYSQL") == "1"
    assert parsed.drivername in {"mysql+aiomysql", "mysql+asyncmy"}
    return mysql_url


def _alembic_config() -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return config


async def _run_with_sync_connection(
    mysql_url: str,
    callback: Callable[[Connection], T],
) -> T:
    engine = create_async_engine(mysql_url)
    try:
        async with engine.begin() as connection:
            return await connection.run_sync(callback)
    finally:
        await engine.dispose()


def _reset_mysql_schema(connection: Connection) -> None:
    inspector = sa.inspect(connection)
    quote = connection.dialect.identifier_preparer.quote
    connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS=0")
    try:
        for view_name in inspector.get_view_names():
            connection.exec_driver_sql(f"DROP VIEW IF EXISTS {quote(view_name)}")
        for table_name in inspector.get_table_names():
            connection.exec_driver_sql(f"DROP TABLE IF EXISTS {quote(table_name)}")
    finally:
        connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS=1")


def _seed_unresolved_graph(connection: Connection) -> dict[str, int]:
    metadata = sa.MetaData()
    metadata.reflect(
        connection,
        only=("users", "namespaces", *TARGET_TABLES),
    )
    now = datetime(2026, 7, 28, 0, 0, 0)
    user_id = connection.execute(
        metadata.tables["users"].insert().values(
            username="contract-migration-owner",
            email="contract-migration-owner@example.test",
            hashed_password="not-used",
            system_role="ADMIN",
            auth_source="LOCAL",
            is_active=True,
            created_at=now,
        )
    ).inserted_primary_key[0]
    namespace_id = connection.execute(
        metadata.tables["namespaces"].insert().values(
            name="contract-migration",
            owner_id=user_id,
            created_at=now,
        )
    ).inserted_primary_key[0]
    runtime_id = connection.execute(
        metadata.tables["runtime_instances"].insert().values(
            namespace_id=None,
            provider="CUSTOM",
            name="contract-runtime",
            deploy_type="PRIVATE",
            status="ACTIVE",
            created_at=now,
            updated_at=now,
        )
    ).inserted_primary_key[0]
    asset_id = connection.execute(
        metadata.tables["ai_assets"].insert().values(
            namespace_id=None,
            asset_type="AGENT",
            name="contract-asset",
            source_provider="CUSTOM",
            source_runtime_id=runtime_id,
            external_id="contract-asset",
            status="ACTIVE",
            criticality="MEDIUM",
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
    ).inserted_primary_key[0]
    binding_id = connection.execute(
        metadata.tables["runtime_bindings"].insert().values(
            namespace_id=None,
            asset_id=asset_id,
            runtime_id=runtime_id,
            environment="test",
            usage_status="active",
            created_at=now,
        )
    ).inserted_primary_key[0]
    trace_id = connection.execute(
        metadata.tables["work_traces"].insert().values(
            namespace_id=None,
            runtime_id=runtime_id,
            asset_id=asset_id,
            title="contract trace",
            trace_type="TASK_RUN",
            sensitivity="INTERNAL",
            created_at=now,
        )
    ).inserted_primary_key[0]
    evidence_id = connection.execute(
        metadata.tables["evidence_items"].insert().values(
            namespace_id=None,
            work_trace_id=trace_id,
            source_type="API",
            source_provider="CUSTOM",
            summary="contract evidence",
            confidence=1.0,
            visibility="NORMAL",
            created_at=now,
        )
    ).inserted_primary_key[0]
    return {
        "namespace_id": int(namespace_id),
        "runtime_instances": int(runtime_id),
        "ai_assets": int(asset_id),
        "runtime_bindings": int(binding_id),
        "work_traces": int(trace_id),
        "evidence_items": int(evidence_id),
    }


def _remediate_graph(connection: Connection, row_ids: dict[str, int]) -> None:
    metadata = sa.MetaData()
    metadata.reflect(connection, only=TARGET_TABLES)
    for table_name in TARGET_TABLES:
        table = metadata.tables[table_name]
        connection.execute(
            table.update()
            .where(table.c.id == row_ids[table_name])
            .values(namespace_id=row_ids["namespace_id"])
        )


def _assert_contracted_graph(
    connection: Connection,
    row_ids: dict[str, int],
) -> None:
    inspector = sa.inspect(connection)
    metadata = sa.MetaData()
    metadata.reflect(connection, only=TARGET_TABLES)
    for table_name in TARGET_TABLES:
        columns = {column["name"]: column for column in inspector.get_columns(table_name)}
        assert columns["namespace_id"]["nullable"] is False
        table = metadata.tables[table_name]
        namespace_id = connection.scalar(
            sa.select(table.c.namespace_id).where(
                table.c.id == row_ids[table_name]
            )
        )
        assert namespace_id == row_ids["namespace_id"]

    for table_name, (_, columns) in EXPECTED_INDEXES.items():
        assert any(
            tuple(index.get("column_names") or ()) == columns
            for index in inspector.get_indexes(table_name)
        )
    assert any(
        tuple(constraint.get("column_names") or ()) == BINDING_UNIQUE_COLUMNS
        for constraint in inspector.get_unique_constraints("runtime_bindings")
    )


@pytest.mark.mysql
def test_mysql_contract_rejects_unresolved_then_preserves_remediated_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mysql_url = _validated_mysql_url()
    monkeypatch.setenv("DATABASE_URL", mysql_url)
    monkeypatch.setattr(settings, "DATABASE_URL", mysql_url)
    config = _alembic_config()

    try:
        asyncio.run(_run_with_sync_connection(mysql_url, _reset_mysql_schema))
        # Build the exact pre-contract fixture directly. Later security
        # revisions intentionally refuse downgrade because their identity and
        # key-rotation evidence is immutable; a Foundation migration test must
        # not cross that boundary merely to reach revision 0028.
        command.upgrade(config, PREVIOUS_REVISION)
        row_ids = asyncio.run(
            _run_with_sync_connection(mysql_url, _seed_unresolved_graph)
        )

        with pytest.raises(
            RuntimeError,
            match=(
                r"(?s)(?=.*check_foundation_tenant_contract\.py)"
                r"(?=.*\"total_blockers\":5)"
            ),
        ):
            command.upgrade(config, "head")

        asyncio.run(
            _run_with_sync_connection(
                mysql_url,
                lambda connection: _remediate_graph(connection, row_ids),
            )
        )
        command.upgrade(config, "head")
        asyncio.run(
            _run_with_sync_connection(
                mysql_url,
                lambda connection: _assert_contracted_graph(
                    connection,
                    row_ids,
                ),
            )
        )
    finally:
        with suppress(Exception):
            asyncio.run(_run_with_sync_connection(mysql_url, _reset_mysql_schema))
