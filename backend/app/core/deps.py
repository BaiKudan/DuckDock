from typing import Annotated
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jwt.exceptions import PyJWTError as JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.security import decode_token, verify_password
from app.core.time import ensure_utc
from app.models.user import User, SystemRole
from app.models.namespace import NamespaceRole
from app.services.iam_service import ensure_builtin_rbac, has_permission

bearer_scheme = HTTPBearer()
optional_bearer_scheme = HTTPBearer(auto_error=False)


async def _authenticate_token(token: str, db: AsyncSession, exc: HTTPException) -> User:
    # Robot token format: dkr_robot_<prefix>_<secret>
    if token.startswith("dkr_robot_"):
        return await _auth_robot(token, db, exc)

    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise exc
        user_id: str = payload.get("sub")
        if not user_id:
            raise exc
    except JWTError:
        raise exc

    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise exc
    return user


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> User:
    token = credentials.credentials
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    return await _authenticate_token(token, db, credentials_exception)


async def get_optional_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(optional_bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> User | None:
    if credentials is None:
        return None
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    return await _authenticate_token(credentials.credentials, db, credentials_exception)


async def _auth_robot(token: str, db: AsyncSession, exc: HTTPException) -> User:
    """Authenticate a robot token and return a non-persistent User-like principal."""
    from app.models.robot import RobotAccount

    # token = "dkr_robot_{prefix}_{secret}"
    parts = token.split("_", 3)   # ["dkr", "robot", prefix, secret]
    if len(parts) != 4:
        raise exc
    prefix, secret = parts[2], parts[3]

    result = await db.execute(
        select(RobotAccount).where(
            RobotAccount.token_prefix == prefix,
            RobotAccount.is_active == True,
        )
    )
    robot = result.scalar_one_or_none()
    if not robot:
        raise exc
    if robot.expires_at and ensure_utc(robot.expires_at) < datetime.now(timezone.utc):
        raise exc
    if not verify_password(secret, robot.token_hash):
        raise exc

    # Update last_used_at (fire-and-forget style, no flush needed here)
    robot.last_used_at = datetime.now(timezone.utc)
    db.add(robot)

    # Return a minimal User object scoped to robot's namespace
    # Endpoints that need namespace checks will use NamespaceMember via robot.namespace_id
    # We inject a virtual user with id=-robot.id to distinguish from real users
    virtual_user = User(
        id=-(robot.id),
        username=f"robot:{robot.name}",
        email="",
        full_name=robot.name,
        hashed_password="",
        is_active=True,
        system_role=SystemRole.USER,
    )
    virtual_user._robot = robot  # type: ignore[attr-defined]
    return virtual_user


def get_robot_account(current_user: User):
    return getattr(current_user, "_robot", None)


async def get_namespace_role(
    current_user: User,
    namespace_id: int,
    db: AsyncSession,
) -> NamespaceRole | None:
    robot = get_robot_account(current_user)
    if robot is not None:
        if robot.namespace_id == namespace_id and robot.is_active:
            return robot.role
        return None

    from app.models.namespace import NamespaceMember

    result = await db.execute(
        select(NamespaceMember.role).where(
            NamespaceMember.namespace_id == namespace_id,
            NamespaceMember.user_id == current_user.id,
        )
    )
    return result.scalar_one_or_none()


async def require_namespace_member(
    current_user: User,
    namespace_id: int,
    db: AsyncSession,
) -> NamespaceRole:
    role = await get_namespace_role(current_user, namespace_id, db)
    if role is not None:
        return role

    await ensure_builtin_rbac(db)
    permissions = {
        "read": await has_permission(db, user=current_user, permission_key="namespace.read", namespace_id=namespace_id),
        "write": await has_permission(db, user=current_user, permission_key="namespace.write", namespace_id=namespace_id),
        "admin": await has_permission(db, user=current_user, permission_key="namespace.admin", namespace_id=namespace_id),
    }
    if permissions["admin"]:
        return NamespaceRole.ADMIN
    if permissions["write"]:
        return NamespaceRole.DEVELOPER
    if permissions["read"]:
        return NamespaceRole.READONLY
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Not a namespace member",
    )


async def require_namespace_writer(
    current_user: User,
    namespace_id: int,
    db: AsyncSession,
) -> NamespaceRole:
    role = await require_namespace_member(current_user, namespace_id, db)
    if role == NamespaceRole.READONLY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Read-only members cannot modify this namespace",
        )
    return role


async def require_namespace_admin(
    current_user: User,
    namespace_id: int,
    db: AsyncSession,
) -> NamespaceRole:
    role = await require_namespace_member(current_user, namespace_id, db)
    if role != NamespaceRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Namespace admin required",
        )
    return role


async def require_namespace_permission(
    current_user: User,
    namespace_id: int,
    db: AsyncSession,
    *,
    permission_key: str,
    allow_legacy_admin: bool = True,
    allowed_legacy_roles: set[NamespaceRole] | None = None,
) -> NamespaceRole | None:
    role = await get_namespace_role(current_user, namespace_id, db)
    if allowed_legacy_roles is None:
        allowed_legacy_roles = {NamespaceRole.ADMIN} if allow_legacy_admin else set()
    if role is not None and role in allowed_legacy_roles:
        return role

    await ensure_builtin_rbac(db)
    if await has_permission(db, user=current_user, permission_key=permission_key, namespace_id=namespace_id):
        return role
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Permission '{permission_key}' required",
    )


async def require_namespace_lifecycle_manager(
    current_user: User,
    namespace_id: int,
    db: AsyncSession,
) -> NamespaceRole | None:
    return await require_namespace_permission(
        current_user,
        namespace_id,
        db,
        permission_key="namespace.lifecycle.manage",
        allowed_legacy_roles={NamespaceRole.ADMIN},
    )


async def require_namespace_integrations_manager(
    current_user: User,
    namespace_id: int,
    db: AsyncSession,
) -> NamespaceRole | None:
    return await require_namespace_permission(
        current_user,
        namespace_id,
        db,
        permission_key="namespace.integrations.manage",
        allowed_legacy_roles={NamespaceRole.ADMIN},
    )


async def require_namespace_scan_reader(
    current_user: User,
    namespace_id: int,
    db: AsyncSession,
) -> NamespaceRole | None:
    return await require_namespace_permission(
        current_user,
        namespace_id,
        db,
        permission_key="namespace.scan.read",
        allowed_legacy_roles={
            NamespaceRole.ADMIN,
            NamespaceRole.DEVELOPER,
            NamespaceRole.READONLY,
        },
    )


async def require_namespace_scan_runner(
    current_user: User,
    namespace_id: int,
    db: AsyncSession,
) -> NamespaceRole | None:
    return await require_namespace_permission(
        current_user,
        namespace_id,
        db,
        permission_key="namespace.scan.run",
        allowed_legacy_roles={NamespaceRole.ADMIN, NamespaceRole.DEVELOPER},
    )


async def require_namespace_clinic_reader(
    current_user: User,
    namespace_id: int,
    db: AsyncSession,
) -> NamespaceRole | None:
    return await require_namespace_permission(
        current_user,
        namespace_id,
        db,
        permission_key="namespace.clinic.read",
        allowed_legacy_roles={
            NamespaceRole.ADMIN,
            NamespaceRole.DEVELOPER,
            NamespaceRole.READONLY,
        },
    )


async def require_namespace_clinic_runner(
    current_user: User,
    namespace_id: int,
    db: AsyncSession,
) -> NamespaceRole | None:
    return await require_namespace_permission(
        current_user,
        namespace_id,
        db,
        permission_key="namespace.clinic.run",
        allowed_legacy_roles={NamespaceRole.ADMIN, NamespaceRole.DEVELOPER},
    )


async def require_admin(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if current_user.system_role != SystemRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_user


async def require_iam_admin(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> User:
    if current_user.system_role == SystemRole.ADMIN:
        return current_user
    await ensure_builtin_rbac(db)
    if not await has_permission(db, user=current_user, permission_key="iam.manage"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="IAM admin privileges required",
    )
    return current_user


async def require_system_permission(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    *,
    permission_key: str,
    error_detail: str,
    alternate_permission_keys: set[str] | None = None,
) -> User:
    if current_user.system_role == SystemRole.ADMIN:
        return current_user
    await ensure_builtin_rbac(db)
    allowed_keys = {permission_key}
    if alternate_permission_keys:
        allowed_keys.update(alternate_permission_keys)
    for key in allowed_keys:
        if await has_permission(db, user=current_user, permission_key=key):
            return current_user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=error_detail,
    )


async def require_users_manager(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> User:
    return await require_system_permission(
        current_user,
        db,
        permission_key="users.manage",
        error_detail="User management privileges required",
        alternate_permission_keys={"iam.manage"},
    )


async def require_org_reader(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> User:
    return await require_system_permission(
        current_user,
        db,
        permission_key="org.read",
        error_detail="Organization read privileges required",
        alternate_permission_keys={"org.manage", "iam.manage"},
    )


async def require_org_manager(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> User:
    return await require_system_permission(
        current_user,
        db,
        permission_key="org.manage",
        error_detail="Organization management privileges required",
        alternate_permission_keys={"iam.manage"},
    )


async def require_sso_manager(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> User:
    return await require_system_permission(
        current_user,
        db,
        permission_key="sso.manage",
        error_detail="SSO management privileges required",
        alternate_permission_keys={"iam.manage"},
    )


async def require_audit_reader(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> User:
    if current_user.system_role == SystemRole.ADMIN:
        return current_user
    await ensure_builtin_rbac(db)
    if await has_permission(db, user=current_user, permission_key="audit.read"):
        return current_user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Audit read privileges required",
    )


async def require_runtime_reader(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> User:
    return await require_system_permission(
        current_user,
        db,
        permission_key="runtime.read",
        error_detail="Runtime read privileges required",
        alternate_permission_keys={"runtime.manage"},
    )


async def require_runtime_manager(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> User:
    return await require_system_permission(
        current_user,
        db,
        permission_key="runtime.manage",
        error_detail="Runtime management privileges required",
    )


async def require_asset_reader(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> User:
    return await require_system_permission(
        current_user,
        db,
        permission_key="asset.read",
        error_detail="Asset read privileges required",
        alternate_permission_keys={"asset.manage"},
    )


async def require_asset_manager(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> User:
    return await require_system_permission(
        current_user,
        db,
        permission_key="asset.manage",
        error_detail="Asset management privileges required",
    )


async def require_worktrace_reader(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> User:
    return await require_system_permission(
        current_user,
        db,
        permission_key="worktrace.read",
        error_detail="Work trace read privileges required",
        alternate_permission_keys={"worktrace.content.read"},
    )


async def require_evidence_reader(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> User:
    return await require_system_permission(
        current_user,
        db,
        permission_key="evidence.read",
        error_detail="Evidence read privileges required",
        alternate_permission_keys={"evidence.sensitive.read"},
    )


async def require_handover_reader(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> User:
    return await require_system_permission(
        current_user,
        db,
        permission_key="handover.read",
        error_detail="Handover read privileges required",
        alternate_permission_keys={"handover.manage"},
    )


async def require_handover_manager(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> User:
    return await require_system_permission(
        current_user,
        db,
        permission_key="handover.manage",
        error_detail="Handover management privileges required",
    )


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalCurrentUser = Annotated[User | None, Depends(get_optional_current_user)]
AdminUser = Annotated[User, Depends(require_admin)]
IamAdminUser = Annotated[User, Depends(require_iam_admin)]
AuditReaderUser = Annotated[User, Depends(require_audit_reader)]
UsersManagerUser = Annotated[User, Depends(require_users_manager)]
OrgReaderUser = Annotated[User, Depends(require_org_reader)]
OrgManagerUser = Annotated[User, Depends(require_org_manager)]
SsoManagerUser = Annotated[User, Depends(require_sso_manager)]
RuntimeReaderUser = Annotated[User, Depends(require_runtime_reader)]
RuntimeManagerUser = Annotated[User, Depends(require_runtime_manager)]
AssetReaderUser = Annotated[User, Depends(require_asset_reader)]
AssetManagerUser = Annotated[User, Depends(require_asset_manager)]
WorktraceReaderUser = Annotated[User, Depends(require_worktrace_reader)]
EvidenceReaderUser = Annotated[User, Depends(require_evidence_reader)]
HandoverReaderUser = Annotated[User, Depends(require_handover_reader)]
HandoverManagerUser = Annotated[User, Depends(require_handover_manager)]
DB = Annotated[AsyncSession, Depends(get_db, scope="function")]
