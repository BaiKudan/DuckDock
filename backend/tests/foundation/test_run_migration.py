"""FND-037 migration contract for AgentSession and AgentRun."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa


BACKEND_DIR = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    BACKEND_DIR
    / "alembic"
    / "versions"
    / "20260728_0031_agent_session_run.py"
)


def _load_migration_module():
    assert MIGRATION_PATH.is_file(), "missing AgentSession/AgentRun revision 0031"
    spec = importlib.util.spec_from_file_location(
        "agent_session_run_0031",
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


def test_revision_0031_creates_metadata_only_session_run_contract() -> None:
    module = _load_migration_module()
    assert module.revision == "20260728_0031"
    assert module.down_revision == "20260728_0030"

    session = module.agent_sessions_table()
    run = module.agent_runs_table()
    assert session.c.namespace_id.nullable is False
    assert session.c.start_envelope_sha256.type.length == 64
    assert run.c.namespace_id.nullable is False
    assert run.c.start_envelope_sha256.type.length == 64
    assert run.c.completion_envelope_sha256.type.length == 64
    assert not {"prompt", "messages", "completion", "tool_arguments", "tool_result"} & {
        column.name for column in run.columns
    }

    assert (
        "namespace_id",
        "runtime_id",
        "external_session_id",
    ) in _unique_columns(session)
    assert (
        "namespace_id",
        "runtime_id",
        "external_run_id",
    ) in _unique_columns(run)
    assert (
        "namespace_id",
        "runtime_id",
        "start_idempotency_key",
    ) in _unique_columns(run)
    assert (
        "namespace_id",
        "runtime_id",
        "completion_idempotency_key",
    ) in _unique_columns(run)
    assert ("namespace_id", "otel_trace_id") in _unique_columns(run)
