from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, or_, select

from app.core.deps import DB, UsersManagerUser
from app.models.control_plane import AssetOwnership, HandoverCase, WorkTrace
from app.models.iam import EmploymentStatus, UserHandoverProfile
from app.models.user import User
from app.schemas.iam import PersonHandoverProfileOut, PersonHandoverProfileUpdate
from app.services.audit_service import audit

router = APIRouter(prefix="/people", tags=["people"])


def _profile_out(
    *,
    user: User,
    profile: UserHandoverProfile | None,
    users_by_id: dict[int, User],
    asset_count: int = 0,
    work_trace_count: int = 0,
    handover_count: int = 0,
) -> PersonHandoverProfileOut:
    status = profile.employment_status if profile is not None else EmploymentStatus.ACTIVE
    manager_user_id = profile.manager_user_id if profile is not None else None
    receiver_user_id = profile.handover_receiver_user_id if profile is not None else None
    manager = users_by_id.get(manager_user_id or 0)
    receiver = users_by_id.get(receiver_user_id or 0)
    return PersonHandoverProfileOut(
        id=user.id,
        enterprise_uid=user.enterprise_uid,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        system_role=user.system_role,
        auth_source=user.auth_source,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        position_title=profile.position_title if profile is not None else None,
        employee_no=profile.employee_no if profile is not None else None,
        manager_user_id=manager_user_id,
        manager_username=manager.username if manager is not None else None,
        handover_receiver_user_id=receiver_user_id,
        handover_receiver_username=receiver.username if receiver is not None else None,
        employment_status=status,
        note=profile.note if profile is not None else None,
        profile_updated_at=profile.updated_at if profile is not None else None,
        asset_count=asset_count,
        work_trace_count=work_trace_count,
        handover_count=handover_count,
        offboarding_visible=status in {EmploymentStatus.LEAVE, EmploymentStatus.OFFBOARDED},
    )


async def _get_user(db: DB, user_id: int) -> User:
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("", response_model=list[PersonHandoverProfileOut])
async def list_people(db: DB, current_user: UsersManagerUser):
    users = (await db.execute(select(User).order_by(User.system_role.desc(), User.username))).scalars().all()
    user_ids = [row.id for row in users]
    users_by_id = {row.id: row for row in users}
    if not user_ids:
        return []

    profiles = (
        await db.execute(select(UserHandoverProfile).where(UserHandoverProfile.user_id.in_(user_ids)))
    ).scalars().all()
    profiles_by_user_id = {row.user_id: row for row in profiles}

    asset_counts = dict(
        (
            await db.execute(
                select(AssetOwnership.user_id, func.count(AssetOwnership.asset_id))
                .where(AssetOwnership.user_id.in_(user_ids))
                .group_by(AssetOwnership.user_id)
            )
        ).all()
    )
    work_trace_counts = dict(
        (
            await db.execute(
                select(WorkTrace.actor_user_id, func.count(WorkTrace.id))
                .where(WorkTrace.actor_user_id.in_(user_ids))
                .group_by(WorkTrace.actor_user_id)
            )
        ).all()
    )
    handover_counts = {user_id: 0 for user_id in user_ids}
    handover_rows = (
        await db.execute(
            select(HandoverCase.subject_user_id, HandoverCase.receiver_user_id).where(
                or_(
                    HandoverCase.subject_user_id.in_(user_ids),
                    HandoverCase.receiver_user_id.in_(user_ids),
                )
            )
        )
    ).all()
    for subject_user_id, receiver_user_id in handover_rows:
        if subject_user_id in handover_counts:
            handover_counts[subject_user_id] += 1
        if receiver_user_id in handover_counts and receiver_user_id != subject_user_id:
            handover_counts[receiver_user_id] += 1

    return [
        _profile_out(
            user=user,
            profile=profiles_by_user_id.get(user.id),
            users_by_id=users_by_id,
            asset_count=int(asset_counts.get(user.id, 0)),
            work_trace_count=int(work_trace_counts.get(user.id, 0)),
            handover_count=int(handover_counts.get(user.id, 0)),
        )
        for user in users
    ]


@router.patch("/{user_id}", response_model=PersonHandoverProfileOut)
async def update_person_handover_profile(
    user_id: int,
    body: PersonHandoverProfileUpdate,
    db: DB,
    current_user: UsersManagerUser,
):
    user = await _get_user(db, user_id)
    users = (await db.execute(select(User))).scalars().all()
    users_by_id = {row.id: row for row in users}

    payload = body.model_dump(exclude_unset=True)
    for key in ("manager_user_id", "handover_receiver_user_id"):
        target_id = payload.get(key)
        if target_id is not None and target_id not in users_by_id:
            raise HTTPException(status_code=404, detail=f"{key} user not found")

    profile = (
        await db.execute(select(UserHandoverProfile).where(UserHandoverProfile.user_id == user.id))
    ).scalar_one_or_none()
    if profile is None:
        profile = UserHandoverProfile(user_id=user.id)
        db.add(profile)
        await db.flush()

    for key, value in payload.items():
        setattr(profile, key, value)

    await db.flush()
    await audit(
        db,
        user=current_user,
        action="people.handover_profile.updated",
        resource_type="user",
        resource_id=user.id,
        details=body.model_dump(exclude_unset=True, mode="json"),
    )
    await db.refresh(profile)
    return _profile_out(user=user, profile=profile, users_by_id=users_by_id)
