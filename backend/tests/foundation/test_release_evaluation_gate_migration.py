from __future__ import annotations

import importlib.util
from pathlib import Path


def test_release_evaluation_gate_migration_is_guarded_and_linear() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260731_0044_release_evaluation_gate.py"
    )
    spec = importlib.util.spec_from_file_location(
        "release_evaluation_gate_0044",
        path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    source = path.read_text(encoding="utf-8")

    assert migration.revision == "20260731_0044"
    assert migration.down_revision == "20260731_0043"
    assert '"release_candidate_evaluation_bindings"' in source
    assert "uq_release_candidate_evaluation_bindings_exact_pins" in source
    assert "uq_release_candidate_evaluation_bindings_idempotency" in source
    assert "ck_release_candidate_evaluation_bindings_no_latest" in source
    assert "ix_release_candidate_evaluation_bindings_selector" in source
    assert "deployment_configuration_digest" in source
    assert "binding_digest" in source
    assert "0044 downgrade refused" in source
