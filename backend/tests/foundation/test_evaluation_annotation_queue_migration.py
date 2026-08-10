from __future__ import annotations

import importlib.util
from pathlib import Path


def test_evaluation_annotation_queue_migration_is_guarded_and_linear() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260803_0049_evaluation_annotation_queue.py"
    )
    spec = importlib.util.spec_from_file_location(
        "evaluation_annotation_queue_0049",
        path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    source = path.read_text(encoding="utf-8")

    assert migration.revision == "20260803_0049"
    assert migration.down_revision == "20260731_0048"
    assert '"evaluation_annotation_queue_bindings"' in source
    assert '"evaluation_annotation_dispatches"' in source
    assert '"evaluation_annotation_dispatch_items"' in source
    assert "evaluation_annotation_dispatch_status" in source
    assert "0049 downgrade refused" in source
