from __future__ import annotations

import importlib.util
from pathlib import Path


def test_evaluation_execution_migration_is_linear_and_reversible() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260731_0041_evaluation_execution.py"
    )
    spec = importlib.util.spec_from_file_location(
        "evaluation_execution_0041",
        path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    source = path.read_text(encoding="utf-8")

    assert migration.revision == "20260731_0041"
    assert migration.down_revision == "20260731_0040"
    assert "uq_evaluations_namespace_execution_key" in source
    assert "ck_evaluations_lease_pair" in source
    assert "ix_evaluations_dispatch" in source
    assert "op.drop_column(\"evaluations\", \"execution_key\")" in source
    assert '"public_id"' not in source

    foundation_path = path.with_name("20260731_0040_evaluation_hub.py")
    foundation_source = foundation_path.read_text(encoding="utf-8")
    evaluations_table = foundation_source.split(
        'op.create_table(\n        "evaluations",',
        maxsplit=1,
    )[1]
    assert 'sa.Column("public_id", sa.String(length=40)' in evaluations_table
