"""Bind EvaluationComparison evidence to exact release candidates.

Revision ID: 20260731_0044
Revises: 20260731_0043
Create Date: 2026-07-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260731_0044"
down_revision: str | None = "20260731_0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "release_candidate_evaluation_bindings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column(
            "release_candidate_ref",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column("deployment_id", sa.Integer(), nullable=False),
        sa.Column(
            "deployment_public_id",
            sa.String(length=36),
            nullable=False,
        ),
        sa.Column(
            "deployment_revision",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column(
            "deployment_configuration_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "evaluation_comparison_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "idempotency_key",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column("schema_name", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("binding_digest", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["namespaces.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"],
            ["agent_deployments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_comparison_id"],
            ["evaluation_comparisons.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "public_id",
            name="uq_release_candidate_evaluation_bindings_public_id",
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_release_candidate_evaluation_bindings_idempotency",
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "release_candidate_ref",
            "deployment_id",
            "evaluation_comparison_id",
            name="uq_release_candidate_evaluation_bindings_exact_pins",
        ),
        sa.CheckConstraint(
            "LOWER(release_candidate_ref) NOT IN "
            "('latest', 'newest', 'current')",
            name="ck_release_candidate_evaluation_bindings_no_latest",
        ),
    )
    for index_name, column_name in (
        ("ix_rc_eval_bindings_namespace", "namespace_id"),
        ("ix_rc_eval_bindings_deployment", "deployment_id"),
        ("ix_rc_eval_bindings_comparison", "evaluation_comparison_id"),
        ("ix_rc_eval_bindings_digest", "binding_digest"),
        ("ix_rc_eval_bindings_creator", "created_by_user_id"),
    ):
        op.create_index(
            index_name,
            "release_candidate_evaluation_bindings",
            [column_name],
        )
    op.create_index(
        "ix_release_candidate_evaluation_bindings_selector",
        "release_candidate_evaluation_bindings",
        [
            "namespace_id",
            "release_candidate_ref",
            "deployment_public_id",
            "deployment_revision",
        ],
    )
    op.create_index(
        "ix_release_candidate_evaluation_bindings_namespace_created",
        "release_candidate_evaluation_bindings",
        ["namespace_id", "created_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    binding_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) "
            "FROM release_candidate_evaluation_bindings"
        )
    ).scalar_one()
    if binding_count:
        raise RuntimeError(
            "0044 downgrade refused: release evaluation bindings require "
            "an explicit archival/remediation plan"
        )
    op.drop_table("release_candidate_evaluation_bindings")
