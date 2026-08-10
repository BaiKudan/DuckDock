"""Add the transactional domain-event outbox.

Revision ID: 20260728_0033
Revises: 20260728_0032
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260728_0033"
down_revision: str | None = "20260728_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _outbox_event_items() -> tuple[sa.SchemaItem, ...]:
    return (
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_public_id", sa.String(length=200), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "LEASED",
                "PUBLISHED",
                "FAILED",
                name="outbox_event_status",
            ),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column(
            "lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["namespaces.id"],
            name="fk_outbox_events_namespace_id_namespaces",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "event_id",
            name="uq_outbox_events_event_id",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_outbox_events_idempotency_key",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_outbox_events_nonnegative_attempts",
        ),
        sa.CheckConstraint(
            "("
            "status = 'LEASED' AND lease_owner IS NOT NULL "
            "AND lease_expires_at IS NOT NULL"
            ") OR ("
            "status <> 'LEASED' AND lease_owner IS NULL "
            "AND lease_expires_at IS NULL"
            ")",
            name="ck_outbox_events_lease_state",
        ),
        sa.CheckConstraint(
            "("
            "status = 'PUBLISHED' AND published_at IS NOT NULL"
            ") OR ("
            "status <> 'PUBLISHED' AND published_at IS NULL"
            ")",
            name="ck_outbox_events_published_state",
        ),
    )


def outbox_events_table() -> sa.Table:
    return sa.Table(
        "outbox_events",
        sa.MetaData(),
        *_outbox_event_items(),
    )


def upgrade() -> None:
    op.create_table("outbox_events", *_outbox_event_items())
    for name, columns in (
        ("ix_outbox_events_namespace_id", ["namespace_id"]),
        ("ix_outbox_events_aggregate_public_id", ["aggregate_public_id"]),
        ("ix_outbox_events_event_type", ["event_type"]),
        ("ix_outbox_events_status", ["status"]),
        ("ix_outbox_events_available_at", ["available_at"]),
        ("ix_outbox_events_lease_expires_at", ["lease_expires_at"]),
        ("ix_outbox_events_published_at", ["published_at"]),
        (
            "ix_outbox_events_dispatch",
            ["status", "available_at", "lease_expires_at"],
        ),
        (
            "ix_outbox_events_expired_lease",
            ["status", "lease_expires_at", "id"],
        ),
        (
            "ix_outbox_events_namespace_status_created",
            ["namespace_id", "status", "created_at"],
        ),
    ):
        op.create_index(name, "outbox_events", columns)


def downgrade() -> None:
    op.drop_table("outbox_events")
