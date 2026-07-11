"""execution action evidence links

Revision ID: 20260624_0021
Revises: 20260622_0020
Create Date: 2026-06-24

ExecutionAction.evidence_ids stores the forward links from a manual execution
receipt to all evidence items used by that receipt. The column is nullable and
guarded because the metadata-bootstrap migration may already create it on a
fresh database.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260624_0021"
down_revision: str | None = "20260622_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE_NAME = "execution_actions"
COLUMN_NAME = "evidence_ids"


def _columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(TABLE_NAME)}


def upgrade() -> None:
    if COLUMN_NAME not in _columns():
        op.add_column(TABLE_NAME, sa.Column(COLUMN_NAME, sa.JSON(), nullable=True))


def downgrade() -> None:
    if COLUMN_NAME in _columns():
        op.drop_column(TABLE_NAME, COLUMN_NAME)
