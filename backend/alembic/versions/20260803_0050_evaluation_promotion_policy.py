"""Add annotation-driven quality/diversity promotion recommendations.

Revision ID: 20260803_0050
Revises: 20260803_0049
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260803_0050"
down_revision: str | None = "20260803_0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _foreign_key(local: str, remote: str, name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [local], [remote], name=name, ondelete="RESTRICT"
    )


def upgrade() -> None:
    op.create_table(
        "evaluation_promotion_policies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=500)),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "RETIRED",
                name="evaluation_promotion_policy_status",
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "namespace_id",
            "namespaces.id",
            "fk_evaluation_promotion_policies_namespace",
        ),
        sa.UniqueConstraint(
            "public_id", name="uq_evaluation_promotion_policies_public_id"
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "name",
            name="uq_evaluation_promotion_policies_namespace_name",
        ),
    )
    for name, columns in (
        (
            "ix_eval_promotion_policies_namespace_status_created",
            ["namespace_id", "status", "created_at"],
        ),
        ("ix_evaluation_promotion_policies_namespace_id", ["namespace_id"]),
        ("ix_evaluation_promotion_policies_status", ["status"]),
    ):
        op.create_index(name, "evaluation_promotion_policies", columns)

    op.create_table(
        "evaluation_promotion_policy_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("policy_id", sa.Integer(), nullable=False),
        sa.Column("binding_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("config_digest", sa.String(length=64), nullable=False),
        sa.Column("score_config_id", sa.String(length=255), nullable=False),
        sa.Column(
            "score_data_type",
            sa.Enum(
                "NUMERIC",
                "BOOLEAN",
                "CATEGORICAL",
                name="evaluation_promotion_score_data_type",
            ),
            nullable=False,
        ),
        sa.Column("minimum_numeric_score", sa.Float()),
        sa.Column("accepted_values_json", sa.JSON()),
        sa.Column(
            "diversity_dimension",
            sa.Enum(
                "NONE",
                "OBSERVATION_NAME",
                "OBSERVATION_TYPE",
                "ENVIRONMENT",
                name="evaluation_promotion_diversity_dimension",
            ),
            nullable=False,
        ),
        sa.Column("min_distinct_buckets", sa.Integer(), nullable=False),
        sa.Column("schema_name", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "policy_id",
            "evaluation_promotion_policies.id",
            "fk_eval_promotion_policy_versions_policy",
        ),
        _foreign_key(
            "binding_id",
            "evaluation_annotation_queue_bindings.id",
            "fk_eval_promotion_policy_versions_binding",
        ),
        _foreign_key(
            "created_by_user_id",
            "users.id",
            "fk_eval_promotion_policy_versions_creator",
        ),
        sa.UniqueConstraint(
            "public_id", name="uq_eval_promotion_policy_versions_public_id"
        ),
        sa.UniqueConstraint(
            "policy_id",
            "version",
            name="uq_eval_promotion_policy_versions_policy_version",
        ),
        sa.UniqueConstraint(
            "policy_id",
            "config_digest",
            name="uq_eval_promotion_policy_versions_policy_digest",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_eval_promotion_policy_versions_positive",
        ),
        sa.CheckConstraint(
            "min_distinct_buckets >= 1 AND min_distinct_buckets <= 20",
            name="ck_eval_promotion_policy_versions_buckets",
        ),
    )
    for name, columns in (
        (
            "ix_eval_promotion_policy_versions_policy_created",
            ["policy_id", "created_at"],
        ),
        ("ix_eval_promotion_policy_versions_policy_id", ["policy_id"]),
        ("ix_eval_promotion_policy_versions_binding_id", ["binding_id"]),
        ("ix_eval_promotion_policy_versions_config_digest", ["config_digest"]),
        (
            "ix_eval_promotion_policy_versions_created_by",
            ["created_by_user_id"],
        ),
    ):
        op.create_index(name, "evaluation_promotion_policy_versions", columns)

    op.create_table(
        "evaluation_promotion_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("policy_version_id", sa.Integer(), nullable=False),
        sa.Column("dispatch_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("evidence_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "outcome",
            sa.Enum(
                "RECOMMENDED",
                "BLOCKED",
                name="evaluation_promotion_outcome",
            ),
            nullable=False,
        ),
        sa.Column("reason_codes_json", sa.JSON(), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("completed_count", sa.Integer(), nullable=False),
        sa.Column("scored_count", sa.Integer(), nullable=False),
        sa.Column("passed_count", sa.Integer(), nullable=False),
        sa.Column("distinct_bucket_count", sa.Integer(), nullable=False),
        sa.Column("schema_name", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "namespace_id",
            "namespaces.id",
            "fk_evaluation_promotion_runs_namespace",
        ),
        _foreign_key(
            "policy_version_id",
            "evaluation_promotion_policy_versions.id",
            "fk_evaluation_promotion_runs_policy_version",
        ),
        _foreign_key(
            "dispatch_id",
            "evaluation_annotation_dispatches.id",
            "fk_evaluation_promotion_runs_dispatch",
        ),
        _foreign_key(
            "created_by_user_id",
            "users.id",
            "fk_evaluation_promotion_runs_creator",
        ),
        sa.UniqueConstraint(
            "public_id", name="uq_evaluation_promotion_runs_public_id"
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_evaluation_promotion_runs_idempotency",
        ),
        sa.UniqueConstraint(
            "policy_version_id",
            "dispatch_id",
            "evidence_digest",
            name="uq_eval_promotion_runs_version_dispatch_evidence",
        ),
        sa.CheckConstraint(
            "item_count >= 1 AND item_count <= 20 "
            "AND completed_count = item_count "
            "AND scored_count >= 0 AND scored_count <= item_count "
            "AND passed_count >= 0 AND passed_count <= scored_count "
            "AND distinct_bucket_count >= 0 "
            "AND distinct_bucket_count <= item_count",
            name="ck_evaluation_promotion_runs_counts",
        ),
    )
    for name, columns in (
        (
            "ix_eval_promotion_runs_namespace_created",
            ["namespace_id", "created_at"],
        ),
        (
            "ix_eval_promotion_runs_dispatch_created",
            ["dispatch_id", "created_at"],
        ),
        ("ix_evaluation_promotion_runs_namespace_id", ["namespace_id"]),
        (
            "ix_evaluation_promotion_runs_policy_version_id",
            ["policy_version_id"],
        ),
        ("ix_evaluation_promotion_runs_dispatch_id", ["dispatch_id"]),
        ("ix_evaluation_promotion_runs_outcome", ["outcome"]),
        (
            "ix_evaluation_promotion_runs_created_by_user_id",
            ["created_by_user_id"],
        ),
    ):
        op.create_index(name, "evaluation_promotion_runs", columns)

    op.create_table(
        "evaluation_promotion_run_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("source_trace_ref", sa.String(length=64), nullable=False),
        sa.Column("source_observation_ref", sa.String(length=64), nullable=False),
        sa.Column("score_present", sa.Boolean(), nullable=False),
        sa.Column("quality_passed", sa.Boolean(), nullable=False),
        sa.Column("diversity_bucket_present", sa.Boolean(), nullable=False),
        sa.Column("score_evidence_digest", sa.String(length=64)),
        sa.Column("diversity_bucket_digest", sa.String(length=64)),
        sa.Column("reason_code", sa.String(length=100), nullable=False),
        _foreign_key(
            "run_id",
            "evaluation_promotion_runs.id",
            "fk_evaluation_promotion_run_items_run",
        ),
        sa.UniqueConstraint(
            "run_id",
            "position",
            name="uq_evaluation_promotion_run_items_position",
        ),
        sa.UniqueConstraint(
            "run_id",
            "source_observation_ref",
            name="uq_evaluation_promotion_run_items_source",
        ),
        sa.CheckConstraint(
            "position >= 1 AND position <= 20",
            name="ck_evaluation_promotion_run_items_position",
        ),
    )
    op.create_index(
        "ix_evaluation_promotion_run_items_run_id",
        "evaluation_promotion_run_items",
        ["run_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    populated = sum(
        bind.execute(sa.text(f"SELECT COUNT(*) FROM {table_name}")).scalar_one()
        for table_name in (
            "evaluation_promotion_policies",
            "evaluation_promotion_policy_versions",
            "evaluation_promotion_runs",
            "evaluation_promotion_run_items",
        )
    )
    if populated:
        raise RuntimeError(
            "0050 downgrade refused: promotion provenance requires an "
            "explicit archival/remediation plan"
        )
    for table_name in (
        "evaluation_promotion_run_items",
        "evaluation_promotion_runs",
        "evaluation_promotion_policy_versions",
        "evaluation_promotion_policies",
    ):
        op.drop_table(table_name)
