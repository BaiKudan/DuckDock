from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.control_plane import AIAsset
    from app.models.namespace import Namespace
    from app.models.user import User


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentPackageStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class PackageSigningKeyStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class PackageSignatureAlgorithm(str, enum.Enum):
    ED25519 = "ED25519"


class AgentPackageVersionStatus(str, enum.Enum):
    VERIFIED = "VERIFIED"


class PackageComponentType(str, enum.Enum):
    SKILL = "skill"
    PROMPT = "prompt"
    TOOL = "tool"
    CONFIG = "config"
    MEMORY_SCHEMA = "memory_schema"
    RUNTIME_BUNDLE = "runtime_bundle"


class PackageDependencyRelationship(str, enum.Enum):
    DEPENDS_ON = "depends_on"
    USES = "uses"
    CONTAINS = "contains"


class PackageSbomFormat(str, enum.Enum):
    CYCLONEDX_JSON = "cyclonedx-json"
    SPDX_JSON = "spdx-json"


class AgentPackage(Base):
    """Namespace-scoped identity for append-only Agent Package versions."""

    __tablename__ = "agent_packages"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_agent_packages_public_id"),
        UniqueConstraint("namespace_id", "name", name="uq_agent_packages_ns_name"),
        CheckConstraint(
            "(status = 'ACTIVE' AND retired_at IS NULL) OR "
            "(status = 'RETIRED' AND retired_at IS NOT NULL)",
            name="ck_agent_packages_status_retired_at",
        ),
        Index("ix_agent_packages_ns_status_created", "namespace_id", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    namespace_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    agent_asset_id: Mapped[int] = mapped_column(
        ForeignKey("ai_assets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    agent_asset_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[AgentPackageStatus] = mapped_column(
        Enum(AgentPackageStatus, name="agent_package_status"),
        nullable=False,
        default=AgentPackageStatus.ACTIVE,
        index=True,
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    namespace: Mapped["Namespace"] = relationship()
    agent_asset: Mapped["AIAsset"] = relationship()
    created_by_user: Mapped["User"] = relationship()
    versions: Mapped[list["AgentPackageVersion"]] = relationship(
        back_populates="package", order_by="AgentPackageVersion.id", lazy="selectin"
    )


class PackageSigningKey(Base):
    """Trusted Namespace Ed25519 public key. Private material is never stored."""

    __tablename__ = "package_signing_keys"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_package_signing_keys_public_id"),
        UniqueConstraint("namespace_id", "key_id", name="uq_package_signing_keys_ns_key_id"),
        UniqueConstraint(
            "namespace_id", "public_key_fingerprint", name="uq_package_signing_keys_ns_fingerprint"
        ),
        UniqueConstraint("rotated_from_id", name="uq_package_signing_keys_rotated_from"),
        CheckConstraint(
            "(status = 'ACTIVE' AND revoked_at IS NULL AND revoked_by_user_id IS NULL) OR "
            "(status = 'REVOKED' AND revoked_at IS NOT NULL AND revoked_by_user_id IS NOT NULL)",
            name="ck_package_signing_keys_status_revoked",
        ),
        Index(
            "ix_package_signing_keys_ns_status_created", "namespace_id", "status", "created_at"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    key_id: Mapped[str] = mapped_column(String(128), nullable=False)
    algorithm: Mapped[PackageSignatureAlgorithm] = mapped_column(
        Enum(PackageSignatureAlgorithm, name="package_signature_algorithm"), nullable=False
    )
    public_key_pem: Mapped[str] = mapped_column(Text, nullable=False)
    public_key_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[PackageSigningKeyStatus] = mapped_column(
        Enum(PackageSigningKeyStatus, name="package_signing_key_status"),
        nullable=False,
        default=PackageSigningKeyStatus.ACTIVE,
        index=True,
    )
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    revoked_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rotated_from_id: Mapped[int | None] = mapped_column(
        ForeignKey("package_signing_keys.id", ondelete="RESTRICT"), index=True
    )
    rotation_sequence: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    namespace: Mapped["Namespace"] = relationship()
    created_by_user: Mapped["User"] = relationship(foreign_keys=[created_by_user_id])
    revoked_by_user: Mapped["User | None"] = relationship(foreign_keys=[revoked_by_user_id])
    rotated_from: Mapped["PackageSigningKey | None"] = relationship(
        remote_side="PackageSigningKey.id", foreign_keys=[rotated_from_id]
    )


class AgentPackageVersion(Base):
    """One immutable, signed and SBOM-backed Agent Package manifest."""

    __tablename__ = "agent_package_versions"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_agent_package_versions_public_id"),
        UniqueConstraint("package_id", "version", name="uq_agent_package_versions_package_version"),
        UniqueConstraint(
            "namespace_id", "idempotency_key", name="uq_agent_package_versions_ns_idempotency"
        ),
        UniqueConstraint(
            "namespace_id", "manifest_digest", name="uq_agent_package_versions_ns_manifest_digest"
        ),
        Index("ix_agent_package_versions_ns_created", "namespace_id", "created_at"),
        Index("ix_agent_package_versions_package_created", "package_id", "created_at"),
        Index("ix_agent_package_versions_manifest_digest", "manifest_digest"),
        Index("ix_agent_package_versions_provenance_digest", "provenance_digest"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    namespace_id: Mapped[int] = mapped_column(
        ForeignKey("namespaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    package_id: Mapped[int] = mapped_column(
        ForeignKey("agent_packages.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[AgentPackageVersionStatus] = mapped_column(
        Enum(AgentPackageVersionStatus, name="agent_package_version_status"),
        nullable=False,
        default=AgentPackageVersionStatus.VERIFIED,
        index=True,
    )
    manifest_schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    manifest_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    graph_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluation_policy_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_repository: Mapped[str] = mapped_column(String(1000), nullable=False)
    source_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    builder_id: Mapped[str] = mapped_column(String(255), nullable=False)
    build_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provenance_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    signing_key_id: Mapped[int] = mapped_column(
        ForeignKey("package_signing_keys.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    signature_algorithm: Mapped[PackageSignatureAlgorithm] = mapped_column(
        Enum(PackageSignatureAlgorithm, name="agent_package_signature_algorithm"), nullable=False
    )
    signature_value: Mapped[str] = mapped_column(String(512), nullable=False)
    signature_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    package: Mapped[AgentPackage] = relationship(back_populates="versions")
    signing_key: Mapped[PackageSigningKey] = relationship(lazy="selectin")
    created_by_user: Mapped["User"] = relationship()
    components: Mapped[list["PackageComponent"]] = relationship(
        back_populates="package_version",
        cascade="all, delete-orphan",
        order_by="PackageComponent.position",
        lazy="selectin",
    )
    dependencies: Mapped[list["PackageDependency"]] = relationship(
        back_populates="package_version",
        cascade="all, delete-orphan",
        order_by="PackageDependency.id",
        foreign_keys="PackageDependency.package_version_id",
        lazy="selectin",
    )
    sbom: Mapped["PackageSbom"] = relationship(
        back_populates="package_version", cascade="all, delete-orphan", uselist=False, lazy="selectin"
    )


class PackageComponent(Base):
    __tablename__ = "package_components"
    __table_args__ = (
        UniqueConstraint(
            "package_version_id", "component_ref", name="uq_package_components_version_ref"
        ),
        UniqueConstraint(
            "package_version_id", "position", name="uq_package_components_version_position"
        ),
        Index("ix_package_components_version_type", "package_version_id", "component_type"),
        Index("ix_package_components_digest", "sha256"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    package_version_id: Mapped[int] = mapped_column(
        ForeignKey("agent_package_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    component_ref: Mapped[str] = mapped_column(String(600), nullable=False)
    component_type: Mapped[PackageComponentType] = mapped_column(
        Enum(PackageComponentType, name="package_component_type"), nullable=False, index=True
    )
    asset_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    version_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    uri: Mapped[str] = mapped_column(String(1500), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    media_type: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    package_version: Mapped[AgentPackageVersion] = relationship(back_populates="components")


class PackageDependency(Base):
    __tablename__ = "package_dependencies"
    __table_args__ = (
        UniqueConstraint(
            "package_version_id",
            "from_component_id",
            "to_component_id",
            "relationship",
            name="uq_package_dependencies_exact_edge",
        ),
        CheckConstraint("from_component_id <> to_component_id", name="ck_package_dependencies_no_self"),
        Index("ix_package_dependencies_version_relation", "package_version_id", "relationship"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    package_version_id: Mapped[int] = mapped_column(
        ForeignKey("agent_package_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_component_id: Mapped[int] = mapped_column(
        ForeignKey("package_components.id", ondelete="CASCADE"), nullable=False, index=True
    )
    to_component_id: Mapped[int] = mapped_column(
        ForeignKey("package_components.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relationship_kind: Mapped[PackageDependencyRelationship] = mapped_column(
        "relationship",
        Enum(PackageDependencyRelationship, name="package_dependency_relationship"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    package_version: Mapped[AgentPackageVersion] = relationship(
        back_populates="dependencies", foreign_keys=[package_version_id]
    )
    from_component: Mapped[PackageComponent] = relationship(foreign_keys=[from_component_id], lazy="joined")
    to_component: Mapped[PackageComponent] = relationship(foreign_keys=[to_component_id], lazy="joined")


class PackageSbom(Base):
    __tablename__ = "package_sboms"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_package_sboms_public_id"),
        UniqueConstraint("package_version_id", name="uq_package_sboms_package_version"),
        UniqueConstraint("object_key", name="uq_package_sboms_object_key"),
        Index("ix_package_sboms_document_digest", "document_sha256"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    package_version_id: Mapped[int] = mapped_column(
        ForeignKey("agent_package_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    format: Mapped[PackageSbomFormat] = mapped_column(
        Enum(PackageSbomFormat, name="package_sbom_format"), nullable=False
    )
    spec_version: Mapped[str] = mapped_column(String(32), nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    component_count: Mapped[int] = mapped_column(Integer, nullable=False)
    stored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    package_version: Mapped[AgentPackageVersion] = relationship(back_populates="sbom")
