"""analysis result artifacts

Revision ID: 20260521_0014
Revises: 20260521_0013
Create Date: 2026-05-21
"""

from collections.abc import Sequence

from alembic import op

from app.models.control_plane import AnalysisResultArtifact


revision: str = "20260521_0014"
down_revision: str | None = "20260521_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    AnalysisResultArtifact.__table__.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    AnalysisResultArtifact.__table__.drop(bind=bind, checkfirst=True)
