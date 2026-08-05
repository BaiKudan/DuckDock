"""Add the native Hermes Reporter adapter profile.

Revision ID: 20260730_0039
Revises: 20260730_0038
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260730_0039"
down_revision: str | None = "20260730_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_PROFILES = (
    "openclaw-reporter",
    "generic-otlp-bridge",
    "pack-atif-import",
)
NEW_PROFILES = (
    "openclaw-reporter",
    "hermes-reporter",
    "generic-otlp-bridge",
    "pack-atif-import",
)


def _profile_enum(values: tuple[str, ...]) -> sa.Enum:
    return sa.Enum(*values, name="adapter_profile")


def upgrade() -> None:
    op.alter_column(
        "adapter_handshakes",
        "profile",
        existing_type=_profile_enum(OLD_PROFILES),
        type_=_profile_enum(NEW_PROFILES),
        existing_nullable=False,
    )
    # Existing pilot builds temporarily negotiated as openclaw-reporter. Rename
    # only the Hermes adapter rows so the first native heartbeat supersedes the
    # same profile/instance instead of leaving two active identities.
    op.get_bind().execute(
        sa.text(
            "UPDATE adapter_handshakes "
            "SET profile = 'hermes-reporter' "
            "WHERE profile = 'openclaw-reporter' "
            "AND adapter_id = 'hermes-reporter-pilot'"
        )
    )


def downgrade() -> None:
    # Preserve Hermes rows under the pre-0039 compatible online Reporter
    # profile before narrowing the MySQL ENUM.
    op.get_bind().execute(
        sa.text(
            "UPDATE adapter_handshakes "
            "SET profile = 'openclaw-reporter' "
            "WHERE profile = 'hermes-reporter'"
        )
    )
    op.alter_column(
        "adapter_handshakes",
        "profile",
        existing_type=_profile_enum(NEW_PROFILES),
        type_=_profile_enum(OLD_PROFILES),
        existing_nullable=False,
    )
