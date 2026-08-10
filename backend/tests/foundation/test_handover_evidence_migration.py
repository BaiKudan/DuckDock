from __future__ import annotations

import importlib.util
from pathlib import Path


def test_handover_evidence_migration_is_complete_guarded_and_secret_free() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260804_0060_handover_evidence_snapshots.py"
    )
    spec = importlib.util.spec_from_file_location("handover_evidence_0060", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    source = path.read_text(encoding="utf-8")

    assert migration.revision == "20260804_0060"
    assert migration.down_revision == "20260804_0059"
    for table in (
        "handover_evidence_snapshots",
        "handover_obligations",
        "handover_obligation_receipts",
        "handover_acceptances",
        "handover_signed_packages",
    ):
        assert f'"{table}"' in source
    for contract in (
        "fallback_owner_user_id",
        "uq_handover_evidence_snapshots_ns_idempotency",
        "uq_handover_obligation_receipts_obligation",
        "uq_handover_signed_packages_acceptance",
        "0060 downgrade refused",
    ):
        assert contract in source
    for forbidden in (
        "prompt_text",
        "message_body",
        "tool_arguments",
        "tool_result",
        "raw_payload",
        "private_key",
        "access_token",
    ):
        assert forbidden not in source
