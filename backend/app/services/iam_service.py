from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.iam import Permission, Role, RoleBinding, RolePermission, RoleScope
from app.models.namespace import NamespaceRole
from app.models.user import SystemRole, User


BUILTIN_PERMISSIONS: list[tuple[str, RoleScope, str]] = [
    ("iam.manage", RoleScope.SYSTEM, "Manage IAM models, role bindings, and SSO settings."),
    ("users.manage", RoleScope.SYSTEM, "Manage enterprise users and directory attributes."),
    ("audit.read", RoleScope.SYSTEM, "Read global audit logs."),
    ("org.read", RoleScope.ORG, "Read organization hierarchy and affiliations."),
    ("org.manage", RoleScope.ORG, "Manage organization hierarchy and user affiliations."),
    ("sso.manage", RoleScope.SYSTEM, "Manage SSO and directory provider configuration."),
    ("runtime.read", RoleScope.SYSTEM, "Read Agent runtime connections."),
    ("runtime.manage", RoleScope.SYSTEM, "Manage Agent runtime connections and synchronization."),
    ("asset.read", RoleScope.SYSTEM, "Read enterprise AI asset catalog."),
    ("asset.manage", RoleScope.SYSTEM, "Manage enterprise AI assets and ownership."),
    ("worktrace.read", RoleScope.SYSTEM, "Read AI Agent work trace summaries."),
    ("worktrace.content.read", RoleScope.SYSTEM, "Read sensitive AI Agent work trace content."),
    ("handover.read", RoleScope.SYSTEM, "Read AI asset handover cases."),
    ("handover.manage", RoleScope.SYSTEM, "Create, approve, and execute AI asset handovers."),
    ("evidence.read", RoleScope.SYSTEM, "Read AI asset evidence summaries."),
    ("evidence.sensitive.read", RoleScope.SYSTEM, "Read sensitive AI asset evidence."),
    ("namespace.read", RoleScope.NAMESPACE, "Read namespace data."),
    ("namespace.write", RoleScope.NAMESPACE, "Modify namespace content."),
    ("namespace.admin", RoleScope.NAMESPACE, "Administer namespace settings and memberships."),
    ("namespace.members.manage", RoleScope.NAMESPACE, "Manage namespace members."),
    ("namespace.lifecycle.manage", RoleScope.NAMESPACE, "Manage lifecycle and quotas."),
    ("namespace.integrations.manage", RoleScope.NAMESPACE, "Manage webhooks, robots, replication, and integrations."),
    ("namespace.scan.read", RoleScope.NAMESPACE, "Read namespace scan results."),
    ("namespace.scan.run", RoleScope.NAMESPACE, "Trigger namespace scans."),
    ("namespace.clinic.read", RoleScope.NAMESPACE, "Read Clinic evaluations and reports."),
    ("namespace.clinic.run", RoleScope.NAMESPACE, "Trigger Clinic evaluations."),
]

BUILTIN_ROLES: dict[str, dict[str, object]] = {
    "enterprise-admin": {
        "name": "Enterprise Admin",
        "scope": RoleScope.SYSTEM,
        "description": "Full enterprise IAM and governance administration.",
        "is_system": True,
        "permissions": [
            "iam.manage",
            "users.manage",
            "audit.read",
            "org.read",
            "org.manage",
            "sso.manage",
            "runtime.read",
            "runtime.manage",
            "asset.read",
            "asset.manage",
            "worktrace.read",
            "worktrace.content.read",
            "handover.read",
            "handover.manage",
            "evidence.read",
            "evidence.sensitive.read",
        ],
    },
    "org-manager": {
        "name": "Org Manager",
        "scope": RoleScope.ORG,
        "description": "Manage organization units and affiliations.",
        "is_system": True,
        "permissions": ["org.read", "org.manage"],
    },
    "namespace-admin": {
        "name": "Namespace Admin",
        "scope": RoleScope.NAMESPACE,
        "description": "Full namespace administration.",
        "is_system": True,
        "permissions": [
            "namespace.read",
            "namespace.write",
            "namespace.admin",
            "namespace.members.manage",
            "namespace.lifecycle.manage",
            "namespace.integrations.manage",
            "namespace.scan.read",
            "namespace.scan.run",
            "namespace.clinic.read",
            "namespace.clinic.run",
        ],
    },
    "namespace-developer": {
        "name": "Namespace Developer",
        "scope": RoleScope.NAMESPACE,
        "description": "Create and modify skill content in a namespace.",
        "is_system": True,
        "permissions": [
            "namespace.read",
            "namespace.write",
            "namespace.scan.read",
            "namespace.scan.run",
            "namespace.clinic.read",
            "namespace.clinic.run",
        ],
    },
    "namespace-readonly": {
        "name": "Namespace Readonly",
        "scope": RoleScope.NAMESPACE,
        "description": "Read-only access to namespace content.",
        "is_system": True,
        "permissions": [
            "namespace.read",
            "namespace.scan.read",
            "namespace.clinic.read",
        ],
    },
}

LEGACY_NAMESPACE_ROLE_PERMISSIONS: dict[NamespaceRole, set[str]] = {
    NamespaceRole.ADMIN: {
        "namespace.read",
        "namespace.write",
        "namespace.admin",
        "namespace.members.manage",
        "namespace.lifecycle.manage",
        "namespace.integrations.manage",
        "namespace.scan.read",
        "namespace.scan.run",
        "namespace.clinic.read",
        "namespace.clinic.run",
    },
    NamespaceRole.DEVELOPER: {
        "namespace.read",
        "namespace.write",
        "namespace.scan.read",
        "namespace.scan.run",
        "namespace.clinic.read",
        "namespace.clinic.run",
    },
    NamespaceRole.READONLY: {
        "namespace.read",
        "namespace.scan.read",
        "namespace.clinic.read",
    },
}


def _role_keys_from_binding(binding: RoleBinding) -> set[str]:
    if binding.role is None:
        return set()
    return {
        row.permission.key
        for row in binding.role.permissions
        if row.permission is not None
    }


async def ensure_builtin_rbac(db: AsyncSession) -> None:
    permission_rows = {
        row.key: row
        for row in (
            await db.execute(select(Permission).where(Permission.key.in_([key for key, _, _ in BUILTIN_PERMISSIONS])))
        ).scalars().all()
    }
    for key, scope, description in BUILTIN_PERMISSIONS:
        if key not in permission_rows:
            row = Permission(key=key, scope=scope, description=description)
            db.add(row)
            await db.flush()
            permission_rows[key] = row

    role_rows = {
        row.key: row
        for row in (
            await db.execute(
                select(Role)
                .where(Role.key.in_(list(BUILTIN_ROLES.keys())))
                .options(selectinload(Role.permissions))
            )
        ).scalars().all()
    }
    for role_key, spec in BUILTIN_ROLES.items():
        role = role_rows.get(role_key)
        if role is None:
            role = Role(
                key=role_key,
                name=str(spec["name"]),
                scope=spec["scope"],
                description=str(spec["description"]),
                is_system=bool(spec["is_system"]),
            )
            db.add(role)
            await db.flush()
            role_rows[role_key] = role
        else:
            role.name = str(spec["name"])
            role.scope = spec["scope"]
            role.description = str(spec["description"])
            role.is_system = bool(spec["is_system"])

        existing_permission_ids = {
            row.permission_id
            for row in (
                await db.execute(select(RolePermission).where(RolePermission.role_id == role.id))
            ).scalars().all()
        }
        desired_ids = {permission_rows[key].id for key in spec["permissions"]}
        missing_ids = desired_ids - existing_permission_ids
        extra_rows = (
            await db.execute(select(RolePermission).where(RolePermission.role_id == role.id))
        ).scalars().all()
        for row in extra_rows:
            if row.permission_id not in desired_ids and role.is_system:
                await db.delete(row)
        for permission_id in missing_ids:
            db.add(RolePermission(role_id=role.id, permission_id=permission_id))

    await db.flush()


async def effective_permissions(
    db: AsyncSession,
    *,
    user: User,
    namespace_id: int | None = None,
    org_unit_id: int | None = None,
) -> set[str]:
    if user.system_role == SystemRole.ADMIN:
        return {key for key, _, _ in BUILTIN_PERMISSIONS}

    now = datetime.now(timezone.utc)
    permissions: set[str] = set()

    if namespace_id is not None:
        from app.models.namespace import NamespaceMember

        legacy_role = (
            await db.execute(
                select(NamespaceMember.role).where(
                    NamespaceMember.namespace_id == namespace_id,
                    NamespaceMember.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if legacy_role is not None:
            permissions.update(LEGACY_NAMESPACE_ROLE_PERMISSIONS.get(legacy_role, set()))

    binding_query = (
        select(RoleBinding)
        .where(RoleBinding.user_id == user.id)
        .where((RoleBinding.expires_at.is_(None)) | (RoleBinding.expires_at > now))
        .options(selectinload(RoleBinding.role).selectinload(Role.permissions).selectinload(RolePermission.permission))
    )
    if namespace_id is not None:
        binding_query = binding_query.where((RoleBinding.namespace_id.is_(None)) | (RoleBinding.namespace_id == namespace_id))
    else:
        binding_query = binding_query.where(RoleBinding.namespace_id.is_(None))
    if org_unit_id is not None:
        binding_query = binding_query.where((RoleBinding.org_unit_id.is_(None)) | (RoleBinding.org_unit_id == org_unit_id))
    else:
        binding_query = binding_query.where(RoleBinding.org_unit_id.is_(None))

    bindings = (await db.execute(binding_query)).scalars().all()
    for binding in bindings:
        if binding.role is None:
            continue
        permissions.update(
            row.permission.key
            for row in binding.role.permissions
            if row.permission is not None
        )
    return permissions


async def has_permission(
    db: AsyncSession,
    *,
    user: User,
    permission_key: str,
    namespace_id: int | None = None,
    org_unit_id: int | None = None,
) -> bool:
    permissions = await effective_permissions(
        db,
        user=user,
        namespace_id=namespace_id,
        org_unit_id=org_unit_id,
    )
    return permission_key in permissions


async def namespace_access_map(
    db: AsyncSession,
    *,
    user: User,
) -> dict[int, NamespaceRole]:
    if user.system_role == SystemRole.ADMIN:
        return {}

    now = datetime.now(timezone.utc)
    rows = (
        await db.execute(
            select(RoleBinding)
            .where(RoleBinding.user_id == user.id, RoleBinding.namespace_id.is_not(None))
            .where((RoleBinding.expires_at.is_(None)) | (RoleBinding.expires_at > now))
            .options(selectinload(RoleBinding.role).selectinload(Role.permissions).selectinload(RolePermission.permission))
        )
    ).scalars().all()
    result: dict[int, NamespaceRole] = {}
    for row in rows:
        namespace_id = row.namespace_id
        if namespace_id is None:
            continue
        permission_keys = _role_keys_from_binding(row)
        if "namespace.admin" in permission_keys:
            next_role = NamespaceRole.ADMIN
        elif "namespace.write" in permission_keys:
            next_role = NamespaceRole.DEVELOPER
        elif "namespace.read" in permission_keys:
            next_role = NamespaceRole.READONLY
        else:
            continue

        current = result.get(namespace_id)
        if current == NamespaceRole.ADMIN:
            continue
        if current == NamespaceRole.DEVELOPER and next_role == NamespaceRole.READONLY:
            continue
        result[namespace_id] = next_role
    return result
