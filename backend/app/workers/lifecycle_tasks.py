"""GC task: apply retention policy to a namespace."""
import asyncio
from datetime import datetime, timedelta, timezone
import logging

from sqlalchemy import delete, select

from app.core.config import settings
from app.workers.celery_app import celery_app
from app.workers.db import worker_db_session

logger = logging.getLogger(__name__)


@celery_app.task
def run_gc(namespace_id: int):
    asyncio.run(_async_gc(namespace_id))


@celery_app.task(name="run_retention_gc")
def run_retention_gc():
    """L0-OPS-RETENTION-BEAT: celery-beat entrypoint that sweeps retention policies.

    Without this, ``run_gc`` only fired from a manual admin POST, leaving retention
    policies inert in steady state. The sweep fans out one per-namespace ``run_gc``
    task for every configured policy so the existing, tested GC logic is reused.
    """
    asyncio.run(_async_retention_gc())


async def _async_retention_gc():
    from app.models.lifecycle import RetentionPolicy

    async with worker_db_session(echo=False) as db:
        await _sweep_global_retention(db)
        policies = (
            await db.execute(select(RetentionPolicy.namespace_id))
        ).scalars().all()

    for namespace_id in policies:
        run_gc.delay(namespace_id)


async def _sweep_global_retention(db) -> dict[str, int]:
    from app.models.audit import AuditLog
    from app.models.public_release import PublicSkillRelease

    now = datetime.now(timezone.utc)
    deleted_counts = {"audit_logs": 0, "public_skill_releases": 0}

    audit_retention_days = settings.AUDIT_LOG_RETENTION_DAYS
    if audit_retention_days > 0:
        audit_cutoff = now - timedelta(days=audit_retention_days)
        audit_result = await db.execute(
            delete(AuditLog).where(AuditLog.created_at < audit_cutoff)
        )
        deleted_counts["audit_logs"] = int(audit_result.rowcount or 0)

    release_result = await db.execute(
        delete(PublicSkillRelease).where(
            PublicSkillRelease.expires_at.is_not(None),
            PublicSkillRelease.expires_at <= now,
        )
    )
    deleted_counts["public_skill_releases"] = int(release_result.rowcount or 0)

    db.add(
        AuditLog(
            action="retention.gc.completed",
            resource_type="retention",
            details={
                "audit_log_retention_days": audit_retention_days,
                "deleted": deleted_counts,
            },
        )
    )
    await db.commit()
    logger.info("Retention GC global sweep completed: %s", deleted_counts)
    return deleted_counts


async def _async_gc(namespace_id: int):
    from app.models.audit import AuditLog
    from app.models.lifecycle import RetentionPolicy
    from app.models.namespace import Namespace
    from app.models.skill import Skill, SkillVersion, SkillVersionStatus
    from app.services.git_service import git_service

    async with worker_db_session(echo=False) as db:
        p_r = await db.execute(
            select(RetentionPolicy).where(RetentionPolicy.namespace_id == namespace_id)
        )
        policy = p_r.scalar_one_or_none()
        if not policy:
            return
        namespace = (
            await db.execute(select(Namespace).where(Namespace.id == namespace_id))
        ).scalar_one()

        skills_r = await db.execute(
            select(Skill).where(Skill.namespace_id == namespace_id)
        )
        skills = skills_r.scalars().all()

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=policy.keep_days)
            if policy.keep_days else None
        )

        for skill in skills:
            vs_r = await db.execute(
                select(SkillVersion)
                .where(SkillVersion.skill_id == skill.id)
                .order_by(SkillVersion.created_at.desc())
            )
            versions = list(vs_r.scalars().all())

            to_delete = []
            kept = 0
            for v in versions:
                if policy.delete_rejected and v.status == SkillVersionStatus.REJECTED:
                    to_delete.append(v)
                    continue
                if cutoff and v.created_at < cutoff:
                    to_delete.append(v)
                    continue
                if policy.keep_last_n and kept >= policy.keep_last_n:
                    to_delete.append(v)
                    continue
                kept += 1

            for v in to_delete:
                try:
                    git_service.delete_tag(
                        namespace.name, skill.name, v.tag
                    )
                except Exception:
                    # Database retention must continue even if the optional Git
                    # tag cleanup fails, while operators still need a signal to
                    # reconcile the orphaned tag.
                    logger.exception(
                        "Failed to delete Git tag during retention GC "
                        "(namespace_id=%s, skill_id=%s, version_id=%s)",
                        namespace.id,
                        skill.id,
                        v.id,
                    )
                await db.delete(v)

        db.add(
            AuditLog(
                action="namespace.retention.gc.completed",
                resource_type="namespace",
                resource_id=namespace.id,
                namespace_id=namespace.id,
                details={"namespace": namespace.name},
            )
        )
        await db.commit()
