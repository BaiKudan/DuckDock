from __future__ import annotations

import importlib.util
from pathlib import Path


def test_identity_security_migration_is_guarded_scoped_and_secret_free() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260804_0061_identity_security_lifecycle.py"
    )
    spec = importlib.util.spec_from_file_location("identity_security_0061", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    source = path.read_text(encoding="utf-8")

    assert migration.revision == "20260804_0061"
    assert migration.down_revision == "20260804_0060"
    for table in (
        "identity_links",
        "directory_credentials",
        "directory_lifecycle_events",
        "reporter_credentials",
        "package_signing_keys",
    ):
        assert f'"{table}"' in source
    for contract in (
        "uq_directory_lifecycle_events_provider_event",
        "workload_identity_kind",
        "rotated_from_id",
        "rotation_sequence",
        "0061 downgrade refused",
    ):
        assert contract in source
    for forbidden in (
        "private_key",
        "plaintext_token",
        "raw_scim_payload",
        "message_body",
        "prompt_text",
    ):
        assert forbidden not in source
