from __future__ import annotations

import importlib.util
from pathlib import Path


def test_semantic_failure_clustering_migration_is_guarded_and_content_free() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260803_0054_semantic_failure_clustering.py"
    )
    spec = importlib.util.spec_from_file_location(
        "semantic_failure_clustering_0054", path
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    source = path.read_text(encoding="utf-8")

    assert migration.revision == "20260803_0054"
    assert migration.down_revision == "20260803_0053"
    assert '"evaluation_semantic_clustering_policies"' in source
    assert '"evaluation_semantic_clustering_policy_versions"' in source
    assert '"evaluation_semantic_clustering_runs"' in source
    assert '"evaluation_semantic_clustering_run_items"' in source
    assert 'sa.Column("content"' not in source
    assert 'sa.Column("vector"' not in source
    assert "source_semantic_clustering_policy_version_id" in source
    assert "source_semantic_clustering_run_id" in source
    assert "0054 downgrade refused" in source
