"""agent insight jobs

Revision ID: 20260709_0025
Revises: 20260708_0024
Create Date: 2026-07-09

Stores one-click AI overview runs for an employee/agent runtime timeline.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260709_0025"
down_revision: str | None = "20260708_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE_NAME = "agent_insight_jobs"


def _tables() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return set(inspector.get_table_names())


def upgrade() -> None:
    if TABLE_NAME in _tables():
        return
    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("runtime_id", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.Integer(), nullable=True),
        sa.Column("status", sa.Enum("PENDING", "RUNNING", "SUCCEEDED", "FAILED", name="agentinsightstatus"), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("ai_assist_json", sa.JSON(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["runtime_id"], ["runtime_instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agent_insight_jobs_created_at"), TABLE_NAME, ["created_at"], unique=False)
    op.create_index(op.f("ix_agent_insight_jobs_finished_at"), TABLE_NAME, ["finished_at"], unique=False)
    op.create_index(op.f("ix_agent_insight_jobs_input_hash"), TABLE_NAME, ["input_hash"], unique=False)
    op.create_index(op.f("ix_agent_insight_jobs_requested_by"), TABLE_NAME, ["requested_by"], unique=False)
    op.create_index(op.f("ix_agent_insight_jobs_runtime_id"), TABLE_NAME, ["runtime_id"], unique=False)
    op.create_index(op.f("ix_agent_insight_jobs_started_at"), TABLE_NAME, ["started_at"], unique=False)
    op.create_index(op.f("ix_agent_insight_jobs_status"), TABLE_NAME, ["status"], unique=False)
    op.create_index(op.f("ix_agent_insight_jobs_user_id"), TABLE_NAME, ["user_id"], unique=False)


def downgrade() -> None:
    if TABLE_NAME in _tables():
        op.drop_table(TABLE_NAME)
