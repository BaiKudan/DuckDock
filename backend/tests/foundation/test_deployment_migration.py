"""FND-024 migration contract for AgentDeployment inventory."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa


BACKEND_DIR = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    BACKEND_DIR
    / "alembic"
    / "versions"
    / "20260728_0030_agent_deployment_inventory.py"
)


def _load_migration_module():
    assert MIGRATION_PATH.is_file(), "missing AgentDeployment inventory revision 0030"
    spec = importlib.util.spec_from_file_location(
        "agent_deployment_inventory_0030",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_0030_creates_inventory_tables_and_constraints() -> None:
    module = _load_migration_module()
    assert module.revision == "20260728_0030"
    assert module.down_revision == "20260728_0029"

    tables = {
        table.name: table
        for table in (module.agent_deployments_table(), module.deployment_components_table())
    }
    deployment = tables["agent_deployments"]
    component = tables["deployment_components"]

    assert deployment.c.namespace_id.nullable is False
    assert deployment.c.runtime_id.nullable is False
    assert deployment.c.configuration_digest.type.length == 64
    assert component.c.component_key.nullable is False
    assert component.c.component_role.type.enums == [
        "AGENT",
        "SKILL",
        "MODEL",
        "TOOL",
        "POLICY",
        "MEMORY",
        "OTHER",
    ]
    assert component.c.configuration_json.type.__class__ is sa.JSON

    deployment_uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in deployment.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert (
        "namespace_id",
        "runtime_id",
        "external_deployment_id",
        "revision",
    ) in deployment_uniques
    assert ("public_id",) in deployment_uniques

    component_uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in component.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert ("deployment_id", "component_key") in component_uniques
