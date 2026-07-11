"""enterprise iam foundation

Revision ID: 20260409_0004
Revises: 20260409_0003
Create Date: 2026-04-09 21:30:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260409_0004"
down_revision = "20260409_0003"
branch_labels = None
depends_on = None


auth_source_enum = postgresql.ENUM("LOCAL", "OIDC", "LDAP", name="authsource", create_type=False)
org_unit_type_enum = postgresql.ENUM("COMPANY", "SUBSIDIARY", "DIVISION", "DEPARTMENT", "TEAM", "BRANCH", name="orgunittype", create_type=False)
role_scope_enum = postgresql.ENUM("SYSTEM", "ORG", "NAMESPACE", name="rolescope", create_type=False)
sso_provider_type_enum = postgresql.ENUM("OIDC", "LDAP", name="ssoprovidertype", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("permissions"):
        return

    sa.Enum("LOCAL", "OIDC", "LDAP", name="authsource").create(bind, checkfirst=True)
    sa.Enum("COMPANY", "SUBSIDIARY", "DIVISION", "DEPARTMENT", "TEAM", "BRANCH", name="orgunittype").create(bind, checkfirst=True)
    sa.Enum("SYSTEM", "ORG", "NAMESPACE", name="rolescope").create(bind, checkfirst=True)
    sa.Enum("OIDC", "LDAP", name="ssoprovidertype").create(bind, checkfirst=True)

    op.add_column("users", sa.Column("full_name", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("auth_source", auth_source_enum, nullable=False, server_default="LOCAL"))
    op.add_column("users", sa.Column("external_subject", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_users_external_subject"), "users", ["external_subject"], unique=True)
    op.alter_column("users", "auth_source", server_default=None)

    op.create_table(
        "org_units",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=True),
        sa.Column("unit_type", org_unit_type_enum, nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("path", sa.String(length=512), nullable=False, server_default="/"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["org_units.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_org_units_code"), "org_units", ["code"], unique=True)
    op.create_index(op.f("ix_org_units_parent_id"), "org_units", ["parent_id"], unique=False)
    op.create_index(op.f("ix_org_units_unit_type"), "org_units", ["unit_type"], unique=False)

    op.create_table(
        "user_affiliations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("org_unit_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=True),
        sa.Column("employee_no", sa.String(length=64), nullable=True),
        sa.Column("manager_user_id", sa.Integer(), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["manager_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["org_unit_id"], ["org_units.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "org_unit_id", name="uq_user_affiliations_user_org"),
    )
    op.create_index(op.f("ix_user_affiliations_user_id"), "user_affiliations", ["user_id"], unique=False)
    op.create_index(op.f("ix_user_affiliations_org_unit_id"), "user_affiliations", ["org_unit_id"], unique=False)
    op.create_index(op.f("ix_user_affiliations_employee_no"), "user_affiliations", ["employee_no"], unique=False)
    op.create_index(op.f("ix_user_affiliations_manager_user_id"), "user_affiliations", ["manager_user_id"], unique=False)

    op.create_table(
        "permissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("scope", role_scope_enum, nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_permissions_key"), "permissions", ["key"], unique=True)
    op.create_index(op.f("ix_permissions_scope"), "permissions", ["scope"], unique=False)

    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("scope", role_scope_enum, nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_roles_key"), "roles", ["key"], unique=True)
    op.create_index(op.f("ix_roles_scope"), "roles", ["scope"], unique=False)

    op.create_table(
        "role_permissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("permission_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("role_id", "permission_id", name="uq_role_permissions_role_permission"),
    )
    op.create_index(op.f("ix_role_permissions_role_id"), "role_permissions", ["role_id"], unique=False)
    op.create_index(op.f("ix_role_permissions_permission_id"), "role_permissions", ["permission_id"], unique=False)

    op.create_table(
        "role_bindings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=True),
        sa.Column("org_unit_id", sa.Integer(), nullable=True),
        sa.Column("granted_by", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("permission_cache", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["granted_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_unit_id"], ["org_units.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_role_bindings_role_id"), "role_bindings", ["role_id"], unique=False)
    op.create_index(op.f("ix_role_bindings_user_id"), "role_bindings", ["user_id"], unique=False)
    op.create_index(op.f("ix_role_bindings_namespace_id"), "role_bindings", ["namespace_id"], unique=False)
    op.create_index(op.f("ix_role_bindings_org_unit_id"), "role_bindings", ["org_unit_id"], unique=False)
    op.create_index(op.f("ix_role_bindings_expires_at"), "role_bindings", ["expires_at"], unique=False)

    op.create_table(
        "sso_provider_configs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider_type", sso_provider_type_enum, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("issuer_url", sa.String(length=512), nullable=True),
        sa.Column("client_id", sa.String(length=255), nullable=True),
        sa.Column("client_secret", sa.Text(), nullable=True),
        sa.Column("ldap_server_url", sa.String(length=512), nullable=True),
        sa.Column("ldap_bind_dn", sa.String(length=512), nullable=True),
        sa.Column("ldap_bind_password", sa.Text(), nullable=True),
        sa.Column("ldap_user_search_base", sa.String(length=512), nullable=True),
        sa.Column("ldap_user_search_filter", sa.String(length=512), nullable=True),
        sa.Column("ldap_group_search_base", sa.String(length=512), nullable=True),
        sa.Column("attribute_mapping", sa.JSON(), nullable=True),
        sa.Column("extra_config", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sso_provider_configs_name"), "sso_provider_configs", ["name"], unique=True)
    op.create_index(op.f("ix_sso_provider_configs_provider_type"), "sso_provider_configs", ["provider_type"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_sso_provider_configs_provider_type"), table_name="sso_provider_configs")
    op.drop_index(op.f("ix_sso_provider_configs_name"), table_name="sso_provider_configs")
    op.drop_table("sso_provider_configs")

    op.drop_index(op.f("ix_role_bindings_expires_at"), table_name="role_bindings")
    op.drop_index(op.f("ix_role_bindings_org_unit_id"), table_name="role_bindings")
    op.drop_index(op.f("ix_role_bindings_namespace_id"), table_name="role_bindings")
    op.drop_index(op.f("ix_role_bindings_user_id"), table_name="role_bindings")
    op.drop_index(op.f("ix_role_bindings_role_id"), table_name="role_bindings")
    op.drop_table("role_bindings")

    op.drop_index(op.f("ix_role_permissions_permission_id"), table_name="role_permissions")
    op.drop_index(op.f("ix_role_permissions_role_id"), table_name="role_permissions")
    op.drop_table("role_permissions")

    op.drop_index(op.f("ix_roles_scope"), table_name="roles")
    op.drop_index(op.f("ix_roles_key"), table_name="roles")
    op.drop_table("roles")

    op.drop_index(op.f("ix_permissions_scope"), table_name="permissions")
    op.drop_index(op.f("ix_permissions_key"), table_name="permissions")
    op.drop_table("permissions")

    op.drop_index(op.f("ix_user_affiliations_manager_user_id"), table_name="user_affiliations")
    op.drop_index(op.f("ix_user_affiliations_employee_no"), table_name="user_affiliations")
    op.drop_index(op.f("ix_user_affiliations_org_unit_id"), table_name="user_affiliations")
    op.drop_index(op.f("ix_user_affiliations_user_id"), table_name="user_affiliations")
    op.drop_table("user_affiliations")

    op.drop_index(op.f("ix_org_units_unit_type"), table_name="org_units")
    op.drop_index(op.f("ix_org_units_parent_id"), table_name="org_units")
    op.drop_index(op.f("ix_org_units_code"), table_name="org_units")
    op.drop_table("org_units")

    op.drop_index(op.f("ix_users_external_subject"), table_name="users")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "external_subject")
    op.drop_column("users", "auth_source")
    op.drop_column("users", "full_name")

    bind = op.get_bind()
    sa.Enum("OIDC", "LDAP", name="ssoprovidertype").drop(bind, checkfirst=True)
    sa.Enum("SYSTEM", "ORG", "NAMESPACE", name="rolescope").drop(bind, checkfirst=True)
    sa.Enum("COMPANY", "SUBSIDIARY", "DIVISION", "DEPARTMENT", "TEAM", "BRANCH", name="orgunittype").drop(bind, checkfirst=True)
    sa.Enum("LOCAL", "OIDC", "LDAP", name="authsource").drop(bind, checkfirst=True)
