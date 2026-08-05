"""Add durable metadata state for authenticated Pack/ATIF imports.

Revision ID: 20260730_0036
Revises: 20260730_0035
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260730_0036"
down_revision: str | None = "20260730_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pack_imports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("runtime_id", sa.Integer(), nullable=False),
        sa.Column("reporter_credential_id", sa.Integer(), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=True),
        sa.Column("pack_id", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING_VALIDATION",
                "VALIDATING",
                "IMPORTED",
                "IMPORTED_PARTIAL",
                "QUARANTINED",
                "REJECTED",
                name="pack_import_status",
            ),
            nullable=False,
        ),
        sa.Column("manifest_schema", sa.String(length=100), nullable=False),
        sa.Column("manifest_version", sa.String(length=50), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "producer_adapter_id",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column(
            "producer_adapter_version",
            sa.String(length=50),
            nullable=False,
        ),
        sa.Column(
            "producer_instance_id",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column(
            "external_session_id",
            sa.String(length=256),
            nullable=True,
        ),
        sa.Column(
            "external_run_id",
            sa.String(length=256),
            nullable=True,
        ),
        sa.Column("run_public_id", sa.String(length=36), nullable=True),
        sa.Column(
            "external_deployment_id",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "deployment_revision",
            sa.String(length=128),
            nullable=True,
        ),
        sa.Column("otel_trace_id", sa.String(length=32), nullable=True),
        sa.Column(
            "staging_object_key",
            sa.String(length=512),
            nullable=False,
        ),
        sa.Column(
            "expected_pack_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "actual_pack_sha256",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "expected_size_bytes",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "actual_size_bytes",
            sa.BigInteger(),
            nullable=True,
        ),
        sa.Column("payload_count", sa.Integer(), nullable=False),
        sa.Column(
            "verified_payload_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("loss_reason", sa.String(length=100), nullable=True),
        sa.Column(
            "trust_level",
            sa.String(length=32),
            nullable=False,
            server_default="CHANNEL_AUTHENTICATED",
        ),
        sa.Column(
            "trust_source",
            sa.String(length=16),
            nullable=False,
            server_default="IMPORT",
        ),
        sa.Column(
            "content_capture_mode",
            sa.String(length=32),
            nullable=False,
            server_default="metadata_only",
        ),
        sa.Column(
            "redaction_policy_version",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "redaction_receipt_sha256",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "last_error_code",
            sa.String(length=100),
            nullable=True,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "validated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["namespaces.id"],
            name="fk_pack_imports_namespace_id_namespaces",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_id"],
            ["runtime_instances.id"],
            name="fk_pack_imports_runtime_id_runtime_instances",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reporter_credential_id"],
            ["reporter_credentials.id"],
            name=(
                "fk_pack_imports_reporter_credential_id_"
                "reporter_credentials"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            name="fk_pack_imports_agent_run_id_agent_runs",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("public_id", name="uq_pack_imports_public_id"),
        sa.UniqueConstraint(
            "namespace_id",
            "runtime_id",
            "pack_id",
            name="uq_pack_imports_tenant_runtime_pack",
        ),
        sa.UniqueConstraint(
            "staging_object_key",
            name="uq_pack_imports_staging_object_key",
        ),
        sa.CheckConstraint(
            "expected_size_bytes > 0 AND "
            "(actual_size_bytes IS NULL OR actual_size_bytes > 0)",
            name="ck_pack_imports_positive_sizes",
        ),
        sa.CheckConstraint(
            "payload_count >= 1 AND verified_payload_count >= 0 "
            "AND verified_payload_count <= payload_count",
            name="ck_pack_imports_payload_counts",
        ),
        sa.CheckConstraint(
            "("
            "status = 'IMPORTED' AND agent_run_id IS NOT NULL "
            "AND verified_payload_count = payload_count "
            "AND loss_reason IS NULL AND last_error_code IS NULL"
            ") OR ("
            "status = 'IMPORTED_PARTIAL' AND agent_run_id IS NOT NULL "
            "AND verified_payload_count = payload_count "
            "AND loss_reason IS NOT NULL AND last_error_code IS NULL"
            ") OR ("
            "status IN ('PENDING_VALIDATION', 'VALIDATING') "
            "AND verified_payload_count = 0"
            ") OR ("
            "status IN ('QUARANTINED', 'REJECTED') "
            "AND last_error_code IS NOT NULL"
            ")",
            name="ck_pack_imports_status_state",
        ),
    )
    op.create_index(
        "ix_pack_imports_namespace_id",
        "pack_imports",
        ["namespace_id"],
    )
    op.create_index(
        "ix_pack_imports_runtime_id",
        "pack_imports",
        ["runtime_id"],
    )
    op.create_index(
        "ix_pack_imports_reporter_credential_id",
        "pack_imports",
        ["reporter_credential_id"],
    )
    op.create_index(
        "ix_pack_imports_agent_run_id",
        "pack_imports",
        ["agent_run_id"],
    )
    op.create_index(
        "ix_pack_imports_status",
        "pack_imports",
        ["status"],
    )
    op.create_index(
        "ix_pack_imports_expires_at",
        "pack_imports",
        ["expires_at"],
    )
    op.create_index(
        "ix_pack_imports_namespace_runtime_status",
        "pack_imports",
        ["namespace_id", "runtime_id", "status"],
    )

    op.create_table(
        "pack_import_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pack_import_id", sa.Integer(), nullable=False),
        sa.Column("agent_run_artifact_id", sa.Integer(), nullable=False),
        sa.Column("payload_path", sa.String(length=512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["pack_import_id"],
            ["pack_imports.id"],
            name=(
                "fk_pack_import_artifacts_pack_import_id_pack_imports"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_artifact_id"],
            ["agent_run_artifacts.id"],
            name=(
                "fk_pack_import_artifacts_artifact_id_"
                "agent_run_artifacts"
            ),
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "pack_import_id",
            "payload_path",
            name="uq_pack_import_artifacts_import_path",
        ),
    )
    op.create_index(
        "ix_pack_import_artifacts_pack_import_id",
        "pack_import_artifacts",
        ["pack_import_id"],
    )
    op.create_index(
        "ix_pack_import_artifacts_agent_run_artifact_id",
        "pack_import_artifacts",
        ["agent_run_artifact_id"],
    )
    # Defaults only make the initial DDL/backfill safe. Runtime writes must
    # provide these governance values explicitly, matching ORM metadata.
    op.alter_column(
        "pack_imports",
        "verified_payload_count",
        existing_type=sa.Integer(),
        server_default=None,
    )
    op.alter_column(
        "pack_imports",
        "trust_level",
        existing_type=sa.String(length=32),
        server_default=None,
    )
    op.alter_column(
        "pack_imports",
        "trust_source",
        existing_type=sa.String(length=16),
        server_default=None,
    )
    op.alter_column(
        "pack_imports",
        "content_capture_mode",
        existing_type=sa.String(length=32),
        server_default=None,
    )


def downgrade() -> None:
    # Dropping the tables also removes their indexes. MySQL will not allow a
    # foreign-key backing index to be dropped independently first.
    op.drop_table("pack_import_artifacts")
    op.drop_table("pack_imports")
