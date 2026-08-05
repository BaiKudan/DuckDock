"""Add provider-neutral Evaluation Hub governance records.

Revision ID: 20260731_0040
Revises: 20260730_0039
Create Date: 2026-07-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260731_0040"
down_revision: str | None = "20260730_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


provider_enum = sa.Enum(
    "LANGFUSE",
    "DEEPEVAL",
    "CUSTOM",
    name="evaluation_provider",
)


def upgrade() -> None:
    op.create_table(
        "evaluation_datasets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=500)),
        sa.Column("provider", provider_enum, nullable=False),
        sa.Column("provider_dataset_ref", sa.String(length=255)),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "ARCHIVED",
                name="evaluation_dataset_status",
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
            name="uq_evaluation_datasets_public_id",
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "name",
            name="uq_evaluation_datasets_namespace_name",
        ),
    )
    op.create_index(
        "ix_evaluation_datasets_namespace_id",
        "evaluation_datasets",
        ["namespace_id"],
    )
    op.create_index(
        "ix_evaluation_datasets_status",
        "evaluation_datasets",
        ["status"],
    )
    op.create_index(
        "ix_evaluation_datasets_namespace_status",
        "evaluation_datasets",
        ["namespace_id", "status"],
    )

    op.create_table(
        "evaluation_dataset_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("schema_name", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("provider_version_ref", sa.String(length=255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["evaluation_datasets.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "public_id",
            name="uq_evaluation_dataset_versions_public_id",
        ),
        sa.UniqueConstraint(
            "dataset_id",
            "version",
            name="uq_evaluation_dataset_versions_dataset_version",
        ),
        sa.UniqueConstraint(
            "dataset_id",
            "content_digest",
            name="uq_evaluation_dataset_versions_dataset_digest",
        ),
        sa.CheckConstraint(
            "version >= 1 AND item_count >= 0",
            name="ck_evaluation_dataset_versions_positive",
        ),
    )
    op.create_index(
        "ix_evaluation_dataset_versions_dataset_id",
        "evaluation_dataset_versions",
        ["dataset_id"],
    )
    op.create_index(
        "ix_evaluation_dataset_versions_dataset_created",
        "evaluation_dataset_versions",
        ["dataset_id", "created_at"],
    )

    op.create_table(
        "evaluators",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "LLM_JUDGE",
                "CODE",
                "RULE",
                "DEEPEVAL",
                name="evaluator_kind",
            ),
            nullable=False,
        ),
        sa.Column("provider", provider_enum, nullable=False),
        sa.Column("provider_evaluator_ref", sa.String(length=255)),
        sa.Column(
            "status",
            sa.Enum("DRAFT", "ACTIVE", "RETIRED", name="evaluator_status"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["namespaces.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("public_id", name="uq_evaluators_public_id"),
        sa.UniqueConstraint(
            "namespace_id",
            "name",
            name="uq_evaluators_namespace_name",
        ),
    )
    op.create_index(
        "ix_evaluators_namespace_id",
        "evaluators",
        ["namespace_id"],
    )
    op.create_index("ix_evaluators_status", "evaluators", ["status"])
    op.create_index(
        "ix_evaluators_namespace_status_kind",
        "evaluators",
        ["namespace_id", "status", "kind"],
    )

    op.create_table(
        "evaluator_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("evaluator_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("config_digest", sa.String(length=64), nullable=False),
        sa.Column("implementation_ref", sa.String(length=512), nullable=False),
        sa.Column("rubric_version", sa.String(length=64)),
        sa.Column("provider_version_ref", sa.String(length=255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["evaluator_id"],
            ["evaluators.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "public_id",
            name="uq_evaluator_versions_public_id",
        ),
        sa.UniqueConstraint(
            "evaluator_id",
            "version",
            name="uq_evaluator_versions_evaluator_version",
        ),
        sa.UniqueConstraint(
            "evaluator_id",
            "config_digest",
            name="uq_evaluator_versions_evaluator_digest",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_evaluator_versions_positive",
        ),
    )
    op.create_index(
        "ix_evaluator_versions_evaluator_id",
        "evaluator_versions",
        ["evaluator_id"],
    )

    op.create_table(
        "evaluation_experiments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("dataset_version_id", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_ref", sa.String(length=255), nullable=False),
        sa.Column("target_digest", sa.String(length=64), nullable=False),
        sa.Column("provider", provider_enum, nullable=False),
        sa.Column("provider_experiment_ref", sa.String(length=255)),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "RUNNING",
                "COMPLETED",
                "FAILED",
                "CANCELLED",
                name="experiment_status",
            ),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["namespaces.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"],
            ["evaluation_dataset_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "public_id",
            name="uq_evaluation_experiments_public_id",
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "name",
            name="uq_evaluation_experiments_namespace_name",
        ),
    )
    op.create_index(
        "ix_evaluation_experiments_namespace_id",
        "evaluation_experiments",
        ["namespace_id"],
    )
    op.create_index(
        "ix_evaluation_experiments_dataset_version_id",
        "evaluation_experiments",
        ["dataset_version_id"],
    )
    op.create_index(
        "ix_evaluation_experiments_status",
        "evaluation_experiments",
        ["status"],
    )
    op.create_index(
        "ix_evaluation_experiments_namespace_status_created",
        "evaluation_experiments",
        ["namespace_id", "status", "created_at"],
    )

    op.create_table(
        "evaluations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("experiment_id", sa.Integer(), nullable=False),
        sa.Column("evaluator_version_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "RUNNING",
                "COMPLETED",
                "FAILED",
                "CANCELLED",
                name="evaluation_status",
            ),
            nullable=False,
        ),
        sa.Column("provider_evaluation_ref", sa.String(length=255)),
        sa.Column("score", sa.Float()),
        sa.Column("total_count", sa.Integer(), nullable=False),
        sa.Column("passed_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=100)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["namespaces.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["evaluation_experiments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evaluator_version_id"],
            ["evaluator_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("public_id", name="uq_evaluations_public_id"),
        sa.UniqueConstraint(
            "experiment_id",
            "evaluator_version_id",
            name="uq_evaluations_experiment_evaluator",
        ),
        sa.CheckConstraint(
            "total_count >= 0 AND passed_count >= 0 AND failed_count >= 0 "
            "AND passed_count + failed_count <= total_count",
            name="ck_evaluations_counts",
        ),
        sa.CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 1)",
            name="ck_evaluations_score_range",
        ),
    )
    for column in (
        "namespace_id",
        "experiment_id",
        "evaluator_version_id",
        "status",
    ):
        op.create_index(
            f"ix_evaluations_{column}",
            "evaluations",
            [column],
        )
    op.create_index(
        "ix_evaluations_namespace_status_created",
        "evaluations",
        ["namespace_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("evaluations")
    op.drop_table("evaluation_experiments")
    op.drop_table("evaluator_versions")
    op.drop_table("evaluators")
    op.drop_table("evaluation_dataset_versions")
    op.drop_table("evaluation_datasets")
