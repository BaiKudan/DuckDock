"""Add decoupled Langfuse annotation queue synchronization.

Revision ID: 20260803_0049
Revises: 20260731_0048
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260803_0049"
down_revision: str | None = "20260731_0048"
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
        "evaluation_annotation_queue_bindings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column(
            "provider",
            sa.Enum(
                "LANGFUSE",
                "DEEPEVAL",
                "CUSTOM",
                name="evaluation_provider",
            ),
            nullable=False,
        ),
        sa.Column("provider_queue_ref", sa.String(length=255), nullable=False),
        sa.Column("provider_queue_name", sa.String(length=200), nullable=False),
        sa.Column("score_config_ids_json", sa.JSON(), nullable=False),
        sa.Column(
            "provider_updated_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "RETIRED",
                name="evaluation_annotation_queue_binding_status",
            ),
            nullable=False,
        ),
        sa.Column("schema_name", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "namespace_id",
            "namespaces.id",
            "fk_evaluation_annotation_queue_bindings_namespace",
        ),
        _foreign_key(
            "created_by_user_id",
            "users.id",
            "fk_evaluation_annotation_queue_bindings_creator",
        ),
        sa.UniqueConstraint(
            "public_id",
            name="uq_evaluation_annotation_queue_bindings_public_id",
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "provider",
            "provider_queue_ref",
            name="uq_eval_annotation_queue_bindings_provider_ref",
        ),
    )
    for name, columns in (
        (
            "ix_eval_annotation_queue_bindings_namespace_status_created",
            ["namespace_id", "status", "created_at"],
        ),
        (
            "ix_evaluation_annotation_queue_bindings_namespace_id",
            ["namespace_id"],
        ),
        (
            "ix_evaluation_annotation_queue_bindings_status",
            ["status"],
        ),
        (
            "ix_evaluation_annotation_queue_bindings_created_by_user_id",
            ["created_by_user_id"],
        ),
    ):
        op.create_index(
            name,
            "evaluation_annotation_queue_bindings",
            columns,
        )

    op.create_table(
        "evaluation_annotation_dispatches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("binding_id", sa.Integer(), nullable=False),
        sa.Column("curation_batch_id", sa.Integer(), nullable=False),
        sa.Column("sampling_run_id", sa.Integer()),
        sa.Column("provider_queue_ref", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "RUNNING",
                "SYNCED",
                "FAILED",
                name="evaluation_annotation_dispatch_status",
            ),
            nullable=False,
        ),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("synced_count", sa.Integer(), nullable=False),
        sa.Column("completed_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=128)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(length=100)),
        sa.Column("schema_name", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("synced_at", sa.DateTime(timezone=True)),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "namespace_id",
            "namespaces.id",
            "fk_evaluation_annotation_dispatches_namespace",
        ),
        _foreign_key(
            "binding_id",
            "evaluation_annotation_queue_bindings.id",
            "fk_evaluation_annotation_dispatches_binding",
        ),
        _foreign_key(
            "curation_batch_id",
            "evaluation_dataset_curation_batches.id",
            "fk_evaluation_annotation_dispatches_curation_batch",
        ),
        _foreign_key(
            "sampling_run_id",
            "evaluation_sampling_runs.id",
            "fk_evaluation_annotation_dispatches_sampling_run",
        ),
        _foreign_key(
            "created_by_user_id",
            "users.id",
            "fk_evaluation_annotation_dispatches_creator",
        ),
        sa.UniqueConstraint(
            "public_id",
            name="uq_evaluation_annotation_dispatches_public_id",
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_evaluation_annotation_dispatches_idempotency",
        ),
        sa.UniqueConstraint(
            "binding_id",
            "curation_batch_id",
            name="uq_evaluation_annotation_dispatches_binding_batch",
        ),
        sa.UniqueConstraint(
            "binding_id",
            "request_digest",
            name="uq_evaluation_annotation_dispatches_request_digest",
        ),
        sa.CheckConstraint(
            "item_count >= 1 AND item_count <= 20 "
            "AND synced_count >= 0 AND completed_count >= 0 "
            "AND failed_count >= 0 AND synced_count <= item_count "
            "AND completed_count <= synced_count "
            "AND failed_count <= item_count "
            "AND synced_count + failed_count <= item_count",
            name="ck_evaluation_annotation_dispatches_counts",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_evaluation_annotation_dispatches_attempts",
        ),
        sa.CheckConstraint(
            "(status = 'RUNNING' AND lease_owner IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'RUNNING' AND lease_owner IS NULL "
            "AND lease_expires_at IS NULL)",
            name="ck_evaluation_annotation_dispatches_lease",
        ),
        sa.CheckConstraint(
            "status <> 'SYNCED' OR "
            "(synced_count = item_count AND failed_count = 0)",
            name="ck_evaluation_annotation_dispatches_synced",
        ),
    )
    for name, columns in (
        (
            "ix_evaluation_annotation_dispatches_dispatch",
            ["status", "available_at", "lease_expires_at"],
        ),
        (
            "ix_eval_annotation_dispatches_namespace_created",
            ["namespace_id", "created_at"],
        ),
        ("ix_evaluation_annotation_dispatches_namespace_id", ["namespace_id"]),
        ("ix_evaluation_annotation_dispatches_binding_id", ["binding_id"]),
        (
            "ix_evaluation_annotation_dispatches_curation_batch_id",
            ["curation_batch_id"],
        ),
        (
            "ix_evaluation_annotation_dispatches_sampling_run_id",
            ["sampling_run_id"],
        ),
        ("ix_evaluation_annotation_dispatches_status", ["status"]),
        ("ix_evaluation_annotation_dispatches_available_at", ["available_at"]),
        (
            "ix_evaluation_annotation_dispatches_lease_expires_at",
            ["lease_expires_at"],
        ),
        (
            "ix_evaluation_annotation_dispatches_created_by_user_id",
            ["created_by_user_id"],
        ),
    ):
        op.create_index(name, "evaluation_annotation_dispatches", columns)

    op.create_table(
        "evaluation_annotation_dispatch_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dispatch_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("source_trace_ref", sa.String(length=64), nullable=False),
        sa.Column("source_observation_ref", sa.String(length=64), nullable=False),
        sa.Column(
            "sync_status",
            sa.Enum(
                "PENDING",
                "SYNCED",
                "FAILED",
                name="evaluation_annotation_item_sync_status",
            ),
            nullable=False,
        ),
        sa.Column("provider_queue_item_ref", sa.String(length=255)),
        sa.Column(
            "provider_annotation_status",
            sa.Enum(
                "PENDING",
                "COMPLETED",
                name="evaluation_annotation_provider_status",
            ),
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=100)),
        sa.Column("provider_created_at", sa.DateTime(timezone=True)),
        sa.Column("provider_updated_at", sa.DateTime(timezone=True)),
        sa.Column("provider_completed_at", sa.DateTime(timezone=True)),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "dispatch_id",
            "evaluation_annotation_dispatches.id",
            "fk_evaluation_annotation_dispatch_items_dispatch",
        ),
        sa.UniqueConstraint(
            "dispatch_id",
            "position",
            name="uq_evaluation_annotation_dispatch_items_position",
        ),
        sa.UniqueConstraint(
            "dispatch_id",
            "source_observation_ref",
            name="uq_evaluation_annotation_dispatch_items_source",
        ),
        sa.UniqueConstraint(
            "dispatch_id",
            "provider_queue_item_ref",
            name="uq_evaluation_annotation_dispatch_items_provider_ref",
        ),
        sa.CheckConstraint(
            "position >= 1 AND position <= 20 AND attempt_count >= 0",
            name="ck_evaluation_annotation_dispatch_items_position_attempts",
        ),
        sa.CheckConstraint(
            "sync_status <> 'SYNCED' OR "
            "(provider_queue_item_ref IS NOT NULL "
            "AND provider_annotation_status IS NOT NULL)",
            name="ck_evaluation_annotation_dispatch_items_synced",
        ),
    )
    for name, columns in (
        (
            "ix_evaluation_annotation_dispatch_items_dispatch_id",
            ["dispatch_id"],
        ),
        (
            "ix_evaluation_annotation_dispatch_items_sync_status",
            ["sync_status"],
        ),
        (
            "ix_eval_annotation_items_provider_status",
            ["provider_annotation_status"],
        ),
    ):
        op.create_index(
            name,
            "evaluation_annotation_dispatch_items",
            columns,
        )


def downgrade() -> None:
    bind = op.get_bind()
    populated = sum(
        bind.execute(sa.text(f"SELECT COUNT(*) FROM {table_name}")).scalar_one()
        for table_name in (
            "evaluation_annotation_queue_bindings",
            "evaluation_annotation_dispatches",
            "evaluation_annotation_dispatch_items",
        )
    )
    if populated:
        raise RuntimeError(
            "0049 downgrade refused: annotation queue provenance requires "
            "an explicit archival/remediation plan"
        )
    for table_name in (
        "evaluation_annotation_dispatch_items",
        "evaluation_annotation_dispatches",
        "evaluation_annotation_queue_bindings",
    ):
        op.drop_table(table_name)
