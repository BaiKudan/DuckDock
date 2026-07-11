"""reporter credentials

Revision ID: 20260708_0024
Revises: 20260624_0023
Create Date: 2026-07-08

Dedicated long-lived Reporter credentials for employee/agent enrollment.
Runtime report tokens remain for administrator-managed compatibility.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260708_0024"
down_revision: str | None = "20260624_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE_NAME = "reporter_credentials"


def _tables() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return set(inspector.get_table_names())


def upgrade() -> None:
    if TABLE_NAME in _tables():
        return
    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("runtime_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("device_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("token_prefix", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=255), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Integer(), nullable=True),
        sa.Column("revoked_reason", sa.Text(), nullable=True),
        sa.Column("rotated_from_id", sa.Integer(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["revoked_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["rotated_from_id"], [f"{TABLE_NAME}.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["runtime_id"], ["runtime_instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_prefix", name="uq_reporter_credentials_prefix"),
    )
    op.create_index(op.f("ix_reporter_credentials_device_id"), TABLE_NAME, ["device_id"], unique=False)
    op.create_index(op.f("ix_reporter_credentials_expires_at"), TABLE_NAME, ["expires_at"], unique=False)
    op.create_index(op.f("ix_reporter_credentials_is_active"), TABLE_NAME, ["is_active"], unique=False)
    op.create_index(op.f("ix_reporter_credentials_last_heartbeat_at"), TABLE_NAME, ["last_heartbeat_at"], unique=False)
    op.create_index(op.f("ix_reporter_credentials_last_used_at"), TABLE_NAME, ["last_used_at"], unique=False)
    op.create_index(op.f("ix_reporter_credentials_revoked_at"), TABLE_NAME, ["revoked_at"], unique=False)
    op.create_index(op.f("ix_reporter_credentials_revoked_by"), TABLE_NAME, ["revoked_by"], unique=False)
    op.create_index(op.f("ix_reporter_credentials_rotated_from_id"), TABLE_NAME, ["rotated_from_id"], unique=False)
    op.create_index(op.f("ix_reporter_credentials_runtime_id"), TABLE_NAME, ["runtime_id"], unique=False)
    op.create_index(op.f("ix_reporter_credentials_token_prefix"), TABLE_NAME, ["token_prefix"], unique=False)
    op.create_index(op.f("ix_reporter_credentials_user_id"), TABLE_NAME, ["user_id"], unique=False)


def downgrade() -> None:
    if TABLE_NAME in _tables():
        op.drop_table(TABLE_NAME)
