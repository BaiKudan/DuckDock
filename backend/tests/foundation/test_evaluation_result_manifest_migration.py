from __future__ import annotations

import importlib.util
from pathlib import Path


def test_evaluation_result_manifest_migration_is_guarded_and_linear() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260731_0042_evaluation_result_manifest.py"
    )
    spec = importlib.util.spec_from_file_location(
        "evaluation_result_manifest_0042",
        path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    source = path.read_text(encoding="utf-8")

    assert migration.revision == "20260731_0042"
    assert migration.down_revision == "20260731_0041"
    assert '"evaluation_result_manifests"' in source
    assert '"PARTIAL"' in source
    assert '"processed_count"' in source
    assert '"scored_count"' in source
    assert '"error_count"' in source
    assert '"result_completeness"' in source
    assert "ck_evaluations_result_state" in source
    assert "ck_evaluation_result_manifests_counts" in source
    assert "ck_evaluation_result_manifests_completeness" in source
    assert "0042 downgrade refused" in source
    assert 'op.drop_table("evaluation_result_manifests")' in source
