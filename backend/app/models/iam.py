import enum
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.services.credential_service import decrypt_credential, encrypt_credential
from app.models.user import AuthSource

if TYPE_CHECKING:
    from app.models.user import User


class EncryptedSecret(TypeDecorator):
    """Transparently Fernet-encrypts a secret at rest (L0-SEC-SSO).

    Reuses ``credential_service``'s Fernet helpers (same key/mechanism as runtime
    credentials). Plaintext lives only in the Python attribute; the DB column
    always stores ciphertext. Legacy plaintext rows (written before encryption
    landed) stay readable: on decrypt failure we return the stored value as-is so
    no data migration is required. Such rows are re-encrypted the next time the
    attribute is assigned and persisted.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: ANN001 - SQLAlchemy hook
        if value is None:
            return None
        return encrypt_credential(str(value))

    def process_result_value(self, value, dialect):  # noqa: ANN001 - SQLAlchemy hook
        if value is None:
            return None
        try:
            return decrypt_credential(str(value))
        except Exception:  # noqa: BLE001 - legacy plaintext or InvalidToken
            # Legacy plaintext written before encryption was introduced.
            return value


class OrgUnitType(str, enum.Enum):
    COMPANY = "company"
    SUBSIDIARY = "subsidiary"
    DIVISION = "division"
    DEPARTMENT = "department"
    TEAM = "team"
    BRANCH = "branch"


class RoleScope(str, enum.Enum):
    SYSTEM = "system"
    ORG = "org"
    NAMESPACE = "namespace"


class SSOProviderType(str, enum.Enum):
    OIDC = "oidc"
    LDAP = "ldap"


class EmploymentStatus(str, enum.Enum):
    ACTIVE = "active"
    ONBOARDING = "onboarding"
    LEAVE = "leave"
    OFFBOARDED = "offboarded"


class OrgUnit(Base):
    __tablename__ = "org_units"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    unit_type: Mapped[OrgUnitType] = mapped_column(Enum(OrgUnitType), nullable=False, index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("org_units.id", ondelete="SET NULL"), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    path: Mapped[str] = mapped_column(String(512), nullable=False, default="/")
    legal_entity: Mapped[str | None] = mapped_column(String(128))
    region: Mapped[str | None] = mapped_column(String(64))
    cost_center: Mapped[str | None] = mapped_column(String(64), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    parent: Mapped["OrgUnit | None"] = relationship("OrgUnit", remote_side="OrgUnit.id", back_populates="children")
    children: Mapped[list["OrgUnit"]] = relationship("OrgUnit", back_populates="parent")


class UserAffiliation(Base):
    __tablename__ = "user_affiliations"
    __table_args__ = (UniqueConstraint("user_id", "org_unit_id", name="uq_user_affiliations_user_org"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    org_unit_id: Mapped[int] = mapped_column(ForeignKey("org_units.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(128))
    employee_no: Mapped[str | None] = mapped_column(String(64), index=True)
    manager_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    employment_status: Mapped[EmploymentStatus] = mapped_column(
        Enum(EmploymentStatus),
        default=EmploymentStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cost_center_override: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class UserHandoverProfile(Base):
    __tablename__ = "user_handover_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    position_title: Mapped[str | None] = mapped_column(String(128))
    employee_no: Mapped[str | None] = mapped_column(String(64), index=True)
    manager_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    handover_receiver_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    employment_status: Mapped[EmploymentStatus] = mapped_column(
        Enum(EmploymentStatus),
        default=EmploymentStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    scope: Mapped[RoleScope] = mapped_column(Enum(RoleScope), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    scope: Mapped[RoleScope] = mapped_column(Enum(RoleScope), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    permissions: Mapped[list["RolePermission"]] = relationship(
        "RolePermission",
        back_populates="role",
        cascade="all, delete-orphan",
    )


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "permission_id", name="uq_role_permissions_role_permission"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True)
    permission_id: Mapped[int] = mapped_column(ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False, index=True)

    role: Mapped["Role"] = relationship("Role", back_populates="permissions")
    permission: Mapped["Permission"] = relationship("Permission")


class RoleBinding(Base):
    __tablename__ = "role_bindings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    namespace_id: Mapped[int | None] = mapped_column(ForeignKey("namespaces.id", ondelete="CASCADE"), index=True)
    org_unit_id: Mapped[int | None] = mapped_column(ForeignKey("org_units.id", ondelete="CASCADE"), index=True)
    granted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    role: Mapped["Role"] = relationship("Role")
    permission_cache: Mapped[dict | None] = mapped_column(JSON)


class SSOProviderConfig(Base):
    __tablename__ = "sso_provider_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_type: Mapped[SSOProviderType] = mapped_column(Enum(SSOProviderType), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    issuer_url: Mapped[str | None] = mapped_column(String(512))
    client_id: Mapped[str | None] = mapped_column(String(255))
    client_secret: Mapped[str | None] = mapped_column(EncryptedSecret)
    ldap_server_url: Mapped[str | None] = mapped_column(String(512))
    ldap_bind_dn: Mapped[str | None] = mapped_column(String(512))
    ldap_bind_password: Mapped[str | None] = mapped_column(EncryptedSecret)
    ldap_user_search_base: Mapped[str | None] = mapped_column(String(512))
    ldap_user_search_filter: Mapped[str | None] = mapped_column(String(512))
    ldap_group_search_base: Mapped[str | None] = mapped_column(String(512))
    attribute_mapping: Mapped[dict | None] = mapped_column(JSON)
    extra_config: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class IdentityLink(Base):
    __tablename__ = "identity_links"
    __table_args__ = (
        UniqueConstraint("provider_id", "external_subject", name="uq_identity_links_provider_subject"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("sso_provider_configs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source: Mapped[AuthSource] = mapped_column(Enum(AuthSource), nullable=False, index=True)
    issuer: Mapped[str | None] = mapped_column(String(512))
    external_subject: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    external_uid: Mapped[str | None] = mapped_column(String(128), index=True)
    username: Mapped[str | None] = mapped_column(String(128))
    email: Mapped[str | None] = mapped_column(String(255), index=True)
    full_name: Mapped[str | None] = mapped_column(String(255))
    claims_json: Mapped[dict | None] = mapped_column(JSON)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    user: Mapped["User"] = relationship("User")
    provider: Mapped[SSOProviderConfig] = relationship("SSOProviderConfig")


class SSORoleMapping(Base):
    __tablename__ = "sso_role_mappings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("sso_provider_configs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    claim_name: Mapped[str] = mapped_column(String(128), nullable=False, default="groups", index=True)
    claim_value: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True)
    namespace_id: Mapped[int | None] = mapped_column(ForeignKey("namespaces.id", ondelete="CASCADE"), index=True)
    org_unit_id: Mapped[int | None] = mapped_column(ForeignKey("org_units.id", ondelete="CASCADE"), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    provider: Mapped[SSOProviderConfig] = relationship("SSOProviderConfig")
    role: Mapped[Role] = relationship("Role")
