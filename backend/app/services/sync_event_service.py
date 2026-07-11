from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.public_release import PublicSkillRelease
from app.models.skill import Skill, SkillVersion, SkillVersionStatus
from app.models.sync_event import (
    RegistrySyncEvent,
    SyncEventEntity,
    SyncEventOperation,
    SyncEventState,
)
from app.services.artifact_service import ArtifactStorageError, artifact_service
from app.services.public_release_service import is_public_release_pending, is_publicly_available
from app.services.webhook_service import dispatch_event


def _public_slug(namespace_name: str, skill_name: str) -> str:
    return f"{namespace_name}--{skill_name}"


def derive_sync_state(
    version: SkillVersion,
    public_release: PublicSkillRelease | None,
) -> SyncEventState:
    if is_publicly_available(version, public_release):
        return SyncEventState.LIVE
    if public_release is None:
        return SyncEventState.HIDDEN
    if is_public_release_pending(public_release) or version.status in {
        SkillVersionStatus.QUARANTINE,
        SkillVersionStatus.SCANNING,
        SkillVersionStatus.REVIEW,
    }:
        return SyncEventState.PENDING
    return SyncEventState.HIDDEN


def _build_version_payload(
    *,
    namespace_name: str,
    skill_name: str,
    description: str | None,
    version: SkillVersion,
    sync_state: SyncEventState,
    public_release: PublicSkillRelease | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "namespace": namespace_name,
        "skill": skill_name,
        "tag": version.tag,
        "commit_sha": version.commit_sha,
        "version_status": version.status.value,
        "sync_state": sync_state.value,
        "is_public_shared": public_release is not None,
        "public_slug": _public_slug(namespace_name, skill_name) if public_release is not None else None,
        "skill_metadata": version.skill_metadata,
        "description": description,
        "created_at": version.created_at.isoformat() if isinstance(version.created_at, datetime) else None,
    }
    if sync_state == SyncEventState.LIVE:
        payload["distribution_ready"] = True
        payload["manifest_path"] = artifact_service.manifest_object_key(namespace_name, skill_name, version.tag)
        payload["artifact_path"] = artifact_service.bundle_object_key(namespace_name, skill_name, version.tag)
        try:
            manifest = artifact_service.ensure_version_artifacts(
                namespace=namespace_name,
                skill_name=skill_name,
                tag=version.tag,
                commit_sha=version.commit_sha,
                description=description,
                status=version.status.value,
                skill_metadata=version.skill_metadata or {},
                changelog=version.changelog,
                publish_tags=version.publish_tags,
                content_fingerprint=version.content_fingerprint,
                file_count=version.file_count,
            )
            artifact = manifest.get("artifact", {})
            payload["artifact_sha256"] = artifact.get("sha256")
            payload["artifact_size_bytes"] = artifact.get("size_bytes")
        except ArtifactStorageError:
            payload["distribution_ready"] = False
    return payload


async def record_version_sync_event(
    db: AsyncSession,
    *,
    namespace_id: int,
    namespace_name: str,
    skill_name: str,
    description: str | None,
    version: SkillVersion,
    public_release: PublicSkillRelease | None,
    previous_state: SyncEventState | None = None,
    reason: str | None = None,
) -> RegistrySyncEvent | None:
    next_state = derive_sync_state(version, public_release)

    if previous_state == SyncEventState.LIVE and next_state != SyncEventState.LIVE:
        event = RegistrySyncEvent(
            namespace_id=namespace_id,
            namespace_name=namespace_name,
            skill_name=skill_name,
            tag=version.tag,
            entity=SyncEventEntity.VERSION,
            operation=SyncEventOperation.TOMBSTONE,
            sync_state=SyncEventState.TOMBSTONE,
            event="registry.version.tombstone",
            is_public=True,
            version_status=version.status.value,
            commit_sha=version.commit_sha,
            reason=reason,
            payload={
                "namespace": namespace_name,
                "skill": skill_name,
                "tag": version.tag,
                "public_slug": _public_slug(namespace_name, skill_name),
                "reason": reason,
                "previous_state": previous_state.value,
            },
        )
        db.add(event)
        await db.flush()
        await _dispatch_sync_change(
            db,
            namespace_id=namespace_id,
            event=event,
        )
        return event

    if next_state == SyncEventState.HIDDEN and previous_state is None:
        return None

    operation = (
        SyncEventOperation.UPSERT
        if next_state == SyncEventState.LIVE
        else SyncEventOperation.STATE
    )
    event_name = (
        "registry.version.upsert"
        if operation == SyncEventOperation.UPSERT
        else "registry.version.state.changed"
    )
    event = RegistrySyncEvent(
        namespace_id=namespace_id,
        namespace_name=namespace_name,
        skill_name=skill_name,
        tag=version.tag,
        entity=SyncEventEntity.VERSION,
        operation=operation,
        sync_state=next_state,
        event=event_name,
        is_public=next_state in {SyncEventState.PENDING, SyncEventState.LIVE},
        version_status=version.status.value,
        commit_sha=version.commit_sha,
        reason=reason,
        payload=_build_version_payload(
            namespace_name=namespace_name,
            skill_name=skill_name,
            description=description,
            version=version,
            sync_state=next_state,
            public_release=public_release,
        ),
    )
    db.add(event)
    await db.flush()
    await _dispatch_sync_change(
        db,
        namespace_id=namespace_id,
        event=event,
    )
    return event


async def record_skill_tombstone_event(
    db: AsyncSession,
    *,
    namespace_id: int | None,
    namespace_name: str,
    skill_name: str,
    had_public_visibility: bool,
    reason: str,
) -> RegistrySyncEvent:
    event = RegistrySyncEvent(
        namespace_id=namespace_id,
        namespace_name=namespace_name,
        skill_name=skill_name,
        tag=None,
        entity=SyncEventEntity.SKILL,
        operation=SyncEventOperation.TOMBSTONE,
        sync_state=SyncEventState.TOMBSTONE,
        event="registry.skill.tombstone",
        is_public=had_public_visibility,
        version_status=None,
        commit_sha=None,
        reason=reason,
        payload={
            "namespace": namespace_name,
            "skill": skill_name,
            "public_slug": _public_slug(namespace_name, skill_name),
            "reason": reason,
        },
    )
    db.add(event)
    await db.flush()
    if namespace_id is not None:
        await _dispatch_sync_change(
            db,
            namespace_id=namespace_id,
            event=event,
        )
    return event


async def record_skill_restore_events(
    db: AsyncSession,
    *,
    namespace_id: int,
    namespace_name: str,
    skill: Skill,
    reason: str,
) -> list[RegistrySyncEvent]:
    versions = (
        await db.execute(
            select(SkillVersion)
            .where(SkillVersion.skill_id == skill.id)
            .order_by(SkillVersion.created_at.asc())
        )
    ).scalars().all()
    if not versions:
        return []

    public_releases = {
        release.version_id: release
        for release in (
            await db.execute(
                select(PublicSkillRelease).where(
                    PublicSkillRelease.version_id.in_([version.id for version in versions])
                )
            )
        ).scalars().all()
    }

    events: list[RegistrySyncEvent] = []
    for version in versions:
        event = await record_version_sync_event(
            db,
            namespace_id=namespace_id,
            namespace_name=namespace_name,
            skill_name=skill.name,
            description=skill.description,
            version=version,
            public_release=public_releases.get(version.id),
            previous_state=SyncEventState.TOMBSTONE,
            reason=reason,
        )
        if event is not None:
            events.append(event)
    return events


async def _dispatch_sync_change(
    db: AsyncSession,
    *,
    namespace_id: int,
    event: RegistrySyncEvent,
) -> None:
    await dispatch_event(
        db,
        namespace_id=namespace_id,
        event="registry.sync.changed",
        payload={
            "cursor": event.id,
            "namespace": event.namespace_name,
            "skill": event.skill_name,
            "tag": event.tag,
            "entity": event.entity.value if hasattr(event.entity, "value") else event.entity,
            "operation": event.operation.value if hasattr(event.operation, "value") else event.operation,
            "sync_state": event.sync_state.value if hasattr(event.sync_state, "value") else event.sync_state,
            "reason": event.reason,
            "is_public": event.is_public,
        },
    )
