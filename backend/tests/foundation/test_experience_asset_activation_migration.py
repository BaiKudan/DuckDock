from __future__ import annotations

import importlib.util
from pathlib import Path


def test_experience_asset_activation_migration_is_guarded_and_linear() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260803_0053_experience_asset_activation.py"
    )
    spec = importlib.util.spec_from_file_location(
        "experience_asset_activation_0053",
        path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    source = path.read_text(encoding="utf-8")

    assert migration.revision == "20260803_0053"
    assert migration.down_revision == "20260803_0052"
    assert '"evaluation_experience_assets"' in source
    assert '"evaluation_experience_asset_versions"' in source
    assert '"evaluation_experience_activation_requests"' in source
    assert '"evaluation_experience_activation_reviews"' in source
    assert "evaluation_experience_asset_version_status" in source
    assert "evaluation_experience_activation_decision" in source
    assert "0053 downgrade refused" in source
