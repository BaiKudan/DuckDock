from __future__ import annotations

from datetime import datetime, timezone
from fnmatch import fnmatch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.namespace import Namespace
from app.models.replication import ReplicationJob, ReplicationJobStatus, ReplicationRule, ReplicationTrigger
from app.models.skill import Skill, SkillVersion
from app.services.artifact_service import artifact_service
from app.services.audit_service import audit
from app.services.git_service import GitServiceError, git_service
from app.services.webhook_service import dispatch_event


async def execute_replication_rule(
    db: AsyncSession,
    rule: ReplicationRule,
    *,
    triggered_by: int | None = None,
    only_skill_name: str | None = None,
    only_tag: str | None = None,
) -> ReplicationJob:
    safe_triggered_by = triggered_by if triggered_by and triggered_by > 0 else None
    job = ReplicationJob(
        rule_id=rule.id,
        status=ReplicationJobStatus.RUNNING,
        log=[],
        started_at=datetime.now(timezone.utc),
        triggered_by=safe_triggered_by,
    )
    db.add(job)
    await db.flush()

    log_lines: list[str] = []
    copied = 0
    skipped = 0
    failed = 0

    src_namespace = (
        await db.execute(
            select(Namespace).where(
                Namespace.id == rule.src_namespace_id,
                Namespace.deleted_at.is_(None),
            )
        )
    ).scalar_one()
    dst_namespace = (
        await db.execute(
            select(Namespace).where(
                Namespace.id == rule.dst_namespace_id,
                Namespace.deleted_at.is_(None),
            )
        )
    ).scalar_one()

    source_skills = (
        await db.execute(
            select(Skill)
            .where(Skill.namespace_id == src_namespace.id, Skill.deleted_at.is_(None))
            .order_by(Skill.name)
        )
    ).scalars().all()

    for source_skill in source_skills:
        if only_skill_name and source_skill.name != only_skill_name:
            continue
        if rule.filter_pattern and not fnmatch(source_skill.name, rule.filter_pattern):
            skipped += 1
            log_lines.append(f"skip skill {source_skill.name}: filter mismatch")
            continue

        destination_skill = (
            await db.execute(
                select(Skill).where(
                    Skill.namespace_id == dst_namespace.id,
                    Skill.name == source_skill.name,
                    Skill.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

        if destination_skill is None:
            try:
                repo_path = git_service.create_repo(dst_namespace.name, source_skill.name)
            except GitServiceError:
                repo_path = git_service.repo_path(dst_namespace.name, source_skill.name)
            destination_skill = Skill(
                namespace_id=dst_namespace.id,
                name=source_skill.name,
                description=source_skill.description,
                git_repo_path=str(repo_path),
            )
            db.add(destination_skill)
            await db.flush()
            log_lines.append(f"create skill {source_skill.name} in {dst_namespace.name}")

        source_versions = (
            await db.execute(
                select(SkillVersion)
                .where(SkillVersion.skill_id == source_skill.id)
                .order_by(SkillVersion.created_at.asc())
            )
        ).scalars().all()

        for source_version in source_versions:
            if only_tag and source_version.tag != only_tag:
                continue

            existing = (
                await db.execute(
                    select(SkillVersion).where(
                        SkillVersion.skill_id == destination_skill.id,
                        SkillVersion.tag == source_version.tag,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                skipped += 1
                log_lines.append(f"skip {source_skill.name}:{source_version.tag}: already mirrored")
                continue

            try:
                # P0-3: use bytes throughout to preserve binary fidelity end-to-end
                # (source git blob → dest git blob → MinIO tarball).
                files = git_service.get_version_files_bytes(
                    src_namespace.name, source_skill.name, source_version.tag
                )
                commit_sha = git_service.publish_version_bytes(
                    namespace=dst_namespace.name,
                    skill_name=destination_skill.name,
                    files=files,
                    tag=source_version.tag,
                    author_name="replication-engine",
                    author_email="replication@duckdock.local",
                    message=f"chore: replicate {source_skill.name} {source_version.tag}",
                )
                artifact_service.publish_version_artifacts(
                    namespace=dst_namespace.name,
                    skill_name=destination_skill.name,
                    tag=source_version.tag,
                    commit_sha=commit_sha,
                    description=(source_version.skill_metadata or {}).get("description") or destination_skill.description,
                    status=source_version.status.value,
                    skill_metadata=source_version.skill_metadata or {},
                    changelog=source_version.changelog,
                    publish_tags=source_version.publish_tags,
                    content_fingerprint=source_version.content_fingerprint,
                    file_count=source_version.file_count,
                    files=files,
                )
                mirrored_version = SkillVersion(
                    skill_id=destination_skill.id,
                    tag=source_version.tag,
                    commit_sha=commit_sha,
                    status=source_version.status,
                    skill_metadata=source_version.skill_metadata,
                    changelog=source_version.changelog,
                    publish_tags=source_version.publish_tags,
                    content_fingerprint=source_version.content_fingerprint or git_service.version_fingerprint(
                        dst_namespace.name,
                        destination_skill.name,
                        source_version.tag,
                    ),
                    file_count=source_version.file_count or len(files),
                    published_by=safe_triggered_by,
                )
                db.add(mirrored_version)
                copied += 1
                log_lines.append(f"copy {source_skill.name}:{source_version.tag}")
            except Exception as exc:
                try:
                    git_service.delete_tag(
                        dst_namespace.name,
                        destination_skill.name,
                        source_version.tag,
                    )
                except GitServiceError:
                    pass
                failed += 1
                log_lines.append(f"fail {source_skill.name}:{source_version.tag}: {exc}")

    job.status = ReplicationJobStatus.FAILED if failed else ReplicationJobStatus.COMPLETED
    job.skills_copied = copied
    job.skills_skipped = skipped
    job.skills_failed = failed
    job.log = log_lines
    job.completed_at = datetime.now(timezone.utc)
    if failed:
        job.error_message = f"{failed} replication item(s) failed"
    await db.flush()

    await audit(
        db,
        action="replication.executed",
        user_id=safe_triggered_by,
        resource_type="replication_rule",
        resource_id=rule.id,
        namespace_id=rule.src_namespace_id,
        details={
            "copied": copied,
            "skipped": skipped,
            "failed": failed,
            "destination_namespace_id": rule.dst_namespace_id,
        },
    )
    await dispatch_event(
        db,
        namespace_id=rule.src_namespace_id,
        event="replication.executed",
        payload={
            "rule_id": rule.id,
            "copied": copied,
            "skipped": skipped,
            "failed": failed,
            "destination_namespace_id": rule.dst_namespace_id,
        },
    )
    return job


async def run_on_publish_replication(
    db: AsyncSession,
    *,
    namespace_id: int,
    skill_name: str,
    tag: str,
    triggered_by: int | None,
) -> None:
    rules = (
        await db.execute(
            select(ReplicationRule).where(
                ReplicationRule.src_namespace_id == namespace_id,
                ReplicationRule.trigger == ReplicationTrigger.ON_PUBLISH,
                ReplicationRule.is_active == True,
            )
        )
    ).scalars().all()

    for rule in rules:
        await execute_replication_rule(
            db,
            rule,
            triggered_by=triggered_by,
            only_skill_name=skill_name,
            only_tag=tag,
        )
