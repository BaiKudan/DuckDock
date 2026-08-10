"""Add immutable SLO, incident and recovery-drill evidence.

Revision ID: 20260804_0062
Revises: 20260804_0061
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260804_0062"
down_revision: str | None = "20260804_0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ops_slo_evaluations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("profile_version", sa.String(length=64), nullable=False),
        sa.Column("window_minutes", sa.Integer(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("http_error_ratio", sa.Float()),
        sa.Column("evidence_ingest_p95_ms", sa.Float()),
        sa.Column("run_timeline_p95_ms", sa.Float()),
        sa.Column("policy_decision_p95_ms", sa.Float()),
        sa.Column("outbox_failed_count", sa.Integer(), nullable=False),
        sa.Column("outbox_oldest_pending_age_seconds", sa.Integer()),
        sa.Column(
            "status",
            sa.Enum("HEALTHY", "DEGRADED", "BREACHED", name="ops_slo_evaluation_status"),
            nullable=False,
        ),
        sa.Column("reason_codes_json", sa.JSON(), nullable=False),
        sa.Column("evidence_digest", sa.String(length=64), nullable=False),
        sa.Column("evaluated_by_user_id", sa.Integer(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["evaluated_by_user_id"],
            ["users.id"],
            name="fk_ops_slo_evaluations_evaluator",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("public_id", name="uq_ops_slo_evaluations_public_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_ops_slo_evaluations_idempotency"),
    )
    for name, columns in (
        ("ix_ops_slo_evaluations_evidence_digest", ["evidence_digest"]),
        ("ix_ops_slo_evaluations_evaluated_by_user_id", ["evaluated_by_user_id"]),
        ("ix_ops_slo_evaluations_status_created", ["status", "created_at"]),
    ):
        op.create_index(name, "ops_slo_evaluations", columns)

    op.create_table(
        "ops_incidents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("slo_evaluation_id", sa.Integer(), nullable=False),
        sa.Column(
            "severity",
            sa.Enum("WARNING", "CRITICAL", name="ops_incident_severity"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("OPEN", "ACKNOWLEDGED", "RESOLVED", name="ops_incident_status"),
            nullable=False,
        ),
        sa.Column("reason_codes_json", sa.JSON(), nullable=False),
        sa.Column("evidence_digest", sa.String(length=64), nullable=False),
        sa.Column("acknowledged_by_user_id", sa.Integer()),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_by_user_id", sa.Integer()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["slo_evaluation_id"],
            ["ops_slo_evaluations.id"],
            name="fk_ops_incidents_evaluation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["acknowledged_by_user_id"],
            ["users.id"],
            name="fk_ops_incidents_acknowledger",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_user_id"],
            ["users.id"],
            name="fk_ops_incidents_resolver",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("public_id", name="uq_ops_incidents_public_id"),
        sa.UniqueConstraint("slo_evaluation_id", name="uq_ops_incidents_evaluation"),
    )
    for name, columns in (
        ("ix_ops_incidents_slo_evaluation_id", ["slo_evaluation_id"]),
        ("ix_ops_incidents_status", ["status"]),
        ("ix_ops_incidents_evidence_digest", ["evidence_digest"]),
        ("ix_ops_incidents_acknowledged_by_user_id", ["acknowledged_by_user_id"]),
        ("ix_ops_incidents_resolved_by_user_id", ["resolved_by_user_id"]),
        ("ix_ops_incidents_status_created", ["status", "created_at"]),
    ):
        op.create_index(name, "ops_incidents", columns)

    op.create_table(
        "ops_recovery_drills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("git_head", sa.String(length=64), nullable=False),
        sa.Column("backup_set_digest", sa.String(length=64), nullable=False),
        sa.Column("mysql_digest", sa.String(length=64), nullable=False),
        sa.Column("object_store_digest", sa.String(length=64), nullable=False),
        sa.Column("mysql_row_count", sa.Integer(), nullable=False),
        sa.Column("object_count", sa.Integer(), nullable=False),
        sa.Column("rpo_seconds", sa.Integer(), nullable=False),
        sa.Column("rto_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("PASSED", "FAILED", name="ops_recovery_drill_status"),
            nullable=False,
        ),
        sa.Column("reason_codes_json", sa.JSON(), nullable=False),
        sa.Column("evidence_digest", sa.String(length=64), nullable=False),
        sa.Column("executed_by_user_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["executed_by_user_id"],
            ["users.id"],
            name="fk_ops_recovery_drills_executor",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("public_id", name="uq_ops_recovery_drills_public_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_ops_recovery_drills_idempotency"),
    )
    for name, columns in (
        ("ix_ops_recovery_drills_evidence_digest", ["evidence_digest"]),
        ("ix_ops_recovery_drills_executed_by_user_id", ["executed_by_user_id"]),
        ("ix_ops_recovery_drills_status_finished", ["status", "finished_at"]),
    ):
        op.create_index(name, "ops_recovery_drills", columns)


def downgrade() -> None:
    connection = op.get_bind()
    for table in ("ops_slo_evaluations", "ops_incidents", "ops_recovery_drills"):
        count = int(connection.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)
        if count:
            raise RuntimeError(
                "0062 downgrade refused: SLO, incident and recovery-drill receipts are immutable operations evidence"
            )

    op.drop_table("ops_recovery_drills")
    op.drop_table("ops_incidents")
    op.drop_table("ops_slo_evaluations")
    for enum_name in (
        "ops_recovery_drill_status",
        "ops_incident_status",
        "ops_incident_severity",
        "ops_slo_evaluation_status",
    ):
        sa.Enum(name=enum_name).drop(connection, checkfirst=True)
