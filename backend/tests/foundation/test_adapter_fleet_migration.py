from __future__ import annotations

import importlib.util
from pathlib import Path


def test_adapter_fleet_migration_is_linear_and_opaque() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260730_0037_adapter_handshake_fleet.py"
    )
    spec = importlib.util.spec_from_file_location("adapter_fleet_0037", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    source = path.read_text(encoding="utf-8")
    assert migration.revision == "20260730_0037"
    assert migration.down_revision == "20260730_0036"
    assert '"adapter_handshakes"' in source
    assert '"adapter_heartbeat_records"' in source
    assert "uq_adapter_handshakes_credential_instance_nonce" in source
    assert "uq_runtime_instances_public_id" in source
    assert "uuid.uuid4" in source
