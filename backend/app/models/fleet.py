from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AdapterProfile(str, enum.Enum):
    OPENCLAW_REPORTER = "openclaw-reporter"
    HERMES_REPORTER = "hermes-reporter"
    GENERIC_OTLP_BRIDGE = "generic-otlp-bridge"
    PACK_ATIF_IMPORT = "pack-atif-import"


class AdapterHandshakeStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"


class AdapterConfigDrift(str, enum.Enum):
    NONE = "NONE"
    CONFIG_CHANGED = "CONFIG_CHANGED"
    BOOT_CHANGED = "BOOT_CHANGED"
    CAPABILITY_CHANGED = "CAPABILITY_CHANGED"


class AdapterHandshake(Base):
    """One credential-derived, replay-safe adapter capability negotiation."""

    __tablename__ = "adapter_handshakes"
    __table_args__ = (
        UniqueConstraint(
            "public_id",
            name="uq_adapter_handshakes_public_id",
        ),
        UniqueConstraint(
            "reporter_credential_id",
            "instance_id",
            "client_nonce_sha256",
            name="uq_adapter_handshakes_credential_instance_nonce",
        ),
        CheckConstraint(
            "expires_at > handshaken_at",
            name="ck_adapter_handshakes_expiry",
        ),
        Index(
            "ix_adapter_handshakes_namespace_runtime_status",
            "namespace_id",
            "runtime_id",
            "status",
        ),
        Index(
            "ix_adapter_handshakes_runtime_profile_handshaken",
            "runtime_id",
            "profile",
            "handshaken_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(35), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    runtime_id: Mapped[int] = mapped_column(
        ForeignKey("runtime_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reporter_credential_id: Mapped[int] = mapped_column(
        ForeignKey("reporter_credentials.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    profile: Mapped[AdapterProfile] = mapped_column(
        Enum(
            AdapterProfile,
            name="adapter_profile",
            values_callable=lambda members: [
                member.value for member in members
            ],
        ),
        nullable=False,
        index=True,
    )
    adapter_id: Mapped[str] = mapped_column(String(100), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(50), nullable=False)
    protocol_version: Mapped[str] = mapped_column(String(16), nullable=False)
    source_schema: Mapped[str] = mapped_column(String(100), nullable=False)
    source_schema_version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    instance_id: Mapped[str] = mapped_column(String(128), nullable=False)
    boot_id: Mapped[str] = mapped_column(String(128), nullable=False)
    client_nonce_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    descriptor_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    config_fingerprint: Mapped[str | None] = mapped_column(String(64))
    claimed_capability_level: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    accepted_capabilities_json: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
    )
    rejected_capabilities_json: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
    )
    content_capture_mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    status: Mapped[AdapterHandshakeStatus] = mapped_column(
        Enum(AdapterHandshakeStatus, name="adapter_handshake_status"),
        nullable=False,
        index=True,
    )
    config_drift: Mapped[AdapterConfigDrift] = mapped_column(
        Enum(AdapterConfigDrift, name="adapter_config_drift"),
        nullable=False,
        index=True,
    )
    heartbeat_status: Mapped[str | None] = mapped_column(String(32))
    collector_status: Mapped[str | None] = mapped_column(String(32))
    collector_version: Mapped[str | None] = mapped_column(String(64))
    client_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    handshaken_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    runtime: Mapped["RuntimeInstance"] = relationship("RuntimeInstance")
    reporter_credential: Mapped["ReporterCredential"] = relationship(
        "ReporterCredential"
    )
    heartbeat_records: Mapped[list["AdapterHeartbeatRecord"]] = relationship(
        "AdapterHeartbeatRecord",
        back_populates="handshake",
        cascade="all, delete-orphan",
    )


class AdapterHeartbeatRecord(Base):
    """Bounded Fleet history source; payload contains operational metadata only."""

    __tablename__ = "adapter_heartbeat_records"
    __table_args__ = (
        Index(
            "ix_adapter_heartbeat_records_handshake_observed",
            "adapter_handshake_id",
            "observed_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    adapter_handshake_id: Mapped[int] = mapped_column(
        ForeignKey("adapter_handshakes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    config_drift: Mapped[AdapterConfigDrift] = mapped_column(
        Enum(AdapterConfigDrift, name="adapter_config_drift"),
        nullable=False,
    )
    collector_status: Mapped[str | None] = mapped_column(String(32))
    collector_version: Mapped[str | None] = mapped_column(String(64))
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    handshake: Mapped[AdapterHandshake] = relationship(
        "AdapterHandshake",
        back_populates="heartbeat_records",
    )


from app.models.control_plane import (  # noqa: E402
    ReporterCredential,
    RuntimeInstance,
)
