from __future__ import annotations

import importlib.util
from pathlib import Path


def test_agent_package_registry_migration_is_guarded_and_private_key_free() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260804_0057_agent_package_registry_v2.py"
    )
    spec = importlib.util.spec_from_file_location("agent_package_registry_0057", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    source = path.read_text(encoding="utf-8")

    assert migration.revision == "20260804_0057"
    assert migration.down_revision == "20260804_0056"
    for table in (
        "agent_packages",
        "package_signing_keys",
        "agent_package_versions",
        "package_components",
        "package_dependencies",
        "package_sboms",
    ):
        assert f'"{table}"' in source
    assert 'op.add_column("agent_deployments"' in source
    assert 'sa.Column("package_version_id"' in source
    assert "private_key" not in source
    assert "0057 downgrade refused" in source
