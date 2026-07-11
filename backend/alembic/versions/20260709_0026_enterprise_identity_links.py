"""enterprise identity links

Revision ID: 20260709_0026
Revises: 20260709_0025
Create Date: 2026-07-09

Adds DuckDock enterprise UIDs plus explicit SSO identity and group-to-role
mapping records. The SSO protocol plumbing already exists; this migration
captures the DuckDock-side identity contract.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260709_0026"
down_revision: str | None = "20260709_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tables() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return set(inspector.get_table_names())


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _backfill_enterprise_uid() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id FROM users WHERE enterprise_uid IS NULL")).fetchall()
    for row in rows:
        bind.execute(
            sa.text("UPDATE users SET enterprise_uid = :enterprise_uid WHERE id = :id"),
            {"enterprise_uid": f"duid_{int(row.id):012d}", "id": int(row.id)},
        )


def upgrade() -> None:
    tables = _tables()
    if "enterprise_uid" not in _columns("users"):
        op.add_column("users", sa.Column("enterprise_uid", sa.String(length=96), nullable=True))
        _backfill_enterprise_uid()
    if "ix_users_enterprise_uid" not in _indexes("users"):
        op.create_index(op.f("ix_users_enterprise_uid"), "users", ["enterprise_uid"], unique=True)

    if "identity_links" not in tables:
        op.create_table(
            "identity_links",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("provider_id", sa.Integer(), nullable=False),
            sa.Column("source", sa.Enum("LOCAL", "OIDC", "LDAP", name="authsource"), nullable=False),
            sa.Column("issuer", sa.String(length=512), nullable=True),
            sa.Column("external_subject", sa.String(length=255), nullable=False),
            sa.Column("external_uid", sa.String(length=128), nullable=True),
            sa.Column("username", sa.String(length=128), nullable=True),
            sa.Column("email", sa.String(length=255), nullable=True),
            sa.Column("full_name", sa.String(length=255), nullable=True),
            sa.Column("claims_json", sa.JSON(), nullable=True),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["provider_id"], ["sso_provider_configs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("provider_id", "external_subject", name="uq_identity_links_provider_subject"),
        )
        op.create_index(op.f("ix_identity_links_email"), "identity_links", ["email"], unique=False)
        op.create_index(op.f("ix_identity_links_external_subject"), "identity_links", ["external_subject"], unique=False)
        op.create_index(op.f("ix_identity_links_external_uid"), "identity_links", ["external_uid"], unique=False)
        op.create_index(op.f("ix_identity_links_last_seen_at"), "identity_links", ["last_seen_at"], unique=False)
        op.create_index(op.f("ix_identity_links_provider_id"), "identity_links", ["provider_id"], unique=False)
        op.create_index(op.f("ix_identity_links_source"), "identity_links", ["source"], unique=False)
        op.create_index(op.f("ix_identity_links_user_id"), "identity_links", ["user_id"], unique=False)

    if "sso_role_mappings" not in tables:
        op.create_table(
            "sso_role_mappings",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("provider_id", sa.Integer(), nullable=False),
            sa.Column("claim_name", sa.String(length=128), nullable=False),
            sa.Column("claim_value", sa.String(length=255), nullable=False),
            sa.Column("role_id", sa.Integer(), nullable=False),
            sa.Column("namespace_id", sa.Integer(), nullable=True),
            sa.Column("org_unit_id", sa.Integer(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("priority", sa.Integer(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["namespace_id"], ["namespaces.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["org_unit_id"], ["org_units.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["provider_id"], ["sso_provider_configs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_sso_role_mappings_claim_name"), "sso_role_mappings", ["claim_name"], unique=False)
        op.create_index(op.f("ix_sso_role_mappings_claim_value"), "sso_role_mappings", ["claim_value"], unique=False)
        op.create_index(op.f("ix_sso_role_mappings_enabled"), "sso_role_mappings", ["enabled"], unique=False)
        op.create_index(op.f("ix_sso_role_mappings_namespace_id"), "sso_role_mappings", ["namespace_id"], unique=False)
        op.create_index(op.f("ix_sso_role_mappings_org_unit_id"), "sso_role_mappings", ["org_unit_id"], unique=False)
        op.create_index(op.f("ix_sso_role_mappings_priority"), "sso_role_mappings", ["priority"], unique=False)
        op.create_index(op.f("ix_sso_role_mappings_provider_id"), "sso_role_mappings", ["provider_id"], unique=False)
        op.create_index(op.f("ix_sso_role_mappings_role_id"), "sso_role_mappings", ["role_id"], unique=False)


def downgrade() -> None:
    tables = _tables()
    if "sso_role_mappings" in tables:
        op.drop_table("sso_role_mappings")
    if "identity_links" in tables:
        op.drop_table("identity_links")
    if "enterprise_uid" in _columns("users"):
        if "ix_users_enterprise_uid" in _indexes("users"):
            op.drop_index(op.f("ix_users_enterprise_uid"), table_name="users")
        op.drop_column("users", "enterprise_uid")
