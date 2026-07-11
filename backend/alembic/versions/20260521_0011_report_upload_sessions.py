"""report upload sessions

Revision ID: 20260521_0011
Revises: 20260520_0010
Create Date: 2026-05-21
"""

from collections.abc import Sequence

from alembic import op

from app.models.control_plane import ReportUploadSession, RuntimeReportToken


revision: str = "20260521_0011"
down_revision: str | None = "20260520_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLES = [
    RuntimeReportToken.__table__,
    ReportUploadSession.__table__,
]


def upgrade() -> None:
    bind = op.get_bind()
    for table in TABLES:
        table.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(TABLES):
        table.drop(bind=bind, checkfirst=True)
