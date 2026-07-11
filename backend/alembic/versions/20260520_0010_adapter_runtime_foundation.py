"""adapter runtime foundation

Revision ID: 20260520_0010
Revises: 20260518_0009
Create Date: 2026-05-20
"""

from collections.abc import Sequence

from alembic import op

from app.models.control_plane import (
    AdapterCursor,
    AdapterError,
    AdapterRunStep,
    ProviderPrincipal,
    RawCollectionRecord,
    RuntimeCapabilitySnapshot,
)


revision: str = "20260520_0010"
down_revision: str | None = "20260518_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLES = [
    AdapterCursor.__table__,
    AdapterRunStep.__table__,
    RawCollectionRecord.__table__,
    AdapterError.__table__,
    ProviderPrincipal.__table__,
    RuntimeCapabilitySnapshot.__table__,
]


def upgrade() -> None:
    bind = op.get_bind()
    for table in TABLES:
        table.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(TABLES):
        table.drop(bind=bind, checkfirst=True)
