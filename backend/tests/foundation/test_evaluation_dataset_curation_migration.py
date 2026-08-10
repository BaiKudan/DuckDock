from __future__ import annotations

import importlib.util
from pathlib import Path


def test_evaluation_dataset_curation_migration_is_guarded_and_linear() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260731_0047_evaluation_dataset_curation.py"
    )
    spec = importlib.util.spec_from_file_location(
        "evaluation_dataset_curation_0047",
        path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    source = path.read_text(encoding="utf-8")

    assert migration.revision == "20260731_0047"
    assert migration.down_revision == "20260731_0046"
    assert '"evaluation_dataset_curation_batches"' in source
    assert '"evaluation_dataset_curation_items"' in source
    assert '"evaluation_dataset_curation_reviews"' in source
    assert '"evaluation_dataset_curation_materializations"' in source
    assert '"evaluation_dataset_curation_materialized_items"' in source
    assert "evaluation_dataset_curation_decision" in source
    assert "0047 downgrade refused" in source
