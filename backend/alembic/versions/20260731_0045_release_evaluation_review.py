"""Add immutable human review for release evaluation evidence.

Revision ID: 20260731_0045
Revises: 20260731_0044
Create Date: 2026-07-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260731_0045"
down_revision: str | None = "20260731_0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "release_candidate_evaluation_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("binding_id", sa.Integer(), nullable=False),
        sa.Column(
            "decision",
            sa.Enum(
                "APPROVED",
                "REJECTED",
                name="release_candidate_review_decision",
            ),
            nullable=False,
        ),
        sa.Column("comment", sa.String(length=1000)),
        sa.Column(
            "idempotency_key",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column("schema_name", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("review_digest", sa.String(length=64), nullable=False),
        sa.Column("reviewed_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["namespaces.id"],
            name="fk_rc_eval_reviews_namespace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["binding_id"],
            ["release_candidate_evaluation_bindings.id"],
            name="fk_rc_eval_reviews_binding",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"],
            ["users.id"],
            name="fk_rc_eval_reviews_reviewer",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "public_id",
            name="uq_release_candidate_eval_reviews_public_id",
        ),
        sa.UniqueConstraint(
            "binding_id",
            name="uq_release_candidate_eval_reviews_binding",
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "idempotency_key",
            name="uq_release_candidate_eval_reviews_idempotency",
        ),
        sa.CheckConstraint(
            "decision = 'APPROVED' OR "
            "(comment IS NOT NULL AND LENGTH(comment) >= 5)",
            name="ck_release_candidate_eval_reviews_rejection_comment",
        ),
    )
    for index_name, columns in (
        ("ix_rc_eval_reviews_namespace_created", ["namespace_id", "created_at"]),
        ("ix_rc_eval_reviews_decision_created", ["decision", "created_at"]),
        ("ix_rc_eval_reviews_binding", ["binding_id"]),
        ("ix_rc_eval_reviews_digest", ["review_digest"]),
        ("ix_rc_eval_reviews_reviewer", ["reviewed_by_user_id"]),
    ):
        op.create_index(
            index_name,
            "release_candidate_evaluation_reviews",
            columns,
        )


def downgrade() -> None:
    bind = op.get_bind()
    review_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) "
            "FROM release_candidate_evaluation_reviews"
        )
    ).scalar_one()
    if review_count:
        raise RuntimeError(
            "0045 downgrade refused: release evaluation reviews require "
            "an explicit archival/remediation plan"
        )
    op.drop_table("release_candidate_evaluation_reviews")
