from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.deps import (
    DB,
    CurrentUser,
    get_robot_account,
    require_namespace_admin,
    require_namespace_member,
)
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.skill import Skill, SkillVersion
from app.models.user import User
from app.schemas.governance import NamespaceGovernancePolicyOut, NamespaceGovernancePolicyUpdate
from app.schemas.namespace import NamespaceCreate, NamespaceOut, NamespaceUpdate, MemberAdd, MemberOut
from app.services.audit_service import audit
from app.services.governance_service import get_or_create_namespace_governance
from app.services.iam_service import namespace_access_map
from app.services.sync_event_service import record_skill_restore_events, record_skill_tombstone_event
from app.services.webhook_service import dispatch_event

router = APIRouter(prefix="/namespaces", tags=["namespaces"])


def _namespace_stmt(name: str, *, include_deleted: bool = False):
    stmt = select(Namespace).where(Namespace.name == name)
    if not include_deleted:
        stmt = stmt.where(Namespace.deleted_at.is_(None))
    return stmt


@router.post("", response_model=NamespaceOut, status_code=status.HTTP_201_CREATED)
async def create_namespace(body: NamespaceCreate, db: DB, current_user: CurrentUser):
    if get_robot_account(current_user):
        raise HTTPException(status_code=403, detail="Robot accounts cannot create namespaces")
    existing = await db.execute(_namespace_stmt(body.name, include_deleted=True))
    existing_namespace = existing.scalar_one_or_none()
    if existing_namespace:
        detail = "Namespace name already taken"
        if existing_namespace.deleted_at is not None:
            detail = "Namespace already exists but is deleted. Restore it instead of recreating."
        raise HTTPException(status_code=409, detail=detail)
    ns = Namespace(name=body.name, description=body.description, owner_id=current_user.id)
    db.add(ns)
    await db.flush()
    # auto-add creator as admin member
    db.add(NamespaceMember(namespace_id=ns.id, user_id=current_user.id, role=NamespaceRole.ADMIN))
    await audit(db, user=current_user, action="namespace.created",
                resource_type="namespace", resource_id=ns.id,
                details={"name": body.name})
    await db.refresh(ns)
    return ns


@router.get("", response_model=list[NamespaceOut])
async def list_namespaces(db: DB, current_user: CurrentUser):
    robot = get_robot_account(current_user)
    if robot:
        result = await db.execute(
            select(Namespace)
            .where(Namespace.id == robot.namespace_id, Namespace.deleted_at.is_(None))
            .order_by(Namespace.name)
        )
        return result.scalars().all()

    membership_rows = (
        await db.execute(
        select(Namespace)
        .join(NamespaceMember, NamespaceMember.namespace_id == Namespace.id)
        .where(NamespaceMember.user_id == current_user.id, Namespace.deleted_at.is_(None))
        .order_by(Namespace.name)
        )
    ).scalars().all()
    access_map = await namespace_access_map(db, user=current_user)
    bound_rows = []
    if access_map:
        bound_rows = (
            await db.execute(
                select(Namespace)
                .where(Namespace.id.in_(list(access_map.keys())), Namespace.deleted_at.is_(None))
                .order_by(Namespace.name)
            )
        ).scalars().all()
    dedup: dict[int, Namespace] = {row.id: row for row in membership_rows}
    for row in bound_rows:
        dedup.setdefault(row.id, row)
    return sorted(dedup.values(), key=lambda item: item.name)


@router.get("/deleted", response_model=list[NamespaceOut])
async def list_deleted_namespaces(db: DB, current_user: CurrentUser):
    robot = get_robot_account(current_user)
    if robot:
        result = await db.execute(
            select(Namespace)
            .where(Namespace.id == robot.namespace_id, Namespace.deleted_at.is_not(None))
            .order_by(Namespace.name)
        )
        return result.scalars().all()

    membership_rows = (
        await db.execute(
        select(Namespace)
        .join(NamespaceMember, NamespaceMember.namespace_id == Namespace.id)
        .where(NamespaceMember.user_id == current_user.id, Namespace.deleted_at.is_not(None))
        .order_by(Namespace.name)
        )
    ).scalars().all()
    access_map = await namespace_access_map(db, user=current_user)
    bound_rows = []
    if access_map:
        bound_rows = (
            await db.execute(
                select(Namespace)
                .where(Namespace.id.in_(list(access_map.keys())), Namespace.deleted_at.is_not(None))
                .order_by(Namespace.name)
            )
        ).scalars().all()
    dedup: dict[int, Namespace] = {row.id: row for row in membership_rows}
    for row in bound_rows:
        dedup.setdefault(row.id, row)
    return sorted(dedup.values(), key=lambda item: item.name)


@router.get("/{name}", response_model=NamespaceOut)
async def get_namespace(name: str, db: DB, current_user: CurrentUser):
    result = await db.execute(_namespace_stmt(name))
    ns = result.scalar_one_or_none()
    if not ns:
        raise HTTPException(status_code=404, detail="Namespace not found")
    await require_namespace_member(current_user, ns.id, db)
    return ns


@router.get("/{name}/governance", response_model=NamespaceGovernancePolicyOut)
async def get_namespace_governance(name: str, db: DB, current_user: CurrentUser):
    result = await db.execute(_namespace_stmt(name))
    ns = result.scalar_one_or_none()
    if not ns:
        raise HTTPException(status_code=404, detail="Namespace not found")
    await require_namespace_admin(current_user, ns.id, db)
    policy = await get_or_create_namespace_governance(db, namespace_id=ns.id)
    await db.flush()
    await db.refresh(policy)
    return policy


@router.put("/{name}/governance", response_model=NamespaceGovernancePolicyOut)
async def update_namespace_governance(
    name: str,
    body: NamespaceGovernancePolicyUpdate,
    db: DB,
    current_user: CurrentUser,
):
    result = await db.execute(_namespace_stmt(name))
    ns = result.scalar_one_or_none()
    if not ns:
        raise HTTPException(status_code=404, detail="Namespace not found")
    await require_namespace_admin(current_user, ns.id, db)
    policy = await get_or_create_namespace_governance(db, namespace_id=ns.id)
    payload = body.model_dump()
    if payload["clinic_gate_enabled"] and payload.get("min_clinic_score") is None:
        raise HTTPException(status_code=422, detail="min_clinic_score is required when Clinic gate is enabled")
    for key, value in payload.items():
        setattr(policy, key, value)
    await audit(
        db,
        user=current_user,
        action="namespace.governance.updated",
        resource_type="namespace_governance_policy",
        resource_id=policy.id,
        namespace_id=ns.id,
        details=payload,
    )
    await dispatch_event(
        db,
        namespace_id=ns.id,
        event="namespace.governance.updated",
        payload={"namespace": ns.name, "policy": payload},
    )
    await db.flush()
    await db.refresh(policy)
    return policy


@router.patch("/{name}", response_model=NamespaceOut)
async def update_namespace(name: str, body: NamespaceUpdate, db: DB, current_user: CurrentUser):
    if get_robot_account(current_user):
        raise HTTPException(status_code=403, detail="Robot accounts cannot manage namespaces")
    result = await db.execute(_namespace_stmt(name))
    ns = result.scalar_one_or_none()
    if not ns:
        raise HTTPException(status_code=404, detail="Namespace not found")
    await require_namespace_admin(current_user, ns.id, db)

    ns.description = body.description
    await db.flush()
    await audit(
        db,
        user=current_user,
        action="namespace.updated",
        resource_type="namespace",
        resource_id=ns.id,
        namespace_id=ns.id,
        details={"name": ns.name, "description": ns.description},
    )
    await dispatch_event(
        db,
        namespace_id=ns.id,
        event="namespace.updated",
        payload={"namespace": ns.name, "description": ns.description},
    )
    await db.refresh(ns)
    return ns


@router.delete("/{name}", status_code=204)
async def delete_namespace(name: str, db: DB, current_user: CurrentUser):
    if get_robot_account(current_user):
        raise HTTPException(status_code=403, detail="Robot accounts cannot manage namespaces")
    result = await db.execute(_namespace_stmt(name))
    ns = result.scalar_one_or_none()
    if not ns:
        raise HTTPException(status_code=404, detail="Namespace not found")
    await require_namespace_admin(current_user, ns.id, db)

    namespace_id = ns.id
    namespace_name = ns.name
    deleted_at = datetime.now(timezone.utc)
    skills = (
        await db.execute(
            select(Skill)
            .where(Skill.namespace_id == namespace_id, Skill.deleted_at.is_(None))
            .options(selectinload(Skill.versions).selectinload(SkillVersion.public_release))
        )
    ).scalars().all()
    for skill in skills:
        skill.deleted_at = deleted_at
        skill.deleted_by = current_user.id
        await record_skill_tombstone_event(
            db,
            namespace_id=namespace_id,
            namespace_name=namespace_name,
            skill_name=skill.name,
            had_public_visibility=any(version.public_release is not None for version in skill.versions),
            reason="namespace_deleted",
        )
    ns.deleted_at = deleted_at
    ns.deleted_by = current_user.id
    await audit(
        db,
        user=current_user,
        action="namespace.deleted",
        resource_type="namespace",
        resource_id=namespace_id,
        namespace_id=namespace_id,
        details={"name": namespace_name},
    )
    await dispatch_event(
        db,
        namespace_id=namespace_id,
        event="namespace.deleted",
        payload={"namespace": namespace_name},
    )
    await db.flush()


@router.post("/{name}/restore", response_model=NamespaceOut)
async def restore_namespace(name: str, db: DB, current_user: CurrentUser):
    if get_robot_account(current_user):
        raise HTTPException(status_code=403, detail="Robot accounts cannot manage namespaces")
    result = await db.execute(_namespace_stmt(name, include_deleted=True))
    ns = result.scalar_one_or_none()
    if not ns:
        raise HTTPException(status_code=404, detail="Namespace not found")
    if ns.deleted_at is None:
        raise HTTPException(status_code=409, detail="Namespace is not deleted")
    await require_namespace_admin(current_user, ns.id, db)

    namespace_deleted_at = ns.deleted_at
    ns.deleted_at = None
    ns.deleted_by = None
    skills = (
        await db.execute(
            select(Skill)
            .where(
                Skill.namespace_id == ns.id,
                Skill.deleted_at == namespace_deleted_at,
            )
            .options(selectinload(Skill.versions))
        )
    ).scalars().all()
    for skill in skills:
        skill.deleted_at = None
        skill.deleted_by = None
        await record_skill_restore_events(
            db,
            namespace_id=ns.id,
            namespace_name=ns.name,
            skill=skill,
            reason="namespace_restored",
        )
    await audit(
        db,
        user=current_user,
        action="namespace.restored",
        resource_type="namespace",
        resource_id=ns.id,
        namespace_id=ns.id,
        details={"name": ns.name},
    )
    await dispatch_event(
        db,
        namespace_id=ns.id,
        event="namespace.restored",
        payload={"namespace": ns.name},
    )
    await db.flush()
    await db.refresh(ns)
    return ns


@router.post("/{name}/members", response_model=MemberOut, status_code=201)
async def add_member(name: str, body: MemberAdd, db: DB, current_user: CurrentUser):
    if get_robot_account(current_user):
        raise HTTPException(status_code=403, detail="Robot accounts cannot manage memberships")
    ns_result = await db.execute(_namespace_stmt(name))
    ns = ns_result.scalar_one_or_none()
    if not ns:
        raise HTTPException(status_code=404, detail="Namespace not found")
    await require_namespace_admin(current_user, ns.id, db)

    user_result = await db.execute(select(User).where(User.username == body.username))
    target = user_result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    existing = await db.execute(
        select(NamespaceMember).where(
            NamespaceMember.namespace_id == ns.id,
            NamespaceMember.user_id == target.id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="User already a member")

    member = NamespaceMember(namespace_id=ns.id, user_id=target.id, role=body.role)
    db.add(member)
    await db.flush()
    await audit(
        db,
        user=current_user,
        action="namespace.member.added",
        resource_type="namespace_member",
        resource_id=member.id,
        namespace_id=ns.id,
        details={"username": target.username, "role": body.role.value},
    )
    await dispatch_event(
        db,
        namespace_id=ns.id,
        event="namespace.member.added",
        payload={"namespace": ns.name, "username": target.username, "role": body.role.value},
    )
    return MemberOut(
        user_id=target.id,
        username=target.username,
        role=member.role,
        created_at=member.created_at,
    )
