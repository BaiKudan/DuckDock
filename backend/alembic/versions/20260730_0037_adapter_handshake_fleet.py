"""Add opaque Runtime IDs and dynamic adapter handshake state.

Revision ID: 20260730_0037
Revises: 20260730_0036
Create Date: 2026-07-30
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260730_0037"
down_revision: str | None = "20260730_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runtime_instances",
        sa.Column("public_id", sa.String(length=35), nullable=True),
    )
    runtime_table = sa.table(
        "runtime_instances",
        sa.column("id", sa.Integer()),
        sa.column("public_id", sa.String(length=35)),
    )
    connection = op.get_bind()
    runtime_ids = connection.execute(
        sa.select(runtime_table.c.id).order_by(runtime_table.c.id)
    ).scalars()
    for runtime_id in runtime_ids:
        connection.execute(
            runtime_table.update()
            .where(runtime_table.c.id == runtime_id)
            .values(public_id=f"rt_{uuid.uuid4().hex}")
        )
    op.alter_column(
        "runtime_instances",
        "public_id",
        existing_type=sa.String(length=35),
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_runtime_instances_public_id",
        "runtime_instances",
        ["public_id"],
    )

    op.create_table(
        "adapter_handshakes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=35), nullable=False),
        sa.Column("namespace_id", sa.Integer(), nullable=False),
        sa.Column("runtime_id", sa.Integer(), nullable=False),
        sa.Column("reporter_credential_id", sa.Integer(), nullable=False),
        sa.Column(
            "profile",
            sa.Enum(
                "openclaw-reporter",
                "generic-otlp-bridge",
                "pack-atif-import",
                name="adapter_profile",
            ),
            nullable=False,
        ),
        sa.Column("adapter_id", sa.String(length=100), nullable=False),
        sa.Column("adapter_version", sa.String(length=50), nullable=False),
        sa.Column("protocol_version", sa.String(length=16), nullable=False),
        sa.Column("source_schema", sa.String(length=100), nullable=False),
        sa.Column(
            "source_schema_version",
            sa.String(length=50),
            nullable=False,
        ),
        sa.Column("instance_id", sa.String(length=128), nullable=False),
        sa.Column("boot_id", sa.String(length=128), nullable=False),
        sa.Column(
            "client_nonce_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "descriptor_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "config_fingerprint",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "claimed_capability_level",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "accepted_capabilities_json",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "rejected_capabilities_json",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "content_capture_mode",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "DEGRADED",
                "EXPIRED",
                "SUPERSEDED",
                name="adapter_handshake_status",
            ),
            nullable=False,
        ),
        sa.Column(
            "config_drift",
            sa.Enum(
                "NONE",
                "CONFIG_CHANGED",
                "BOOT_CHANGED",
                "CAPABILITY_CHANGED",
                name="adapter_config_drift",
            ),
            nullable=False,
        ),
        sa.Column(
            "heartbeat_status",
            sa.String(length=32),
            nullable=True,
        ),
        sa.Column(
            "collector_status",
            sa.String(length=32),
            nullable=True,
        ),
        sa.Column(
            "collector_version",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "client_time",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "handshaken_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "last_heartbeat_at",
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
            name="fk_adapter_handshakes_namespace_id_namespaces",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_id"],
            ["runtime_instances.id"],
            name=(
                "fk_adapter_handshakes_runtime_id_runtime_instances"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reporter_credential_id"],
            ["reporter_credentials.id"],
            name="fk_adapter_handshakes_reporter_credential",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "public_id",
            name="uq_adapter_handshakes_public_id",
        ),
        sa.UniqueConstraint(
            "reporter_credential_id",
            "instance_id",
            "client_nonce_sha256",
            name="uq_adapter_handshakes_credential_instance_nonce",
        ),
        sa.CheckConstraint(
            "expires_at > handshaken_at",
            name="ck_adapter_handshakes_expiry",
        ),
    )
    for column in (
        "namespace_id",
        "runtime_id",
        "reporter_credential_id",
        "profile",
        "status",
        "config_drift",
        "expires_at",
        "last_heartbeat_at",
    ):
        op.create_index(
            f"ix_adapter_handshakes_{column}",
            "adapter_handshakes",
            [column],
        )
    op.create_index(
        "ix_adapter_handshakes_namespace_runtime_status",
        "adapter_handshakes",
        ["namespace_id", "runtime_id", "status"],
    )
    op.create_index(
        "ix_adapter_handshakes_runtime_profile_handshaken",
        "adapter_handshakes",
        ["runtime_id", "profile", "handshaken_at"],
    )

    op.create_table(
        "adapter_heartbeat_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("adapter_handshake_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "config_drift",
            sa.Enum(
                "NONE",
                "CONFIG_CHANGED",
                "BOOT_CHANGED",
                "CAPABILITY_CHANGED",
                name="adapter_config_drift",
            ),
            nullable=False,
        ),
        sa.Column(
            "collector_status",
            sa.String(length=32),
            nullable=True,
        ),
        sa.Column(
            "collector_version",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["adapter_handshake_id"],
            ["adapter_handshakes.id"],
            name=(
                "fk_adapter_heartbeat_records_handshake_"
                "adapter_handshakes"
            ),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_adapter_heartbeat_records_adapter_handshake_id",
        "adapter_heartbeat_records",
        ["adapter_handshake_id"],
    )
    op.create_index(
        "ix_adapter_heartbeat_records_observed_at",
        "adapter_heartbeat_records",
        ["observed_at"],
    )
    op.create_index(
        "ix_adapter_heartbeat_records_handshake_observed",
        "adapter_heartbeat_records",
        ["adapter_handshake_id", "observed_at"],
    )

    op.add_column(
        "pack_imports",
        sa.Column(
            "adapter_handshake_id",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_pack_imports_adapter_handshake_id_adapter_handshakes",
        "pack_imports",
        "adapter_handshakes",
        ["adapter_handshake_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_pack_imports_adapter_handshake_id",
        "pack_imports",
        ["adapter_handshake_id"],
    )


def downgrade() -> None:
    with op.batch_alter_table("pack_imports") as batch:
        batch.drop_constraint(
            "fk_pack_imports_adapter_handshake_id_adapter_handshakes",
            type_="foreignkey",
        )
        batch.drop_index("ix_pack_imports_adapter_handshake_id")
        batch.drop_column("adapter_handshake_id")
    op.drop_table("adapter_heartbeat_records")
    op.drop_table("adapter_handshakes")
    with op.batch_alter_table("runtime_instances") as batch:
        batch.drop_constraint(
            "uq_runtime_instances_public_id",
            type_="unique",
        )
        batch.drop_column("public_id")
