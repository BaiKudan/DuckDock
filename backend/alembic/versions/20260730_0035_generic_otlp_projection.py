"""Add metadata-only Generic OTLP trace reconciliation projections.

Revision ID: 20260730_0035
Revises: 20260728_0034
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260730_0035"
down_revision: str | None = "20260728_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _generic_trace_projection_items() -> tuple[sa.SchemaItem, ...]:
    return (
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("runtime_id", sa.Integer(), nullable=False),
        sa.Column("telemetry_sink_id", sa.Integer(), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=True),
        sa.Column(
            "external_trace_id",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("root_span_id", sa.String(length=16), nullable=True),
        sa.Column(
            "external_run_id",
            sa.String(length=256),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "MAPPED",
                "UNMATCHED",
                "QUARANTINED",
                name="generic_trace_projection_status",
            ),
            nullable=False,
        ),
        sa.Column("reason_code", sa.String(length=100), nullable=True),
        sa.Column("source_schema", sa.String(length=100), nullable=False),
        sa.Column(
            "source_schema_version",
            sa.String(length=50),
            nullable=False,
        ),
        sa.Column(
            "normalizer_version",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("candidate_root_count", sa.Integer(), nullable=False),
        sa.Column("observed_span_count", sa.Integer(), nullable=False),
        sa.Column(
            "first_observed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "last_observed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "content_capture_mode",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["namespaces.id"],
            name=(
                "fk_generic_trace_projections_namespace_id_"
                "namespaces"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_id"],
            ["runtime_instances.id"],
            name=(
                "fk_generic_trace_projections_runtime_id_"
                "runtime_instances"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["telemetry_sink_id"],
            ["telemetry_sinks.id"],
            name=(
                "fk_generic_trace_projections_sink_id_"
                "telemetry_sinks"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            name=(
                "fk_generic_trace_projections_agent_run_id_"
                "agent_runs"
            ),
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "public_id",
            name="uq_generic_trace_projections_public_id",
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "telemetry_sink_id",
            "external_trace_id",
            name="uq_generic_trace_projections_sink_trace",
        ),
        sa.CheckConstraint(
            "candidate_root_count >= 0 AND observed_span_count >= 1",
            name="ck_generic_trace_projections_nonnegative_counts",
        ),
        sa.CheckConstraint(
            "("
            "status = 'MAPPED' AND agent_run_id IS NOT NULL "
            "AND reason_code IS NULL"
            ") OR ("
            "status = 'UNMATCHED' AND agent_run_id IS NULL "
            "AND reason_code IS NOT NULL"
            ") OR ("
            "status = 'QUARANTINED' AND reason_code IS NOT NULL"
            ")",
            name="ck_generic_trace_projections_status_mapping",
        ),
    )


def generic_trace_projections_table() -> sa.Table:
    return sa.Table(
        "generic_trace_projections",
        sa.MetaData(),
        *_generic_trace_projection_items(),
    )


def upgrade() -> None:
    op.create_table(
        "generic_trace_projections",
        *_generic_trace_projection_items(),
    )
    for name, columns in (
        (
            "ix_generic_trace_projections_namespace_id",
            ["namespace_id"],
        ),
        (
            "ix_generic_trace_projections_runtime_id",
            ["runtime_id"],
        ),
        (
            "ix_generic_trace_projections_telemetry_sink_id",
            ["telemetry_sink_id"],
        ),
        (
            "ix_generic_trace_projections_agent_run_id",
            ["agent_run_id"],
        ),
        (
            "ix_generic_trace_projections_status",
            ["status"],
        ),
        (
            "ix_generic_trace_projections_namespace_runtime_status",
            ["namespace_id", "runtime_id", "status"],
        ),
    ):
        op.create_index(name, "generic_trace_projections", columns)


def downgrade() -> None:
    op.drop_table("generic_trace_projections")
