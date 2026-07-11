"""analysis worker pipeline

Revision ID: 20260521_0013
Revises: 20260521_0012
Create Date: 2026-05-21
"""

from collections.abc import Sequence

from alembic import op

from app.models.control_plane import AnalysisWorker, MemoryCandidate, ReportAnalysisJob


revision: str = "20260521_0013"
down_revision: str | None = "20260521_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLES = [
    AnalysisWorker.__table__,
    ReportAnalysisJob.__table__,
    MemoryCandidate.__table__,
]


def upgrade() -> None:
    bind = op.get_bind()
    for table in TABLES:
        table.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(TABLES):
        table.drop(bind=bind, checkfirst=True)
