from __future__ import annotations

import importlib.util
from pathlib import Path


def test_trace_to_dataset_migration_is_guarded_and_linear() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260731_0046_trace_to_dataset.py"
    )
    spec = importlib.util.spec_from_file_location(
        "trace_to_dataset_0046",
        path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    source = path.read_text(encoding="utf-8")

    assert migration.revision == "20260731_0046"
    assert migration.down_revision == "20260731_0045"
    assert '"evaluation_dataset_materializations"' in source
    assert "uq_evaluation_dataset_materializations_idempotency" in source
    assert "uq_evaluation_dataset_materializations_source" in source
    assert "uq_evaluation_dataset_materializations_version" in source
    assert "evaluation_dataset_source_type" in source
    assert "0046 downgrade refused" in source
