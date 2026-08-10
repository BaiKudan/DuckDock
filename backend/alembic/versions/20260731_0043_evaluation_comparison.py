"""Add reproducible Evaluation baseline comparisons.

Revision ID: 20260731_0043
Revises: 20260731_0042
Create Date: 2026-07-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260731_0043"
down_revision: str | None = "20260731_0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "evaluation_result_manifests",
        sa.Column("score", sa.Float()),
    )
    op.execute(
        sa.text(
            """
            UPDATE evaluation_result_manifests AS manifest
            JOIN evaluations AS evaluation
              ON evaluation.id = manifest.evaluation_id
             AND evaluation.provider_evaluation_ref
                 = manifest.provider_experiment_ref
            SET manifest.score = evaluation.score
            """
        )
    )
    op.create_check_constraint(
        "ck_evaluation_result_manifests_score_range",
        "evaluation_result_manifests",
        "score IS NULL OR (score >= 0 AND score <= 1)",
    )

    op.create_table(
        "regression_policies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=500)),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "RETIRED",
                name="regression_policy_status",
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["namespaces.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "public_id",
            name="uq_regression_policies_public_id",
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "name",
            name="uq_regression_policies_namespace_name",
        ),
    )
    op.create_index(
        "ix_regression_policies_namespace_id",
        "regression_policies",
        ["namespace_id"],
    )
    op.create_index(
        "ix_regression_policies_status",
        "regression_policies",
        ["status"],
    )
    op.create_index(
        "ix_regression_policies_namespace_status_created",
        "regression_policies",
        ["namespace_id", "status", "created_at"],
    )

    op.create_table(
        "regression_policy_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("policy_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("minimum_candidate_score", sa.Float()),
        sa.Column("maximum_score_drop", sa.Float(), nullable=False),
        sa.Column("maximum_pass_rate_drop", sa.Float(), nullable=False),
        sa.Column(
            "require_complete_results",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["regression_policies.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "public_id",
            name="uq_regression_policy_versions_public_id",
        ),
        sa.UniqueConstraint(
            "policy_id",
            "version",
            name="uq_regression_policy_versions_policy_version",
        ),
        sa.UniqueConstraint(
            "policy_id",
            "content_digest",
            name="uq_regression_policy_versions_policy_digest",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_regression_policy_versions_positive",
        ),
        sa.CheckConstraint(
            "minimum_candidate_score IS NULL OR "
            "(minimum_candidate_score >= 0 "
            "AND minimum_candidate_score <= 1)",
            name="ck_regression_policy_versions_min_score",
        ),
        sa.CheckConstraint(
            "maximum_score_drop >= 0 AND maximum_score_drop <= 1 "
            "AND maximum_pass_rate_drop >= 0 "
            "AND maximum_pass_rate_drop <= 1",
            name="ck_regression_policy_versions_drop_ranges",
        ),
        sa.CheckConstraint(
            "require_complete_results = 1",
            name="ck_regression_policy_versions_complete_only",
        ),
    )
    op.create_index(
        "ix_regression_policy_versions_policy_id",
        "regression_policy_versions",
        ["policy_id"],
    )
    op.create_index(
        "ix_regression_policy_versions_content_digest",
        "regression_policy_versions",
        ["content_digest"],
    )
    op.create_index(
        "ix_regression_policy_versions_policy_created",
        "regression_policy_versions",
        ["policy_id", "created_at"],
    )

    op.create_table(
        "evaluation_comparisons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("baseline_evaluation_id", sa.Integer(), nullable=False),
        sa.Column("candidate_evaluation_id", sa.Integer(), nullable=False),
        sa.Column("baseline_manifest_id", sa.Integer(), nullable=False),
        sa.Column("candidate_manifest_id", sa.Integer(), nullable=False),
        sa.Column("policy_version_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column(
            "outcome",
            sa.Enum(
                "PASS",
                "REGRESSION",
                "INCONCLUSIVE",
                name="evaluation_comparison_outcome",
            ),
            nullable=False,
        ),
        sa.Column("reason_code", sa.String(length=100), nullable=False),
        sa.Column("baseline_score", sa.Float()),
        sa.Column("candidate_score", sa.Float()),
        sa.Column("score_delta", sa.Float()),
        sa.Column("baseline_pass_rate", sa.Float()),
        sa.Column("candidate_pass_rate", sa.Float()),
        sa.Column("pass_rate_delta", sa.Float()),
        sa.Column(
            "score_floor_breached",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column(
            "score_drop_breached",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column(
            "pass_rate_drop_breached",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column("schema_name", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column(
            "reproducibility_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["namespaces.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["baseline_evaluation_id"],
            ["evaluations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_evaluation_id"],
            ["evaluations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["baseline_manifest_id"],
            ["evaluation_result_manifests.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_manifest_id"],
            ["evaluation_result_manifests.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["policy_version_id"],
            ["regression_policy_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "public_id",
            name="uq_evaluation_comparisons_public_id",
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_evaluation_comparisons_namespace_idempotency",
        ),
        sa.UniqueConstraint(
            "baseline_manifest_id",
            "candidate_manifest_id",
            "policy_version_id",
            name="uq_evaluation_comparisons_exact_pins",
        ),
        sa.CheckConstraint(
            "baseline_evaluation_id <> candidate_evaluation_id",
            name="ck_evaluation_comparisons_distinct_evaluations",
        ),
        sa.CheckConstraint(
            "baseline_manifest_id <> candidate_manifest_id",
            name="ck_evaluation_comparisons_distinct_manifests",
        ),
        sa.CheckConstraint(
            "(baseline_score IS NULL OR "
            "(baseline_score >= 0 AND baseline_score <= 1)) "
            "AND (candidate_score IS NULL OR "
            "(candidate_score >= 0 AND candidate_score <= 1)) "
            "AND (baseline_pass_rate IS NULL OR "
            "(baseline_pass_rate >= 0 AND baseline_pass_rate <= 1)) "
            "AND (candidate_pass_rate IS NULL OR "
            "(candidate_pass_rate >= 0 AND candidate_pass_rate <= 1))",
            name="ck_evaluation_comparisons_metric_ranges",
        ),
        sa.CheckConstraint(
            "(score_delta IS NULL OR "
            "(score_delta >= -1 AND score_delta <= 1)) "
            "AND (pass_rate_delta IS NULL OR "
            "(pass_rate_delta >= -1 AND pass_rate_delta <= 1))",
            name="ck_evaluation_comparisons_delta_ranges",
        ),
    )
    for column_name in (
        "namespace_id",
        "baseline_evaluation_id",
        "candidate_evaluation_id",
        "baseline_manifest_id",
        "candidate_manifest_id",
        "policy_version_id",
        "outcome",
        "reproducibility_digest",
    ):
        op.create_index(
            f"ix_evaluation_comparisons_{column_name}",
            "evaluation_comparisons",
            [column_name],
        )
    op.create_index(
        "ix_evaluation_comparisons_namespace_outcome_created",
        "evaluation_comparisons",
        ["namespace_id", "outcome", "created_at"],
    )
    op.create_index(
        "ix_evaluation_comparisons_candidate_created",
        "evaluation_comparisons",
        ["candidate_evaluation_id", "created_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    comparison_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM evaluation_comparisons")
    ).scalar_one()
    policy_version_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM regression_policy_versions")
    ).scalar_one()
    if comparison_count or policy_version_count:
        raise RuntimeError(
            "0043 downgrade refused: comparisons or policy versions "
            "require an explicit archival/remediation plan"
        )

    op.drop_table("evaluation_comparisons")
    op.drop_table("regression_policy_versions")
    op.drop_table("regression_policies")
    op.drop_constraint(
        "ck_evaluation_result_manifests_score_range",
        "evaluation_result_manifests",
        type_="check",
    )
    op.drop_column("evaluation_result_manifests", "score")
