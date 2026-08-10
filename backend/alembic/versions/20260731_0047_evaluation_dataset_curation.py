"""Add governed Trace2Dataset curation batches.

Revision ID: 20260731_0047
Revises: 20260731_0046
Create Date: 2026-07-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260731_0047"
down_revision: str | None = "20260731_0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _foreign_key(
    local: str,
    remote: str,
    name: str,
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [local],
        [remote],
        name=name,
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    op.create_table(
        "evaluation_dataset_curation_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("selection_digest", sa.String(length=64), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("schema_name", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("submitted_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "namespace_id",
            "namespaces.id",
            "fk_eval_dataset_curation_batches_namespace",
        ),
        _foreign_key(
            "dataset_id",
            "evaluation_datasets.id",
            "fk_eval_dataset_curation_batches_dataset",
        ),
        _foreign_key(
            "submitted_by_user_id",
            "users.id",
            "fk_eval_dataset_curation_batches_submitter",
        ),
        sa.UniqueConstraint(
            "public_id",
            name="uq_evaluation_dataset_curation_batches_public_id",
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_eval_dataset_curation_batches_idempotency",
        ),
        sa.UniqueConstraint(
            "dataset_id",
            "selection_digest",
            name="uq_eval_dataset_curation_batches_selection",
        ),
        sa.CheckConstraint(
            "item_count >= 1 AND item_count <= 20",
            name="ck_eval_dataset_curation_batches_item_count",
        ),
    )
    for name, columns in (
        (
            "ix_eval_dataset_curation_batches_namespace_created",
            ["namespace_id", "created_at"],
        ),
        (
            "ix_eval_dataset_curation_batches_dataset_created",
            ["dataset_id", "created_at"],
        ),
        (
            "ix_eval_dataset_curation_batches_namespace_id",
            ["namespace_id"],
        ),
        (
            "ix_eval_dataset_curation_batches_dataset_id",
            ["dataset_id"],
        ),
        (
            "ix_eval_dataset_curation_batches_submitted_by",
            ["submitted_by_user_id"],
        ),
    ):
        op.create_index(name, "evaluation_dataset_curation_batches", columns)

    op.create_table(
        "evaluation_dataset_curation_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("source_trace_ref", sa.String(length=64), nullable=False),
        sa.Column(
            "source_observation_ref",
            sa.String(length=64),
            nullable=False,
        ),
        _foreign_key(
            "batch_id",
            "evaluation_dataset_curation_batches.id",
            "fk_eval_dataset_curation_items_batch",
        ),
        sa.UniqueConstraint(
            "batch_id",
            "position",
            name="uq_eval_dataset_curation_items_position",
        ),
        sa.UniqueConstraint(
            "batch_id",
            "source_trace_ref",
            "source_observation_ref",
            name="uq_eval_dataset_curation_items_source",
        ),
    )
    op.create_index(
        "ix_eval_dataset_curation_items_batch_id",
        "evaluation_dataset_curation_items",
        ["batch_id"],
    )

    op.create_table(
        "evaluation_dataset_curation_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column(
            "decision",
            sa.Enum(
                "APPROVED",
                "REJECTED",
                name="evaluation_dataset_curation_decision",
            ),
            nullable=False,
        ),
        sa.Column("comment", sa.String(length=1000)),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("review_digest", sa.String(length=64), nullable=False),
        sa.Column("reviewed_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "namespace_id",
            "namespaces.id",
            "fk_eval_dataset_curation_reviews_namespace",
        ),
        _foreign_key(
            "batch_id",
            "evaluation_dataset_curation_batches.id",
            "fk_eval_dataset_curation_reviews_batch",
        ),
        _foreign_key(
            "reviewed_by_user_id",
            "users.id",
            "fk_eval_dataset_curation_reviews_reviewer",
        ),
        sa.UniqueConstraint(
            "public_id",
            name="uq_evaluation_dataset_curation_reviews_public_id",
        ),
        sa.UniqueConstraint(
            "batch_id",
            name="uq_evaluation_dataset_curation_reviews_batch",
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_eval_dataset_curation_reviews_idempotency",
        ),
    )
    for name, columns in (
        (
            "ix_eval_dataset_curation_reviews_namespace_id",
            ["namespace_id"],
        ),
        ("ix_eval_dataset_curation_reviews_batch_id", ["batch_id"]),
        (
            "ix_eval_dataset_curation_reviews_reviewed_by",
            ["reviewed_by_user_id"],
        ),
    ):
        op.create_index(name, "evaluation_dataset_curation_reviews", columns)

    op.create_table(
        "evaluation_dataset_curation_materializations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("dataset_version_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "namespace_id",
            "namespaces.id",
            "fk_eval_dataset_curation_materializations_namespace",
        ),
        _foreign_key(
            "batch_id",
            "evaluation_dataset_curation_batches.id",
            "fk_eval_dataset_curation_materializations_batch",
        ),
        _foreign_key(
            "dataset_version_id",
            "evaluation_dataset_versions.id",
            "fk_eval_dataset_curation_materializations_version",
        ),
        _foreign_key(
            "created_by_user_id",
            "users.id",
            "fk_eval_dataset_curation_materializations_creator",
        ),
        sa.UniqueConstraint(
            "public_id",
            name="uq_eval_dataset_curation_materializations_public_id",
        ),
        sa.UniqueConstraint(
            "batch_id",
            name="uq_eval_dataset_curation_materializations_batch",
        ),
        sa.UniqueConstraint(
            "dataset_version_id",
            name="uq_eval_dataset_curation_materializations_version",
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_eval_dataset_curation_materializations_idempotency",
        ),
    )
    for name, columns in (
        (
            "ix_eval_dataset_curation_materializations_namespace_id",
            ["namespace_id"],
        ),
        (
            "ix_eval_dataset_curation_materializations_batch_id",
            ["batch_id"],
        ),
        (
            "ix_eval_dataset_curation_materializations_version_id",
            ["dataset_version_id"],
        ),
        (
            "ix_eval_dataset_curation_materializations_created_by",
            ["created_by_user_id"],
        ),
    ):
        op.create_index(
            name,
            "evaluation_dataset_curation_materializations",
            columns,
        )

    op.create_table(
        "evaluation_dataset_curation_materialized_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("materialization_id", sa.Integer(), nullable=False),
        sa.Column("curation_item_id", sa.Integer(), nullable=False),
        sa.Column(
            "provider_dataset_item_ref",
            sa.String(length=255),
            nullable=False,
        ),
        _foreign_key(
            "materialization_id",
            "evaluation_dataset_curation_materializations.id",
            "fk_eval_dataset_curation_materialized_items_materialization",
        ),
        _foreign_key(
            "curation_item_id",
            "evaluation_dataset_curation_items.id",
            "fk_eval_dataset_curation_materialized_items_item",
        ),
        sa.UniqueConstraint(
            "materialization_id",
            "curation_item_id",
            name="uq_eval_dataset_curation_materialized_items_item",
        ),
        sa.UniqueConstraint(
            "materialization_id",
            "provider_dataset_item_ref",
            name="uq_eval_dataset_curation_materialized_items_provider",
        ),
    )
    op.create_index(
        "ix_eval_dataset_curation_materialized_items_materialization",
        "evaluation_dataset_curation_materialized_items",
        ["materialization_id"],
    )
    op.create_index(
        "ix_eval_dataset_curation_materialized_items_curation_item",
        "evaluation_dataset_curation_materialized_items",
        ["curation_item_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    batch_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM evaluation_dataset_curation_batches")
    ).scalar_one()
    if batch_count:
        raise RuntimeError(
            "0047 downgrade refused: curation decisions require an explicit "
            "archival/remediation plan"
        )
    for table_name in (
        "evaluation_dataset_curation_materialized_items",
        "evaluation_dataset_curation_materializations",
        "evaluation_dataset_curation_reviews",
        "evaluation_dataset_curation_items",
        "evaluation_dataset_curation_batches",
    ):
        op.drop_table(table_name)
