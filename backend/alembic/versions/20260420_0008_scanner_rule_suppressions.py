"""Namespace-scoped scanner rule suppressions.

Revision ID: 20260420_0008
Revises: 20260413_0007
Create Date: 2026-04-20 07:40:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260420_0008"
down_revision = "20260413_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("scanner_rule_suppressions"):
        return

    op.create_table(
        "scanner_rule_suppressions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "namespace_id",
            sa.Integer(),
            sa.ForeignKey("namespaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rule_id", sa.String(length=64), nullable=False),
        sa.Column(
            "file_pattern",
            sa.String(length=255),
            nullable=False,
            server_default="*",
        ),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "rule_id",
            "file_pattern",
            name="uq_scanner_suppression",
        ),
    )
    op.create_index(
        "ix_scanner_rule_suppressions_namespace_id",
        "scanner_rule_suppressions",
        ["namespace_id"],
    )
    op.alter_column("scanner_rule_suppressions", "file_pattern", server_default=None)
    op.alter_column("scanner_rule_suppressions", "created_at", server_default=None)


def downgrade() -> None:
    op.drop_index(
        "ix_scanner_rule_suppressions_namespace_id",
        table_name="scanner_rule_suppressions",
    )
    op.drop_table("scanner_rule_suppressions")
