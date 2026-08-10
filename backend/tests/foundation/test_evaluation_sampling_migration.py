from __future__ import annotations

import importlib.util
from pathlib import Path


def test_evaluation_sampling_migration_is_guarded_and_linear() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260731_0048_evaluation_sampling_policy.py"
    )
    spec = importlib.util.spec_from_file_location(
        "evaluation_sampling_policy_0048",
        path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    source = path.read_text(encoding="utf-8")

    assert migration.revision == "20260731_0048"
    assert migration.down_revision == "20260731_0047"
    assert '"evaluation_sampling_policies"' in source
    assert '"evaluation_sampling_policy_versions"' in source
    assert '"evaluation_sampling_runs"' in source
    assert '"evaluation_sampling_run_items"' in source
    assert "evaluation_sampling_strategy" in source
    assert "exclude_governed = 1" in source
    assert "0048 downgrade refused" in source
