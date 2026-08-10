from __future__ import annotations

import importlib.util
from pathlib import Path


def test_operations_migration_has_immutable_slo_incident_and_restore_receipts() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260804_0062_operations_slo_recovery.py"
    )
    spec = importlib.util.spec_from_file_location("operations_0062", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    source = path.read_text(encoding="utf-8")

    assert migration.revision == "20260804_0062"
    assert migration.down_revision == "20260804_0061"
    for table in ("ops_slo_evaluations", "ops_incidents", "ops_recovery_drills"):
        assert f'"{table}"' in source
    for contract in (
        "uq_ops_slo_evaluations_idempotency",
        "uq_ops_incidents_evaluation",
        "uq_ops_recovery_drills_idempotency",
        "0062 downgrade refused",
    ):
        assert contract in source
    for forbidden in ("backup_path", "database_url", "access_token", "secret_key"):
        assert forbidden not in source
