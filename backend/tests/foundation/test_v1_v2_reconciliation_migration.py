"""G1 compatibility reconciliation migration contract."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa


BACKEND_DIR = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    BACKEND_DIR
    / "alembic"
    / "versions"
    / "20260728_0034_v1_v2_reconciliation.py"
)


def _load_migration_module():
    assert MIGRATION_PATH.is_file(), "missing reconciliation revision 0034"
    spec = importlib.util.spec_from_file_location(
        "v1_v2_reconciliation_0034",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_0034_adds_consumer_receipts_and_reconciliation() -> None:
    module = _load_migration_module()
    assert module.revision == "20260728_0034"
    assert module.down_revision == "20260728_0033"

    receipts = module.outbox_consumer_receipts_table()
    receipt_uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in receipts.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert ("consumer_name", "event_id") in receipt_uniques
    assert receipts.c.event_id.nullable is False

    reconciliation = module.v1_v2_reconciliations_table()
    assert reconciliation.c.namespace_id.nullable is False
    assert reconciliation.c.runtime_id.nullable is False
    assert reconciliation.c.work_trace_id.nullable is False
    assert reconciliation.c.status.type.enums == [
        "MATCHED",
        "EXPECTED_LEGACY_ONLY",
        "MISMATCH",
    ]
    reconciliation_uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in reconciliation.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert ("work_trace_id",) in reconciliation_uniques
