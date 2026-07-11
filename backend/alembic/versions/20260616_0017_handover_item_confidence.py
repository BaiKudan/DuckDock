"""handover item advisor confidence score (specs/001 T042)

Revision ID: 20260616_0017
Revises: 20260612_0016
Create Date: 2026-06-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260616_0017"
down_revision: str | None = "20260612_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0009 用 live model(HandoverItem.__table__)建表,模型加上 confidence 后,空库经 0009
    # 已带该列;此处必须幂等——仅在缺列时补,兼容「0009 早于本列定型」的历史库与「列已存在」的新库。
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("handover_items")}
    if "confidence" not in columns:
        op.add_column(
            "handover_items",
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("handover_items")}
    if "confidence" in columns:
        op.drop_column("handover_items", "confidence")
