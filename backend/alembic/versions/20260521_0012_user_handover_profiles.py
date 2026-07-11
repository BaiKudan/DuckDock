"""user handover profiles

Revision ID: 20260521_0012
Revises: 20260521_0011
Create Date: 2026-05-21
"""

from collections.abc import Sequence

from alembic import op

from app.models.iam import UserHandoverProfile


revision: str = "20260521_0012"
down_revision: str | None = "20260521_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    UserHandoverProfile.__table__.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    UserHandoverProfile.__table__.drop(bind=bind, checkfirst=True)
