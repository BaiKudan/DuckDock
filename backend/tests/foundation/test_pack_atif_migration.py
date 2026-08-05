from __future__ import annotations

import importlib.util
from pathlib import Path


def test_pack_atif_migration_is_linear_and_defensive() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260730_0036_pack_atif_import.py"
    )
    spec = importlib.util.spec_from_file_location("pack_atif_0036", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "20260730_0036"
    assert migration.down_revision == "20260730_0035"
    source = path.read_text(encoding="utf-8")
    assert '"pack_imports"' in source
    assert '"pack_import_artifacts"' in source
    assert "uq_pack_imports_tenant_runtime_pack" in source
    assert "ck_pack_imports_status_state" in source
    assert "pack_import_status" in source
