from fastapi import APIRouter, HTTPException
from sqlalchemy import or_, select

from app.core.deps import DB, CurrentUser, get_robot_account
from app.models.audit import AuditLog
from app.models.namespace import Namespace, NamespaceMember
from app.schemas.audit import AuditLogOut
from app.services.iam_service import has_permission, namespace_access_map

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/logs", response_model=list[AuditLogOut])
async def list_audit_logs(
    db: DB,
    current_user: CurrentUser,
    namespace: str | None = None,
    action: str | None = None,
    limit: int = 100,
):
    limit = max(1, min(limit, 200))
    robot = get_robot_account(current_user)

    query = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)

    if namespace:
        namespace_result = await db.execute(select(Namespace).where(Namespace.name == namespace))
        namespace_obj = namespace_result.scalar_one_or_none()
        if not namespace_obj:
            raise HTTPException(404, "Namespace not found")
        if robot:
            if robot.namespace_id != namespace_obj.id:
                raise HTTPException(403, "Access denied")
        else:
            membership = (
                await db.execute(
                    select(NamespaceMember.id).where(
                        NamespaceMember.namespace_id == namespace_obj.id,
                        NamespaceMember.user_id == current_user.id,
                    )
                )
            ).scalar_one_or_none()
            can_read_audit = await has_permission(
                db,
                user=current_user,
                permission_key="audit.read",
            )
            namespace_grant = namespace_obj.id in (await namespace_access_map(db, user=current_user))
            if not membership and not can_read_audit and not namespace_grant:
                raise HTTPException(403, "Access denied")
        query = query.where(AuditLog.namespace_id == namespace_obj.id)
    elif robot:
        query = query.where(AuditLog.namespace_id == robot.namespace_id)
    else:
        namespace_ids = set(
            (
                await db.execute(
                    select(NamespaceMember.namespace_id).where(
                        NamespaceMember.user_id == current_user.id
                    )
                )
            ).scalars().all()
        )
        namespace_ids.update((await namespace_access_map(db, user=current_user)).keys())
        if await has_permission(db, user=current_user, permission_key="audit.read"):
            pass
        elif namespace_ids:
            query = query.where(
                or_(AuditLog.namespace_id.in_(sorted(namespace_ids)), AuditLog.user_id == current_user.id)
            )
        else:
            query = query.where(AuditLog.user_id == current_user.id)

    if action:
        query = query.where(AuditLog.action == action)

    result = await db.execute(query)
    return result.scalars().all()
