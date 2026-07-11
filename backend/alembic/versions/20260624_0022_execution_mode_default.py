"""normalize execution action execution_mode default

Revision ID: 20260624_0022
Revises: 20260624_0021
Create Date: 2026-06-24

Historical databases may already have execution_actions.execution_mode from
the metadata-bootstrap migration, which made 0016 skip adding the column and
therefore skip applying the MANUAL server default. Normalize that default so
alembic check remains clean after the P3 evidence migration.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.control_plane import ExecutionMode


revision: str = "20260624_0022"
down_revision: str | None = "20260624_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE_NAME = "execution_actions"
COLUMN_NAME = "execution_mode"


def _has_column() -> bool:
    inspector = sa.inspect(op.get_bind())
    return COLUMN_NAME in {column["name"] for column in inspector.get_columns(TABLE_NAME)}


def upgrade() -> None:
    if _has_column():
        op.alter_column(
            TABLE_NAME,
            COLUMN_NAME,
            existing_type=sa.Enum(ExecutionMode),
            existing_nullable=False,
            server_default="MANUAL",
        )


def downgrade() -> None:
    if _has_column():
        op.alter_column(
            TABLE_NAME,
            COLUMN_NAME,
            existing_type=sa.Enum(ExecutionMode),
            existing_nullable=False,
            server_default=None,
        )
