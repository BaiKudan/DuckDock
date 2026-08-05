"""Add versioned Experience assets and independent activation approval.

Revision ID: 20260803_0053
Revises: 20260803_0052
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260803_0053"
down_revision: str | None = "20260803_0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _foreign_key(local: str, remote: str, name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [local], [remote], name=name, ondelete="RESTRICT"
    )


def upgrade() -> None:
    op.create_table(
        "evaluation_experience_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("source_candidate_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=500)),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "namespace_id",
            "namespaces.id",
            "fk_eval_experience_assets_namespace",
        ),
        _foreign_key(
            "source_candidate_id",
            "evaluation_experience_candidates.id",
            "fk_eval_experience_assets_source_candidate",
        ),
        _foreign_key(
            "created_by_user_id",
            "users.id",
            "fk_eval_experience_assets_creator",
        ),
        sa.UniqueConstraint(
            "public_id", name="uq_eval_experience_assets_public_id"
        ),
        sa.UniqueConstraint(
            "source_candidate_id",
            name="uq_eval_experience_assets_source_candidate",
        ),
        sa.UniqueConstraint(
            "namespace_id",
            "name",
            name="uq_eval_experience_assets_namespace_name",
        ),
    )
    op.create_index(
        "ix_eval_experience_assets_namespace_created",
        "evaluation_experience_assets",
        ["namespace_id", "created_at"],
    )
    op.create_index(
        "ix_evaluation_experience_assets_namespace_id",
        "evaluation_experience_assets",
        ["namespace_id"],
    )

    op.create_table(
        "evaluation_experience_asset_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "PENDING_ACTIVATION",
                "ACTIVE",
                "REJECTED",
                "RETIRED",
                name="evaluation_experience_asset_version_status",
            ),
            nullable=False,
        ),
        sa.Column("body", sa.String(length=4000), nullable=False),
        sa.Column("applicability", sa.String(length=1000), nullable=False),
        sa.Column("change_summary", sa.String(length=1000)),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("source_evidence_digest", sa.String(length=64), nullable=False),
        sa.Column("schema_name", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "asset_id",
            "evaluation_experience_assets.id",
            "fk_eval_experience_asset_versions_asset",
        ),
        _foreign_key(
            "created_by_user_id",
            "users.id",
            "fk_eval_experience_asset_versions_creator",
        ),
        sa.UniqueConstraint(
            "public_id", name="uq_eval_experience_asset_versions_public_id"
        ),
        sa.UniqueConstraint(
            "asset_id",
            "version",
            name="uq_eval_experience_asset_versions_asset_version",
        ),
        sa.UniqueConstraint(
            "asset_id",
            "content_digest",
            name="uq_eval_experience_asset_versions_asset_digest",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_eval_experience_asset_versions_positive",
        ),
    )
    for name, columns in (
        (
            "ix_eval_experience_asset_versions_asset_created",
            ["asset_id", "created_at"],
        ),
        (
            "ix_eval_experience_asset_versions_status_created",
            ["status", "created_at"],
        ),
        (
            "ix_evaluation_experience_asset_versions_status",
            ["status"],
        ),
    ):
        op.create_index(name, "evaluation_experience_asset_versions", columns)

    op.create_table(
        "evaluation_experience_activation_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("request_note", sa.String(length=1000)),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "namespace_id",
            "namespaces.id",
            "fk_eval_experience_activation_requests_namespace",
        ),
        _foreign_key(
            "version_id",
            "evaluation_experience_asset_versions.id",
            "fk_eval_experience_activation_requests_version",
        ),
        _foreign_key(
            "requested_by_user_id",
            "users.id",
            "fk_eval_experience_activation_requests_requester",
        ),
        sa.UniqueConstraint(
            "public_id", name="uq_eval_experience_activation_requests_public_id"
        ),
        sa.UniqueConstraint(
            "version_id", name="uq_eval_experience_activation_requests_version"
        ),
    )
    op.create_index(
        "ix_eval_experience_activation_requests_namespace_created",
        "evaluation_experience_activation_requests",
        ["namespace_id", "created_at"],
    )
    op.create_index(
        "ix_evaluation_experience_activation_requests_namespace_id",
        "evaluation_experience_activation_requests",
        ["namespace_id"],
    )

    op.create_table(
        "evaluation_experience_activation_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("activation_request_id", sa.Integer(), nullable=False),
        sa.Column(
            "decision",
            sa.Enum(
                "APPROVED",
                "REJECTED",
                name="evaluation_experience_activation_decision",
            ),
            nullable=False,
        ),
        sa.Column("comment", sa.String(length=1000)),
        sa.Column("review_digest", sa.String(length=64), nullable=False),
        sa.Column("reviewed_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "activation_request_id",
            "evaluation_experience_activation_requests.id",
            "fk_eval_experience_activation_reviews_request",
        ),
        _foreign_key(
            "reviewed_by_user_id",
            "users.id",
            "fk_eval_experience_activation_reviews_reviewer",
        ),
        sa.UniqueConstraint(
            "public_id", name="uq_eval_experience_activation_reviews_public_id"
        ),
        sa.UniqueConstraint(
            "activation_request_id",
            name="uq_eval_experience_activation_reviews_request",
        ),
    )
    op.create_index(
        "ix_eval_experience_activation_reviews_reviewer_created",
        "evaluation_experience_activation_reviews",
        ["reviewed_by_user_id", "created_at"],
    )
    op.create_index(
        "ix_evaluation_experience_activation_reviews_decision",
        "evaluation_experience_activation_reviews",
        ["decision"],
    )


def downgrade() -> None:
    raise RuntimeError(
        "20260803_0053 downgrade refused: Experience activation decisions are "
        "immutable governance evidence; restore from a verified backup instead"
    )
