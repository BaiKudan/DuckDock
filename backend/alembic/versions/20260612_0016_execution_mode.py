"""execution mode on execution actions (FR-019 dual-mode, manual default)

Revision ID: 20260612_0016
Revises: 20260612_0015
Create Date: 2026-06-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.control_plane import ExecutionMode


revision: str = "20260612_0016"
down_revision: str | None = "20260612_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0009 建表用的是 live model(ExecutionAction.__table__),模型加上 execution_mode
    # 后,空库经 0009 已带该列;此处必须幂等——仅在缺列/缺索引时补,
    # 兼容「0009 早于本列定型」的历史库与「列已存在」的新库。
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("execution_actions")}
    if "execution_mode" not in columns:
        # server_default 用枚举成员名(MANUAL),与 SQLAlchemy Enum 默认的按名持久化一致
        op.add_column(
            "execution_actions",
            sa.Column("execution_mode", sa.Enum(ExecutionMode), nullable=False, server_default="MANUAL"),
        )
    indexes = {ix["name"] for ix in inspector.get_indexes("execution_actions")}
    if "ix_execution_actions_execution_mode" not in indexes:
        op.create_index("ix_execution_actions_execution_mode", "execution_actions", ["execution_mode"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    indexes = {ix["name"] for ix in inspector.get_indexes("execution_actions")}
    if "ix_execution_actions_execution_mode" in indexes:
        op.drop_index("ix_execution_actions_execution_mode", table_name="execution_actions")
    columns = {c["name"] for c in inspector.get_columns("execution_actions")}
    if "execution_mode" in columns:
        op.drop_column("execution_actions", "execution_mode")
