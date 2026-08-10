"""Add reproducible metadata-only evaluation sampling policies.

Revision ID: 20260731_0048
Revises: 20260731_0047
Create Date: 2026-07-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260731_0048"
down_revision: str | None = "20260731_0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _foreign_key(
    local: str,
    remote: str,
    name: str,
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [local],
        [remote],
        name=name,
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    op.create_table(
        "evaluation_sampling_policies",
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
                name="evaluation_sampling_policy_status",
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "namespace_id",
            "namespaces.id",
            "fk_evaluation_sampling_policies_namespace",
        ),
        sa.UniqueConstraint(
            "public_id",
            name="uq_evaluation_sampling_policies_public_id",
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "name",
            name="uq_evaluation_sampling_policies_namespace_name",
        ),
    )
    op.create_index(
        "ix_evaluation_sampling_policies_namespace_status_created",
        "evaluation_sampling_policies",
        ["namespace_id", "status", "created_at"],
    )
    op.create_index(
        "ix_evaluation_sampling_policies_namespace_id",
        "evaluation_sampling_policies",
        ["namespace_id"],
    )
    op.create_index(
        "ix_evaluation_sampling_policies_status",
        "evaluation_sampling_policies",
        ["status"],
    )

    op.create_table(
        "evaluation_sampling_policy_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("policy_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("config_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "strategy",
            sa.Enum(
                "STABLE_HASH",
                name="evaluation_sampling_strategy",
            ),
            nullable=False,
        ),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("minimum_sample_size", sa.Integer(), nullable=False),
        sa.Column("candidate_limit", sa.Integer(), nullable=False),
        sa.Column("observation_name", sa.String(length=200)),
        sa.Column("observation_type", sa.String(length=32)),
        sa.Column("environment", sa.String(length=100)),
        sa.Column("root_only", sa.Boolean(), nullable=False),
        sa.Column("exclude_governed", sa.Boolean(), nullable=False),
        sa.Column("schema_name", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "policy_id",
            "evaluation_sampling_policies.id",
            "fk_evaluation_sampling_policy_versions_policy",
        ),
        _foreign_key(
            "created_by_user_id",
            "users.id",
            "fk_evaluation_sampling_policy_versions_creator",
        ),
        sa.UniqueConstraint(
            "public_id",
            name="uq_evaluation_sampling_policy_versions_public_id",
        ),
        sa.UniqueConstraint(
            "policy_id",
            "version",
            name="uq_evaluation_sampling_policy_versions_policy_version",
        ),
        sa.UniqueConstraint(
            "policy_id",
            "config_digest",
            name="uq_evaluation_sampling_policy_versions_policy_digest",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_evaluation_sampling_policy_versions_positive",
        ),
        sa.CheckConstraint(
            "sample_size >= 1 AND sample_size <= 20 "
            "AND minimum_sample_size >= 1 "
            "AND minimum_sample_size <= sample_size",
            name="ck_evaluation_sampling_policy_versions_sample_size",
        ),
        sa.CheckConstraint(
            "candidate_limit >= sample_size AND candidate_limit <= 100",
            name="ck_evaluation_sampling_policy_versions_candidate_limit",
        ),
        sa.CheckConstraint(
            "exclude_governed = 1",
            name="ck_evaluation_sampling_policy_versions_exclude_governed",
        ),
    )
    for name, columns in (
        (
            "ix_evaluation_sampling_policy_versions_policy_created",
            ["policy_id", "created_at"],
        ),
        (
            "ix_evaluation_sampling_policy_versions_policy_id",
            ["policy_id"],
        ),
        (
            "ix_evaluation_sampling_policy_versions_config_digest",
            ["config_digest"],
        ),
        (
            "ix_evaluation_sampling_policy_versions_created_by_user_id",
            ["created_by_user_id"],
        ),
    ):
        op.create_index(
            name,
            "evaluation_sampling_policy_versions",
            columns,
        )

    op.create_table(
        "evaluation_sampling_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("policy_version_id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("curation_batch_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column(
            "from_start_time", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "to_start_time", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("eligible_count", sa.Integer(), nullable=False),
        sa.Column("selected_count", sa.Integer(), nullable=False),
        sa.Column("selection_digest", sa.String(length=64), nullable=False),
        sa.Column("run_digest", sa.String(length=64), nullable=False),
        sa.Column("schema_name", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "namespace_id",
            "namespaces.id",
            "fk_evaluation_sampling_runs_namespace",
        ),
        _foreign_key(
            "policy_version_id",
            "evaluation_sampling_policy_versions.id",
            "fk_evaluation_sampling_runs_policy_version",
        ),
        _foreign_key(
            "dataset_id",
            "evaluation_datasets.id",
            "fk_evaluation_sampling_runs_dataset",
        ),
        _foreign_key(
            "curation_batch_id",
            "evaluation_dataset_curation_batches.id",
            "fk_evaluation_sampling_runs_curation_batch",
        ),
        _foreign_key(
            "created_by_user_id",
            "users.id",
            "fk_evaluation_sampling_runs_creator",
        ),
        sa.UniqueConstraint(
            "public_id",
            name="uq_evaluation_sampling_runs_public_id",
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_evaluation_sampling_runs_idempotency",
        ),
        sa.UniqueConstraint(
            "dataset_id",
            "run_digest",
            name="uq_evaluation_sampling_runs_dataset_digest",
        ),
        sa.UniqueConstraint(
            "curation_batch_id",
            name="uq_evaluation_sampling_runs_curation_batch",
        ),
        sa.CheckConstraint(
            "candidate_count >= 0 AND eligible_count >= 0 "
            "AND selected_count >= 1 "
            "AND eligible_count <= candidate_count "
            "AND selected_count <= eligible_count "
            "AND selected_count <= 20",
            name="ck_evaluation_sampling_runs_counts",
        ),
    )
    for name, columns in (
        (
            "ix_evaluation_sampling_runs_namespace_created",
            ["namespace_id", "created_at"],
        ),
        (
            "ix_evaluation_sampling_runs_policy_version_created",
            ["policy_version_id", "created_at"],
        ),
        (
            "ix_evaluation_sampling_runs_dataset_created",
            ["dataset_id", "created_at"],
        ),
        ("ix_evaluation_sampling_runs_namespace_id", ["namespace_id"]),
        (
            "ix_evaluation_sampling_runs_policy_version_id",
            ["policy_version_id"],
        ),
        ("ix_evaluation_sampling_runs_dataset_id", ["dataset_id"]),
        (
            "ix_evaluation_sampling_runs_curation_batch_id",
            ["curation_batch_id"],
        ),
        (
            "ix_evaluation_sampling_runs_created_by_user_id",
            ["created_by_user_id"],
        ),
    ):
        op.create_index(name, "evaluation_sampling_runs", columns)

    op.create_table(
        "evaluation_sampling_run_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("source_trace_ref", sa.String(length=64), nullable=False),
        sa.Column(
            "source_observation_ref",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("rank_digest", sa.String(length=64), nullable=False),
        _foreign_key(
            "run_id",
            "evaluation_sampling_runs.id",
            "fk_evaluation_sampling_run_items_run",
        ),
        sa.UniqueConstraint(
            "run_id",
            "position",
            name="uq_evaluation_sampling_run_items_position",
        ),
        sa.UniqueConstraint(
            "run_id",
            "source_trace_ref",
            "source_observation_ref",
            name="uq_evaluation_sampling_run_items_source",
        ),
    )
    op.create_index(
        "ix_evaluation_sampling_run_items_run_id",
        "evaluation_sampling_run_items",
        ["run_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    policy_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM evaluation_sampling_policies")
    ).scalar_one()
    if policy_count:
        raise RuntimeError(
            "0048 downgrade refused: sampling policy provenance requires "
            "an explicit archival/remediation plan"
        )
    for table_name in (
        "evaluation_sampling_run_items",
        "evaluation_sampling_runs",
        "evaluation_sampling_policy_versions",
        "evaluation_sampling_policies",
    ):
        op.drop_table(table_name)
