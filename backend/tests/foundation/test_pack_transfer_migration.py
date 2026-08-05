from __future__ import annotations

import importlib.util
from pathlib import Path


def test_pack_transfer_migration_is_linear_and_durable() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260730_0038_pack_transfer_replay.py"
    )
    spec = importlib.util.spec_from_file_location(
        "pack_transfer_0038",
        path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    source = path.read_text(encoding="utf-8")
    assert migration.revision == "20260730_0038"
    assert migration.down_revision == "20260730_0037"
    assert '"pack_batch_streams"' in source
    assert '"pack_batch_receipts"' in source
    assert '"pack_exports"' in source
    assert "uq_pack_batch_receipts_stream_sequence" in source
    assert "uq_pack_batch_receipts_stream_key" in source
    assert "ck_pack_batch_streams_nonnegative_cursor" in source
