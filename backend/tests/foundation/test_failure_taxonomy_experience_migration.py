from __future__ import annotations

import importlib.util
from pathlib import Path


def test_failure_taxonomy_experience_migration_is_guarded_and_linear() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260803_0052_failure_taxonomy_experience.py"
    )
    spec = importlib.util.spec_from_file_location(
        "failure_taxonomy_experience_0052",
        path,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    source = path.read_text(encoding="utf-8")

    assert migration.revision == "20260803_0052"
    assert migration.down_revision == "20260803_0051"
    assert '"evaluation_failure_taxonomy_policies"' in source
    assert '"evaluation_failure_taxonomy_policy_versions"' in source
    assert '"evaluation_experience_extraction_runs"' in source
    assert '"evaluation_experience_candidates"' in source
    assert '"evaluation_experience_candidate_evidence"' in source
    assert '"evaluation_experience_candidate_reviews"' in source
    assert "evaluation_failure_category" in source
    assert "evaluation_experience_candidate_status" in source
    assert "0052 downgrade refused" in source
