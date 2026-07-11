from datetime import datetime, timezone
import os

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from app.core.deps import (
    DB,
    CurrentUser,
    get_robot_account,
    require_namespace_lifecycle_manager,
    require_namespace_member,
)
from app.models.lifecycle import NamespaceQuota, RetentionPolicy
from app.models.namespace import Namespace
from app.models.skill import Skill, SkillVersion
from app.schemas.lifecycle import QuotaOut, QuotaUpdate, RetentionPolicyOut, RetentionPolicyUpdate
from app.services.audit_service import audit

router = APIRouter(tags=["lifecycle"])


async def _get_namespace(ns_name: str, db: DB) -> Namespace:
    result = await db.execute(
        select(Namespace).where(Namespace.name == ns_name, Namespace.deleted_at.is_(None))
    )
    namespace = result.scalar_one_or_none()
    if not namespace:
        raise HTTPException(404, "Namespace not found")
    return namespace


def _dir_size_bytes(path_str: str) -> int:
    total = 0
    for root, _, files in os.walk(path_str):
        for name in files:
            file_path = os.path.join(root, name)
            try:
                total += os.path.getsize(file_path)
            except OSError:
                continue
    return total


async def _usage(namespace_id: int, db: DB) -> tuple[int, int, int]:
    skill_count = (
        await db.execute(
            select(func.count()).select_from(Skill).where(
                Skill.namespace_id == namespace_id,
                Skill.deleted_at.is_(None),
            )
        )
    ).scalar_one()
    version_count = (
        await db.execute(
            select(func.count())
            .select_from(SkillVersion)
            .join(Skill, Skill.id == SkillVersion.skill_id)
            .where(Skill.namespace_id == namespace_id, Skill.deleted_at.is_(None))
        )
    ).scalar_one()
    skill_paths = (
        await db.execute(
            select(Skill.git_repo_path).where(Skill.namespace_id == namespace_id, Skill.deleted_at.is_(None))
        )
    ).scalars().all()
    storage_bytes = sum(_dir_size_bytes(path) for path in skill_paths)
    return skill_count, version_count, storage_bytes


@router.get("/namespaces/{ns_name}/quota", response_model=QuotaOut)
async def get_quota(ns_name: str, db: DB, current_user: CurrentUser):
    namespace = await _get_namespace(ns_name, db)
    await require_namespace_member(current_user, namespace.id, db)

    result = await db.execute(
        select(NamespaceQuota).where(NamespaceQuota.namespace_id == namespace.id)
    )
    quota = result.scalar_one_or_none()
    current_skills, current_total_versions, current_storage_bytes = await _usage(namespace.id, db)

    if not quota:
        return QuotaOut(
            namespace_id=namespace.id,
            max_skills=100,
            max_versions_per_skill=50,
            max_total_versions=2000,
            max_storage_bytes=536870912,
            current_skills=current_skills,
            current_total_versions=current_total_versions,
            current_storage_bytes=current_storage_bytes,
        )

    return QuotaOut(
        id=quota.id,
        namespace_id=namespace.id,
        max_skills=quota.max_skills,
        max_versions_per_skill=quota.max_versions_per_skill,
        max_total_versions=quota.max_total_versions,
        max_storage_bytes=quota.max_storage_bytes,
        current_skills=current_skills,
        current_total_versions=current_total_versions,
        current_storage_bytes=current_storage_bytes,
    )


@router.put("/namespaces/{ns_name}/quota", response_model=QuotaOut)
async def update_quota(ns_name: str, body: QuotaUpdate, db: DB, current_user: CurrentUser):
    if get_robot_account(current_user):
        raise HTTPException(403, "Robot accounts cannot manage quotas")

    namespace = await _get_namespace(ns_name, db)
    await require_namespace_lifecycle_manager(current_user, namespace.id, db)

    result = await db.execute(
        select(NamespaceQuota).where(NamespaceQuota.namespace_id == namespace.id)
    )
    quota = result.scalar_one_or_none()
    if quota:
        quota.max_skills = body.max_skills
        quota.max_versions_per_skill = body.max_versions_per_skill
        quota.max_total_versions = body.max_total_versions
        quota.max_storage_bytes = body.max_storage_bytes
        quota.updated_at = datetime.now(timezone.utc)
    else:
        quota = NamespaceQuota(
            namespace_id=namespace.id,
            max_skills=body.max_skills,
            max_versions_per_skill=body.max_versions_per_skill,
            max_total_versions=body.max_total_versions,
            max_storage_bytes=body.max_storage_bytes,
        )
    db.add(quota)
    await db.flush()
    await db.refresh(quota)
    current_skills, current_total_versions, current_storage_bytes = await _usage(namespace.id, db)
    await audit(
        db,
        user=current_user,
        action="namespace.quota.updated",
        resource_type="namespace_quota",
        resource_id=quota.id,
        namespace_id=namespace.id,
        details={
            "max_skills": quota.max_skills,
            "max_versions_per_skill": quota.max_versions_per_skill,
            "max_total_versions": quota.max_total_versions,
            "max_storage_bytes": quota.max_storage_bytes,
        },
    )
    return QuotaOut(
        id=quota.id,
        namespace_id=namespace.id,
        max_skills=quota.max_skills,
        max_versions_per_skill=quota.max_versions_per_skill,
        max_total_versions=quota.max_total_versions,
        max_storage_bytes=quota.max_storage_bytes,
        current_skills=current_skills,
        current_total_versions=current_total_versions,
        current_storage_bytes=current_storage_bytes,
    )


@router.get("/namespaces/{ns_name}/retention", response_model=RetentionPolicyOut)
async def get_retention(ns_name: str, db: DB, current_user: CurrentUser):
    namespace = await _get_namespace(ns_name, db)
    await require_namespace_member(current_user, namespace.id, db)

    result = await db.execute(
        select(RetentionPolicy).where(RetentionPolicy.namespace_id == namespace.id)
    )
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(404, "No retention policy set")
    return policy


@router.put("/namespaces/{ns_name}/retention", response_model=RetentionPolicyOut)
async def upsert_retention(
    ns_name: str,
    body: RetentionPolicyUpdate,
    db: DB,
    current_user: CurrentUser,
):
    if get_robot_account(current_user):
        raise HTTPException(403, "Robot accounts cannot manage retention policies")

    namespace = await _get_namespace(ns_name, db)
    await require_namespace_lifecycle_manager(current_user, namespace.id, db)

    result = await db.execute(
        select(RetentionPolicy).where(RetentionPolicy.namespace_id == namespace.id)
    )
    policy = result.scalar_one_or_none()
    if policy:
        policy.keep_last_n = body.keep_last_n
        policy.keep_days = body.keep_days
        policy.delete_rejected = body.delete_rejected
        policy.updated_at = datetime.now(timezone.utc)
    else:
        policy = RetentionPolicy(
            namespace_id=namespace.id,
            keep_last_n=body.keep_last_n,
            keep_days=body.keep_days,
            delete_rejected=body.delete_rejected,
        )
    db.add(policy)
    await db.flush()
    await db.refresh(policy)
    await audit(
        db,
        user=current_user,
        action="namespace.retention.updated",
        resource_type="retention_policy",
        resource_id=policy.id,
        namespace_id=namespace.id,
        details={
            "keep_last_n": policy.keep_last_n,
            "keep_days": policy.keep_days,
            "delete_rejected": policy.delete_rejected,
        },
    )
    return policy


@router.post("/namespaces/{ns_name}/retention/gc", status_code=202)
async def trigger_gc(ns_name: str, db: DB, current_user: CurrentUser):
    if get_robot_account(current_user):
        raise HTTPException(403, "Robot accounts cannot trigger retention GC")

    namespace = await _get_namespace(ns_name, db)
    await require_namespace_lifecycle_manager(current_user, namespace.id, db)

    result = await db.execute(
        select(RetentionPolicy).where(RetentionPolicy.namespace_id == namespace.id)
    )
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(404, "No retention policy configured")

    from app.workers.lifecycle_tasks import run_gc

    run_gc.delay(namespace.id)
    await audit(
        db,
        user=current_user,
        action="namespace.retention.gc.triggered",
        resource_type="retention_policy",
        resource_id=policy.id,
        namespace_id=namespace.id,
        details={"namespace": namespace.name},
    )
    return {"message": "GC job enqueued"}
