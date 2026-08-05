from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.iam import EmploymentStatus, OrgUnitType, RoleScope, SSOProviderType
from app.models.user import AuthSource, SystemRole


class OrgUnitCreate(BaseModel):
    name: str
    code: str | None = None
    unit_type: OrgUnitType
    parent_id: int | None = None
    description: str | None = None
    legal_entity: str | None = None
    region: str | None = None
    cost_center: str | None = None
    is_active: bool = True


class OrgUnitUpdate(BaseModel):
    name: str | None = None
    code: str | None = None
    parent_id: int | None = None
    description: str | None = None
    legal_entity: str | None = None
    region: str | None = None
    cost_center: str | None = None
    is_active: bool | None = None


class OrgUnitOut(BaseModel):
    id: int
    name: str
    code: str | None = None
    unit_type: OrgUnitType
    parent_id: int | None = None
    description: str | None = None
    path: str
    legal_entity: str | None = None
    region: str | None = None
    cost_center: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OrgUnitTreeNode(OrgUnitOut):
    children: list["OrgUnitTreeNode"] = Field(default_factory=list)


class UserAffiliationCreate(BaseModel):
    org_unit_id: int
    title: str | None = None
    employee_no: str | None = None
    manager_user_id: int | None = None
    is_primary: bool = False
    employment_status: EmploymentStatus = EmploymentStatus.ACTIVE
    joined_at: datetime | None = None
    left_at: datetime | None = None
    cost_center_override: str | None = None


class UserAffiliationUpdate(BaseModel):
    title: str | None = None
    employee_no: str | None = None
    manager_user_id: int | None = None
    is_primary: bool | None = None
    employment_status: EmploymentStatus | None = None
    joined_at: datetime | None = None
    left_at: datetime | None = None
    cost_center_override: str | None = None


class UserAffiliationOut(BaseModel):
    id: int
    user_id: int
    org_unit_id: int
    title: str | None = None
    employee_no: str | None = None
    manager_user_id: int | None = None
    is_primary: bool
    employment_status: EmploymentStatus
    joined_at: datetime | None = None
    left_at: datetime | None = None
    cost_center_override: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PersonHandoverProfileUpdate(BaseModel):
    position_title: str | None = Field(default=None, max_length=128)
    employee_no: str | None = Field(default=None, max_length=64)
    manager_user_id: int | None = None
    handover_receiver_user_id: int | None = None
    employment_status: EmploymentStatus | None = None
    note: str | None = Field(default=None, max_length=1000)


class PersonHandoverProfileOut(BaseModel):
    id: int
    enterprise_uid: str | None = None
    username: str
    email: str
    full_name: str | None = None
    system_role: SystemRole
    auth_source: AuthSource
    is_active: bool
    last_login_at: datetime | None = None
    position_title: str | None = None
    employee_no: str | None = None
    manager_user_id: int | None = None
    manager_username: str | None = None
    handover_receiver_user_id: int | None = None
    handover_receiver_username: str | None = None
    employment_status: EmploymentStatus
    note: str | None = None
    profile_updated_at: datetime | None = None
    asset_count: int = 0
    work_trace_count: int = 0
    handover_count: int = 0
    offboarding_visible: bool = False


class PermissionOut(BaseModel):
    id: int
    key: str
    scope: RoleScope
    description: str | None = None

    model_config = ConfigDict(from_attributes=True)


class RoleCreate(BaseModel):
    key: str
    name: str
    scope: RoleScope
    description: str | None = None


class RoleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class RoleOut(BaseModel):
    id: int
    key: str
    name: str
    scope: RoleScope
    description: str | None = None
    is_system: bool
    created_at: datetime
    permissions: list[str] = Field(default_factory=list)


class RolePermissionUpdate(BaseModel):
    permission_keys: list[str]


class RoleBindingCreate(BaseModel):
    role_id: int
    user_id: int
    namespace_id: int | None = None
    org_unit_id: int | None = None
    expires_at: datetime | None = None


class RoleBindingOut(BaseModel):
    id: int
    role_id: int
    user_id: int
    namespace_id: int | None = None
    org_unit_id: int | None = None
    granted_by: int | None = None
    expires_at: datetime | None = None
    created_at: datetime
    permission_keys: list[str] = Field(default_factory=list)


class IdentityLinkOut(BaseModel):
    id: int
    user_id: int
    provider_id: int
    source: AuthSource
    issuer: str | None = None
    external_subject: str
    external_uid: str | None = None
    username: str | None = None
    email: str | None = None
    full_name: str | None = None
    claims_json: dict | None = None
    is_active: bool
    disabled_at: datetime | None = None
    last_seen_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SSORoleMappingCreate(BaseModel):
    provider_id: int
    claim_name: str = Field(default="groups", max_length=128)
    claim_value: str = Field(max_length=255)
    role_id: int
    namespace_id: int | None = None
    org_unit_id: int | None = None
    enabled: bool = True
    priority: int = 100
    description: str | None = None


class SSORoleMappingUpdate(BaseModel):
    claim_name: str | None = Field(default=None, max_length=128)
    claim_value: str | None = Field(default=None, max_length=255)
    role_id: int | None = None
    namespace_id: int | None = None
    org_unit_id: int | None = None
    enabled: bool | None = None
    priority: int | None = None
    description: str | None = None


class SSORoleMappingOut(BaseModel):
    id: int
    provider_id: int
    claim_name: str
    claim_value: str
    role_id: int
    namespace_id: int | None = None
    org_unit_id: int | None = None
    enabled: bool
    priority: int
    description: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SSOProviderConfigCreate(BaseModel):
    provider_type: SSOProviderType
    name: str
    enabled: bool = False
    issuer_url: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    ldap_server_url: str | None = None
    ldap_bind_dn: str | None = None
    ldap_bind_password: str | None = None
    ldap_user_search_base: str | None = None
    ldap_user_search_filter: str | None = None
    ldap_group_search_base: str | None = None
    attribute_mapping: dict | None = None
    extra_config: dict | None = None


class SSOProviderConfigUpdate(BaseModel):
    enabled: bool | None = None
    issuer_url: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    ldap_server_url: str | None = None
    ldap_bind_dn: str | None = None
    ldap_bind_password: str | None = None
    ldap_user_search_base: str | None = None
    ldap_user_search_filter: str | None = None
    ldap_group_search_base: str | None = None
    attribute_mapping: dict | None = None
    extra_config: dict | None = None


class SSOProviderConfigOut(BaseModel):
    id: int
    provider_type: SSOProviderType
    name: str
    enabled: bool
    issuer_url: str | None = None
    client_id: str | None = None
    ldap_server_url: str | None = None
    ldap_bind_dn: str | None = None
    ldap_user_search_base: str | None = None
    ldap_user_search_filter: str | None = None
    ldap_group_search_base: str | None = None
    attribute_mapping: dict | None = None
    extra_config: dict | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EffectivePermissionOut(BaseModel):
    permission_keys: list[str]


OrgUnitTreeNode.model_rebuild()
