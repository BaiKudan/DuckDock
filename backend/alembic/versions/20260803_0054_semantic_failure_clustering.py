"""Add provider-neutral, content-free semantic failure clustering.

Revision ID: 20260803_0054
Revises: 20260803_0053
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260803_0054"
down_revision: str | None = "20260803_0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _foreign_key(local: str, remote: str, name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [local], [remote], name=name, ondelete="RESTRICT"
    )


def upgrade() -> None:
    op.create_table(
        "evaluation_semantic_clustering_policies",
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
                name="evaluation_semantic_clustering_policy_status",
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "namespace_id", "namespaces.id", "fk_eval_semantic_policies_namespace"
        ),
        sa.UniqueConstraint(
            "public_id", name="uq_eval_semantic_clustering_policies_public_id"
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "name",
            name="uq_eval_semantic_clustering_policies_ns_name",
        ),
    )
    for name, columns in (
        ("ix_evaluation_semantic_clustering_policies_namespace_id", ["namespace_id"]),
        ("ix_evaluation_semantic_clustering_policies_status", ["status"]),
        (
            "ix_eval_semantic_clustering_policies_ns_status_created",
            ["namespace_id", "status", "created_at"],
        ),
    ):
        op.create_index(name, "evaluation_semantic_clustering_policies", columns)

    op.create_table(
        "evaluation_semantic_clustering_policy_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("policy_id", sa.Integer(), nullable=False),
        sa.Column(
            "source_case_routing_policy_version_id", sa.Integer(), nullable=False
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("config_digest", sa.String(length=64), nullable=False),
        sa.Column("embedding_profile", sa.String(length=100), nullable=False),
        sa.Column("model_ref", sa.String(length=255), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("similarity_threshold", sa.Float(), nullable=False),
        sa.Column("min_cluster_size", sa.Integer(), nullable=False),
        sa.Column("max_items", sa.Integer(), nullable=False),
        sa.Column("max_content_chars", sa.Integer(), nullable=False),
        sa.Column("schema_name", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "policy_id",
            "evaluation_semantic_clustering_policies.id",
            "fk_eval_semantic_versions_policy",
        ),
        _foreign_key(
            "source_case_routing_policy_version_id",
            "evaluation_case_routing_policy_versions.id",
            "fk_eval_semantic_versions_source_routing",
        ),
        _foreign_key(
            "created_by_user_id", "users.id", "fk_eval_semantic_versions_creator"
        ),
        sa.UniqueConstraint(
            "public_id", name="uq_eval_semantic_clustering_versions_public_id"
        ),
        sa.UniqueConstraint(
            "policy_id",
            "version",
            name="uq_eval_semantic_clustering_versions_policy_version",
        ),
        sa.UniqueConstraint(
            "policy_id",
            "config_digest",
            name="uq_eval_semantic_clustering_versions_policy_digest",
        ),
        sa.CheckConstraint(
            "version >= 1 AND dimensions >= 1 AND dimensions <= 4096 "
            "AND similarity_threshold >= 0 AND similarity_threshold <= 1 "
            "AND min_cluster_size >= 2 AND min_cluster_size <= 20 "
            "AND max_items >= 2 AND max_items <= 20 "
            "AND max_content_chars >= 100 AND max_content_chars <= 16000",
            name="ck_eval_semantic_clustering_versions_limits",
        ),
    )
    op.create_index(
        "ix_eval_semantic_clustering_versions_policy_created",
        "evaluation_semantic_clustering_policy_versions",
        ["policy_id", "created_at"],
    )
    op.create_index(
        "ix_eval_semantic_clustering_versions_source_routing",
        "evaluation_semantic_clustering_policy_versions",
        ["source_case_routing_policy_version_id"],
    )

    op.create_table(
        "evaluation_semantic_clustering_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("policy_version_id", sa.Integer(), nullable=False),
        sa.Column("source_case_routing_run_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("evidence_digest", sa.String(length=64), nullable=False),
        sa.Column("clustering_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "outcome",
            sa.Enum(
                "CLUSTERED",
                "BLOCKED",
                name="evaluation_semantic_clustering_outcome",
            ),
            nullable=False,
        ),
        sa.Column("reason_codes_json", sa.JSON(), nullable=False),
        sa.Column("source_item_count", sa.Integer(), nullable=False),
        sa.Column("cluster_count", sa.Integer(), nullable=False),
        sa.Column("eligible_cluster_count", sa.Integer(), nullable=False),
        sa.Column("schema_name", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key("namespace_id", "namespaces.id", "fk_eval_semantic_runs_namespace"),
        _foreign_key(
            "policy_version_id",
            "evaluation_semantic_clustering_policy_versions.id",
            "fk_eval_semantic_runs_policy_version",
        ),
        _foreign_key(
            "source_case_routing_run_id",
            "evaluation_case_routing_runs.id",
            "fk_eval_semantic_runs_source_routing",
        ),
        _foreign_key(
            "created_by_user_id", "users.id", "fk_eval_semantic_runs_creator"
        ),
        sa.UniqueConstraint(
            "public_id", name="uq_eval_semantic_clustering_runs_public_id"
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_eval_semantic_clustering_runs_idempotency",
        ),
        sa.UniqueConstraint(
            "policy_version_id",
            "source_case_routing_run_id",
            "evidence_digest",
            name="uq_eval_semantic_clustering_runs_evidence",
        ),
        sa.CheckConstraint(
            "source_item_count >= 1 AND source_item_count <= 20 "
            "AND cluster_count >= 1 AND cluster_count <= source_item_count "
            "AND eligible_cluster_count >= 0 "
            "AND eligible_cluster_count <= cluster_count",
            name="ck_eval_semantic_clustering_runs_counts",
        ),
    )
    for name, columns in (
        ("ix_evaluation_semantic_clustering_runs_namespace_id", ["namespace_id"]),
        ("ix_evaluation_semantic_clustering_runs_policy_version_id", ["policy_version_id"]),
        ("ix_evaluation_semantic_clustering_runs_outcome", ["outcome"]),
        (
            "ix_eval_semantic_clustering_runs_ns_created",
            ["namespace_id", "created_at"],
        ),
        (
            "ix_eval_semantic_clustering_runs_source_routing",
            ["source_case_routing_run_id"],
        ),
    ):
        op.create_index(name, "evaluation_semantic_clustering_runs", columns)

    op.create_table(
        "evaluation_semantic_clustering_run_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("source_case_routing_item_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("semantic_cluster_digest", sa.String(length=64), nullable=False),
        sa.Column("cluster_size", sa.Integer(), nullable=False),
        sa.Column("similarity_to_centroid", sa.Float(), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("embedding_digest", sa.String(length=64), nullable=False),
        _foreign_key(
            "run_id", "evaluation_semantic_clustering_runs.id", "fk_eval_semantic_items_run"
        ),
        _foreign_key(
            "source_case_routing_item_id",
            "evaluation_case_routing_run_items.id",
            "fk_eval_semantic_items_source",
        ),
        sa.UniqueConstraint(
            "run_id", "position", name="uq_eval_semantic_clustering_items_position"
        ),
        sa.UniqueConstraint(
            "run_id",
            "source_case_routing_item_id",
            name="uq_eval_semantic_clustering_items_source",
        ),
        sa.CheckConstraint(
            "position >= 1 AND position <= 20 "
            "AND cluster_size >= 1 AND cluster_size <= 20 "
            "AND similarity_to_centroid >= -1 "
            "AND similarity_to_centroid <= 1",
            name="ck_eval_semantic_clustering_items_limits",
        ),
    )
    for name, columns in (
        ("ix_eval_semantic_clustering_items_run", ["run_id"]),
        ("ix_eval_semantic_clustering_items_source", ["source_case_routing_item_id"]),
        ("ix_eval_semantic_clustering_items_cluster", ["semantic_cluster_digest"]),
    ):
        op.create_index(name, "evaluation_semantic_clustering_run_items", columns)

    op.add_column(
        "evaluation_failure_taxonomy_policy_versions",
        sa.Column("source_semantic_clustering_policy_version_id", sa.Integer()),
    )
    op.create_foreign_key(
        "fk_eval_failure_taxonomy_versions_source_semantic",
        "evaluation_failure_taxonomy_policy_versions",
        "evaluation_semantic_clustering_policy_versions",
        ["source_semantic_clustering_policy_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_eval_failure_taxonomy_versions_source_semantic",
        "evaluation_failure_taxonomy_policy_versions",
        ["source_semantic_clustering_policy_version_id"],
    )
    op.add_column(
        "evaluation_experience_extraction_runs",
        sa.Column("source_semantic_clustering_run_id", sa.Integer()),
    )
    op.create_foreign_key(
        "fk_eval_experience_runs_source_semantic",
        "evaluation_experience_extraction_runs",
        "evaluation_semantic_clustering_runs",
        ["source_semantic_clustering_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_eval_experience_runs_source_semantic",
        "evaluation_experience_extraction_runs",
        ["source_semantic_clustering_run_id"],
    )


def downgrade() -> None:
    raise RuntimeError(
        "0054 downgrade refused: semantic clustering receipts and governance "
        "bindings are immutable evidence"
    )
