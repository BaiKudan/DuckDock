"""Add metadata-only AgentSession and AgentRun lifecycle records.

Revision ID: 20260728_0031
Revises: 20260728_0030
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260728_0031"
down_revision: str | None = "20260728_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _session_items() -> tuple[sa.SchemaItem, ...]:
    return (
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("runtime_id", sa.Integer(), nullable=False),
        sa.Column("deployment_id", sa.Integer(), nullable=True),
        sa.Column("work_trace_id", sa.Integer(), nullable=True),
        sa.Column("external_session_id", sa.String(length=256), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("OPEN", "ENDED", "ABANDONED", name="agent_session_status"),
            nullable=False,
        ),
        sa.Column(
            "content_capture_mode",
            sa.Enum("METADATA_ONLY", name="content_capture_mode"),
            nullable=False,
        ),
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
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("start_idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("start_envelope_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "completion_idempotency_key",
            sa.String(length=128),
            nullable=True,
        ),
        sa.Column(
            "completion_envelope_sha256",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["namespaces.id"],
            name="fk_agent_sessions_namespace_id_namespaces",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_id"],
            ["runtime_instances.id"],
            name="fk_agent_sessions_runtime_id_runtime_instances",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"],
            ["agent_deployments.id"],
            name="fk_agent_sessions_deployment_id_agent_deployments",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["work_trace_id"],
            ["work_traces.id"],
            name="fk_agent_sessions_work_trace_id_work_traces",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_agent_sessions_actor_user_id_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("public_id", name="uq_agent_sessions_public_id"),
        sa.UniqueConstraint(
            "namespace_id",
            "runtime_id",
            "external_session_id",
            name="uq_agent_sessions_tenant_runtime_external",
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "runtime_id",
            "start_idempotency_key",
            name="uq_agent_sessions_tenant_runtime_start_key",
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "runtime_id",
            "completion_idempotency_key",
            name="uq_agent_sessions_tenant_runtime_completion_key",
        ),
        sa.CheckConstraint(
            "("
            "status = 'OPEN' AND ended_at IS NULL "
            "AND completion_idempotency_key IS NULL "
            "AND completion_envelope_sha256 IS NULL"
            ") OR ("
            "status IN ('ENDED', 'ABANDONED') AND ended_at IS NOT NULL "
            "AND completion_idempotency_key IS NOT NULL "
            "AND completion_envelope_sha256 IS NOT NULL"
            ")",
            name="ck_agent_sessions_status_completion",
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="ck_agent_sessions_time_order",
        ),
        sa.CheckConstraint(
            "run_count >= 0 AND error_count >= 0",
            name="ck_agent_sessions_nonnegative_counts",
        ),
    )


def _run_items() -> tuple[sa.SchemaItem, ...]:
    return (
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("runtime_id", sa.Integer(), nullable=False),
        sa.Column("deployment_id", sa.Integer(), nullable=True),
        sa.Column("work_trace_id", sa.Integer(), nullable=True),
        sa.Column("external_run_id", sa.String(length=256), nullable=False),
        sa.Column("otel_trace_id", sa.String(length=32), nullable=True),
        sa.Column("root_span_id", sa.String(length=16), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "STARTED",
                "SUCCEEDED",
                "FAILED",
                "CANCELLED",
                "TIMED_OUT",
                name="agent_run_status",
            ),
            nullable=False,
        ),
        sa.Column(
            "trust_level",
            sa.Enum(
                "CHANNEL_AUTHENTICATED",
                "PRODUCER_ATTESTED",
                "UNVERIFIED",
                name="agent_run_trust_level",
            ),
            nullable=False,
        ),
        sa.Column(
            "trust_source",
            sa.Enum(
                "REPORTER",
                "COLLECTOR",
                "IMPORT",
                "ADMIN",
                name="agent_run_trust_source",
            ),
            nullable=False,
        ),
        sa.Column("source_schema", sa.String(length=100), nullable=False),
        sa.Column("source_schema_version", sa.String(length=50), nullable=False),
        sa.Column("normalizer_version", sa.String(length=64), nullable=False),
        sa.Column(
            "content_capture_mode",
            sa.Enum("METADATA_ONLY", name="content_capture_mode"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("step_count", sa.Integer(), nullable=True),
        sa.Column("model_call_count", sa.Integer(), nullable=True),
        sa.Column("tool_call_count", sa.Integer(), nullable=True),
        sa.Column("input_token_count", sa.BigInteger(), nullable=True),
        sa.Column("output_token_count", sa.BigInteger(), nullable=True),
        sa.Column("error_type", sa.String(length=100), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("start_idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("start_envelope_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "completion_idempotency_key",
            sa.String(length=128),
            nullable=True,
        ),
        sa.Column(
            "completion_envelope_sha256",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["namespaces.id"],
            name="fk_agent_runs_namespace_id_namespaces",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["agent_sessions.id"],
            name="fk_agent_runs_session_id_agent_sessions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_id"],
            ["runtime_instances.id"],
            name="fk_agent_runs_runtime_id_runtime_instances",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"],
            ["agent_deployments.id"],
            name="fk_agent_runs_deployment_id_agent_deployments",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["work_trace_id"],
            ["work_traces.id"],
            name="fk_agent_runs_work_trace_id_work_traces",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("public_id", name="uq_agent_runs_public_id"),
        sa.UniqueConstraint(
            "namespace_id",
            "runtime_id",
            "external_run_id",
            name="uq_agent_runs_tenant_runtime_external",
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "runtime_id",
            "start_idempotency_key",
            name="uq_agent_runs_tenant_runtime_start_key",
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "runtime_id",
            "completion_idempotency_key",
            name="uq_agent_runs_tenant_runtime_completion_key",
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "otel_trace_id",
            name="uq_agent_runs_tenant_otel_trace",
        ),
        sa.CheckConstraint(
            "attempt >= 1",
            name="ck_agent_runs_positive_attempt",
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_agent_runs_nonnegative_duration",
        ),
        sa.CheckConstraint(
            "(step_count IS NULL OR step_count >= 0) AND "
            "(model_call_count IS NULL OR model_call_count >= 0) AND "
            "(tool_call_count IS NULL OR tool_call_count >= 0) AND "
            "(input_token_count IS NULL OR input_token_count >= 0) AND "
            "(output_token_count IS NULL OR output_token_count >= 0)",
            name="ck_agent_runs_nonnegative_aggregates",
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="ck_agent_runs_time_order",
        ),
        sa.CheckConstraint(
            "("
            "status = 'STARTED' AND ended_at IS NULL "
            "AND completion_idempotency_key IS NULL "
            "AND completion_envelope_sha256 IS NULL"
            ") OR ("
            "status IN ('SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT') "
            "AND ended_at IS NOT NULL "
            "AND completion_idempotency_key IS NOT NULL "
            "AND completion_envelope_sha256 IS NOT NULL"
            ")",
            name="ck_agent_runs_status_completion",
        ),
    )


def agent_sessions_table() -> sa.Table:
    return sa.Table("agent_sessions", sa.MetaData(), *_session_items())


def agent_runs_table() -> sa.Table:
    return sa.Table("agent_runs", sa.MetaData(), *_run_items())


def upgrade() -> None:
    op.create_table("agent_sessions", *_session_items())
    op.create_index(
        "ix_agent_sessions_namespace_id",
        "agent_sessions",
        ["namespace_id"],
    )
    op.create_index(
        "ix_agent_sessions_runtime_id",
        "agent_sessions",
        ["runtime_id"],
    )
    op.create_index(
        "ix_agent_sessions_deployment_id",
        "agent_sessions",
        ["deployment_id"],
    )
    op.create_index(
        "ix_agent_sessions_work_trace_id",
        "agent_sessions",
        ["work_trace_id"],
    )
    op.create_index(
        "ix_agent_sessions_actor_user_id",
        "agent_sessions",
        ["actor_user_id"],
    )
    op.create_index(
        "ix_agent_sessions_status",
        "agent_sessions",
        ["status"],
    )
    op.create_index(
        "ix_agent_sessions_sensitivity",
        "agent_sessions",
        ["sensitivity"],
    )
    op.create_index(
        "ix_agent_sessions_started_at",
        "agent_sessions",
        ["started_at"],
    )
    op.create_index(
        "ix_agent_sessions_namespace_status_started",
        "agent_sessions",
        ["namespace_id", "status", "started_at"],
    )

    op.create_table("agent_runs", *_run_items())
    for index_name, columns in (
        ("ix_agent_runs_namespace_id", ["namespace_id"]),
        ("ix_agent_runs_session_id", ["session_id"]),
        ("ix_agent_runs_runtime_id", ["runtime_id"]),
        ("ix_agent_runs_deployment_id", ["deployment_id"]),
        ("ix_agent_runs_work_trace_id", ["work_trace_id"]),
        ("ix_agent_runs_status", ["status"]),
        ("ix_agent_runs_trust_level", ["trust_level"]),
        ("ix_agent_runs_trust_source", ["trust_source"]),
        ("ix_agent_runs_started_at", ["started_at"]),
        ("ix_agent_runs_namespace_started", ["namespace_id", "started_at"]),
        (
            "ix_agent_runs_namespace_runtime_started",
            ["namespace_id", "runtime_id", "started_at"],
        ),
        (
            "ix_agent_runs_namespace_deployment_started",
            ["namespace_id", "deployment_id", "started_at"],
        ),
        (
            "ix_agent_runs_namespace_status_started",
            ["namespace_id", "status", "started_at"],
        ),
    ):
        op.create_index(index_name, "agent_runs", columns)


def downgrade() -> None:
    op.drop_table("agent_runs")
    op.drop_table("agent_sessions")
