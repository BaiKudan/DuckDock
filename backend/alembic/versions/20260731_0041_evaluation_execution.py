"""Add recoverable execution state to Eval Hub evaluations.

Revision ID: 20260731_0041
Revises: 20260731_0040
Create Date: 2026-07-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260731_0041"
down_revision: str | None = "20260731_0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "evaluations",
        sa.Column("execution_key", sa.String(length=128)),
    )
    op.add_column(
        "evaluations",
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "evaluations",
        sa.Column("lease_owner", sa.String(length=128)),
    )
    op.add_column(
        "evaluations",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "evaluations",
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "evaluations",
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
    )
    op.create_unique_constraint(
        "uq_evaluations_namespace_execution_key",
        "evaluations",
        ["namespace_id", "execution_key"],
    )
    op.create_check_constraint(
        "ck_evaluations_nonnegative_attempts",
        "evaluations",
        "attempt_count >= 0",
    )
    op.create_check_constraint(
        "ck_evaluations_lease_pair",
        "evaluations",
        "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
        "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
    )
    op.create_index(
        "ix_evaluations_available_at",
        "evaluations",
        ["available_at"],
    )
    op.create_index(
        "ix_evaluations_lease_expires_at",
        "evaluations",
        ["lease_expires_at"],
    )
    op.create_index(
        "ix_evaluations_cancel_requested_at",
        "evaluations",
        ["cancel_requested_at"],
    )
    op.create_index(
        "ix_evaluations_dispatch",
        "evaluations",
        ["status", "available_at", "lease_expires_at"],
    )
    op.alter_column(
        "evaluations",
        "available_at",
        server_default=None,
    )
    op.alter_column(
        "evaluations",
        "attempt_count",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_index("ix_evaluations_dispatch", table_name="evaluations")
    op.drop_index(
        "ix_evaluations_cancel_requested_at",
        table_name="evaluations",
    )
    op.drop_index(
        "ix_evaluations_lease_expires_at",
        table_name="evaluations",
    )
    op.drop_index("ix_evaluations_available_at", table_name="evaluations")
    op.drop_constraint(
        "ck_evaluations_lease_pair",
        "evaluations",
        type_="check",
    )
    op.drop_constraint(
        "ck_evaluations_nonnegative_attempts",
        "evaluations",
        type_="check",
    )
    op.drop_constraint(
        "uq_evaluations_namespace_execution_key",
        "evaluations",
        type_="unique",
    )
    op.drop_column("evaluations", "cancel_requested_at")
    op.drop_column("evaluations", "attempt_count")
    op.drop_column("evaluations", "lease_expires_at")
    op.drop_column("evaluations", "lease_owner")
    op.drop_column("evaluations", "available_at")
    op.drop_column("evaluations", "execution_key")
