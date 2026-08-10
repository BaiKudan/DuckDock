"""Add Agent Package Registry v2, signing trust and SBOM evidence.

Revision ID: 20260804_0057
Revises: 20260804_0056
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260804_0057"
down_revision: str | None = "20260804_0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _foreign_key(local: str, remote: str, name: str, *, ondelete: str = "RESTRICT") -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([local], [remote], name=name, ondelete=ondelete)


def upgrade() -> None:
    op.create_table(
        "agent_packages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("namespace_ref", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=1000)),
        sa.Column("agent_asset_id", sa.Integer(), nullable=False),
        sa.Column("agent_asset_ref", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "RETIRED", name="agent_package_status"),
            nullable=False,
        ),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key("namespace_id", "namespaces.id", "fk_agent_packages_namespace"),
        _foreign_key("agent_asset_id", "ai_assets.id", "fk_agent_packages_asset"),
        _foreign_key("created_by_user_id", "users.id", "fk_agent_packages_creator"),
        sa.UniqueConstraint("public_id", name="uq_agent_packages_public_id"),
        sa.UniqueConstraint("namespace_id", "name", name="uq_agent_packages_ns_name"),
        sa.CheckConstraint(
            "(status = 'ACTIVE' AND retired_at IS NULL) OR "
            "(status = 'RETIRED' AND retired_at IS NOT NULL)",
            name="ck_agent_packages_status_retired_at",
        ),
    )
    for name, columns in (
        ("ix_agent_packages_namespace_id", ["namespace_id"]),
        ("ix_agent_packages_agent_asset_id", ["agent_asset_id"]),
        ("ix_agent_packages_status", ["status"]),
        ("ix_agent_packages_created_by_user_id", ["created_by_user_id"]),
        ("ix_agent_packages_ns_status_created", ["namespace_id", "status", "created_at"]),
    ):
        op.create_index(name, "agent_packages", columns)

    op.create_table(
        "package_signing_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("key_id", sa.String(length=128), nullable=False),
        sa.Column(
            "algorithm",
            sa.Enum("ED25519", name="package_signature_algorithm"),
            nullable=False,
        ),
        sa.Column("public_key_pem", sa.Text(), nullable=False),
        sa.Column("public_key_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "REVOKED", name="package_signing_key_status"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("revoked_by_user_id", sa.Integer()),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key("namespace_id", "namespaces.id", "fk_package_signing_keys_namespace"),
        _foreign_key("created_by_user_id", "users.id", "fk_package_signing_keys_creator"),
        _foreign_key("revoked_by_user_id", "users.id", "fk_package_signing_keys_revoker"),
        sa.UniqueConstraint("public_id", name="uq_package_signing_keys_public_id"),
        sa.UniqueConstraint("namespace_id", "key_id", name="uq_package_signing_keys_ns_key_id"),
        sa.UniqueConstraint(
            "namespace_id",
            "public_key_fingerprint",
            name="uq_package_signing_keys_ns_fingerprint",
        ),
        sa.CheckConstraint(
            "(status = 'ACTIVE' AND revoked_at IS NULL AND revoked_by_user_id IS NULL) OR "
            "(status = 'REVOKED' AND revoked_at IS NOT NULL AND revoked_by_user_id IS NOT NULL)",
            name="ck_package_signing_keys_status_revoked",
        ),
    )
    for name, columns in (
        ("ix_package_signing_keys_namespace_id", ["namespace_id"]),
        ("ix_package_signing_keys_public_key_fingerprint", ["public_key_fingerprint"]),
        ("ix_package_signing_keys_status", ["status"]),
        ("ix_package_signing_keys_created_by_user_id", ["created_by_user_id"]),
        ("ix_package_signing_keys_revoked_by_user_id", ["revoked_by_user_id"]),
        (
            "ix_package_signing_keys_ns_status_created",
            ["namespace_id", "status", "created_at"],
        ),
    ):
        op.create_index(name, "package_signing_keys", columns)

    op.create_table(
        "agent_package_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("package_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.Enum("VERIFIED", name="agent_package_version_status"),
            nullable=False,
        ),
        sa.Column("manifest_schema_version", sa.String(length=16), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("graph_digest", sa.String(length=64), nullable=False),
        sa.Column("evaluation_policy_id", sa.String(length=255), nullable=False),
        sa.Column("source_repository", sa.String(length=1000), nullable=False),
        sa.Column("source_revision", sa.String(length=64), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("builder_id", sa.String(length=255), nullable=False),
        sa.Column("build_id", sa.String(length=255), nullable=False),
        sa.Column("provenance_digest", sa.String(length=64), nullable=False),
        sa.Column("signing_key_id", sa.Integer(), nullable=False),
        sa.Column(
            "signature_algorithm",
            sa.Enum("ED25519", name="agent_package_signature_algorithm"),
            nullable=False,
        ),
        sa.Column("signature_value", sa.String(length=512), nullable=False),
        sa.Column("signature_digest", sa.String(length=64), nullable=False),
        sa.Column("signature_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key("namespace_id", "namespaces.id", "fk_agent_package_versions_namespace"),
        _foreign_key("package_id", "agent_packages.id", "fk_agent_package_versions_package"),
        _foreign_key("signing_key_id", "package_signing_keys.id", "fk_agent_package_versions_key"),
        _foreign_key("created_by_user_id", "users.id", "fk_agent_package_versions_creator"),
        sa.UniqueConstraint("public_id", name="uq_agent_package_versions_public_id"),
        sa.UniqueConstraint(
            "package_id", "version", name="uq_agent_package_versions_package_version"
        ),
        sa.UniqueConstraint(
            "namespace_id", "idempotency_key", name="uq_agent_package_versions_ns_idempotency"
        ),
        sa.UniqueConstraint(
            "namespace_id", "manifest_digest", name="uq_agent_package_versions_ns_manifest_digest"
        ),
    )
    for name, columns in (
        ("ix_agent_package_versions_namespace_id", ["namespace_id"]),
        ("ix_agent_package_versions_package_id", ["package_id"]),
        ("ix_agent_package_versions_status", ["status"]),
        ("ix_agent_package_versions_signing_key_id", ["signing_key_id"]),
        ("ix_agent_package_versions_created_by_user_id", ["created_by_user_id"]),
        ("ix_agent_package_versions_ns_created", ["namespace_id", "created_at"]),
        ("ix_agent_package_versions_package_created", ["package_id", "created_at"]),
        ("ix_agent_package_versions_manifest_digest", ["manifest_digest"]),
        ("ix_agent_package_versions_provenance_digest", ["provenance_digest"]),
    ):
        op.create_index(name, "agent_package_versions", columns)

    op.create_table(
        "package_components",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("package_version_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("component_ref", sa.String(length=600), nullable=False),
        sa.Column(
            "component_type",
            sa.Enum(
                "SKILL",
                "PROMPT",
                "TOOL",
                "CONFIG",
                "MEMORY_SCHEMA",
                "RUNTIME_BUNDLE",
                name="package_component_type",
            ),
            nullable=False,
        ),
        sa.Column("asset_ref", sa.String(length=255), nullable=False),
        sa.Column("version_ref", sa.String(length=255), nullable=False),
        sa.Column("uri", sa.String(length=1500), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("media_type", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "package_version_id",
            "agent_package_versions.id",
            "fk_package_components_version",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "package_version_id", "component_ref", name="uq_package_components_version_ref"
        ),
        sa.UniqueConstraint(
            "package_version_id", "position", name="uq_package_components_version_position"
        ),
    )
    for name, columns in (
        ("ix_package_components_package_version_id", ["package_version_id"]),
        ("ix_package_components_component_type", ["component_type"]),
        ("ix_package_components_version_type", ["package_version_id", "component_type"]),
        ("ix_package_components_digest", ["sha256"]),
    ):
        op.create_index(name, "package_components", columns)

    op.create_table(
        "package_dependencies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("package_version_id", sa.Integer(), nullable=False),
        sa.Column("from_component_id", sa.Integer(), nullable=False),
        sa.Column("to_component_id", sa.Integer(), nullable=False),
        sa.Column(
            "relationship",
            sa.Enum("DEPENDS_ON", "USES", "CONTAINS", name="package_dependency_relationship"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "package_version_id",
            "agent_package_versions.id",
            "fk_package_dependencies_version",
            ondelete="CASCADE",
        ),
        _foreign_key(
            "from_component_id",
            "package_components.id",
            "fk_package_dependencies_from",
            ondelete="CASCADE",
        ),
        _foreign_key(
            "to_component_id",
            "package_components.id",
            "fk_package_dependencies_to",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "package_version_id",
            "from_component_id",
            "to_component_id",
            "relationship",
            name="uq_package_dependencies_exact_edge",
        ),
        sa.CheckConstraint(
            "from_component_id <> to_component_id", name="ck_package_dependencies_no_self"
        ),
    )
    for name, columns in (
        ("ix_package_dependencies_package_version_id", ["package_version_id"]),
        ("ix_package_dependencies_from_component_id", ["from_component_id"]),
        ("ix_package_dependencies_to_component_id", ["to_component_id"]),
        ("ix_package_dependencies_version_relation", ["package_version_id", "relationship"]),
    ):
        op.create_index(name, "package_dependencies", columns)

    op.create_table(
        "package_sboms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("package_version_id", sa.Integer(), nullable=False),
        sa.Column(
            "format",
            sa.Enum("CYCLONEDX_JSON", "SPDX_JSON", name="package_sbom_format"),
            nullable=False,
        ),
        sa.Column("spec_version", sa.String(length=32), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("document_sha256", sa.String(length=64), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("component_count", sa.Integer(), nullable=False),
        sa.Column("stored_at", sa.DateTime(timezone=True), nullable=False),
        _foreign_key(
            "package_version_id",
            "agent_package_versions.id",
            "fk_package_sboms_version",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("public_id", name="uq_package_sboms_public_id"),
        sa.UniqueConstraint("package_version_id", name="uq_package_sboms_package_version"),
        sa.UniqueConstraint("object_key", name="uq_package_sboms_object_key"),
    )
    op.create_index("ix_package_sboms_package_version_id", "package_sboms", ["package_version_id"])
    op.create_index("ix_package_sboms_document_digest", "package_sboms", ["document_sha256"])

    op.add_column("agent_deployments", sa.Column("package_version_id", sa.Integer()))
    op.create_foreign_key(
        "fk_agent_deployments_package_version",
        "agent_deployments",
        "agent_package_versions",
        ["package_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_agent_deployments_package_version_id", "agent_deployments", ["package_version_id"]
    )


def downgrade() -> None:
    raise RuntimeError(
        "0057 downgrade refused: signed PackageVersion, SBOM and provenance records are immutable release evidence"
    )
