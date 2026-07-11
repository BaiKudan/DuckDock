"""add package metadata fields to skill versions

Revision ID: 20260409_0003
Revises: 20260409_0002
Create Date: 2026-04-09 20:45:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260409_0003"
down_revision = "20260409_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("skill_versions")}
    if "changelog" not in columns:
        op.add_column("skill_versions", sa.Column("changelog", sa.Text(), nullable=True))
    if "publish_tags" not in columns:
        op.add_column("skill_versions", sa.Column("publish_tags", sa.JSON(), nullable=True))
    if "content_fingerprint" not in columns:
        op.add_column("skill_versions", sa.Column("content_fingerprint", sa.String(length=64), nullable=True))
        op.create_index(
            op.f("ix_skill_versions_content_fingerprint"),
            "skill_versions",
            ["content_fingerprint"],
            unique=False,
        )
    if "file_count" not in columns:
        op.add_column("skill_versions", sa.Column("file_count", sa.Integer(), nullable=False, server_default="0"))
        op.alter_column("skill_versions", "file_count", server_default=None)


def downgrade() -> None:
    op.drop_index(op.f("ix_skill_versions_content_fingerprint"), table_name="skill_versions")
    op.drop_column("skill_versions", "file_count")
    op.drop_column("skill_versions", "content_fingerprint")
    op.drop_column("skill_versions", "publish_tags")
    op.drop_column("skill_versions", "changelog")
