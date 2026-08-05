from __future__ import annotations

import importlib.util
from pathlib import Path


def test_hermes_adapter_profile_migration_is_linear_and_reversible() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260730_0039_hermes_adapter_profile.py"
    )
    spec = importlib.util.spec_from_file_location(
        "hermes_adapter_profile_0039",
        path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    source = path.read_text(encoding="utf-8")

    assert migration.revision == "20260730_0039"
    assert migration.down_revision == "20260730_0038"
    assert "hermes-reporter" in migration.NEW_PROFILES
    assert "hermes-reporter" not in migration.OLD_PROFILES
    assert "adapter_id = 'hermes-reporter-pilot'" in source
    assert "SET profile = 'openclaw-reporter'" in source
