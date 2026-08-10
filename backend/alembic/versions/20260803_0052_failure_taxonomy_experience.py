"""Add failure taxonomy and Experience candidate extraction.

Revision ID: 20260803_0052
Revises: 20260803_0051
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260803_0052"
down_revision: str | None = "20260803_0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _foreign_key(local: str, remote: str, name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [local], [remote], name=name, ondelete="RESTRICT"
    )


def upgrade() -> None:
    op.create_table(
        "evaluation_failure_taxonomy_policies",
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
                name="evaluation_failure_taxonomy_policy_status",
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "namespace_id",
            "namespaces.id",
            "fk_eval_failure_taxonomy_policies_namespace",
        ),
        sa.UniqueConstraint(
            "public_id", name="uq_eval_failure_taxonomy_policies_public_id"
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "name",
            name="uq_eval_failure_taxonomy_policies_ns_name",
        ),
    )
    for name, columns in (
        (
            "ix_eval_failure_taxonomy_policies_ns_status_created",
            ["namespace_id", "status", "created_at"],
        ),
        (
            "ix_evaluation_failure_taxonomy_policies_namespace_id",
            ["namespace_id"],
        ),
        (
            "ix_evaluation_failure_taxonomy_policies_status",
            ["status"],
        ),
    ):
        op.create_index(name, "evaluation_failure_taxonomy_policies", columns)

    op.create_table(
        "evaluation_failure_taxonomy_policy_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("policy_id", sa.Integer(), nullable=False),
        sa.Column(
            "source_case_routing_policy_version_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("config_digest", sa.String(length=64), nullable=False),
        sa.Column("min_cluster_occurrences", sa.Integer(), nullable=False),
        sa.Column("min_source_runs", sa.Integer(), nullable=False),
        sa.Column("include_isolated", sa.Boolean(), nullable=False),
        sa.Column("max_candidates", sa.Integer(), nullable=False),
        sa.Column("schema_name", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "policy_id",
            "evaluation_failure_taxonomy_policies.id",
            "fk_eval_failure_taxonomy_versions_policy",
        ),
        _foreign_key(
            "source_case_routing_policy_version_id",
            "evaluation_case_routing_policy_versions.id",
            "fk_eval_failure_taxonomy_versions_source_routing",
        ),
        _foreign_key(
            "created_by_user_id",
            "users.id",
            "fk_eval_failure_taxonomy_versions_creator",
        ),
        sa.UniqueConstraint(
            "public_id", name="uq_eval_failure_taxonomy_versions_public_id"
        ),
        sa.UniqueConstraint(
            "policy_id",
            "version",
            name="uq_eval_failure_taxonomy_versions_policy_version",
        ),
        sa.UniqueConstraint(
            "policy_id",
            "config_digest",
            name="uq_eval_failure_taxonomy_versions_policy_digest",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_eval_failure_taxonomy_versions_positive",
        ),
        sa.CheckConstraint(
            "min_cluster_occurrences >= 2 "
            "AND min_cluster_occurrences <= 20 "
            "AND min_source_runs >= 2 AND min_source_runs <= 20 "
            "AND max_candidates >= 1 AND max_candidates <= 20",
            name="ck_eval_failure_taxonomy_versions_limits",
        ),
    )
    for name, columns in (
        (
            "ix_eval_failure_taxonomy_versions_policy_created",
            ["policy_id", "created_at"],
        ),
        (
            "ix_eval_failure_taxonomy_versions_source_routing",
            ["source_case_routing_policy_version_id"],
        ),
    ):
        op.create_index(
            name, "evaluation_failure_taxonomy_policy_versions", columns
        )

    op.create_table(
        "evaluation_experience_extraction_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("policy_version_id", sa.Integer(), nullable=False),
        sa.Column("source_case_routing_run_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("evidence_digest", sa.String(length=64), nullable=False),
        sa.Column("extraction_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "outcome",
            sa.Enum(
                "EXTRACTED",
                "BLOCKED",
                name="evaluation_experience_extraction_outcome",
            ),
            nullable=False,
        ),
        sa.Column("reason_codes_json", sa.JSON(), nullable=False),
        sa.Column("source_bad_case_count", sa.Integer(), nullable=False),
        sa.Column("cluster_count", sa.Integer(), nullable=False),
        sa.Column("eligible_cluster_count", sa.Integer(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("schema_name", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "namespace_id",
            "namespaces.id",
            "fk_eval_experience_runs_namespace",
        ),
        _foreign_key(
            "policy_version_id",
            "evaluation_failure_taxonomy_policy_versions.id",
            "fk_eval_experience_runs_policy_version",
        ),
        _foreign_key(
            "source_case_routing_run_id",
            "evaluation_case_routing_runs.id",
            "fk_eval_experience_runs_source_routing",
        ),
        _foreign_key(
            "created_by_user_id",
            "users.id",
            "fk_eval_experience_runs_creator",
        ),
        sa.UniqueConstraint(
            "public_id", name="uq_eval_experience_runs_public_id"
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_eval_experience_runs_idempotency",
        ),
        sa.UniqueConstraint(
            "policy_version_id",
            "source_case_routing_run_id",
            "evidence_digest",
            name="uq_eval_experience_runs_evidence",
        ),
        sa.CheckConstraint(
            "source_bad_case_count >= 1 AND source_bad_case_count <= 20 "
            "AND cluster_count >= 1 AND cluster_count <= source_bad_case_count "
            "AND eligible_cluster_count >= 0 "
            "AND eligible_cluster_count <= cluster_count "
            "AND candidate_count >= 0 AND candidate_count <= 20 "
            "AND candidate_count <= eligible_cluster_count",
            name="ck_eval_experience_runs_counts",
        ),
    )
    for name, columns in (
        (
            "ix_eval_experience_runs_ns_created",
            ["namespace_id", "created_at"],
        ),
        (
            "ix_eval_experience_runs_source_routing",
            ["source_case_routing_run_id"],
        ),
        (
            "ix_evaluation_experience_extraction_runs_namespace_id",
            ["namespace_id"],
        ),
        (
            "ix_evaluation_experience_extraction_runs_policy_version_id",
            ["policy_version_id"],
        ),
        (
            "ix_evaluation_experience_extraction_runs_outcome",
            ["outcome"],
        ),
    ):
        op.create_index(name, "evaluation_experience_extraction_runs", columns)

    op.create_table(
        "evaluation_experience_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "category",
            sa.Enum(
                "CROSS_RUN_RECURRING",
                "SINGLE_RUN_RECURRING",
                "ISOLATED",
                name="evaluation_failure_category",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING_REVIEW",
                "APPROVED",
                "REJECTED",
                name="evaluation_experience_candidate_status",
            ),
            nullable=False,
        ),
        sa.Column("cluster_digest", sa.String(length=64), nullable=False),
        sa.Column("rank_digest", sa.String(length=64), nullable=False),
        sa.Column("evidence_digest", sa.String(length=64), nullable=False),
        sa.Column("source_item_count", sa.Integer(), nullable=False),
        sa.Column("source_run_count", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "run_id",
            "evaluation_experience_extraction_runs.id",
            "fk_eval_experience_candidates_run",
        ),
        sa.UniqueConstraint(
            "public_id", name="uq_eval_experience_candidates_public_id"
        ),
        sa.UniqueConstraint(
            "run_id",
            "position",
            name="uq_eval_experience_candidates_position",
        ),
        sa.UniqueConstraint(
            "run_id",
            "cluster_digest",
            name="uq_eval_experience_candidates_cluster",
        ),
        sa.CheckConstraint(
            "position >= 1 AND position <= 20 "
            "AND source_item_count >= 1 AND source_item_count <= 20 "
            "AND source_run_count >= 1 "
            "AND source_run_count <= source_item_count",
            name="ck_eval_experience_candidates_counts",
        ),
    )
    for name, columns in (
        ("ix_eval_experience_candidates_run_id", ["run_id"]),
        (
            "ix_eval_experience_candidates_status_created",
            ["status", "created_at"],
        ),
        ("ix_evaluation_experience_candidates_category", ["category"]),
        ("ix_evaluation_experience_candidates_status", ["status"]),
    ):
        op.create_index(name, "evaluation_experience_candidates", columns)

    op.create_table(
        "evaluation_experience_candidate_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("source_case_routing_item_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        _foreign_key(
            "candidate_id",
            "evaluation_experience_candidates.id",
            "fk_eval_experience_evidence_candidate",
        ),
        _foreign_key(
            "source_case_routing_item_id",
            "evaluation_case_routing_run_items.id",
            "fk_eval_experience_evidence_source_routing",
        ),
        sa.UniqueConstraint(
            "candidate_id",
            "position",
            name="uq_eval_experience_evidence_position",
        ),
        sa.UniqueConstraint(
            "candidate_id",
            "source_case_routing_item_id",
            name="uq_eval_experience_evidence_source",
        ),
        sa.CheckConstraint(
            "position >= 1 AND position <= 20",
            name="ck_eval_experience_evidence_position",
        ),
    )
    for name, columns in (
        ("ix_eval_experience_evidence_candidate", ["candidate_id"]),
        (
            "ix_eval_experience_evidence_source_routing",
            ["source_case_routing_item_id"],
        ),
    ):
        op.create_index(
            name, "evaluation_experience_candidate_evidence", columns
        )

    op.create_table(
        "evaluation_experience_candidate_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column(
            "decision",
            sa.Enum(
                "APPROVED",
                "REJECTED",
                name="evaluation_experience_review_decision",
            ),
            nullable=False,
        ),
        sa.Column("comment", sa.String(length=1000)),
        sa.Column("review_digest", sa.String(length=64), nullable=False),
        sa.Column("reviewed_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "candidate_id",
            "evaluation_experience_candidates.id",
            "fk_eval_experience_reviews_candidate",
        ),
        _foreign_key(
            "reviewed_by_user_id",
            "users.id",
            "fk_eval_experience_reviews_reviewer",
        ),
        sa.UniqueConstraint(
            "public_id", name="uq_eval_experience_reviews_public_id"
        ),
        sa.UniqueConstraint(
            "candidate_id", name="uq_eval_experience_reviews_candidate"
        ),
    )
    for name, columns in (
        (
            "ix_eval_experience_reviews_reviewer_created",
            ["reviewed_by_user_id", "created_at"],
        ),
        (
            "ix_evaluation_experience_candidate_reviews_decision",
            ["decision"],
        ),
    ):
        op.create_index(
            name, "evaluation_experience_candidate_reviews", columns
        )


def downgrade() -> None:
    bind = op.get_bind()
    populated = sum(
        bind.execute(sa.text(f"SELECT COUNT(*) FROM {table_name}")).scalar_one()
        for table_name in (
            "evaluation_failure_taxonomy_policies",
            "evaluation_failure_taxonomy_policy_versions",
            "evaluation_experience_extraction_runs",
            "evaluation_experience_candidates",
            "evaluation_experience_candidate_evidence",
            "evaluation_experience_candidate_reviews",
        )
    )
    if populated:
        raise RuntimeError(
            "0052 downgrade refused: failure-taxonomy and Experience "
            "provenance requires an explicit archival/remediation plan"
        )
    for table_name in (
        "evaluation_experience_candidate_reviews",
        "evaluation_experience_candidate_evidence",
        "evaluation_experience_candidates",
        "evaluation_experience_extraction_runs",
        "evaluation_failure_taxonomy_policy_versions",
        "evaluation_failure_taxonomy_policies",
    ):
        op.drop_table(table_name)
