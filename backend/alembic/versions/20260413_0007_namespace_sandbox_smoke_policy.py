"""Add namespace sandbox smoke policy settings.

Revision ID: 20260413_0007
Revises: 20260413_0006
Create Date: 2026-04-13 21:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260413_0007"
down_revision = "20260413_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("namespace_governance_policies")
    }
    if "sandbox_agent_smoke_enabled" not in columns:
        op.add_column(
            "namespace_governance_policies",
            sa.Column("sandbox_agent_smoke_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
        op.alter_column("namespace_governance_policies", "sandbox_agent_smoke_enabled", server_default=None)
    if "sandbox_agent_smoke_timeout_seconds" not in columns:
        op.add_column(
            "namespace_governance_policies",
            sa.Column("sandbox_agent_smoke_timeout_seconds", sa.Integer(), nullable=True, server_default="45"),
        )
        op.alter_column("namespace_governance_policies", "sandbox_agent_smoke_timeout_seconds", server_default=None)


def downgrade() -> None:
    op.drop_column("namespace_governance_policies", "sandbox_agent_smoke_timeout_seconds")
    op.drop_column("namespace_governance_policies", "sandbox_agent_smoke_enabled")
