from __future__ import annotations

import importlib.util
from pathlib import Path


def test_release_promotion_receipts_migration_is_complete_and_guarded() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260804_0059_release_promotion_receipts.py"
    )
    spec = importlib.util.spec_from_file_location("release_promotion_receipts_0059", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    source = path.read_text(encoding="utf-8")

    assert migration.revision == "20260804_0059"
    assert migration.down_revision == "20260804_0058"
    for table in (
        "release_candidate_approvals",
        "release_policy_exceptions",
        "release_policy_exception_reviews",
        "release_promotions",
        "release_rollbacks",
        "release_deployment_receipts",
        "release_environment_releases",
        "release_canary_evaluations",
    ):
        assert f'"{table}"' in source
    for contract in (
        "fk_release_rollbacks_target_release",
        "0059 downgrade refused",
    ):
        assert contract in source
    for forbidden in (
        "prompt_text",
        "message_body",
        "tool_arguments",
        "tool_result",
        "raw_payload",
        "private_key",
    ):
        assert forbidden not in source
