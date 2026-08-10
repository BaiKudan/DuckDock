"""Add immutable Trace2Dataset materialization receipts.

Revision ID: 20260731_0046
Revises: 20260731_0045
Create Date: 2026-07-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260731_0046"
down_revision: str | None = "20260731_0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_dataset_materializations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("dataset_version_id", sa.Integer(), nullable=False),
        sa.Column(
            "source_type",
            sa.Enum(
                "LANGFUSE_TRACE",
                name="evaluation_dataset_source_type",
            ),
            nullable=False,
        ),
        sa.Column("source_trace_ref", sa.String(length=64), nullable=False),
        sa.Column(
            "source_observation_ref",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "provider_dataset_item_ref",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("schema_name", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["namespaces.id"],
            name="fk_eval_dataset_materializations_namespace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["evaluation_datasets.id"],
            name="fk_eval_dataset_materializations_dataset",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"],
            ["evaluation_dataset_versions.id"],
            name="fk_eval_dataset_materializations_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_eval_dataset_materializations_creator",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "public_id",
            name="uq_evaluation_dataset_materializations_public_id",
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_evaluation_dataset_materializations_idempotency",
        ),
        sa.UniqueConstraint(
            "dataset_id",
            "source_trace_ref",
            "source_observation_ref",
            name="uq_evaluation_dataset_materializations_source",
        ),
        sa.UniqueConstraint(
            "dataset_version_id",
            name="uq_evaluation_dataset_materializations_version",
        ),
    )
    for index_name, columns in (
        (
            "ix_evaluation_dataset_materializations_namespace_created",
            ["namespace_id", "created_at"],
        ),
        (
            "ix_evaluation_dataset_materializations_dataset_created",
            ["dataset_id", "created_at"],
        ),
        (
            "ix_evaluation_dataset_materializations_namespace_id",
            ["namespace_id"],
        ),
        (
            "ix_evaluation_dataset_materializations_dataset_id",
            ["dataset_id"],
        ),
        (
            "ix_evaluation_dataset_materializations_dataset_version_id",
            ["dataset_version_id"],
        ),
        (
            "ix_evaluation_dataset_materializations_created_by_user_id",
            ["created_by_user_id"],
        ),
    ):
        op.create_index(
            index_name,
            "evaluation_dataset_materializations",
            columns,
        )


def downgrade() -> None:
    bind = op.get_bind()
    materialization_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM evaluation_dataset_materializations"
        )
    ).scalar_one()
    if materialization_count:
        raise RuntimeError(
            "0046 downgrade refused: Trace2Dataset receipts require an "
            "explicit archival/remediation plan"
        )
    op.drop_table("evaluation_dataset_materializations")
