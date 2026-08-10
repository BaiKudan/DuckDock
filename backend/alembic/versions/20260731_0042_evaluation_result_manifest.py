"""Add provider-hosted Evaluation result manifests.

Revision ID: 20260731_0042
Revises: 20260731_0041
Create Date: 2026-07-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260731_0042"
down_revision: str | None = "20260731_0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OLD_EXPERIMENT_STATUS = sa.Enum(
    "DRAFT",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    name="experiment_status",
)
NEW_EXPERIMENT_STATUS = sa.Enum(
    "DRAFT",
    "RUNNING",
    "COMPLETED",
    "PARTIAL",
    "FAILED",
    "CANCELLED",
    name="experiment_status",
)
OLD_EVALUATION_STATUS = sa.Enum(
    "PENDING",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    name="evaluation_status",
)
NEW_EVALUATION_STATUS = sa.Enum(
    "PENDING",
    "RUNNING",
    "COMPLETED",
    "PARTIAL",
    "FAILED",
    "CANCELLED",
    name="evaluation_status",
)
RESULT_COMPLETENESS = sa.Enum(
    "COMPLETE",
    "PARTIAL",
    name="evaluation_result_completeness",
)


def upgrade() -> None:
    op.alter_column(
        "evaluation_experiments",
        "status",
        existing_type=OLD_EXPERIMENT_STATUS,
        type_=NEW_EXPERIMENT_STATUS,
        existing_nullable=False,
    )
    op.alter_column(
        "evaluations",
        "status",
        existing_type=OLD_EVALUATION_STATUS,
        type_=NEW_EVALUATION_STATUS,
        existing_nullable=False,
    )
    for column_name in (
        "processed_count",
        "scored_count",
        "error_count",
    ):
        op.add_column(
            "evaluations",
            sa.Column(
                column_name,
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )
    op.add_column(
        "evaluations",
        sa.Column("result_completeness", RESULT_COMPLETENESS),
    )
    op.create_index(
        "ix_evaluations_result_completeness",
        "evaluations",
        ["result_completeness"],
    )

    op.drop_constraint(
        "ck_evaluations_counts",
        "evaluations",
        type_="check",
    )
    op.execute(
        sa.text(
            """
            UPDATE evaluations
            SET processed_count = passed_count + failed_count,
                scored_count = passed_count + failed_count,
                error_count = total_count - passed_count - failed_count
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE evaluations
            SET processed_count = total_count,
                scored_count = passed_count + failed_count,
                error_count = total_count - passed_count - failed_count,
                result_completeness = CASE
                    WHEN passed_count + failed_count = total_count
                    THEN 'COMPLETE'
                    ELSE 'PARTIAL'
                END,
                status = CASE
                    WHEN passed_count + failed_count = total_count
                    THEN 'COMPLETED'
                    ELSE 'PARTIAL'
                END
            WHERE status = 'COMPLETED'
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE evaluation_experiments AS experiment
            SET experiment.status = 'PARTIAL'
            WHERE experiment.status = 'COMPLETED'
              AND EXISTS (
                  SELECT 1
                  FROM evaluations AS evaluation
                  WHERE evaluation.experiment_id = experiment.id
                    AND evaluation.status = 'PARTIAL'
              )
            """
        )
    )
    op.create_check_constraint(
        "ck_evaluations_counts",
        "evaluations",
        "total_count >= 0 AND processed_count >= 0 "
        "AND scored_count >= 0 AND passed_count >= 0 "
        "AND failed_count >= 0 AND error_count >= 0 "
        "AND processed_count <= total_count "
        "AND scored_count <= processed_count "
        "AND passed_count + failed_count = scored_count "
        "AND scored_count + error_count = total_count",
    )
    op.create_check_constraint(
        "ck_evaluations_result_state",
        "evaluations",
        "("
        "result_completeness IS NULL "
        "AND status NOT IN ('COMPLETED', 'PARTIAL')"
        ") OR ("
        "result_completeness = 'COMPLETE' "
        "AND status = 'COMPLETED' AND error_count = 0"
        ") OR ("
        "result_completeness = 'PARTIAL' "
        "AND status = 'PARTIAL' AND error_count > 0"
        ")",
    )

    op.create_table(
        "evaluation_result_manifests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("evaluation_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "provider",
            sa.Enum(
                "LANGFUSE",
                "DEEPEVAL",
                "CUSTOM",
                name="evaluation_provider",
            ),
            nullable=False,
        ),
        sa.Column(
            "provider_dataset_ref",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "provider_experiment_ref",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column("schema_name", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("expected_count", sa.Integer(), nullable=False),
        sa.Column("processed_count", sa.Integer(), nullable=False),
        sa.Column("scored_count", sa.Integer(), nullable=False),
        sa.Column("passed_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("completeness", RESULT_COMPLETENESS, nullable=False),
        sa.Column("loss_reason", sa.String(length=100)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["namespaces.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_id"],
            ["evaluations.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "public_id",
            name="uq_evaluation_result_manifests_public_id",
        ),
        sa.UniqueConstraint(
            "evaluation_id",
            "version",
            name="uq_evaluation_result_manifests_evaluation_version",
        ),
        sa.UniqueConstraint(
            "provider",
            "provider_experiment_ref",
            name="uq_evaluation_result_manifests_provider_experiment",
        ),
        sa.CheckConstraint(
            "version >= 1 AND expected_count >= 0 "
            "AND processed_count >= 0 AND scored_count >= 0 "
            "AND passed_count >= 0 AND failed_count >= 0 "
            "AND error_count >= 0 "
            "AND processed_count <= expected_count "
            "AND scored_count <= processed_count "
            "AND passed_count + failed_count = scored_count "
            "AND scored_count + error_count = expected_count",
            name="ck_evaluation_result_manifests_counts",
        ),
        sa.CheckConstraint(
            "("
            "completeness = 'COMPLETE' AND error_count = 0 "
            "AND processed_count = expected_count "
            "AND loss_reason IS NULL"
            ") OR ("
            "completeness = 'PARTIAL' AND error_count > 0 "
            "AND loss_reason IS NOT NULL"
            ")",
            name="ck_evaluation_result_manifests_completeness",
        ),
    )
    for column_name in (
        "namespace_id",
        "evaluation_id",
        "content_digest",
        "completeness",
    ):
        op.create_index(
            f"ix_evaluation_result_manifests_{column_name}",
            "evaluation_result_manifests",
            [column_name],
        )
    op.create_index(
        "ix_evaluation_result_manifests_namespace_created",
        "evaluation_result_manifests",
        ["namespace_id", "created_at"],
    )
    op.create_index(
        "ix_evaluation_result_manifests_evaluation_created",
        "evaluation_result_manifests",
        ["evaluation_id", "created_at"],
    )
    for column_name in (
        "processed_count",
        "scored_count",
        "error_count",
    ):
        op.alter_column(
            "evaluations",
            column_name,
            existing_type=sa.Integer(),
            existing_nullable=False,
            server_default=None,
        )


def downgrade() -> None:
    bind = op.get_bind()
    manifest_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM evaluation_result_manifests")
    ).scalar_one()
    partial_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM evaluations "
            "WHERE status = 'PARTIAL'"
        )
    ).scalar_one()
    if manifest_count or partial_count:
        raise RuntimeError(
            "0042 downgrade refused: result manifests or PARTIAL "
            "evaluations require an explicit archival/remediation plan"
        )

    op.drop_table("evaluation_result_manifests")
    op.drop_constraint(
        "ck_evaluations_result_state",
        "evaluations",
        type_="check",
    )
    op.drop_constraint(
        "ck_evaluations_counts",
        "evaluations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_evaluations_counts",
        "evaluations",
        "total_count >= 0 AND passed_count >= 0 AND failed_count >= 0 "
        "AND passed_count + failed_count <= total_count",
    )
    op.drop_index(
        "ix_evaluations_result_completeness",
        table_name="evaluations",
    )
    op.drop_column("evaluations", "result_completeness")
    op.drop_column("evaluations", "error_count")
    op.drop_column("evaluations", "scored_count")
    op.drop_column("evaluations", "processed_count")
    op.alter_column(
        "evaluations",
        "status",
        existing_type=NEW_EVALUATION_STATUS,
        type_=OLD_EVALUATION_STATUS,
        existing_nullable=False,
    )
    op.alter_column(
        "evaluation_experiments",
        "status",
        existing_type=NEW_EXPERIMENT_STATUS,
        type_=OLD_EXPERIMENT_STATUS,
        existing_nullable=False,
    )
