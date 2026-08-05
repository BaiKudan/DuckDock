from __future__ import annotations

import importlib.util
from pathlib import Path


def test_evaluation_comparison_migration_is_guarded_and_linear() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260731_0043_evaluation_comparison.py"
    )
    spec = importlib.util.spec_from_file_location(
        "evaluation_comparison_0043",
        path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    source = path.read_text(encoding="utf-8")

    assert migration.revision == "20260731_0043"
    assert migration.down_revision == "20260731_0042"
    assert '"regression_policies"' in source
    assert '"regression_policy_versions"' in source
    assert '"evaluation_comparisons"' in source
    assert '"score"' in source
    assert "uq_evaluation_comparisons_exact_pins" in source
    assert "ck_regression_policy_versions_complete_only" in source
    assert "ck_evaluation_comparisons_distinct_evaluations" in source
    assert "ck_evaluation_comparisons_metric_ranges" in source
    assert "reproducibility_digest" in source
    assert "0043 downgrade refused" in source
    assert 'op.drop_table("evaluation_comparisons")' in source
    assert 'op.drop_column("evaluation_result_manifests", "score")' in source
