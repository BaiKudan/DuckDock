"""Add immutable AgentDeployment and DeploymentComponent inventory.

Revision ID: 20260728_0030
Revises: 20260728_0029
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260728_0030"
down_revision: str | None = "20260728_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _agent_deployment_items() -> tuple[sa.SchemaItem, ...]:
    return (
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("runtime_id", sa.Integer(), nullable=False),
        sa.Column("agent_asset_id", sa.Integer(), nullable=True),
        sa.Column("external_deployment_id", sa.String(length=255), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.String(length=128), nullable=False),
        sa.Column("configuration_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "REGISTERED",
                "ACTIVE",
                "RETIRED",
                "FAILED",
                name="agent_deployment_status",
            ),
            nullable=False,
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["namespaces.id"],
            name="fk_agent_deployments_namespace_id_namespaces",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_id"],
            ["runtime_instances.id"],
            name="fk_agent_deployments_runtime_id_runtime_instances",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["agent_asset_id"],
            ["ai_assets.id"],
            name="fk_agent_deployments_agent_asset_id_ai_assets",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_agent_deployments_created_by_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "public_id",
            name="uq_agent_deployments_public_id",
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "runtime_id",
            "external_deployment_id",
            "revision",
            name="uq_agent_deployments_tenant_runtime_external_revision",
        ),
        sa.CheckConstraint(
            "("
            "status = 'REGISTERED' AND activated_at IS NULL AND retired_at IS NULL"
            ") OR ("
            "status = 'ACTIVE' AND activated_at IS NOT NULL AND retired_at IS NULL"
            ") OR ("
            "status = 'RETIRED' AND activated_at IS NOT NULL AND retired_at IS NOT NULL"
            ") OR ("
            "status = 'FAILED' AND activated_at IS NULL AND retired_at IS NULL"
            ")",
            name="ck_agent_deployments_status_timestamps",
        ),
    )


def _deployment_component_items() -> tuple[sa.SchemaItem, ...]:
    return (
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("deployment_id", sa.Integer(), nullable=False),
        sa.Column("component_key", sa.String(length=128), nullable=False),
        sa.Column(
            "component_role",
            sa.Enum(
                "AGENT",
                "SKILL",
                "MODEL",
                "TOOL",
                "POLICY",
                "MEMORY",
                "OTHER",
                name="deployment_component_role",
            ),
            nullable=False,
        ),
        sa.Column("ai_asset_id", sa.Integer(), nullable=True),
        sa.Column("skill_version_id", sa.Integer(), nullable=True),
        sa.Column("external_version", sa.String(length=255), nullable=True),
        sa.Column("content_digest", sa.String(length=64), nullable=True),
        sa.Column("configuration_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["deployment_id"],
            ["agent_deployments.id"],
            name="fk_deployment_components_deployment_id_agent_deployments",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ai_asset_id"],
            ["ai_assets.id"],
            name="fk_deployment_components_ai_asset_id_ai_assets",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["skill_version_id"],
            ["skill_versions.id"],
            name="fk_deployment_components_skill_version_id_skill_versions",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "deployment_id",
            "component_key",
            name="uq_deployment_components_deployment_key",
        ),
        sa.CheckConstraint(
            "ai_asset_id IS NOT NULL OR "
            "skill_version_id IS NOT NULL OR "
            "external_version IS NOT NULL OR "
            "content_digest IS NOT NULL",
            name="ck_deployment_components_version_identity",
        ),
    )


def agent_deployments_table() -> sa.Table:
    return sa.Table(
        "agent_deployments",
        sa.MetaData(),
        *_agent_deployment_items(),
    )


def deployment_components_table() -> sa.Table:
    return sa.Table(
        "deployment_components",
        sa.MetaData(),
        *_deployment_component_items(),
    )


def upgrade() -> None:
    op.create_table("agent_deployments", *_agent_deployment_items())
    op.create_index(
        "ix_agent_deployments_namespace_id",
        "agent_deployments",
        ["namespace_id"],
    )
    op.create_index(
        "ix_agent_deployments_runtime_id",
        "agent_deployments",
        ["runtime_id"],
    )
    op.create_index(
        "ix_agent_deployments_agent_asset_id",
        "agent_deployments",
        ["agent_asset_id"],
    )
    op.create_index(
        "ix_agent_deployments_status",
        "agent_deployments",
        ["status"],
    )
    op.create_index(
        "ix_agent_deployments_environment",
        "agent_deployments",
        ["environment"],
    )
    op.create_index(
        "ix_agent_deployments_created_by_user_id",
        "agent_deployments",
        ["created_by_user_id"],
    )
    op.create_index(
        "ix_agent_deployments_namespace_env_status_created",
        "agent_deployments",
        ["namespace_id", "environment", "status", "created_at"],
    )

    op.create_table("deployment_components", *_deployment_component_items())
    op.create_index(
        "ix_deployment_components_deployment_id",
        "deployment_components",
        ["deployment_id"],
    )
    op.create_index(
        "ix_deployment_components_component_role",
        "deployment_components",
        ["component_role"],
    )
    op.create_index(
        "ix_deployment_components_ai_asset_id",
        "deployment_components",
        ["ai_asset_id"],
    )
    op.create_index(
        "ix_deployment_components_skill_version_id",
        "deployment_components",
        ["skill_version_id"],
    )
    op.create_index(
        "ix_deployment_components_deployment_role",
        "deployment_components",
        ["deployment_id", "component_role"],
    )


def downgrade() -> None:
    op.drop_table("deployment_components")
    op.drop_table("agent_deployments")
