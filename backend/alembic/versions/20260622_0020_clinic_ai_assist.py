"""clinic evaluation ai_assist degradation indicator (L2-CLINIC degrade)

Revision ID: 20260622_0020
Revises: 20260622_0019
Create Date: 2026-06-22

ClinicEvaluation 新增 nullable JSON 列 ai_assist {"mode","degraded","reason"},
暴露某次评测到底是真实 LLM 评审还是启发式回落,使 release-gate 不再把启发式当
AI 静默使用。0009 用 live model 建表,空库经 0009 已带该列;此处必须幂等——
仅在缺列时补,兼容历史库与新库。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260622_0020"
down_revision: str | None = "20260622_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("clinic_evaluations")}
    if "ai_assist" not in columns:
        op.add_column(
            "clinic_evaluations",
            sa.Column("ai_assist", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("clinic_evaluations")}
    if "ai_assist" in columns:
        op.drop_column("clinic_evaluations", "ai_assist")
