"""clinic evaluation langfuse trace id

Revision ID: 20260624_0023
Revises: 20260624_0022
Create Date: 2026-06-24

Persist the Langfuse trace id generated during clinic evaluation so evaluation
details can link back to observability without doing a best-effort trace search.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260624_0023"
down_revision: str | None = "20260624_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE_NAME = "clinic_evaluations"
COLUMN_NAME = "trace_id"
INDEX_NAME = "ix_clinic_evaluations_trace_id"


def _columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(TABLE_NAME)}


def _indexes() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(TABLE_NAME)}


def upgrade() -> None:
    if COLUMN_NAME not in _columns():
        op.add_column(TABLE_NAME, sa.Column(COLUMN_NAME, sa.String(length=128), nullable=True))
    if INDEX_NAME not in _indexes():
        op.create_index(INDEX_NAME, TABLE_NAME, [COLUMN_NAME], unique=False)


def downgrade() -> None:
    if INDEX_NAME in _indexes():
        op.drop_index(INDEX_NAME, table_name=TABLE_NAME)
    if COLUMN_NAME in _columns():
        op.drop_column(TABLE_NAME, COLUMN_NAME)
