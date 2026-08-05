"""Add cross-batch Golden/Bad Case routing policies.

Revision ID: 20260803_0051
Revises: 20260803_0050
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260803_0051"
down_revision: str | None = "20260803_0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _foreign_key(local: str, remote: str, name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [local], [remote], name=name, ondelete="RESTRICT"
    )


def upgrade() -> None:
    op.create_table(
        "evaluation_case_routing_policies",
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
                name="evaluation_case_routing_policy_status",
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "namespace_id",
            "namespaces.id",
            "fk_eval_case_routing_policies_namespace",
        ),
        sa.UniqueConstraint(
            "public_id", name="uq_eval_case_routing_policies_public_id"
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "name",
            name="uq_eval_case_routing_policies_ns_name",
        ),
    )
    for name, columns in (
        (
            "ix_eval_case_routing_policies_ns_status_created",
            ["namespace_id", "status", "created_at"],
        ),
        (
            "ix_evaluation_case_routing_policies_namespace_id",
            ["namespace_id"],
        ),
        ("ix_evaluation_case_routing_policies_status", ["status"]),
    ):
        op.create_index(name, "evaluation_case_routing_policies", columns)

    op.create_table(
        "evaluation_case_routing_policy_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("policy_id", sa.Integer(), nullable=False),
        sa.Column(
            "source_promotion_policy_version_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("config_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "strategy",
            sa.Enum(
                "CLUSTER_ROUND_ROBIN",
                name="evaluation_case_routing_strategy",
            ),
            nullable=False,
        ),
        sa.Column("golden_target_size", sa.Integer(), nullable=False),
        sa.Column("golden_min_items", sa.Integer(), nullable=False),
        sa.Column("bad_case_target_size", sa.Integer(), nullable=False),
        sa.Column("bad_case_min_items", sa.Integer(), nullable=False),
        sa.Column("schema_name", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "policy_id",
            "evaluation_case_routing_policies.id",
            "fk_eval_case_routing_versions_policy",
        ),
        _foreign_key(
            "source_promotion_policy_version_id",
            "evaluation_promotion_policy_versions.id",
            "fk_eval_case_routing_versions_source_promotion",
        ),
        _foreign_key(
            "created_by_user_id",
            "users.id",
            "fk_eval_case_routing_versions_creator",
        ),
        sa.UniqueConstraint(
            "public_id", name="uq_eval_case_routing_versions_public_id"
        ),
        sa.UniqueConstraint(
            "policy_id",
            "version",
            name="uq_eval_case_routing_versions_policy_version",
        ),
        sa.UniqueConstraint(
            "policy_id",
            "config_digest",
            name="uq_eval_case_routing_versions_policy_digest",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_eval_case_routing_versions_positive",
        ),
        sa.CheckConstraint(
            "golden_target_size >= 1 AND golden_target_size <= 20 "
            "AND golden_min_items >= 1 "
            "AND golden_min_items <= golden_target_size "
            "AND bad_case_target_size >= 1 "
            "AND bad_case_target_size <= 20 "
            "AND bad_case_min_items >= 1 "
            "AND bad_case_min_items <= bad_case_target_size",
            name="ck_eval_case_routing_versions_sizes",
        ),
    )
    for name, columns in (
        (
            "ix_eval_case_routing_versions_policy_created",
            ["policy_id", "created_at"],
        ),
        (
            "ix_eval_case_routing_versions_source_promotion",
            ["source_promotion_policy_version_id"],
        ),
        (
            "ix_evaluation_case_routing_policy_versions_policy_id",
            ["policy_id"],
        ),
        (
            "ix_evaluation_case_routing_policy_versions_created_by_user_id",
            ["created_by_user_id"],
        ),
    ):
        op.create_index(
            name, "evaluation_case_routing_policy_versions", columns
        )

    op.create_table(
        "evaluation_case_routing_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("policy_version_id", sa.Integer(), nullable=False),
        sa.Column("golden_dataset_id", sa.Integer(), nullable=False),
        sa.Column("bad_case_dataset_id", sa.Integer(), nullable=False),
        sa.Column("golden_curation_batch_id", sa.Integer()),
        sa.Column("bad_case_curation_batch_id", sa.Integer()),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("evidence_digest", sa.String(length=64), nullable=False),
        sa.Column("routing_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "outcome",
            sa.Enum(
                "ROUTED",
                "BLOCKED",
                name="evaluation_case_routing_outcome",
            ),
            nullable=False,
        ),
        sa.Column("reason_codes_json", sa.JSON(), nullable=False),
        sa.Column("source_run_count", sa.Integer(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("golden_candidate_count", sa.Integer(), nullable=False),
        sa.Column("bad_case_candidate_count", sa.Integer(), nullable=False),
        sa.Column("excluded_count", sa.Integer(), nullable=False),
        sa.Column("golden_selected_count", sa.Integer(), nullable=False),
        sa.Column("bad_case_selected_count", sa.Integer(), nullable=False),
        sa.Column("golden_cluster_count", sa.Integer(), nullable=False),
        sa.Column("bad_case_cluster_count", sa.Integer(), nullable=False),
        sa.Column("schema_name", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "namespace_id",
            "namespaces.id",
            "fk_eval_case_routing_runs_namespace",
        ),
        _foreign_key(
            "policy_version_id",
            "evaluation_case_routing_policy_versions.id",
            "fk_eval_case_routing_runs_policy_version",
        ),
        _foreign_key(
            "golden_dataset_id",
            "evaluation_datasets.id",
            "fk_eval_case_routing_runs_golden_dataset",
        ),
        _foreign_key(
            "bad_case_dataset_id",
            "evaluation_datasets.id",
            "fk_eval_case_routing_runs_bad_dataset",
        ),
        _foreign_key(
            "golden_curation_batch_id",
            "evaluation_dataset_curation_batches.id",
            "fk_eval_case_routing_runs_golden_batch",
        ),
        _foreign_key(
            "bad_case_curation_batch_id",
            "evaluation_dataset_curation_batches.id",
            "fk_eval_case_routing_runs_bad_batch",
        ),
        _foreign_key(
            "created_by_user_id",
            "users.id",
            "fk_eval_case_routing_runs_creator",
        ),
        sa.UniqueConstraint(
            "public_id", name="uq_eval_case_routing_runs_public_id"
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_eval_case_routing_runs_idempotency",
        ),
        sa.UniqueConstraint(
            "policy_version_id",
            "golden_dataset_id",
            "bad_case_dataset_id",
            "evidence_digest",
            name="uq_eval_case_routing_runs_evidence",
        ),
        sa.UniqueConstraint(
            "golden_curation_batch_id",
            name="uq_evaluation_case_routing_runs_golden_curation_batch_id",
        ),
        sa.UniqueConstraint(
            "bad_case_curation_batch_id",
            name="uq_evaluation_case_routing_runs_bad_case_curation_batch_id",
        ),
        sa.CheckConstraint(
            "source_run_count >= 1 AND source_run_count <= 20 "
            "AND candidate_count >= 1 AND candidate_count <= 400 "
            "AND golden_candidate_count >= 0 "
            "AND bad_case_candidate_count >= 0 "
            "AND excluded_count >= 0 "
            "AND golden_candidate_count + bad_case_candidate_count "
            "+ excluded_count = candidate_count "
            "AND golden_selected_count >= 0 "
            "AND golden_selected_count <= 20 "
            "AND golden_selected_count <= golden_candidate_count "
            "AND bad_case_selected_count >= 0 "
            "AND bad_case_selected_count <= 20 "
            "AND bad_case_selected_count <= bad_case_candidate_count "
            "AND golden_cluster_count >= 0 "
            "AND golden_cluster_count <= golden_candidate_count "
            "AND bad_case_cluster_count >= 0 "
            "AND bad_case_cluster_count <= bad_case_candidate_count",
            name="ck_eval_case_routing_runs_counts",
        ),
    )
    for name, columns in (
        (
            "ix_eval_case_routing_runs_ns_created",
            ["namespace_id", "created_at"],
        ),
        ("ix_evaluation_case_routing_runs_namespace_id", ["namespace_id"]),
        (
            "ix_evaluation_case_routing_runs_policy_version_id",
            ["policy_version_id"],
        ),
        (
            "ix_evaluation_case_routing_runs_golden_dataset_id",
            ["golden_dataset_id"],
        ),
        (
            "ix_evaluation_case_routing_runs_bad_case_dataset_id",
            ["bad_case_dataset_id"],
        ),
        ("ix_evaluation_case_routing_runs_outcome", ["outcome"]),
        (
            "ix_evaluation_case_routing_runs_created_by_user_id",
            ["created_by_user_id"],
        ),
    ):
        op.create_index(name, "evaluation_case_routing_runs", columns)

    op.create_table(
        "evaluation_case_routing_run_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("source_promotion_run_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("source_trace_ref", sa.String(length=64), nullable=False),
        sa.Column("source_observation_ref", sa.String(length=64), nullable=False),
        sa.Column(
            "lane",
            sa.Enum(
                "GOLDEN",
                "BAD_CASE",
                "EXCLUDED",
                name="evaluation_case_routing_lane",
            ),
            nullable=False,
        ),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("cluster_digest", sa.String(length=64)),
        sa.Column("rank_digest", sa.String(length=64)),
        sa.Column("reason_code", sa.String(length=100), nullable=False),
        _foreign_key(
            "run_id",
            "evaluation_case_routing_runs.id",
            "fk_eval_case_routing_items_run",
        ),
        _foreign_key(
            "source_promotion_run_id",
            "evaluation_promotion_runs.id",
            "fk_eval_case_routing_items_source_promotion",
        ),
        sa.UniqueConstraint(
            "run_id",
            "position",
            name="uq_eval_case_routing_items_position",
        ),
        sa.UniqueConstraint(
            "run_id",
            "source_observation_ref",
            name="uq_eval_case_routing_items_source",
        ),
        sa.CheckConstraint(
            "position >= 1 AND position <= 400",
            name="ck_eval_case_routing_items_position",
        ),
    )
    for name, columns in (
        ("ix_eval_case_routing_items_run_id", ["run_id"]),
        (
            "ix_eval_case_routing_items_source_promotion_run",
            ["source_promotion_run_id"],
        ),
        ("ix_evaluation_case_routing_run_items_lane", ["lane"]),
    ):
        op.create_index(name, "evaluation_case_routing_run_items", columns)


def downgrade() -> None:
    bind = op.get_bind()
    populated = sum(
        bind.execute(sa.text(f"SELECT COUNT(*) FROM {table_name}")).scalar_one()
        for table_name in (
            "evaluation_case_routing_policies",
            "evaluation_case_routing_policy_versions",
            "evaluation_case_routing_runs",
            "evaluation_case_routing_run_items",
        )
    )
    if populated:
        raise RuntimeError(
            "0051 downgrade refused: case-routing provenance requires an "
            "explicit archival/remediation plan"
        )
    for table_name in (
        "evaluation_case_routing_run_items",
        "evaluation_case_routing_runs",
        "evaluation_case_routing_policy_versions",
        "evaluation_case_routing_policies",
    ):
        op.drop_table(table_name)
