"""Contract tests for the nullable EvidenceItem -> WorkTrace typed link."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from typing import Any

import sqlalchemy as sa
import pytest

from app.models.control_plane import EvidenceItem


BACKEND_DIR = Path(__file__).resolve().parents[2]
FOUNDATION_MIGRATION_PATH = (
    BACKEND_DIR / "alembic" / "versions" / "20260518_0009_agent_control_plane.py"
)
MIGRATION_PATH = (
    BACKEND_DIR / "alembic" / "versions" / "20260720_0028_evidence_work_trace_link.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("evidence_work_trace_link_0028", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_foundation_revision_orders_internal_fk_targets_before_dependents() -> None:
    spec = importlib.util.spec_from_file_location(
        "agent_control_plane_0009", FOUNDATION_MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    table_positions = {table.name: position for position, table in enumerate(module.TABLES)}
    ordering_violations = [
        (table.name, foreign_key.column.table.name)
        for table in module.TABLES
        for constraint in table.foreign_key_constraints
        for foreign_key in constraint.elements
        if foreign_key.column.table.name in table_positions
        and table_positions[foreign_key.column.table.name] >= table_positions[table.name]
    ]

    assert ordering_violations == []


class _OperationRecorder:
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


def test_evidence_model_has_nullable_indexed_work_trace_fk() -> None:
    column = EvidenceItem.__table__.c.work_trace_id
    assert column.nullable is True
    assert {foreign_key.target_fullname for foreign_key in column.foreign_keys} == {
        "work_traces.id"
    }
    foreign_key = next(iter(column.foreign_keys))
    assert foreign_key.ondelete == "SET NULL"
    assert any(
        tuple(index_column.name for index_column in index.columns) == ("work_trace_id",)
        for index in EvidenceItem.__table__.indexes
    )


def test_revision_0028_is_expand_only_and_creates_typed_link() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MIGRATION_PATH))
    assignments = {
        node.target.id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id in {"revision", "down_revision"}
    }
    assert assignments == {
        "revision": "20260720_0028",
        "down_revision": "20260717_0027",
    }

    upgrade = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
    )
    prohibited = {
        node.func.attr
        for node in ast.walk(upgrade)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"alter_column", "drop_column", "execute", "bulk_insert"}
    }
    assert not prohibited

    module = _load_migration_module()
    recorder = _OperationRecorder()
    module.op = recorder
    module._has_column = lambda: False
    module._has_index = lambda: False
    module._has_fk = lambda: False
    module.upgrade()

    assert [name for name, _, _ in recorder.calls] == [
        "add_column",
        "create_index",
        "create_foreign_key",
    ]
    column = recorder.calls[0][1][1]
    assert isinstance(column, sa.Column)
    assert column.name == "work_trace_id"
    assert column.nullable is True
    fk_call = recorder.calls[2]
    assert fk_call[1][1:] == (
        "evidence_items",
        "work_traces",
        ["work_trace_id"],
        ["id"],
    )
    assert fk_call[2]["ondelete"] == "SET NULL"


def test_revision_0028_downgrade_refuses_to_erase_typed_links() -> None:
    module = _load_migration_module()
    recorder = _OperationRecorder()
    module.op = recorder
    module._has_column = lambda: True
    module._non_null_link_count = lambda: 2

    with pytest.raises(RuntimeError, match=r"2 row\(s\) must be remediated first"):
        module.downgrade()

    assert recorder.calls == []
