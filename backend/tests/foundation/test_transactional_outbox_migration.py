"""FND-065 migration contract for the transactional Outbox."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa


BACKEND_DIR = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    BACKEND_DIR
    / "alembic"
    / "versions"
    / "20260728_0033_transactional_outbox.py"
)


def _load_migration_module():
    assert MIGRATION_PATH.is_file(), "missing transactional Outbox revision 0033"
    spec = importlib.util.spec_from_file_location(
        "transactional_outbox_0033",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_0033_creates_bounded_leased_outbox() -> None:
    module = _load_migration_module()
    assert module.revision == "20260728_0033"
    assert module.down_revision == "20260728_0032"
    table = module.outbox_events_table()

    uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert ("event_id",) in uniques
    assert ("idempotency_key",) in uniques
    assert table.c.namespace_id.nullable is False
    assert table.c.payload_json.type.__class__ is sa.JSON
    assert table.c.last_error.type.length == 500
    assert table.c.status.type.enums == [
        "PENDING",
        "LEASED",
        "PUBLISHED",
        "FAILED",
    ]
