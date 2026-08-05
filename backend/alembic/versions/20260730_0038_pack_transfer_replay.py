"""Add resumable Pack transfer, batch receipts and export indexes.

Revision ID: 20260730_0038
Revises: 20260730_0037
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260730_0038"
down_revision: str | None = "20260730_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pack_imports",
        sa.Column(
            "upload_mode",
            sa.Enum("SINGLE_PUT", "MULTIPART", name="pack_upload_mode"),
            nullable=False,
            server_default="SINGLE_PUT",
        ),
    )
    op.alter_column(
        "pack_imports",
        "upload_mode",
        existing_type=sa.Enum(
            "SINGLE_PUT",
            "MULTIPART",
            name="pack_upload_mode",
        ),
        nullable=False,
        server_default=None,
    )
    op.add_column(
        "pack_imports",
        sa.Column("multipart_upload_id", sa.String(length=255)),
    )
    op.add_column(
        "pack_imports",
        sa.Column("multipart_part_size_bytes", sa.Integer()),
    )
    op.add_column(
        "pack_imports",
        sa.Column("multipart_completed_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "pack_batch_streams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("runtime_id", sa.Integer(), nullable=False),
        sa.Column("reporter_credential_id", sa.Integer(), nullable=False),
        sa.Column("stream_key", sa.String(length=128), nullable=False),
        sa.Column(
            "ack_cursor",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["namespaces.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_id"],
            ["runtime_instances.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reporter_credential_id"],
            ["reporter_credentials.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "public_id",
            name="uq_pack_batch_streams_public_id",
        ),
        sa.UniqueConstraint(
            "reporter_credential_id",
            "stream_key",
            name="uq_pack_batch_streams_credential_key",
        ),
        sa.CheckConstraint(
            "ack_cursor >= 0",
            name="ck_pack_batch_streams_nonnegative_cursor",
        ),
    )
    for column in (
        "namespace_id",
        "runtime_id",
        "reporter_credential_id",
    ):
        op.create_index(
            f"ix_pack_batch_streams_{column}",
            "pack_batch_streams",
            [column],
        )
    op.create_index(
        "ix_pack_batch_streams_namespace_runtime_updated",
        "pack_batch_streams",
        ["namespace_id", "runtime_id", "updated_at"],
    )

    op.create_table(
        "pack_batch_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pack_batch_stream_id", sa.Integer(), nullable=False),
        sa.Column("pack_import_id", sa.Integer()),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "disposition",
            sa.Enum(
                "ACCEPTED",
                "REJECTED",
                name="pack_batch_disposition",
            ),
            nullable=False,
        ),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["pack_batch_stream_id"],
            ["pack_batch_streams.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["pack_import_id"],
            ["pack_imports.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "pack_batch_stream_id",
            "sequence",
            name="uq_pack_batch_receipts_stream_sequence",
        ),
        sa.UniqueConstraint(
            "pack_batch_stream_id",
            "idempotency_key",
            name="uq_pack_batch_receipts_stream_key",
        ),
        sa.CheckConstraint(
            "sequence >= 1",
            name="ck_pack_batch_receipts_positive_sequence",
        ),
    )
    op.create_index(
        "ix_pack_batch_receipts_pack_batch_stream_id",
        "pack_batch_receipts",
        ["pack_batch_stream_id"],
    )
    op.create_index(
        "ix_pack_batch_receipts_pack_import_id",
        "pack_batch_receipts",
        ["pack_import_id"],
    )

    op.create_table(
        "pack_exports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("runtime_id", sa.Integer(), nullable=False),
        sa.Column("reporter_credential_id", sa.Integer(), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("pack_id", sa.String(length=128), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("payload_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["namespace_id"],
            ["namespaces.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_id"],
            ["runtime_instances.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reporter_credential_id"],
            ["reporter_credentials.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("public_id", name="uq_pack_exports_public_id"),
        sa.UniqueConstraint(
            "reporter_credential_id",
            "idempotency_key",
            name="uq_pack_exports_credential_key",
        ),
        sa.UniqueConstraint(
            "object_key",
            name="uq_pack_exports_object_key",
        ),
        sa.CheckConstraint(
            "size_bytes > 0 AND payload_count >= 1",
            name="ck_pack_exports_positive_sizes",
        ),
    )
    for column in (
        "namespace_id",
        "runtime_id",
        "reporter_credential_id",
        "agent_run_id",
    ):
        op.create_index(
            f"ix_pack_exports_{column}",
            "pack_exports",
            [column],
        )
    op.create_index(
        "ix_pack_exports_namespace_runtime_created",
        "pack_exports",
        ["namespace_id", "runtime_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("pack_exports")
    op.drop_table("pack_batch_receipts")
    op.drop_table("pack_batch_streams")

    op.drop_column("pack_imports", "multipart_completed_at")
    op.drop_column("pack_imports", "multipart_part_size_bytes")
    op.drop_column("pack_imports", "multipart_upload_id")
    op.drop_column("pack_imports", "upload_mode")
