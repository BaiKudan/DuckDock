from __future__ import annotations

import importlib.util
from pathlib import Path


def test_semantic_monitor_migration_is_guarded_and_content_free() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260804_0056_semantic_drift_monitors.py"
    )
    spec = importlib.util.spec_from_file_location("semantic_drift_monitors_0056", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    source = path.read_text(encoding="utf-8")

    assert migration.revision == "20260804_0056"
    assert migration.down_revision == "20260803_0055"
    assert '"evaluation_semantic_monitors"' in source
    assert '"evaluation_semantic_monitor_runs"' in source
    assert '"evaluation_semantic_monitor_alerts"' in source
    assert '"uq_eval_semantic_clustering_runs_evidence"' in source
    assert '"ix_eval_semantic_clustering_runs_evidence"' in source
    assert 'sa.Column("content"' not in source
    assert 'sa.Column("vector"' not in source
    assert 'sa.Column("embedding"' not in source
    assert "0056 downgrade refused" in source
