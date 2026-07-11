from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.core.deps import (
    DB,
    CurrentUser,
    get_robot_account,
    require_namespace_integrations_manager,
    require_namespace_member,
)
from app.models.namespace import Namespace
from app.models.replication import ReplicationJob, ReplicationRule
from app.schemas.replication import (
    ReplicationJobOut,
    ReplicationRuleCreate,
    ReplicationRuleOut,
    ReplicationRuleUpdate,
)
from app.services.audit_service import audit
from app.services.replication_service import execute_replication_rule

router = APIRouter(tags=["replication"])


async def _get_namespace(ns_name: str, db: DB) -> Namespace:
    result = await db.execute(
        select(Namespace).where(Namespace.name == ns_name, Namespace.deleted_at.is_(None))
    )
    namespace = result.scalar_one_or_none()
    if not namespace:
        raise HTTPException(404, "Namespace not found")
    return namespace


async def _rule_out(rule: ReplicationRule, db: DB) -> ReplicationRuleOut:
    src_name = (
        await db.execute(select(Namespace.name).where(Namespace.id == rule.src_namespace_id))
    ).scalar_one()
    dst_name = (
        await db.execute(select(Namespace.name).where(Namespace.id == rule.dst_namespace_id))
    ).scalar_one()
    last_job = (
        await db.execute(
            select(ReplicationJob)
            .where(ReplicationJob.rule_id == rule.id)
            .order_by(ReplicationJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return ReplicationRuleOut(
        id=rule.id,
        name=rule.name,
        src_namespace_id=rule.src_namespace_id,
        dst_namespace_id=rule.dst_namespace_id,
        src_namespace_name=src_name,
        dst_namespace_name=dst_name,
        filter_pattern=rule.filter_pattern,
        trigger=rule.trigger,
        is_active=rule.is_active,
        created_at=rule.created_at,
        last_job_status=last_job.status if last_job else None,
        last_job_at=last_job.created_at if last_job else None,
    )


@router.get("/namespaces/{ns_name}/replication/rules", response_model=list[ReplicationRuleOut])
async def list_rules(ns_name: str, db: DB, current_user: CurrentUser):
    namespace = await _get_namespace(ns_name, db)
    await require_namespace_member(current_user, namespace.id, db)

    rules = (
        await db.execute(
            select(ReplicationRule)
            .where(ReplicationRule.src_namespace_id == namespace.id)
            .order_by(ReplicationRule.created_at.desc())
        )
    ).scalars().all()
    return [await _rule_out(rule, db) for rule in rules]


@router.post(
    "/namespaces/{ns_name}/replication/rules",
    response_model=ReplicationRuleOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_rule(
    ns_name: str,
    body: ReplicationRuleCreate,
    db: DB,
    current_user: CurrentUser,
):
    if get_robot_account(current_user):
        raise HTTPException(403, "Robot accounts cannot manage replication")

    src_namespace = await _get_namespace(ns_name, db)
    await require_namespace_integrations_manager(current_user, src_namespace.id, db)

    dst_namespace = await _get_namespace(body.destination_namespace, db)
    await require_namespace_member(current_user, dst_namespace.id, db)

    rule = ReplicationRule(
        name=body.name,
        src_namespace_id=src_namespace.id,
        dst_namespace_id=dst_namespace.id,
        filter_pattern=body.filter_pattern,
        trigger=body.trigger,
        is_active=body.is_active,
        created_by=current_user.id,
    )
    db.add(rule)
    await db.flush()
    await db.refresh(rule)
    await audit(
        db,
        user=current_user,
        action="replication.rule.created",
        resource_type="replication_rule",
        resource_id=rule.id,
        namespace_id=src_namespace.id,
        details={"destination_namespace": dst_namespace.name, "trigger": rule.trigger.value},
    )
    return await _rule_out(rule, db)


@router.patch(
    "/namespaces/{ns_name}/replication/rules/{rule_id}",
    response_model=ReplicationRuleOut,
)
async def update_rule(
    ns_name: str,
    rule_id: int,
    body: ReplicationRuleUpdate,
    db: DB,
    current_user: CurrentUser,
):
    if get_robot_account(current_user):
        raise HTTPException(403, "Robot accounts cannot manage replication")

    src_namespace = await _get_namespace(ns_name, db)
    await require_namespace_integrations_manager(current_user, src_namespace.id, db)

    result = await db.execute(
        select(ReplicationRule).where(
            ReplicationRule.id == rule_id,
            ReplicationRule.src_namespace_id == src_namespace.id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(404, "Replication rule not found")

    if body.destination_namespace is not None:
        dst_namespace = await _get_namespace(body.destination_namespace, db)
        await require_namespace_member(current_user, dst_namespace.id, db)
        rule.dst_namespace_id = dst_namespace.id
    if body.name is not None:
        rule.name = body.name
    if body.filter_pattern is not None:
        rule.filter_pattern = body.filter_pattern
    if body.trigger is not None:
        rule.trigger = body.trigger
    if body.is_active is not None:
        rule.is_active = body.is_active

    db.add(rule)
    await db.flush()
    await db.refresh(rule)
    await audit(
        db,
        user=current_user,
        action="replication.rule.updated",
        resource_type="replication_rule",
        resource_id=rule.id,
        namespace_id=src_namespace.id,
        details={"is_active": rule.is_active, "trigger": rule.trigger.value},
    )
    return await _rule_out(rule, db)


@router.delete("/namespaces/{ns_name}/replication/rules/{rule_id}", status_code=204)
async def delete_rule(ns_name: str, rule_id: int, db: DB, current_user: CurrentUser):
    if get_robot_account(current_user):
        raise HTTPException(403, "Robot accounts cannot manage replication")

    src_namespace = await _get_namespace(ns_name, db)
    await require_namespace_integrations_manager(current_user, src_namespace.id, db)

    result = await db.execute(
        select(ReplicationRule).where(
            ReplicationRule.id == rule_id,
            ReplicationRule.src_namespace_id == src_namespace.id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(404, "Replication rule not found")

    await audit(
        db,
        user=current_user,
        action="replication.rule.deleted",
        resource_type="replication_rule",
        resource_id=rule.id,
        namespace_id=src_namespace.id,
        details={"name": rule.name},
    )
    await db.delete(rule)


@router.post(
    "/namespaces/{ns_name}/replication/rules/{rule_id}/run",
    response_model=ReplicationJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_rule(ns_name: str, rule_id: int, db: DB, current_user: CurrentUser):
    if get_robot_account(current_user):
        raise HTTPException(403, "Robot accounts cannot trigger replication")

    src_namespace = await _get_namespace(ns_name, db)
    await require_namespace_integrations_manager(current_user, src_namespace.id, db)

    result = await db.execute(
        select(ReplicationRule).where(
            ReplicationRule.id == rule_id,
            ReplicationRule.src_namespace_id == src_namespace.id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(404, "Replication rule not found")

    job = await execute_replication_rule(db, rule, triggered_by=current_user.id)
    await db.refresh(job)
    return job


@router.get(
    "/namespaces/{ns_name}/replication/rules/{rule_id}/jobs",
    response_model=list[ReplicationJobOut],
)
async def list_jobs(ns_name: str, rule_id: int, db: DB, current_user: CurrentUser):
    src_namespace = await _get_namespace(ns_name, db)
    await require_namespace_member(current_user, src_namespace.id, db)

    rule_result = await db.execute(
        select(ReplicationRule).where(
            ReplicationRule.id == rule_id,
            ReplicationRule.src_namespace_id == src_namespace.id,
        )
    )
    if not rule_result.scalar_one_or_none():
        raise HTTPException(404, "Replication rule not found")

    jobs = (
        await db.execute(
            select(ReplicationJob)
            .where(ReplicationJob.rule_id == rule_id)
            .order_by(ReplicationJob.created_at.desc())
            .limit(20)
        )
    ).scalars().all()
    return jobs
