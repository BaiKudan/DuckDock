"""Red-first contract tests for the foundation tenant expand migration.

The migration deliberately starts with nullable tenant columns so existing rows
remain valid.  Backfill and the eventual non-null contract belong to later
revisions and must not leak into this expand step.
"""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import os
import re
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
VERSIONS_DIR = BACKEND_DIR / "alembic" / "versions"
MIGRATION_PATH = VERSIONS_DIR / "20260717_0027_foundation_tenant_expand.py"
PREVIOUS_REVISION = "20260709_0026"
TARGET_REVISION = "20260717_0027"

FOUNDATION_MODELS = (
    RuntimeInstance,
    AIAsset,
    RuntimeBinding,
    WorkTrace,
    EvidenceItem,
)
TARGET_TABLES = tuple(model.__table__.name for model in FOUNDATION_MODELS)
SYSTEM_MYSQL_DATABASES = {"information_schema", "mysql", "performance_schema", "sys"}

T = TypeVar("T")


@pytest.mark.parametrize("model", FOUNDATION_MODELS, ids=lambda model: model.__name__)
def test_foundation_model_has_contracted_indexed_namespace_fk(model: type[Any]) -> None:
    table = model.__table__
    assert "namespace_id" in table.c, (
        f"{model.__name__} ({table.name}) must expose namespace_id during the "
        "foundation tenant expand"
    )

    column = table.c.namespace_id
    assert column.nullable is False, (
        f"{table.name}.namespace_id must be non-null in current model metadata; "
        "revision 0027 separately preserves the historical expand shape"
    )
    assert {foreign_key.target_fullname for foreign_key in column.foreign_keys} == {"namespaces.id"}, (
        f"{table.name}.namespace_id must reference namespaces.id"
    )

    namespace_indexes = [
        index
        for index in table.indexes
        if tuple(index_column.name for index_column in index.columns) == ("namespace_id",)
    ]
    assert namespace_indexes, f"{table.name}.namespace_id must have a single-column index"


def _literal_assignment(tree: ast.Module, name: str) -> Any:
    for node in tree.body:
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            value = node.value
        if value is not None:
            return ast.literal_eval(value)
    raise AssertionError(f"{name!r} is not assigned a literal value")


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("foundation_tenant_expand_0027", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _call_name(function: ast.expr) -> str:
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        prefix = _call_name(function.value)
        return f"{prefix}.{function.attr}" if prefix else function.attr
    return ""


class _OperationRecorder:
    """Small Alembic-op double that records the expand DDL without a database."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    @staticmethod
    def f(name: str) -> str:
        return name

    def add_column(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("add_column", args, kwargs))

    def create_index(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("create_index", args, kwargs))

    def create_foreign_key(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("create_foreign_key", args, kwargs))

    def __getattr__(self, name: str) -> Callable[..., None]:
        def record_unsupported(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, args, kwargs))

        return record_unsupported


def _argument(
    call: tuple[str, tuple[Any, ...], dict[str, Any]],
    position: int,
    keyword: str,
) -> Any:
    _, args, kwargs = call
    return args[position] if len(args) > position else kwargs[keyword]


def test_revision_0027_is_linear_and_expand_only() -> None:
    assert MIGRATION_PATH.is_file(), (
        "missing foundation tenant expand revision: "
        f"{MIGRATION_PATH.relative_to(BACKEND_DIR)}"
    )

    source = MIGRATION_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MIGRATION_PATH))
    assert _literal_assignment(tree, "revision") == TARGET_REVISION
    assert _literal_assignment(tree, "down_revision") == PREVIOUS_REVISION

    direct_children: list[str] = []
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        candidate_tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        with suppress(AssertionError, ValueError, SyntaxError):
            if _literal_assignment(candidate_tree, "down_revision") == PREVIOUS_REVISION:
                direct_children.append(path.name)
    assert direct_children == [MIGRATION_PATH.name], (
        f"{PREVIOUS_REVISION} must have exactly one direct child, got {direct_children}"
    )

    upgrade = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "upgrade"
        ),
        None,
    )
    assert upgrade is not None, "revision 0027 must define upgrade()"

    prohibited_calls: list[str] = []
    for node in ast.walk(upgrade):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node.func)
        leaf = name.rsplit(".", 1)[-1]
        if leaf in {"alter_column", "bulk_insert", "delete", "execute", "insert", "rename_table", "update"}:
            prohibited_calls.append(name)
        if leaf.startswith("drop_"):
            prohibited_calls.append(name)
        for keyword in node.keywords:
            if keyword.arg == "nullable" and isinstance(keyword.value, ast.Constant):
                assert keyword.value.value is not False, (
                    "revision 0027 is expand-only and must not introduce nullable=False"
                )
    assert not prohibited_calls, (
        "revision 0027 must not backfill, alter, rename, or contract schema; "
        f"found {prohibited_calls}"
    )

    sql_literals = [
        node.value
        for node in ast.walk(upgrade)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    data_mutation_sql = [
        literal
        for literal in sql_literals
        if re.search(r"\b(?:UPDATE|DELETE\s+FROM|INSERT\s+INTO)\b", literal, flags=re.IGNORECASE)
    ]
    assert not data_mutation_sql, (
        "revision 0027 must not backfill or otherwise mutate existing rows"
    )

    module = _load_migration_module()
    assert module.revision == TARGET_REVISION
    assert module.down_revision == PREVIOUS_REVISION

    recorder = _OperationRecorder()
    module.op = recorder
    # Production must tolerate fresh databases where historical revision 0009
    # creates tables from current metadata.  Force the old-schema branch here
    # so this unit contract still records and validates every intended 0027 DDL
    # operation while the migration itself remains idempotent.
    module._has_namespace_column = lambda _table_name: False
    module._has_namespace_index = lambda _table_name: False
    module._has_namespace_fk = lambda _table_name: False
    module.upgrade()

    operation_names = [name for name, _, _ in recorder.calls]
    allowed_operations = {"add_column", "create_foreign_key", "create_index"}
    assert set(operation_names) <= allowed_operations, (
        "revision 0027 may only add namespace columns, foreign keys, and indexes; "
        f"recorded {operation_names}"
    )

    add_column_calls = [call for call in recorder.calls if call[0] == "add_column"]
    assert len(add_column_calls) == len(TARGET_TABLES)
    added_columns: dict[str, sa.Column[Any]] = {}
    for call in add_column_calls:
        table_name = _argument(call, 0, "table_name")
        column = _argument(call, 1, "column")
        assert table_name in TARGET_TABLES
        assert table_name not in added_columns, f"duplicate namespace_id add for {table_name}"
        assert isinstance(column, sa.Column)
        assert column.name == "namespace_id"
        assert isinstance(column.type, sa.Integer)
        assert column.nullable is True, f"{table_name}.namespace_id must be nullable during expand"
        added_columns[table_name] = column
    assert set(added_columns) == set(TARGET_TABLES)

    index_calls = [call for call in recorder.calls if call[0] == "create_index"]
    assert len(index_calls) == len(TARGET_TABLES)
    indexed_tables: set[str] = set()
    for call in index_calls:
        table_name = _argument(call, 1, "table_name")
        columns = tuple(_argument(call, 2, "columns"))
        assert table_name in TARGET_TABLES
        assert table_name not in indexed_tables, f"duplicate namespace_id index for {table_name}"
        assert columns == ("namespace_id",)
        assert not call[2].get("unique", False), f"{table_name}.namespace_id index must not be unique"
        indexed_tables.add(table_name)
    assert indexed_tables == set(TARGET_TABLES)

    fk_calls = [call for call in recorder.calls if call[0] == "create_foreign_key"]
    inline_fk_tables = {
        table_name
        for table_name, column in added_columns.items()
        if {foreign_key.target_fullname for foreign_key in column.foreign_keys} == {"namespaces.id"}
    }
    external_fk_tables: set[str] = set()
    for call in fk_calls:
        source_table = _argument(call, 1, "source_table")
        referent_table = _argument(call, 2, "referent_table")
        local_columns = tuple(_argument(call, 3, "local_cols"))
        remote_columns = tuple(_argument(call, 4, "remote_cols"))
        assert source_table in TARGET_TABLES
        assert source_table not in external_fk_tables, f"duplicate namespace_id FK for {source_table}"
        assert referent_table == "namespaces"
        assert local_columns == ("namespace_id",)
        assert remote_columns == ("id",)
        external_fk_tables.add(source_table)

    assert inline_fk_tables.isdisjoint(external_fk_tables), (
        "namespace_id FK must not be declared both inline and via create_foreign_key"
    )
    assert inline_fk_tables | external_fk_tables == set(TARGET_TABLES), (
        "all five namespace_id columns must reference namespaces.id"
    )


def test_revision_0027_downgrade_refuses_to_erase_assigned_namespace() -> None:
    module = _load_migration_module()
    recorder = _OperationRecorder()
    module.op = recorder
    module._non_null_namespace_counts = lambda: {
        "runtime_instances": 1,
        "ai_assets": 0,
    }

    with pytest.raises(
        RuntimeError,
        match="Refusing to remove populated foundation namespace ownership",
    ):
        module.downgrade()

    assert recorder.calls == [], "a blocked downgrade must not execute partial DDL"


def _validated_mysql_url() -> str:
    mysql_url = os.getenv("TEST_MYSQL_URL")
    if not mysql_url:
        pytest.skip("TEST_MYSQL_URL is not configured")

    try:
        parsed = make_url(mysql_url)
    except Exception as exc:  # pragma: no cover - exercised only by a configured lane
        pytest.fail(f"TEST_MYSQL_URL is invalid: {exc}")

    assert parsed.get_backend_name() == "mysql", "TEST_MYSQL_URL must use a MySQL dialect"
    database = (parsed.database or "").strip().lower()
    assert database, "TEST_MYSQL_URL must name a database"
    assert database not in SYSTEM_MYSQL_DATABASES, (
        f"refusing to run destructive migration test against MySQL system database {database!r}"
    )
    assert "test" in database or os.getenv("ALLOW_DESTRUCTIVE_TEST_MYSQL") == "1", (
        "TEST_MYSQL_URL must point to a database whose name contains 'test', or "
        "ALLOW_DESTRUCTIVE_TEST_MYSQL=1 must be set explicitly"
    )
    assert parsed.drivername in {"mysql+aiomysql", "mysql+asyncmy"}, (
        "TEST_MYSQL_URL must use an async MySQL driver (mysql+aiomysql or mysql+asyncmy)"
    )
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
    """Drop every object owned by the explicitly validated test database."""

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


def _seed_legacy_rows(connection: Connection) -> dict[str, int]:
    metadata = sa.MetaData()
    metadata.reflect(connection, only=TARGET_TABLES)
    now = datetime(2026, 7, 17, 0, 0, 0)

    runtime = metadata.tables["runtime_instances"]
    runtime_id = connection.execute(
        runtime.insert().values(
            provider="CUSTOM",
            name="foundation-tenant-expand-runtime",
            deploy_type="PRIVATE",
            status="ACTIVE",
            created_at=now,
            updated_at=now,
        )
    ).inserted_primary_key[0]

    asset = metadata.tables["ai_assets"]
    asset_id = connection.execute(
        asset.insert().values(
            asset_type="AGENT",
            name="foundation-tenant-expand-asset",
            source_provider="CUSTOM",
            source_runtime_id=runtime_id,
            external_id="foundation-tenant-expand-asset",
            status="ACTIVE",
            criticality="MEDIUM",
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
    ).inserted_primary_key[0]

    binding = metadata.tables["runtime_bindings"]
    binding_id = connection.execute(
        binding.insert().values(
            asset_id=asset_id,
            runtime_id=runtime_id,
            environment="test",
            usage_status="active",
            created_at=now,
        )
    ).inserted_primary_key[0]

    trace = metadata.tables["work_traces"]
    trace_id = connection.execute(
        trace.insert().values(
            runtime_id=runtime_id,
            asset_id=asset_id,
            title="foundation tenant expand trace",
            trace_type="TASK_RUN",
            sensitivity="INTERNAL",
            created_at=now,
        )
    ).inserted_primary_key[0]

    evidence = metadata.tables["evidence_items"]
    evidence_id = connection.execute(
        evidence.insert().values(
            source_type="API",
            source_provider="CUSTOM",
            summary="foundation tenant expand evidence",
            confidence=1.0,
            visibility="NORMAL",
            created_at=now,
        )
    ).inserted_primary_key[0]

    return {
        "runtime_instances": int(runtime_id),
        "ai_assets": int(asset_id),
        "runtime_bindings": int(binding_id),
        "work_traces": int(trace_id),
        "evidence_items": int(evidence_id),
    }


def _assert_populated_expand(connection: Connection, row_ids: dict[str, int]) -> None:
    inspector = sa.inspect(connection)
    metadata = sa.MetaData()
    metadata.reflect(connection, only=TARGET_TABLES)

    for table_name, row_id in row_ids.items():
        columns = {column["name"]: column for column in inspector.get_columns(table_name)}
        assert "namespace_id" in columns, f"{table_name}.namespace_id was not added"
        assert columns["namespace_id"]["nullable"] is True, (
            f"{table_name}.namespace_id must remain nullable after the expand migration"
        )

        namespace_fks = [
            foreign_key
            for foreign_key in inspector.get_foreign_keys(table_name)
            if tuple(foreign_key["constrained_columns"]) == ("namespace_id",)
        ]
        assert any(
            foreign_key["referred_table"] == "namespaces"
            and tuple(foreign_key["referred_columns"]) == ("id",)
            for foreign_key in namespace_fks
        ), f"{table_name}.namespace_id must reference namespaces.id"

        assert any(
            tuple(index["column_names"]) == ("namespace_id",)
            for index in inspector.get_indexes(table_name)
        ), f"{table_name}.namespace_id must have a single-column index"

        table = metadata.tables[table_name]
        primary_key = next(iter(table.primary_key.columns))
        row = connection.execute(
            sa.select(table.c.namespace_id).where(primary_key == row_id)
        ).one_or_none()
        assert row is not None, f"pre-existing {table_name} row was lost during upgrade"
        assert row.namespace_id is None, (
            f"pre-existing {table_name} row must survive with namespace_id=NULL"
        )


@pytest.mark.mysql
def test_mysql_populated_upgrade_from_0026_preserves_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    mysql_url = _validated_mysql_url()
    monkeypatch.setenv("DATABASE_URL", mysql_url)
    monkeypatch.setattr(settings, "DATABASE_URL", mysql_url)
    config = _alembic_config()

    try:
        asyncio.run(_run_with_sync_connection(mysql_url, _reset_mysql_schema))
        # Revision 0009 creates tables from live model metadata, so a fresh
        # upgrade to 0026 may already contain the new nullable columns.  Round
        # trip through 0027 and its guarded downgrade to reconstruct the real
        # deployed-0026 shape before inserting legacy rows.
        command.upgrade(config, TARGET_REVISION)
        command.downgrade(config, PREVIOUS_REVISION)
        row_ids = asyncio.run(_run_with_sync_connection(mysql_url, _seed_legacy_rows))

        command.upgrade(config, TARGET_REVISION)
        asyncio.run(
            _run_with_sync_connection(
                mysql_url,
                lambda connection: _assert_populated_expand(connection, row_ids),
            )
        )
    finally:
        # Leave the destructive test database empty.  Older repository
        # downgrades are not a reliable reset because revision 0009 creates
        # tables from live metadata; a stale Alembic stamp could poison later
        # metadata-based fixtures and the CI upgrade-head step.
        with suppress(Exception):
            asyncio.run(_run_with_sync_connection(mysql_url, _reset_mysql_schema))
