"""sandbox validation runs

Revision ID: 20260410_0005
Revises: 20260409_0004
Create Date: 2026-04-10 11:30:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260410_0005"
down_revision = "20260409_0004"
branch_labels = None
depends_on = None


sandbox_validation_status_enum = postgresql.ENUM(
    "PENDING",
    "RUNNING",
    "PASSED",
    "FAILED",
    "SKIPPED",
    name="sandboxvalidationstatus",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("sandbox_validation_runs"):
        return

    sa.Enum(
        "PENDING",
        "RUNNING",
        "PASSED",
        "FAILED",
        "SKIPPED",
        name="sandboxvalidationstatus",
    ).create(bind, checkfirst=True)

    op.create_table(
        "sandbox_validation_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("status", sandbox_validation_status_enum, nullable=False, server_default="PENDING"),
        sa.Column("engine", sa.String(length=64), nullable=False, server_default="openclaw-docker"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("checks", sa.JSON(), nullable=True),
        sa.Column("logs", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["version_id"], ["skill_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version_id"),
    )
    op.create_index(op.f("ix_sandbox_validation_runs_version_id"), "sandbox_validation_runs", ["version_id"], unique=True)
    op.create_index(op.f("ix_sandbox_validation_runs_status"), "sandbox_validation_runs", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_sandbox_validation_runs_status"), table_name="sandbox_validation_runs")
    op.drop_index(op.f("ix_sandbox_validation_runs_version_id"), table_name="sandbox_validation_runs")
    op.drop_table("sandbox_validation_runs")

    bind = op.get_bind()
    sa.Enum(
        "PENDING",
        "RUNNING",
        "PASSED",
        "FAILED",
        "SKIPPED",
        name="sandboxvalidationstatus",
    ).drop(bind, checkfirst=True)
