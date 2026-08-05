from __future__ import annotations

import importlib.util
from pathlib import Path


def test_release_evaluation_review_migration_is_guarded_and_linear() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260731_0045_release_evaluation_review.py"
    )
    spec = importlib.util.spec_from_file_location(
        "release_evaluation_review_0045",
        path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    source = path.read_text(encoding="utf-8")

    assert migration.revision == "20260731_0045"
    assert migration.down_revision == "20260731_0044"
    assert '"release_candidate_evaluation_reviews"' in source
    assert "uq_release_candidate_eval_reviews_binding" in source
    assert "uq_release_candidate_eval_reviews_idempotency" in source
    assert "ck_release_candidate_eval_reviews_rejection_comment" in source
    assert "release_candidate_review_decision" in source
    assert "review_digest" in source
    assert "0045 downgrade refused" in source
