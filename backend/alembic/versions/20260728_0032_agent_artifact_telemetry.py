"""Add AgentRun artifact metadata and provider-neutral telemetry references.

Revision ID: 20260728_0032
Revises: 20260728_0031
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260728_0032"
down_revision: str | None = "20260728_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _agent_run_artifact_items() -> tuple[sa.SchemaItem, ...]:
    return (
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "TRAJECTORY",
                "EVALUATION",
                "LOG_BUNDLE",
                "OTHER",
                name="agent_run_artifact_kind",
            ),
            nullable=False,
        ),
        sa.Column("schema_name", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("object_uri", sa.String(length=512), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "sensitivity",
            sa.Enum(
                "PUBLIC",
                "INTERNAL",
                "CONFIDENTIAL",
                "RESTRICTED",
                name="sensitivity",
            ),
            nullable=False,
        ),
        sa.Column(
            "redaction_policy_version",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "completeness",
            sa.Enum(
                "COMPLETE",
                "PARTIAL",
                "UNKNOWN",
                name="agent_run_artifact_completeness",
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["namespaces.id"],
            name="fk_agent_run_artifacts_namespace_id_namespaces",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            name="fk_agent_run_artifacts_agent_run_id_agent_runs",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "public_id",
            name="uq_agent_run_artifacts_public_id",
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "agent_run_id",
            "kind",
            "sha256",
            name="uq_agent_run_artifacts_run_kind_sha256",
        ),
        sa.CheckConstraint(
            "size_bytes >= 0",
            name="ck_agent_run_artifacts_nonnegative_size",
        ),
    )


def _telemetry_sink_items() -> tuple[sa.SchemaItem, ...]:
    return (
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("endpoint", sa.String(length=512), nullable=False),
        sa.Column("project_ref", sa.String(length=255), nullable=True),
        sa.Column("credential_ref", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "DISABLED",
                "ERROR",
                name="telemetry_sink_status",
            ),
            nullable=False,
        ),
        sa.Column("config_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["namespaces.id"],
            name="fk_telemetry_sinks_namespace_id_namespaces",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "public_id",
            name="uq_telemetry_sinks_public_id",
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "name",
            name="uq_telemetry_sinks_namespace_name",
        ),
    )


def _trace_backend_ref_items() -> tuple[sa.SchemaItem, ...]:
    return (
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=False),
        sa.Column("telemetry_sink_id", sa.Integer(), nullable=False),
        sa.Column("external_trace_id", sa.String(length=256), nullable=False),
        sa.Column(
            "external_session_id",
            sa.String(length=256),
            nullable=True,
        ),
        sa.Column("trace_url", sa.String(length=512), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "CONFIRMED",
                "ERROR",
                "STALE",
                name="trace_backend_ref_status",
            ),
            nullable=False,
        ),
        sa.Column(
            "last_confirmed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["namespaces.id"],
            name="fk_trace_backend_refs_namespace_id_namespaces",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            name="fk_trace_backend_refs_agent_run_id_agent_runs",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["telemetry_sink_id"],
            ["telemetry_sinks.id"],
            name="fk_trace_backend_refs_sink_id_telemetry_sinks",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "telemetry_sink_id",
            "external_trace_id",
            name="uq_trace_backend_refs_sink_external_trace",
        ),
        sa.UniqueConstraint(
            "agent_run_id",
            "telemetry_sink_id",
            name="uq_trace_backend_refs_run_sink",
        ),
    )


def agent_run_artifacts_table() -> sa.Table:
    return sa.Table(
        "agent_run_artifacts",
        sa.MetaData(),
        *_agent_run_artifact_items(),
    )


def telemetry_sinks_table() -> sa.Table:
    return sa.Table(
        "telemetry_sinks",
        sa.MetaData(),
        *_telemetry_sink_items(),
    )


def trace_backend_refs_table() -> sa.Table:
    return sa.Table(
        "trace_backend_refs",
        sa.MetaData(),
        *_trace_backend_ref_items(),
    )


def upgrade() -> None:
    op.create_table("agent_run_artifacts", *_agent_run_artifact_items())
    for name, columns in (
        ("ix_agent_run_artifacts_namespace_id", ["namespace_id"]),
        ("ix_agent_run_artifacts_agent_run_id", ["agent_run_id"]),
        ("ix_agent_run_artifacts_kind", ["kind"]),
        ("ix_agent_run_artifacts_sha256", ["sha256"]),
        ("ix_agent_run_artifacts_sensitivity", ["sensitivity"]),
        (
            "ix_agent_run_artifacts_namespace_run_created",
            ["namespace_id", "agent_run_id", "created_at"],
        ),
    ):
        op.create_index(name, "agent_run_artifacts", columns)

    op.create_table("telemetry_sinks", *_telemetry_sink_items())
    for name, columns in (
        ("ix_telemetry_sinks_namespace_id", ["namespace_id"]),
        ("ix_telemetry_sinks_status", ["status"]),
        (
            "ix_telemetry_sinks_namespace_status_provider",
            ["namespace_id", "status", "provider"],
        ),
    ):
        op.create_index(name, "telemetry_sinks", columns)

    op.create_table("trace_backend_refs", *_trace_backend_ref_items())
    for name, columns in (
        ("ix_trace_backend_refs_namespace_id", ["namespace_id"]),
        ("ix_trace_backend_refs_agent_run_id", ["agent_run_id"]),
        ("ix_trace_backend_refs_telemetry_sink_id", ["telemetry_sink_id"]),
        ("ix_trace_backend_refs_status", ["status"]),
        (
            "ix_trace_backend_refs_namespace_run_status",
            ["namespace_id", "agent_run_id", "status"],
        ),
    ):
        op.create_index(name, "trace_backend_refs", columns)


def downgrade() -> None:
    op.drop_table("trace_backend_refs")
    op.drop_table("telemetry_sinks")
    op.drop_table("agent_run_artifacts")
