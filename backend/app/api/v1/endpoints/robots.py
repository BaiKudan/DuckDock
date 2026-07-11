import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.core.deps import DB, CurrentUser, get_robot_account, require_namespace_integrations_manager
from app.core.security import hash_password
from app.models.namespace import Namespace
from app.models.robot import RobotAccount
from app.schemas.robot import RobotCreate, RobotCreated, RobotOut
from app.services.audit_service import audit

router = APIRouter(tags=["robots"])


async def _get_namespace(ns_name: str, db: DB) -> Namespace:
    result = await db.execute(
        select(Namespace).where(Namespace.name == ns_name, Namespace.deleted_at.is_(None))
    )
    namespace = result.scalar_one_or_none()
    if not namespace:
        raise HTTPException(404, "Namespace not found")
    return namespace


@router.post(
    "/namespaces/{ns_name}/robots",
    response_model=RobotCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_robot(ns_name: str, body: RobotCreate, db: DB, current_user: CurrentUser):
    if get_robot_account(current_user):
        raise HTTPException(403, "Robot accounts cannot create robots")

    namespace = await _get_namespace(ns_name, db)
    await require_namespace_integrations_manager(current_user, namespace.id, db)

    duplicate = await db.execute(
        select(RobotAccount).where(
            RobotAccount.namespace_id == namespace.id,
            RobotAccount.name == body.name,
        )
    )
    if duplicate.scalar_one_or_none():
        raise HTTPException(409, "Robot name already exists in this namespace")

    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    raw_token = f"dkr_robot_{prefix}_{secret}"
    expires_at = None
    if body.expires_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_days)

    robot = RobotAccount(
        namespace_id=namespace.id,
        name=body.name,
        description=body.description,
        token_hash=hash_password(secret),
        token_prefix=prefix,
        role=body.role,
        expires_at=expires_at,
        created_by=current_user.id,
    )
    db.add(robot)
    await db.flush()
    await db.refresh(robot)

    await audit(
        db,
        user=current_user,
        action="robot.created",
        resource_type="robot_account",
        resource_id=robot.id,
        namespace_id=namespace.id,
        details={"name": robot.name, "role": robot.role.value},
    )

    return RobotCreated(
        id=robot.id,
        namespace_id=robot.namespace_id,
        name=robot.name,
        description=robot.description,
        token_prefix=robot.token_prefix,
        role=robot.role,
        is_active=robot.is_active,
        expires_at=robot.expires_at,
        last_used_at=robot.last_used_at,
        created_at=robot.created_at,
        token=raw_token,
    )


@router.get("/namespaces/{ns_name}/robots", response_model=list[RobotOut])
async def list_robots(ns_name: str, db: DB, current_user: CurrentUser):
    namespace = await _get_namespace(ns_name, db)
    await require_namespace_integrations_manager(current_user, namespace.id, db)

    result = await db.execute(
        select(RobotAccount)
        .where(RobotAccount.namespace_id == namespace.id)
        .order_by(RobotAccount.name)
    )
    return result.scalars().all()


@router.delete("/namespaces/{ns_name}/robots/{robot_id}", status_code=204)
async def delete_robot(ns_name: str, robot_id: int, db: DB, current_user: CurrentUser):
    if get_robot_account(current_user):
        raise HTTPException(403, "Robot accounts cannot delete robots")

    namespace = await _get_namespace(ns_name, db)
    await require_namespace_integrations_manager(current_user, namespace.id, db)

    result = await db.execute(
        select(RobotAccount).where(
            RobotAccount.id == robot_id,
            RobotAccount.namespace_id == namespace.id,
        )
    )
    robot = result.scalar_one_or_none()
    if not robot:
        raise HTTPException(404, "Robot not found")

    await audit(
        db,
        user=current_user,
        action="robot.deleted",
        resource_type="robot_account",
        resource_id=robot.id,
        namespace_id=namespace.id,
        details={"name": robot.name},
    )
    await db.delete(robot)


@router.patch("/namespaces/{ns_name}/robots/{robot_id}/disable", response_model=RobotOut)
async def disable_robot(ns_name: str, robot_id: int, db: DB, current_user: CurrentUser):
    if get_robot_account(current_user):
        raise HTTPException(403, "Robot accounts cannot disable robots")

    namespace = await _get_namespace(ns_name, db)
    await require_namespace_integrations_manager(current_user, namespace.id, db)

    result = await db.execute(
        select(RobotAccount).where(
            RobotAccount.id == robot_id,
            RobotAccount.namespace_id == namespace.id,
        )
    )
    robot = result.scalar_one_or_none()
    if not robot:
        raise HTTPException(404, "Robot not found")

    robot.is_active = False
    db.add(robot)
    await db.flush()
    await db.refresh(robot)
    await audit(
        db,
        user=current_user,
        action="robot.disabled",
        resource_type="robot_account",
        resource_id=robot.id,
        namespace_id=namespace.id,
        details={"name": robot.name},
    )
    return robot
