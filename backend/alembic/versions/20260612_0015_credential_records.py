"""credential records (Fernet-encrypted runtime credentials)

Revision ID: 20260612_0015
Revises: 20260521_0014
Create Date: 2026-06-12
"""

from collections.abc import Sequence

from alembic import op

from app.models.control_plane import CredentialRecord


revision: str = "20260612_0015"
down_revision: str | None = "20260521_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    CredentialRecord.__table__.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    CredentialRecord.__table__.drop(bind=bind, checkfirst=True)
