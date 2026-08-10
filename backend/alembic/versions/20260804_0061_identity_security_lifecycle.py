"""Add directory lifecycle, workload identity and signing-key rotation evidence.

Revision ID: 20260804_0061
Revises: 20260804_0060
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260804_0061"
down_revision: str | None = "20260804_0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "identity_links",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "identity_links", sa.Column("disabled_at", sa.DateTime(timezone=True))
    )
    op.create_index("ix_identity_links_is_active", "identity_links", ["is_active"])
    op.create_index("ix_identity_links_disabled_at", "identity_links", ["disabled_at"])

    op.create_table(
        "directory_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("token_prefix", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=255), nullable=False),
        sa.Column("scopes_json", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_by_user_id", sa.Integer()),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["sso_provider_configs.id"],
            name="fk_directory_credentials_provider",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"],
            ["users.id"],
            name="fk_directory_credentials_revoker",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_directory_credentials_creator",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("public_id", name="uq_directory_credentials_public_id"),
        sa.UniqueConstraint("token_prefix", name="uq_directory_credentials_token_prefix"),
        sa.CheckConstraint(
            "(is_active = 1 AND revoked_at IS NULL) OR "
            "(is_active = 0 AND revoked_at IS NOT NULL)",
            name="ck_directory_credentials_active_revoked",
        ),
    )
    for name, columns in (
        ("ix_directory_credentials_provider_id", ["provider_id"]),
        ("ix_directory_credentials_is_active", ["is_active"]),
        ("ix_directory_credentials_expires_at", ["expires_at"]),
        ("ix_directory_credentials_last_used_at", ["last_used_at"]),
        ("ix_directory_credentials_revoked_at", ["revoked_at"]),
        ("ix_directory_credentials_revoked_by_user_id", ["revoked_by_user_id"]),
        ("ix_directory_credentials_created_by_user_id", ["created_by_user_id"]),
        (
            "ix_directory_credentials_provider_active_created",
            ["provider_id", "is_active", "created_at"],
        ),
    ):
        op.create_index(name, "directory_credentials", columns)

    op.create_table(
        "directory_lifecycle_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("credential_id", sa.Integer()),
        sa.Column("external_event_id", sa.String(length=128), nullable=False),
        sa.Column("subject_digest", sa.String(length=64), nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "action",
            sa.Enum("UPSERT", "DISABLE", "REENABLE", name="directory_lifecycle_action"),
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.Enum("SCIM", "STAGING", name="directory_lifecycle_source"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("user_was_active", sa.Boolean(), nullable=False),
        sa.Column("credentials_revoked", sa.Integer(), nullable=False),
        sa.Column("runtime_tokens_revoked", sa.Integer(), nullable=False),
        sa.Column("memberships_removed", sa.Integer(), nullable=False),
        sa.Column("role_bindings_removed", sa.Integer(), nullable=False),
        sa.Column("handover_case_ids_json", sa.JSON(), nullable=False),
        sa.Column("outcome_digest", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["sso_provider_configs.id"],
            name="fk_directory_lifecycle_events_provider",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["directory_credentials.id"],
            name="fk_directory_lifecycle_events_credential",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_directory_lifecycle_events_user",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("public_id", name="uq_directory_lifecycle_events_public_id"),
        sa.UniqueConstraint(
            "provider_id",
            "external_event_id",
            name="uq_directory_lifecycle_events_provider_event",
        ),
    )
    for name, columns in (
        ("ix_directory_lifecycle_events_provider_id", ["provider_id"]),
        ("ix_directory_lifecycle_events_credential_id", ["credential_id"]),
        ("ix_directory_lifecycle_events_subject_digest", ["subject_digest"]),
        ("ix_directory_lifecycle_events_action", ["action"]),
        ("ix_directory_lifecycle_events_source", ["source"]),
        ("ix_directory_lifecycle_events_user_id", ["user_id"]),
        (
            "ix_directory_lifecycle_events_user_occurred",
            ["user_id", "occurred_at"],
        ),
    ):
        op.create_index(name, "directory_lifecycle_events", columns)

    op.add_column(
        "reporter_credentials", sa.Column("public_id", sa.String(length=40))
    )
    op.execute(
        sa.text(
            "UPDATE reporter_credentials "
            "SET public_id = CONCAT('wid_legacy_', LPAD(id, 20, '0')) "
            "WHERE public_id IS NULL"
        )
    )
    op.alter_column(
        "reporter_credentials", "public_id", existing_type=sa.String(length=40), nullable=False
    )
    op.create_unique_constraint(
        "uq_reporter_credentials_public_id", "reporter_credentials", ["public_id"]
    )
    op.add_column(
        "reporter_credentials",
        sa.Column(
            "principal_kind",
            sa.Enum("DEVICE", "SERVICE", name="workload_identity_kind"),
            nullable=False,
            server_default="DEVICE",
        ),
    )
    op.execute(
        sa.text(
            "UPDATE reporter_credentials SET principal_kind = 'SERVICE' "
            "WHERE CAST(scopes AS CHAR) LIKE '%release.receipt%'"
        )
    )
    op.add_column(
        "reporter_credentials",
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index(
        "ix_reporter_credentials_principal_kind",
        "reporter_credentials",
        ["principal_kind"],
    )

    op.add_column("package_signing_keys", sa.Column("rotated_from_id", sa.Integer()))
    op.add_column(
        "package_signing_keys",
        sa.Column("rotation_sequence", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_foreign_key(
        "fk_package_signing_keys_rotated_from",
        "package_signing_keys",
        "package_signing_keys",
        ["rotated_from_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_package_signing_keys_rotated_from", "package_signing_keys", ["rotated_from_id"]
    )
    op.create_index(
        "ix_package_signing_keys_rotated_from_id",
        "package_signing_keys",
        ["rotated_from_id"],
    )


def downgrade() -> None:
    raise RuntimeError(
        "0061 downgrade refused: directory lifecycle, credential revocation and "
        "signing-key rotation are immutable security evidence"
    )
