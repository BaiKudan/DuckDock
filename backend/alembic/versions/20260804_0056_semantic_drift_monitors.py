"""Add scheduled semantic drift monitors and in-app alerts.

Revision ID: 20260804_0056
Revises: 20260803_0055
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260804_0056"
down_revision: str | None = "20260803_0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _foreign_key(local: str, remote: str, name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([local], [remote], name=name, ondelete="RESTRICT")


def upgrade() -> None:
    # Periodic observations are independent evidence receipts even when an
    # unchanged provider response produces the same digest.
    op.drop_constraint(
        "uq_eval_semantic_clustering_runs_evidence",
        "evaluation_semantic_clustering_runs",
        type_="unique",
    )
    op.create_index(
        "ix_eval_semantic_clustering_runs_evidence",
        "evaluation_semantic_clustering_runs",
        ["policy_version_id", "source_case_routing_run_id", "evidence_digest"],
    )

    op.create_table(
        "evaluation_semantic_monitors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=500)),
        sa.Column("baseline_run_id", sa.Integer(), nullable=False),
        sa.Column("candidate_policy_version_id", sa.Integer(), nullable=False),
        sa.Column("regression_policy_version_id", sa.Integer(), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("config_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "PAUSED", "RETIRED", name="evaluation_semantic_monitor_status"),
            nullable=False,
        ),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key("namespace_id", "namespaces.id", "fk_eval_semantic_monitors_ns"),
        _foreign_key(
            "baseline_run_id",
            "evaluation_semantic_clustering_runs.id",
            "fk_eval_semantic_monitors_baseline",
        ),
        _foreign_key(
            "candidate_policy_version_id",
            "evaluation_semantic_clustering_policy_versions.id",
            "fk_eval_semantic_monitors_candidate_policy",
        ),
        _foreign_key(
            "regression_policy_version_id",
            "evaluation_semantic_regression_policy_versions.id",
            "fk_eval_semantic_monitors_regression_policy",
        ),
        _foreign_key("created_by_user_id", "users.id", "fk_eval_semantic_monitors_creator"),
        sa.UniqueConstraint("public_id", name="uq_eval_semantic_monitors_public_id"),
        sa.UniqueConstraint("namespace_id", "name", name="uq_eval_semantic_monitors_ns_name"),
        sa.UniqueConstraint(
            "namespace_id",
            "config_digest",
            name="uq_eval_semantic_monitors_ns_config",
        ),
        sa.CheckConstraint(
            "interval_seconds >= 60 AND interval_seconds <= 2592000",
            name="ck_eval_semantic_monitors_interval",
        ),
    )
    for name, columns in (
        ("ix_evaluation_semantic_monitors_namespace_id", ["namespace_id"]),
        ("ix_evaluation_semantic_monitors_baseline_run_id", ["baseline_run_id"]),
        (
            "ix_evaluation_semantic_monitors_candidate_policy_version_id",
            ["candidate_policy_version_id"],
        ),
        (
            "ix_evaluation_semantic_monitors_regression_policy_version_id",
            ["regression_policy_version_id"],
        ),
        ("ix_evaluation_semantic_monitors_status", ["status"]),
        ("ix_evaluation_semantic_monitors_next_run_at", ["next_run_at"]),
        (
            "ix_eval_semantic_monitors_ns_status_due",
            ["namespace_id", "status", "next_run_at"],
        ),
    ):
        op.create_index(name, "evaluation_semantic_monitors", columns)

    op.create_table(
        "evaluation_semantic_monitor_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("monitor_id", sa.Integer(), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "QUEUED",
                "RUNNING",
                "COMPLETED",
                "FAILED",
                name="evaluation_semantic_monitor_run_status",
            ),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=128)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("candidate_run_id", sa.Integer()),
        sa.Column("comparison_id", sa.Integer()),
        sa.Column(
            "outcome",
            sa.Enum(
                "PASS",
                "DRIFTED",
                "INCONCLUSIVE",
                name="evaluation_semantic_monitor_run_outcome",
            ),
        ),
        sa.Column("error_code", sa.String(length=100)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key("namespace_id", "namespaces.id", "fk_eval_semantic_monitor_runs_ns"),
        _foreign_key("monitor_id", "evaluation_semantic_monitors.id", "fk_eval_semantic_monitor_runs_monitor"),
        _foreign_key(
            "candidate_run_id",
            "evaluation_semantic_clustering_runs.id",
            "fk_eval_semantic_monitor_runs_candidate",
        ),
        _foreign_key(
            "comparison_id",
            "evaluation_semantic_regression_comparisons.id",
            "fk_eval_semantic_monitor_runs_comparison",
        ),
        sa.UniqueConstraint("public_id", name="uq_eval_semantic_monitor_runs_public_id"),
        sa.UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_eval_semantic_monitor_runs_idempotency",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 100",
            name="ck_eval_semantic_monitor_runs_attempts",
        ),
    )
    for name, columns in (
        ("ix_evaluation_semantic_monitor_runs_namespace_id", ["namespace_id"]),
        ("ix_evaluation_semantic_monitor_runs_monitor_id", ["monitor_id"]),
        ("ix_evaluation_semantic_monitor_runs_status", ["status"]),
        ("ix_evaluation_semantic_monitor_runs_candidate_run_id", ["candidate_run_id"]),
        ("ix_evaluation_semantic_monitor_runs_comparison_id", ["comparison_id"]),
        (
            "ix_eval_semantic_monitor_runs_dispatch",
            ["status", "available_at", "lease_expires_at"],
        ),
        (
            "ix_eval_semantic_monitor_runs_ns_created",
            ["namespace_id", "created_at"],
        ),
        (
            "ix_eval_semantic_monitor_runs_monitor_scheduled",
            ["monitor_id", "scheduled_for"],
        ),
    ):
        op.create_index(name, "evaluation_semantic_monitor_runs", columns)

    op.create_table(
        "evaluation_semantic_monitor_alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("monitor_id", sa.Integer(), nullable=False),
        sa.Column("monitor_run_id", sa.Integer(), nullable=False),
        sa.Column("comparison_id", sa.Integer()),
        sa.Column(
            "severity",
            sa.Enum("WARNING", "CRITICAL", name="evaluation_semantic_monitor_alert_severity"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("OPEN", "ACKNOWLEDGED", name="evaluation_semantic_monitor_alert_status"),
            nullable=False,
        ),
        sa.Column("reason_codes_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=100)),
        sa.Column("acknowledged_by_user_id", sa.Integer()),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledgement_note", sa.String(length=500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key("namespace_id", "namespaces.id", "fk_eval_semantic_monitor_alerts_ns"),
        _foreign_key("monitor_id", "evaluation_semantic_monitors.id", "fk_eval_semantic_monitor_alerts_monitor"),
        _foreign_key(
            "monitor_run_id",
            "evaluation_semantic_monitor_runs.id",
            "fk_eval_semantic_monitor_alerts_run",
        ),
        _foreign_key(
            "comparison_id",
            "evaluation_semantic_regression_comparisons.id",
            "fk_eval_semantic_monitor_alerts_comparison",
        ),
        _foreign_key(
            "acknowledged_by_user_id",
            "users.id",
            "fk_eval_semantic_monitor_alerts_ack_user",
        ),
        sa.UniqueConstraint("public_id", name="uq_eval_semantic_monitor_alerts_public_id"),
        sa.UniqueConstraint("monitor_run_id", name="uq_eval_semantic_monitor_alerts_run"),
    )
    for name, columns in (
        ("ix_evaluation_semantic_monitor_alerts_namespace_id", ["namespace_id"]),
        ("ix_evaluation_semantic_monitor_alerts_monitor_id", ["monitor_id"]),
        ("ix_evaluation_semantic_monitor_alerts_comparison_id", ["comparison_id"]),
        ("ix_evaluation_semantic_monitor_alerts_severity", ["severity"]),
        ("ix_evaluation_semantic_monitor_alerts_status", ["status"]),
        (
            "ix_eval_semantic_monitor_alerts_ns_status_created",
            ["namespace_id", "status", "created_at"],
        ),
    ):
        op.create_index(name, "evaluation_semantic_monitor_alerts", columns)


def downgrade() -> None:
    raise RuntimeError(
        "0056 downgrade refused: semantic monitor runs and alerts are immutable governance evidence"
    )
