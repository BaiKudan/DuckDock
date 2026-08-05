"""RT-06 Generic OTLP metadata projection migration contract."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa


BACKEND_DIR = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    BACKEND_DIR
    / "alembic"
    / "versions"
    / "20260730_0035_generic_otlp_projection.py"
)


def _load_migration_module():
    assert MIGRATION_PATH.is_file(), "missing Generic OTLP revision 0035"
    spec = importlib.util.spec_from_file_location(
        "generic_otlp_projection_0035",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_0035_adds_metadata_only_trace_projection() -> None:
    module = _load_migration_module()
    assert module.revision == "20260730_0035"
    assert module.down_revision == "20260728_0034"

    projection = module.generic_trace_projections_table()
    uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in projection.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert (
        "namespace_id",
        "telemetry_sink_id",
        "external_trace_id",
    ) in uniques
    assert projection.c.agent_run_id.nullable is True
    assert projection.c.external_trace_id.type.length == 32
    assert projection.c.root_span_id.type.length == 16
    assert projection.c.status.type.enums == [
        "MAPPED",
        "UNMATCHED",
        "QUARANTINED",
    ]
    assert "payload" not in projection.c
    assert "attributes" not in projection.c
    assert "events" not in projection.c
    assert "prompt" not in projection.c
