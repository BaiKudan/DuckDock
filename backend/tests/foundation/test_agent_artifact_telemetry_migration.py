"""FND-052 migration contract for artifacts, sinks, and trace references."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa


BACKEND_DIR = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    BACKEND_DIR
    / "alembic"
    / "versions"
    / "20260728_0032_agent_artifact_telemetry.py"
)


def _load_migration_module():
    assert MIGRATION_PATH.is_file(), "missing artifact/telemetry revision 0032"
    spec = importlib.util.spec_from_file_location(
        "agent_artifact_telemetry_0032",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _unique_columns(table: sa.Table) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }


def test_revision_0032_creates_provider_neutral_metadata_tables() -> None:
    module = _load_migration_module()
    assert module.revision == "20260728_0032"
    assert module.down_revision == "20260728_0031"

    artifact = module.agent_run_artifacts_table()
    sink = module.telemetry_sinks_table()
    trace_ref = module.trace_backend_refs_table()

    assert artifact.c.namespace_id.nullable is False
    assert artifact.c.object_uri.type.length == 512
    assert artifact.c.sha256.type.length == 64
    assert artifact.c.size_bytes.type.__class__ is sa.BigInteger
    assert "payload" not in artifact.c
    assert "prompt" not in artifact.c
    assert (
        "namespace_id",
        "agent_run_id",
        "kind",
        "sha256",
    ) in _unique_columns(artifact)

    assert sink.c.credential_ref.nullable is False
    assert "credential" not in sink.c
    assert "api_key" not in sink.c
    assert ("namespace_id", "name") in _unique_columns(sink)

    assert trace_ref.c.last_error_code.type.length == 100
    assert ("agent_run_id", "telemetry_sink_id") in _unique_columns(trace_ref)
    assert (
        "telemetry_sink_id",
        "external_trace_id",
    ) in _unique_columns(trace_ref)
