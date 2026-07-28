"""agent control plane foundation

Revision ID: 20260518_0009
Revises: 20260420_0008
Create Date: 2026-05-18
"""

from collections.abc import Sequence

from alembic import op

from app.models.control_plane import (
    AIAsset,
    ApprovalTask,
    AssetOwnership,
    CollectionJob,
    EvidenceItem,
    ExecutionAction,
    HandoverCase,
    HandoverItem,
    RuntimeBinding,
    RuntimeInstance,
    WorkArtifact,
    WorkTrace,
)


revision: str = "20260518_0009"
down_revision: str | None = "20260420_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLES = [
    RuntimeInstance.__table__,
    CollectionJob.__table__,
    AIAsset.__table__,
    WorkTrace.__table__,
    EvidenceItem.__table__,
    AssetOwnership.__table__,
    RuntimeBinding.__table__,
    WorkArtifact.__table__,
    HandoverCase.__table__,
    HandoverItem.__table__,
    ApprovalTask.__table__,
    ExecutionAction.__table__,
]


def upgrade() -> None:
    bind = op.get_bind()
    for table in TABLES:
        table.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(TABLES):
        table.drop(bind=bind, checkfirst=True)
