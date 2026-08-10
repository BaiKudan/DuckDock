"""Add idempotent v1/v2 compatibility reconciliation projections.

Revision ID: 20260728_0034
Revises: 20260728_0033
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260728_0034"
down_revision: str | None = "20260728_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _outbox_consumer_receipt_items() -> tuple[sa.SchemaItem, ...]:
    return (
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("consumer_name", sa.String(length=100), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("result_code", sa.String(length=100), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["outbox_events.event_id"],
            name=(
                "fk_outbox_consumer_receipts_event_id_"
                "outbox_events"
            ),
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "consumer_name",
            "event_id",
            name="uq_outbox_consumer_receipts_consumer_event",
        ),
    )


def outbox_consumer_receipts_table() -> sa.Table:
    return sa.Table(
        "outbox_consumer_receipts",
        sa.MetaData(),
        *_outbox_consumer_receipt_items(),
    )


def _v1_v2_reconciliation_items() -> tuple[sa.SchemaItem, ...]:
    return (
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("runtime_id", sa.Integer(), nullable=False),
        sa.Column("work_trace_id", sa.Integer(), nullable=False),
        sa.Column(
            "source_report_id",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "MATCHED",
                "EXPECTED_LEGACY_ONLY",
                "MISMATCH",
                name="v1_v2_reconciliation_status",
            ),
            nullable=False,
        ),
        sa.Column(
            "reason_code",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column(
            "linked_run_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "linked_artifact_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("last_event_id", sa.String(length=36), nullable=False),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["namespaces.id"],
            name=(
                "fk_v1_v2_reconciliations_namespace_id_"
                "namespaces"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_id"],
            ["runtime_instances.id"],
            name=(
                "fk_v1_v2_reconciliations_runtime_id_"
                "runtime_instances"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["work_trace_id"],
            ["work_traces.id"],
            name=(
                "fk_v1_v2_reconciliations_work_trace_id_"
                "work_traces"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["last_event_id"],
            ["outbox_events.event_id"],
            name=(
                "fk_v1_v2_reconciliations_last_event_id_"
                "outbox_events"
            ),
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "work_trace_id",
            name="uq_v1_v2_reconciliations_work_trace",
        ),
        sa.CheckConstraint(
            "linked_run_count >= 0 AND linked_artifact_count >= 0",
            name="ck_v1_v2_reconciliations_nonnegative_counts",
        ),
    )


def v1_v2_reconciliations_table() -> sa.Table:
    return sa.Table(
        "v1_v2_reconciliations",
        sa.MetaData(),
        *_v1_v2_reconciliation_items(),
    )


def upgrade() -> None:
    op.create_table(
        "outbox_consumer_receipts",
        *_outbox_consumer_receipt_items(),
    )
    op.create_index(
        "ix_outbox_consumer_receipts_event_id",
        "outbox_consumer_receipts",
        ["event_id"],
    )
    op.create_index(
        "ix_outbox_consumer_receipts_consumer_processed",
        "outbox_consumer_receipts",
        ["consumer_name", "processed_at"],
    )

    op.create_table(
        "v1_v2_reconciliations",
        *_v1_v2_reconciliation_items(),
    )
    for name, columns in (
        ("ix_v1_v2_reconciliations_namespace_id", ["namespace_id"]),
        ("ix_v1_v2_reconciliations_runtime_id", ["runtime_id"]),
        ("ix_v1_v2_reconciliations_work_trace_id", ["work_trace_id"]),
        ("ix_v1_v2_reconciliations_source_report_id", ["source_report_id"]),
        ("ix_v1_v2_reconciliations_status", ["status"]),
        (
            "ix_v1_v2_reconciliations_status_checked",
            ["status", "checked_at"],
        ),
        (
            "ix_v1_v2_reconciliations_namespace_status",
            ["namespace_id", "status"],
        ),
    ):
        op.create_index(name, "v1_v2_reconciliations", columns)


def downgrade() -> None:
    op.drop_table("v1_v2_reconciliations")
    op.drop_table("outbox_consumer_receipts")
