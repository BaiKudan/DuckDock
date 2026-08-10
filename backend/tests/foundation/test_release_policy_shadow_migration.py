from __future__ import annotations

import importlib.util
from pathlib import Path


def test_release_policy_shadow_migration_is_complete_and_guarded() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260804_0058_release_policy_shadow.py"
    )
    spec = importlib.util.spec_from_file_location("release_policy_shadow_0058", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    source = path.read_text(encoding="utf-8")

    assert migration.revision == "20260804_0058"
    assert migration.down_revision == "20260804_0057"
    for table in (
        "release_environments",
        "release_policies",
        "release_policy_versions",
        "release_candidates",
        "release_policy_decisions",
        "release_policy_rule_results",
    ):
        assert f'"{table}"' in source
    for mode in ("SHADOW", "WARN", "ENFORCE"):
        assert f'"{mode}"' in source
    assert "0058 downgrade refused" in source
    for forbidden in (
        "prompt_text",
        "message_body",
        "tool_arguments",
        "tool_result",
        "sbom_document",
        "private_key",
    ):
        assert forbidden not in source
