from __future__ import annotations

import importlib.util
from pathlib import Path


def test_evaluation_case_routing_migration_is_guarded_and_linear() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260803_0051_evaluation_case_routing.py"
    )
    spec = importlib.util.spec_from_file_location(
        "evaluation_case_routing_0051",
        path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    source = path.read_text(encoding="utf-8")

    assert migration.revision == "20260803_0051"
    assert migration.down_revision == "20260803_0050"
    assert '"evaluation_case_routing_policies"' in source
    assert '"evaluation_case_routing_policy_versions"' in source
    assert '"evaluation_case_routing_runs"' in source
    assert '"evaluation_case_routing_run_items"' in source
    assert "evaluation_case_routing_outcome" in source
    assert "evaluation_case_routing_lane" in source
    assert "0051 downgrade refused" in source
