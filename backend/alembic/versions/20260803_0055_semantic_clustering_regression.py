"""Add semantic clustering quality and drift regression evidence.

Revision ID: 20260803_0055
Revises: 20260803_0054
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260803_0055"
down_revision: str | None = "20260803_0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _foreign_key(local: str, remote: str, name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([local], [remote], name=name, ondelete="RESTRICT")


def upgrade() -> None:
    op.create_table(
        "evaluation_semantic_regression_policies",
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
                name="evaluation_semantic_regression_policy_status",
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key("namespace_id", "namespaces.id", "fk_eval_semantic_reg_policies_ns"),
        sa.UniqueConstraint("public_id", name="uq_eval_semantic_regression_policies_public_id"),
        sa.UniqueConstraint(
            "namespace_id",
            "name",
            name="uq_eval_semantic_regression_policies_ns_name",
        ),
    )
    for name, columns in (
        ("ix_evaluation_semantic_regression_policies_namespace_id", ["namespace_id"]),
        ("ix_evaluation_semantic_regression_policies_status", ["status"]),
        (
            "ix_eval_semantic_regression_policies_ns_status_created",
            ["namespace_id", "status", "created_at"],
        ),
    ):
        op.create_index(name, "evaluation_semantic_regression_policies", columns)

    op.create_table(
        "evaluation_semantic_regression_policy_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("policy_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("config_digest", sa.String(length=64), nullable=False),
        sa.Column("minimum_pairwise_assignment_agreement", sa.Float(), nullable=False),
        sa.Column("maximum_cluster_count_change_ratio", sa.Float(), nullable=False),
        sa.Column("maximum_mean_centroid_similarity_drop", sa.Float(), nullable=False),
        sa.Column("maximum_eligible_cluster_ratio_drop", sa.Float(), nullable=False),
        sa.Column("require_exact_source_content", sa.Boolean(), nullable=False),
        sa.Column("schema_name", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "policy_id",
            "evaluation_semantic_regression_policies.id",
            "fk_eval_semantic_reg_versions_policy",
        ),
        _foreign_key("created_by_user_id", "users.id", "fk_eval_semantic_reg_versions_creator"),
        sa.UniqueConstraint("public_id", name="uq_eval_semantic_regression_versions_public_id"),
        sa.UniqueConstraint(
            "policy_id",
            "version",
            name="uq_eval_semantic_regression_versions_policy_version",
        ),
        sa.UniqueConstraint(
            "policy_id",
            "config_digest",
            name="uq_eval_semantic_regression_versions_policy_digest",
        ),
        sa.CheckConstraint(
            "version >= 1 "
            "AND minimum_pairwise_assignment_agreement >= 0 "
            "AND minimum_pairwise_assignment_agreement <= 1 "
            "AND maximum_cluster_count_change_ratio >= 0 "
            "AND maximum_cluster_count_change_ratio <= 1 "
            "AND maximum_mean_centroid_similarity_drop >= 0 "
            "AND maximum_mean_centroid_similarity_drop <= 1 "
            "AND maximum_eligible_cluster_ratio_drop >= 0 "
            "AND maximum_eligible_cluster_ratio_drop <= 1 "
            "AND require_exact_source_content = 1",
            name="ck_eval_semantic_regression_versions_limits",
        ),
    )
    op.create_index(
        "ix_evaluation_semantic_regression_policy_versions_policy_id",
        "evaluation_semantic_regression_policy_versions",
        ["policy_id"],
    )
    op.create_index(
        "ix_eval_semantic_regression_versions_policy_created",
        "evaluation_semantic_regression_policy_versions",
        ["policy_id", "created_at"],
    )

    op.create_table(
        "evaluation_semantic_regression_comparisons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("baseline_run_id", sa.Integer(), nullable=False),
        sa.Column("candidate_run_id", sa.Integer(), nullable=False),
        sa.Column("policy_version_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column(
            "outcome",
            sa.Enum(
                "PASS",
                "DRIFTED",
                "INCONCLUSIVE",
                name="evaluation_semantic_regression_outcome",
            ),
            nullable=False,
        ),
        sa.Column("reason_codes_json", sa.JSON(), nullable=False),
        sa.Column("source_item_count", sa.Integer(), nullable=False),
        sa.Column("pairwise_assignment_agreement", sa.Float()),
        sa.Column("baseline_cluster_count", sa.Integer(), nullable=False),
        sa.Column("candidate_cluster_count", sa.Integer(), nullable=False),
        sa.Column("cluster_count_change_ratio", sa.Float(), nullable=False),
        sa.Column("baseline_eligible_cluster_ratio", sa.Float(), nullable=False),
        sa.Column("candidate_eligible_cluster_ratio", sa.Float(), nullable=False),
        sa.Column("eligible_cluster_ratio_drop", sa.Float(), nullable=False),
        sa.Column("baseline_mean_centroid_similarity", sa.Float(), nullable=False),
        sa.Column("candidate_mean_centroid_similarity", sa.Float(), nullable=False),
        sa.Column("mean_centroid_similarity_drop", sa.Float(), nullable=False),
        sa.Column("assignment_agreement_breached", sa.Boolean(), nullable=False),
        sa.Column("cluster_count_change_breached", sa.Boolean(), nullable=False),
        sa.Column("eligible_cluster_ratio_drop_breached", sa.Boolean(), nullable=False),
        sa.Column("centroid_similarity_drop_breached", sa.Boolean(), nullable=False),
        sa.Column("reproducibility_digest", sa.String(length=64), nullable=False),
        sa.Column("schema_name", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key("namespace_id", "namespaces.id", "fk_eval_semantic_reg_comparisons_ns"),
        _foreign_key(
            "baseline_run_id",
            "evaluation_semantic_clustering_runs.id",
            "fk_eval_semantic_reg_comparisons_baseline",
        ),
        _foreign_key(
            "candidate_run_id",
            "evaluation_semantic_clustering_runs.id",
            "fk_eval_semantic_reg_comparisons_candidate",
        ),
        _foreign_key(
            "policy_version_id",
            "evaluation_semantic_regression_policy_versions.id",
            "fk_eval_semantic_reg_comparisons_policy_version",
        ),
        _foreign_key(
            "created_by_user_id",
            "users.id",
            "fk_eval_semantic_reg_comparisons_creator",
        ),
        sa.UniqueConstraint("public_id", name="uq_eval_semantic_regression_comparisons_public_id"),
        sa.UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_eval_semantic_regression_comparisons_idempotency",
        ),
        sa.UniqueConstraint(
            "baseline_run_id",
            "candidate_run_id",
            "policy_version_id",
            name="uq_eval_semantic_regression_comparisons_exact_pins",
        ),
        sa.CheckConstraint(
            "baseline_run_id <> candidate_run_id",
            name="ck_eval_semantic_regression_comparisons_distinct_runs",
        ),
        sa.CheckConstraint(
            "source_item_count >= 2 AND source_item_count <= 20 "
            "AND baseline_cluster_count >= 1 "
            "AND candidate_cluster_count >= 1",
            name="ck_eval_semantic_regression_comparisons_counts",
        ),
        sa.CheckConstraint(
            "(pairwise_assignment_agreement IS NULL OR "
            "(pairwise_assignment_agreement >= 0 "
            "AND pairwise_assignment_agreement <= 1)) "
            "AND baseline_eligible_cluster_ratio >= 0 "
            "AND baseline_eligible_cluster_ratio <= 1 "
            "AND candidate_eligible_cluster_ratio >= 0 "
            "AND candidate_eligible_cluster_ratio <= 1 "
            "AND cluster_count_change_ratio >= 0 "
            "AND cluster_count_change_ratio <= 1 "
            "AND eligible_cluster_ratio_drop >= 0 "
            "AND eligible_cluster_ratio_drop <= 1",
            name="ck_eval_semantic_regression_comparisons_metric_ranges",
        ),
        sa.CheckConstraint(
            "baseline_mean_centroid_similarity >= -1 "
            "AND baseline_mean_centroid_similarity <= 1 "
            "AND candidate_mean_centroid_similarity >= -1 "
            "AND candidate_mean_centroid_similarity <= 1 "
            "AND mean_centroid_similarity_drop >= 0 "
            "AND mean_centroid_similarity_drop <= 2",
            name="ck_eval_semantic_regression_comparisons_similarity_ranges",
        ),
    )
    for name, columns in (
        ("ix_evaluation_semantic_regression_comparisons_namespace_id", ["namespace_id"]),
        ("ix_evaluation_semantic_regression_comparisons_baseline_run_id", ["baseline_run_id"]),
        ("ix_evaluation_semantic_regression_comparisons_candidate_run_id", ["candidate_run_id"]),
        ("ix_evaluation_semantic_regression_comparisons_policy_version_id", ["policy_version_id"]),
        ("ix_evaluation_semantic_regression_comparisons_outcome", ["outcome"]),
        (
            "ix_eval_semantic_regression_comparisons_ns_outcome_created",
            ["namespace_id", "outcome", "created_at"],
        ),
        (
            "ix_eval_semantic_regression_comparisons_candidate",
            ["candidate_run_id", "created_at"],
        ),
    ):
        op.create_index(name, "evaluation_semantic_regression_comparisons", columns)


def downgrade() -> None:
    raise RuntimeError(
        "0055 downgrade refused: semantic regression policies and comparison receipts are immutable evidence"
    )
