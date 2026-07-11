"""governance and release gate foundation

Revision ID: 20260413_0006
Revises: 20260410_0005
Create Date: 2026-04-13 11:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260413_0006"
down_revision = "20260410_0005"
branch_labels = None
depends_on = None


skill_version_review_status_enum = sa.Enum(
    "NOT_REQUIRED",
    "PENDING",
    "APPROVED",
    "REJECTED",
    name="skillversionreviewstatus",
)
public_release_approval_status_enum = sa.Enum(
    "PENDING",
    "APPROVED",
    "REJECTED",
    name="publicreleaseapprovalstatus",
)
employment_status_enum = sa.Enum(
    "ACTIVE",
    "ONBOARDING",
    "LEAVE",
    "OFFBOARDED",
    name="employmentstatus",
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    skill_version_columns = {
        column["name"] for column in inspector.get_columns("skill_versions")
    }
    if inspector.has_table("namespace_governance_policies") and "review_status" in skill_version_columns:
        return

    sa.Enum(
        "NOT_REQUIRED",
        "PENDING",
        "APPROVED",
        "REJECTED",
        name="skillversionreviewstatus",
    ).create(bind, checkfirst=True)
    sa.Enum(
        "PENDING",
        "APPROVED",
        "REJECTED",
        name="publicreleaseapprovalstatus",
    ).create(bind, checkfirst=True)
    sa.Enum(
        "ACTIVE",
        "ONBOARDING",
        "LEAVE",
        "OFFBOARDED",
        name="employmentstatus",
    ).create(bind, checkfirst=True)

    op.create_table(
        "namespace_governance_policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("manual_review_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("require_examples", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("require_validation_spec", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("require_sandbox_success", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("clinic_gate_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("min_clinic_score", sa.Float(), nullable=True, server_default="75"),
        sa.Column("clinic_max_age_hours", sa.Integer(), nullable=False, server_default="168"),
        sa.Column("public_sharing_requires_approval", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("public_share_default_expiry_days", sa.Integer(), nullable=True, server_default="30"),
        sa.Column("require_license_attestation", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("allowed_public_licenses", sa.JSON(), nullable=True),
        sa.Column("sandbox_network_mode", sa.String(length=32), nullable=False, server_default="registry_only"),
        sa.Column("sandbox_workspace_mode", sa.String(length=32), nullable=False, server_default="ephemeral"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace_id", name="uq_namespace_governance_policy_namespace"),
    )
    op.create_index(
        op.f("ix_namespace_governance_policies_namespace_id"),
        "namespace_governance_policies",
        ["namespace_id"],
        unique=True,
    )

    op.add_column(
        "skill_versions",
        sa.Column(
            "review_status",
            skill_version_review_status_enum,
            nullable=False,
            server_default="NOT_REQUIRED",
        ),
    )
    op.add_column("skill_versions", sa.Column("review_required", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("skill_versions", sa.Column("review_notes", sa.Text(), nullable=True))
    op.add_column("skill_versions", sa.Column("review_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("skill_versions", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("skill_versions", sa.Column("reviewed_by", sa.Integer(), nullable=True))
    op.add_column("skill_versions", sa.Column("gate_result", sa.JSON(), nullable=True))
    op.create_foreign_key(
        "fk_skill_versions_reviewed_by_users",
        "skill_versions",
        "users",
        ["reviewed_by"],
        ["id"],
    )
    op.create_index(op.f("ix_skill_versions_review_status"), "skill_versions", ["review_status"], unique=False)

    op.add_column(
        "public_skill_releases",
        sa.Column(
            "approval_status",
            public_release_approval_status_enum,
            nullable=False,
            server_default="APPROVED",
        ),
    )
    op.add_column("public_skill_releases", sa.Column("approval_notes", sa.Text(), nullable=True))
    op.add_column("public_skill_releases", sa.Column("approved_by", sa.Integer(), nullable=True))
    op.add_column("public_skill_releases", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("public_skill_releases", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("public_skill_releases", sa.Column("license_name", sa.String(length=128), nullable=True))
    op.add_column(
        "public_skill_releases",
        sa.Column("license_attested", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("public_skill_releases", sa.Column("license_attested_by", sa.Integer(), nullable=True))
    op.add_column("public_skill_releases", sa.Column("license_attested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "public_skill_releases",
        sa.Column("risk_acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("public_skill_releases", sa.Column("risk_acknowledged_by", sa.Integer(), nullable=True))
    op.add_column("public_skill_releases", sa.Column("risk_acknowledged_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_public_skill_releases_approved_by_users",
        "public_skill_releases",
        "users",
        ["approved_by"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_public_skill_releases_license_attested_by_users",
        "public_skill_releases",
        "users",
        ["license_attested_by"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_public_skill_releases_risk_acknowledged_by_users",
        "public_skill_releases",
        "users",
        ["risk_acknowledged_by"],
        ["id"],
    )
    op.create_index(
        op.f("ix_public_skill_releases_approval_status"),
        "public_skill_releases",
        ["approval_status"],
        unique=False,
    )
    op.create_index(op.f("ix_public_skill_releases_expires_at"), "public_skill_releases", ["expires_at"], unique=False)
    op.execute("UPDATE public_skill_releases SET approval_status = 'APPROVED' WHERE approval_status IS NULL")

    op.add_column("org_units", sa.Column("legal_entity", sa.String(length=128), nullable=True))
    op.add_column("org_units", sa.Column("region", sa.String(length=64), nullable=True))
    op.add_column("org_units", sa.Column("cost_center", sa.String(length=64), nullable=True))

    op.add_column(
        "user_affiliations",
        sa.Column(
            "employment_status",
            employment_status_enum,
            nullable=False,
            server_default="ACTIVE",
        ),
    )
    op.add_column("user_affiliations", sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("user_affiliations", sa.Column("left_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("user_affiliations", sa.Column("cost_center_override", sa.String(length=64), nullable=True))
    op.create_index(
        op.f("ix_user_affiliations_employment_status"),
        "user_affiliations",
        ["employment_status"],
        unique=False,
    )

    op.alter_column("skill_versions", "review_status", server_default=None)
    op.alter_column("skill_versions", "review_required", server_default=None)
    op.alter_column("public_skill_releases", "approval_status", server_default=None)
    op.alter_column("public_skill_releases", "license_attested", server_default=None)
    op.alter_column("public_skill_releases", "risk_acknowledged", server_default=None)
    op.alter_column("user_affiliations", "employment_status", server_default=None)


def downgrade() -> None:
    op.drop_index(op.f("ix_user_affiliations_employment_status"), table_name="user_affiliations")
    op.drop_column("user_affiliations", "cost_center_override")
    op.drop_column("user_affiliations", "left_at")
    op.drop_column("user_affiliations", "joined_at")
    op.drop_column("user_affiliations", "employment_status")

    op.drop_column("org_units", "cost_center")
    op.drop_column("org_units", "region")
    op.drop_column("org_units", "legal_entity")

    op.drop_index(op.f("ix_public_skill_releases_expires_at"), table_name="public_skill_releases")
    op.drop_index(op.f("ix_public_skill_releases_approval_status"), table_name="public_skill_releases")
    op.drop_constraint("fk_public_skill_releases_risk_acknowledged_by_users", "public_skill_releases", type_="foreignkey")
    op.drop_constraint("fk_public_skill_releases_license_attested_by_users", "public_skill_releases", type_="foreignkey")
    op.drop_constraint("fk_public_skill_releases_approved_by_users", "public_skill_releases", type_="foreignkey")
    op.drop_column("public_skill_releases", "risk_acknowledged_at")
    op.drop_column("public_skill_releases", "risk_acknowledged_by")
    op.drop_column("public_skill_releases", "risk_acknowledged")
    op.drop_column("public_skill_releases", "license_attested_at")
    op.drop_column("public_skill_releases", "license_attested_by")
    op.drop_column("public_skill_releases", "license_attested")
    op.drop_column("public_skill_releases", "license_name")
    op.drop_column("public_skill_releases", "expires_at")
    op.drop_column("public_skill_releases", "approved_at")
    op.drop_column("public_skill_releases", "approved_by")
    op.drop_column("public_skill_releases", "approval_notes")
    op.drop_column("public_skill_releases", "approval_status")

    op.drop_index(op.f("ix_skill_versions_review_status"), table_name="skill_versions")
    op.drop_constraint("fk_skill_versions_reviewed_by_users", "skill_versions", type_="foreignkey")
    op.drop_column("skill_versions", "gate_result")
    op.drop_column("skill_versions", "reviewed_by")
    op.drop_column("skill_versions", "reviewed_at")
    op.drop_column("skill_versions", "review_requested_at")
    op.drop_column("skill_versions", "review_notes")
    op.drop_column("skill_versions", "review_required")
    op.drop_column("skill_versions", "review_status")

    op.drop_index(op.f("ix_namespace_governance_policies_namespace_id"), table_name="namespace_governance_policies")
    op.drop_table("namespace_governance_policies")

    bind = op.get_bind()
    sa.Enum(
        "ACTIVE",
        "ONBOARDING",
        "LEAVE",
        "OFFBOARDED",
        name="employmentstatus",
    ).drop(bind, checkfirst=True)
    sa.Enum(
        "PENDING",
        "APPROVED",
        "REJECTED",
        name="publicreleaseapprovalstatus",
    ).drop(bind, checkfirst=True)
    sa.Enum(
        "NOT_REQUIRED",
        "PENDING",
        "APPROVED",
        "REJECTED",
        name="skillversionreviewstatus",
    ).drop(bind, checkfirst=True)
