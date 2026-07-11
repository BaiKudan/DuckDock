from __future__ import annotations

from collections.abc import Sequence

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.deps import (
    CurrentUser,
    DB,
    IamAdminUser,
    OrgManagerUser,
    OrgReaderUser,
    SsoManagerUser,
    UsersManagerUser,
)
from app.models.iam import (
    IdentityLink,
    OrgUnit,
    Permission,
    Role,
    RoleBinding,
    RolePermission,
    RoleScope,
    SSOProviderConfig,
    SSORoleMapping,
    UserAffiliation,
)
from app.models.namespace import Namespace
from app.models.user import User
from app.schemas.iam import (
    EffectivePermissionOut,
    OrgUnitCreate,
    OrgUnitOut,
    OrgUnitTreeNode,
    OrgUnitUpdate,
    PermissionOut,
    RoleBindingCreate,
    RoleBindingOut,
    RoleCreate,
    RoleOut,
    RolePermissionUpdate,
    RoleUpdate,
    IdentityLinkOut,
    SSORoleMappingCreate,
    SSORoleMappingOut,
    SSORoleMappingUpdate,
    SSOProviderConfigCreate,
    SSOProviderConfigOut,
    SSOProviderConfigUpdate,
    UserAffiliationCreate,
    UserAffiliationOut,
    UserAffiliationUpdate,
)
from app.schemas.user import UserOut
from app.services.audit_service import audit
from app.services.iam_service import effective_permissions, ensure_builtin_rbac

router = APIRouter(prefix="/iam", tags=["iam"])


def _org_unit_path(parent: OrgUnit | None, name: str) -> str:
    segment = name.strip().lower().replace(" ", "-")
    base = parent.path.rstrip("/") if parent is not None else ""
    return f"{base}/{segment}" if base else f"/{segment}"


def _role_out(role: Role) -> RoleOut:
    return RoleOut(
        id=role.id,
        key=role.key,
        name=role.name,
        scope=role.scope,
        description=role.description,
        is_system=role.is_system,
        created_at=role.created_at,
        permissions=[
            row.permission.key
            for row in role.permissions
            if row.permission is not None
        ],
    )


def _binding_out(binding: RoleBinding) -> RoleBindingOut:
    permission_keys: list[str] = []
    if binding.permission_cache:
        permission_keys = list(binding.permission_cache.get("keys", []))
    elif binding.role is not None:
        permission_keys = [
            row.permission.key
            for row in binding.role.permissions
            if row.permission is not None
        ]
    return RoleBindingOut(
        id=binding.id,
        role_id=binding.role_id,
        user_id=binding.user_id,
        namespace_id=binding.namespace_id,
        org_unit_id=binding.org_unit_id,
        granted_by=binding.granted_by,
        expires_at=binding.expires_at,
        created_at=binding.created_at,
        permission_keys=permission_keys,
    )


def _validate_binding_scope(role: Role, body: RoleBindingCreate) -> None:
    """Enforce that the binding target matches the role's scope.

    - NAMESPACE-scoped roles MUST be bound with a namespace_id (and no org_unit_id).
    - ORG-scoped roles MUST be bound with an org_unit_id (and no namespace_id).
    - SYSTEM-scoped roles MUST be bound globally (neither namespace_id nor org_unit_id).
    """
    has_namespace = body.namespace_id is not None
    has_org_unit = body.org_unit_id is not None
    if role.scope is RoleScope.NAMESPACE:
        if not has_namespace or has_org_unit:
            raise HTTPException(
                status_code=422,
                detail="A namespace-scoped role must be bound with a namespace_id and no org_unit_id",
            )
    elif role.scope is RoleScope.ORG:
        if not has_org_unit or has_namespace:
            raise HTTPException(
                status_code=422,
                detail="An org-scoped role must be bound with an org_unit_id and no namespace_id",
            )
    else:  # RoleScope.SYSTEM
        if has_namespace or has_org_unit:
            raise HTTPException(
                status_code=422,
                detail="A system-scoped role must be bound globally without a namespace_id or org_unit_id",
            )


def _validate_role_scope_target(role: Role, *, namespace_id: int | None, org_unit_id: int | None) -> None:
    _validate_binding_scope(
        role,
        RoleBindingCreate(
            role_id=role.id,
            user_id=1,
            namespace_id=namespace_id,
            org_unit_id=org_unit_id,
        ),
    )


def _mapping_out(row: SSORoleMapping) -> SSORoleMappingOut:
    return SSORoleMappingOut.model_validate(row)


def _build_org_tree(rows: Sequence[OrgUnit]) -> list[OrgUnitTreeNode]:
    nodes = {
        row.id: OrgUnitTreeNode.model_validate(row, from_attributes=True)
        for row in rows
    }
    roots: list[OrgUnitTreeNode] = []
    for row in rows:
        node = nodes[row.id]
        if row.parent_id and row.parent_id in nodes:
            nodes[row.parent_id].children.append(node)
        else:
            roots.append(node)
    return roots


async def _reload_role(db: DB, role_id: int) -> Role:
    return (
        await db.execute(
            select(Role)
            .where(Role.id == role_id)
            .options(selectinload(Role.permissions).selectinload(RolePermission.permission))
        )
    ).scalar_one()


async def _refresh_descendant_paths(db: DB, parent_id: int) -> None:
    children = (
        await db.execute(select(OrgUnit).where(OrgUnit.parent_id == parent_id))
    ).scalars().all()
    for child in children:
        parent = (await db.execute(select(OrgUnit).where(OrgUnit.id == child.parent_id))).scalar_one_or_none()
        child.path = _org_unit_path(parent, child.name)
        await _refresh_descendant_paths(db, child.id)


@router.post("/bootstrap")
async def bootstrap_iam(db: DB, current_user: IamAdminUser):
    await ensure_builtin_rbac(db)
    await audit(
        db,
        user=current_user,
        action="iam.bootstrap",
        resource_type="iam",
        details={"status": "ok"},
    )
    return {"ok": True}


@router.get("/users", response_model=list[UserOut])
async def list_users(db: DB, current_user: UsersManagerUser):
    result = await db.execute(select(User).order_by(User.created_at.desc(), User.username))
    return result.scalars().all()


@router.get("/users/{user_id}/permissions", response_model=EffectivePermissionOut)
async def get_user_permissions(
    user_id: int,
    db: DB,
    current_user: IamAdminUser,
    namespace_id: int | None = None,
    org_unit_id: int | None = None,
):
    await ensure_builtin_rbac(db)
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    permission_keys = sorted(
        await effective_permissions(
            db,
            user=target,
            namespace_id=namespace_id,
            org_unit_id=org_unit_id,
        )
    )
    return EffectivePermissionOut(permission_keys=permission_keys)


@router.get("/me/permissions", response_model=EffectivePermissionOut)
async def get_my_permissions(
    db: DB,
    current_user: CurrentUser,
    namespace_id: int | None = None,
    org_unit_id: int | None = None,
):
    await ensure_builtin_rbac(db)
    permission_keys = sorted(
        await effective_permissions(
            db,
            user=current_user,
            namespace_id=namespace_id,
            org_unit_id=org_unit_id,
        )
    )
    return EffectivePermissionOut(permission_keys=permission_keys)


@router.get("/org-units", response_model=list[OrgUnitOut])
async def list_org_units(db: DB, current_user: OrgReaderUser):
    result = await db.execute(select(OrgUnit).order_by(OrgUnit.path, OrgUnit.name))
    return result.scalars().all()


@router.get("/org-units/tree", response_model=list[OrgUnitTreeNode])
async def org_unit_tree(db: DB, current_user: OrgReaderUser):
    rows = (await db.execute(select(OrgUnit).order_by(OrgUnit.path, OrgUnit.name))).scalars().all()
    return _build_org_tree(rows)


@router.post("/org-units", response_model=OrgUnitOut, status_code=status.HTTP_201_CREATED)
async def create_org_unit(body: OrgUnitCreate, db: DB, current_user: OrgManagerUser):
    parent = None
    if body.parent_id is not None:
        parent = (await db.execute(select(OrgUnit).where(OrgUnit.id == body.parent_id))).scalar_one_or_none()
        if parent is None:
            raise HTTPException(status_code=404, detail="Parent org unit not found")
    if body.code:
        existing = (await db.execute(select(OrgUnit).where(OrgUnit.code == body.code))).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(status_code=409, detail="Org unit code already exists")
    row = OrgUnit(
        name=body.name,
        code=body.code,
        unit_type=body.unit_type,
        parent_id=body.parent_id,
        description=body.description,
        is_active=body.is_active,
        path=_org_unit_path(parent, body.name),
    )
    db.add(row)
    await db.flush()
    await audit(db, user=current_user, action="iam.org_unit.created", resource_type="org_unit", resource_id=row.id)
    await db.refresh(row)
    return row


@router.patch("/org-units/{org_unit_id}", response_model=OrgUnitOut)
async def update_org_unit(org_unit_id: int, body: OrgUnitUpdate, db: DB, current_user: OrgManagerUser):
    row = (await db.execute(select(OrgUnit).where(OrgUnit.id == org_unit_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Org unit not found")
    parent = None
    if body.parent_id is not None:
        if body.parent_id == row.id:
            raise HTTPException(status_code=409, detail="Org unit cannot be its own parent")
        parent = (await db.execute(select(OrgUnit).where(OrgUnit.id == body.parent_id))).scalar_one_or_none()
        if parent is None:
            raise HTTPException(status_code=404, detail="Parent org unit not found")
        row.parent_id = parent.id
    if body.code is not None:
        existing = (await db.execute(select(OrgUnit).where(OrgUnit.code == body.code, OrgUnit.id != row.id))).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(status_code=409, detail="Org unit code already exists")
        row.code = body.code
    if body.name is not None:
        row.name = body.name
    if body.description is not None:
        row.description = body.description
    if body.is_active is not None:
        row.is_active = body.is_active
    current_parent = parent
    if current_parent is None and row.parent_id is not None:
        current_parent = (await db.execute(select(OrgUnit).where(OrgUnit.id == row.parent_id))).scalar_one_or_none()
    row.path = _org_unit_path(current_parent, row.name)
    await _refresh_descendant_paths(db, row.id)
    await db.flush()
    await audit(db, user=current_user, action="iam.org_unit.updated", resource_type="org_unit", resource_id=row.id)
    await db.refresh(row)
    return row


@router.get("/users/{user_id}/affiliations", response_model=list[UserAffiliationOut])
async def list_user_affiliations(user_id: int, db: DB, current_user: OrgReaderUser):
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    result = await db.execute(select(UserAffiliation).where(UserAffiliation.user_id == user_id).order_by(UserAffiliation.is_primary.desc(), UserAffiliation.id))
    return result.scalars().all()


@router.post("/users/{user_id}/affiliations", response_model=UserAffiliationOut, status_code=status.HTTP_201_CREATED)
async def add_user_affiliation(user_id: int, body: UserAffiliationCreate, db: DB, current_user: OrgManagerUser):
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    org_unit = (await db.execute(select(OrgUnit).where(OrgUnit.id == body.org_unit_id))).scalar_one_or_none()
    if org_unit is None:
        raise HTTPException(status_code=404, detail="Org unit not found")
    existing = (
        await db.execute(
            select(UserAffiliation).where(
                UserAffiliation.user_id == user_id,
                UserAffiliation.org_unit_id == body.org_unit_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Affiliation already exists")
    if body.is_primary:
        primary_rows = (await db.execute(select(UserAffiliation).where(UserAffiliation.user_id == user_id, UserAffiliation.is_primary.is_(True)))).scalars().all()
        for row in primary_rows:
            row.is_primary = False
    row = UserAffiliation(
        user_id=user_id,
        org_unit_id=body.org_unit_id,
        title=body.title,
        employee_no=body.employee_no,
        manager_user_id=body.manager_user_id,
        is_primary=body.is_primary,
        employment_status=body.employment_status,
        joined_at=body.joined_at,
        left_at=body.left_at,
        cost_center_override=body.cost_center_override,
    )
    db.add(row)
    await db.flush()
    await audit(db, user=current_user, action="iam.user_affiliation.created", resource_type="user_affiliation", resource_id=row.id, details={"user_id": user_id, "org_unit_id": body.org_unit_id})
    await db.refresh(row)
    return row


@router.patch("/user-affiliations/{affiliation_id}", response_model=UserAffiliationOut)
async def update_user_affiliation(affiliation_id: int, body: UserAffiliationUpdate, db: DB, current_user: OrgManagerUser):
    row = (await db.execute(select(UserAffiliation).where(UserAffiliation.id == affiliation_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Affiliation not found")
    payload = body.model_dump(exclude_unset=True)
    if payload.get("is_primary") is True:
        primary_rows = (await db.execute(select(UserAffiliation).where(UserAffiliation.user_id == row.user_id, UserAffiliation.is_primary.is_(True), UserAffiliation.id != row.id))).scalars().all()
        for existing in primary_rows:
            existing.is_primary = False
    for key, value in payload.items():
        setattr(row, key, value)
    await db.flush()
    await audit(db, user=current_user, action="iam.user_affiliation.updated", resource_type="user_affiliation", resource_id=row.id)
    await db.refresh(row)
    return row


@router.get("/permissions", response_model=list[PermissionOut])
async def list_permissions(db: DB, current_user: IamAdminUser):
    await ensure_builtin_rbac(db)
    result = await db.execute(select(Permission).order_by(Permission.scope, Permission.key))
    return result.scalars().all()


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(db: DB, current_user: SsoManagerUser):
    await ensure_builtin_rbac(db)
    rows = (
        await db.execute(select(Role).options(selectinload(Role.permissions).selectinload(RolePermission.permission)).order_by(Role.scope, Role.key))
    ).scalars().all()
    return [_role_out(row) for row in rows]


@router.post("/roles", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
async def create_role(body: RoleCreate, db: DB, current_user: IamAdminUser):
    await ensure_builtin_rbac(db)
    existing = (await db.execute(select(Role).where(Role.key == body.key))).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Role key already exists")
    row = Role(
        key=body.key,
        name=body.name,
        scope=body.scope,
        description=body.description,
        is_system=False,
    )
    db.add(row)
    await db.flush()
    await audit(db, user=current_user, action="iam.role.created", resource_type="role", resource_id=row.id, details={"key": row.key})
    return _role_out(await _reload_role(db, row.id))


@router.patch("/roles/{role_id}", response_model=RoleOut)
async def update_role(role_id: int, body: RoleUpdate, db: DB, current_user: IamAdminUser):
    row = (
        await db.execute(
            select(Role).where(Role.id == role_id).options(selectinload(Role.permissions).selectinload(RolePermission.permission))
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Role not found")
    if body.name is not None:
        row.name = body.name
    if body.description is not None:
        row.description = body.description
    await db.flush()
    await audit(db, user=current_user, action="iam.role.updated", resource_type="role", resource_id=row.id)
    return _role_out(await _reload_role(db, row.id))


@router.put("/roles/{role_id}/permissions", response_model=RoleOut)
async def update_role_permissions(role_id: int, body: RolePermissionUpdate, db: DB, current_user: IamAdminUser):
    await ensure_builtin_rbac(db)
    role = (
        await db.execute(
            select(Role).where(Role.id == role_id).options(selectinload(Role.permissions).selectinload(RolePermission.permission))
        )
    ).scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    permissions = (
        await db.execute(select(Permission).where(Permission.key.in_(body.permission_keys)))
    ).scalars().all()
    found = {row.key for row in permissions}
    missing = sorted(set(body.permission_keys) - found)
    if missing:
        raise HTTPException(status_code=404, detail=f"Permissions not found: {', '.join(missing)}")
    existing_rows = (await db.execute(select(RolePermission).where(RolePermission.role_id == role.id))).scalars().all()
    existing_ids = {row.permission_id for row in existing_rows}
    desired_ids = {row.id for row in permissions}
    for row in existing_rows:
        if row.permission_id not in desired_ids:
            await db.delete(row)
    for permission_id in desired_ids - existing_ids:
        db.add(RolePermission(role_id=role.id, permission_id=permission_id))
    await db.flush()
    role = await _reload_role(db, role.id)
    await audit(db, user=current_user, action="iam.role.permissions.updated", resource_type="role", resource_id=role.id)
    return _role_out(role)


@router.get("/bindings", response_model=list[RoleBindingOut])
async def list_bindings(db: DB, current_user: IamAdminUser):
    await ensure_builtin_rbac(db)
    rows = (
        await db.execute(
            select(RoleBinding)
            .options(selectinload(RoleBinding.role).selectinload(Role.permissions).selectinload(RolePermission.permission))
            .order_by(RoleBinding.created_at.desc(), RoleBinding.id.desc())
        )
    ).scalars().all()
    return [_binding_out(row) for row in rows]


@router.post("/bindings", response_model=RoleBindingOut, status_code=status.HTTP_201_CREATED)
async def create_binding(body: RoleBindingCreate, db: DB, current_user: IamAdminUser):
    await ensure_builtin_rbac(db)
    role = (
        await db.execute(
            select(Role).where(Role.id == body.role_id).options(selectinload(Role.permissions).selectinload(RolePermission.permission))
        )
    ).scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    _validate_binding_scope(role, body)
    user = (await db.execute(select(User).where(User.id == body.user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if body.namespace_id is not None:
        namespace = (await db.execute(select(Namespace).where(Namespace.id == body.namespace_id))).scalar_one_or_none()
        if namespace is None:
            raise HTTPException(status_code=404, detail="Namespace not found")
    if body.org_unit_id is not None:
        org = (await db.execute(select(OrgUnit).where(OrgUnit.id == body.org_unit_id))).scalar_one_or_none()
        if org is None:
            raise HTTPException(status_code=404, detail="Org unit not found")
    permission_keys = [row.permission.key for row in role.permissions if row.permission is not None]
    row = RoleBinding(
        role_id=body.role_id,
        user_id=body.user_id,
        namespace_id=body.namespace_id,
        org_unit_id=body.org_unit_id,
        granted_by=current_user.id,
        expires_at=body.expires_at,
        permission_cache={"keys": permission_keys},
    )
    db.add(row)
    await db.flush()
    await audit(
        db,
        user=current_user,
        action="iam.binding.created",
        resource_type="role_binding",
        resource_id=row.id,
        details={"role_id": row.role_id, "user_id": row.user_id},
    )
    await db.refresh(row)
    row.role = role
    return _binding_out(row)


@router.delete("/bindings/{binding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_binding(binding_id: int, db: DB, current_user: IamAdminUser):
    row = (await db.execute(select(RoleBinding).where(RoleBinding.id == binding_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Binding not found")
    await audit(db, user=current_user, action="iam.binding.deleted", resource_type="role_binding", resource_id=row.id)
    await db.delete(row)


@router.get("/sso/providers", response_model=list[SSOProviderConfigOut])
async def list_sso_providers(db: DB, current_user: SsoManagerUser):
    rows = (await db.execute(select(SSOProviderConfig).order_by(SSOProviderConfig.provider_type, SSOProviderConfig.name))).scalars().all()
    return rows


@router.post("/sso/providers", response_model=SSOProviderConfigOut, status_code=status.HTTP_201_CREATED)
async def create_sso_provider(body: SSOProviderConfigCreate, db: DB, current_user: SsoManagerUser):
    existing = (await db.execute(select(SSOProviderConfig).where(SSOProviderConfig.name == body.name))).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="SSO provider name already exists")
    row = SSOProviderConfig(**body.model_dump())
    db.add(row)
    await db.flush()
    await audit(db, user=current_user, action="iam.sso_provider.created", resource_type="sso_provider", resource_id=row.id, details={"name": row.name, "provider_type": row.provider_type.value})
    await db.refresh(row)
    return row


@router.patch("/sso/providers/{provider_id}", response_model=SSOProviderConfigOut)
async def update_sso_provider(provider_id: int, body: SSOProviderConfigUpdate, db: DB, current_user: SsoManagerUser):
    row = (await db.execute(select(SSOProviderConfig).where(SSOProviderConfig.id == provider_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="SSO provider not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    await db.flush()
    await audit(db, user=current_user, action="iam.sso_provider.updated", resource_type="sso_provider", resource_id=row.id, details={"name": row.name})
    await db.refresh(row)
    return row


@router.get("/sso/identity-links", response_model=list[IdentityLinkOut])
async def list_identity_links(db: DB, current_user: SsoManagerUser, user_id: int | None = None, provider_id: int | None = None):
    stmt = select(IdentityLink).order_by(IdentityLink.last_seen_at.desc(), IdentityLink.id.desc())
    if user_id is not None:
        stmt = stmt.where(IdentityLink.user_id == user_id)
    if provider_id is not None:
        stmt = stmt.where(IdentityLink.provider_id == provider_id)
    return (await db.execute(stmt)).scalars().all()


@router.get("/sso/role-mappings", response_model=list[SSORoleMappingOut])
async def list_sso_role_mappings(db: DB, current_user: SsoManagerUser, provider_id: int | None = None):
    stmt = select(SSORoleMapping).order_by(SSORoleMapping.provider_id, SSORoleMapping.priority, SSORoleMapping.id)
    if provider_id is not None:
        stmt = stmt.where(SSORoleMapping.provider_id == provider_id)
    return [_mapping_out(row) for row in (await db.execute(stmt)).scalars().all()]


async def _validate_mapping_references(db: DB, body: SSORoleMappingCreate | SSORoleMappingUpdate, *, existing: SSORoleMapping | None = None) -> Role:
    if isinstance(body, SSORoleMappingCreate):
        provider_id = body.provider_id
        role_id = body.role_id
        namespace_id = body.namespace_id
        org_unit_id = body.org_unit_id
    else:
        if existing is None:
            raise HTTPException(status_code=422, detail="existing mapping is required")
        provider_id = existing.provider_id
        role_id = body.role_id if body.role_id is not None else existing.role_id
        fields_set: set[str] = body.model_fields_set
        namespace_id = body.namespace_id if "namespace_id" in fields_set else existing.namespace_id
        org_unit_id = body.org_unit_id if "org_unit_id" in fields_set else existing.org_unit_id

    provider = (await db.execute(select(SSOProviderConfig).where(SSOProviderConfig.id == provider_id))).scalar_one_or_none()
    if provider is None:
        raise HTTPException(status_code=404, detail="SSO provider not found")

    role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none() if role_id is not None else None
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")

    _validate_role_scope_target(role, namespace_id=namespace_id, org_unit_id=org_unit_id)
    if namespace_id is not None:
        namespace = (await db.execute(select(Namespace).where(Namespace.id == namespace_id))).scalar_one_or_none()
        if namespace is None:
            raise HTTPException(status_code=404, detail="Namespace not found")
    if org_unit_id is not None:
        org = (await db.execute(select(OrgUnit).where(OrgUnit.id == org_unit_id))).scalar_one_or_none()
        if org is None:
            raise HTTPException(status_code=404, detail="Org unit not found")
    return role


@router.post("/sso/role-mappings", response_model=SSORoleMappingOut, status_code=status.HTTP_201_CREATED)
async def create_sso_role_mapping(body: SSORoleMappingCreate, db: DB, current_user: SsoManagerUser):
    await ensure_builtin_rbac(db)
    await _validate_mapping_references(db, body)
    row = SSORoleMapping(**body.model_dump())
    db.add(row)
    await db.flush()
    await audit(
        db,
        user=current_user,
        action="iam.sso_role_mapping.created",
        resource_type="sso_role_mapping",
        resource_id=row.id,
        details={"provider_id": row.provider_id, "claim_name": row.claim_name, "claim_value": row.claim_value},
    )
    await db.refresh(row)
    return _mapping_out(row)


@router.patch("/sso/role-mappings/{mapping_id}", response_model=SSORoleMappingOut)
async def update_sso_role_mapping(mapping_id: int, body: SSORoleMappingUpdate, db: DB, current_user: SsoManagerUser):
    row = (await db.execute(select(SSORoleMapping).where(SSORoleMapping.id == mapping_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="SSO role mapping not found")
    await ensure_builtin_rbac(db)
    await _validate_mapping_references(db, body, existing=row)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    await db.flush()
    await audit(
        db,
        user=current_user,
        action="iam.sso_role_mapping.updated",
        resource_type="sso_role_mapping",
        resource_id=row.id,
        details={"provider_id": row.provider_id, "claim_name": row.claim_name, "claim_value": row.claim_value},
    )
    await db.refresh(row)
    return _mapping_out(row)


@router.delete("/sso/role-mappings/{mapping_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sso_role_mapping(mapping_id: int, db: DB, current_user: SsoManagerUser):
    row = (await db.execute(select(SSORoleMapping).where(SSORoleMapping.id == mapping_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="SSO role mapping not found")
    await db.delete(row)
    await db.flush()
    await audit(db, user=current_user, action="iam.sso_role_mapping.deleted", resource_type="sso_role_mapping", resource_id=mapping_id)
