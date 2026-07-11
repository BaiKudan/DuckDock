"""add soft delete columns for namespaces and skills

Revision ID: 20260409_0002
Revises: 20260409_0001
Create Date: 2026-04-09
"""

from alembic import op
import sqlalchemy as sa


revision = "20260409_0002"
down_revision = "20260409_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    namespace_columns = {column["name"] for column in inspector.get_columns("namespaces")}
    skill_columns = {column["name"] for column in inspector.get_columns("skills")}

    if "deleted_at" not in namespace_columns:
        op.add_column("namespaces", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        op.create_index("ix_namespaces_deleted_at", "namespaces", ["deleted_at"], unique=False)
    if "deleted_by" not in namespace_columns:
        op.add_column("namespaces", sa.Column("deleted_by", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_namespaces_deleted_by_users",
            "namespaces",
            "users",
            ["deleted_by"],
            ["id"],
        )

    if "deleted_at" not in skill_columns:
        op.add_column("skills", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        op.create_index("ix_skills_deleted_at", "skills", ["deleted_at"], unique=False)
    if "deleted_by" not in skill_columns:
        op.add_column("skills", sa.Column("deleted_by", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_skills_deleted_by_users",
            "skills",
            "users",
            ["deleted_by"],
            ["id"],
        )


def downgrade() -> None:
    op.drop_index("ix_skills_deleted_at", table_name="skills")
    op.drop_constraint("fk_skills_deleted_by_users", "skills", type_="foreignkey")
    op.drop_column("skills", "deleted_by")
    op.drop_column("skills", "deleted_at")

    op.drop_index("ix_namespaces_deleted_at", table_name="namespaces")
    op.drop_constraint("fk_namespaces_deleted_by_users", "namespaces", type_="foreignkey")
    op.drop_column("namespaces", "deleted_by")
    op.drop_column("namespaces", "deleted_at")
