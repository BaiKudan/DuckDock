"""L0-OPS-RETENTION-BEAT: retention GC must run on a celery beat schedule.

Previously ``run_gc`` only fired from a manual admin POST, so retention policies
were inert in steady state. These tests pin the beat entry, the registered task,
and that the sweep dispatches per-namespace GC.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import settings
from app.models.audit import AuditLog
from app.models.lifecycle import RetentionPolicy
from app.models.namespace import Namespace
from app.models.public_release import PublicSkillRelease
from app.models.skill import Skill, SkillVersion, SkillVersionStatus
from app.models.user import User
from app.workers import lifecycle_tasks
from app.workers.celery_app import celery_app

RETENTION_TASK_NAME = "run_retention_gc"
BEAT_ENTRY_NAME = "run-retention-gc"


# ---------------------------------------------------------------------------
# Beat schedule wiring
# ---------------------------------------------------------------------------


def test_beat_schedule_contains_retention_entry():
    beat_schedule = celery_app.conf.beat_schedule
    assert isinstance(beat_schedule, dict)
    assert BEAT_ENTRY_NAME in beat_schedule
    entry = beat_schedule[BEAT_ENTRY_NAME]
    assert entry["task"] == RETENTION_TASK_NAME


def test_beat_schedule_period_is_configurable():
    entry = celery_app.conf.beat_schedule[BEAT_ENTRY_NAME]
    assert entry["schedule"] == settings.RETENTION_GC_SCHEDULE_SECONDS
    assert settings.RETENTION_GC_SCHEDULE_SECONDS > 0


def test_retention_task_is_registered_under_its_name():
    assert RETENTION_TASK_NAME in celery_app.tasks


def test_lifecycle_tasks_in_celery_include_list():
    assert "app.workers.lifecycle_tasks" in celery_app.conf.include


def test_run_retention_gc_is_importable():
    task = lifecycle_tasks.run_retention_gc
    assert task.name == RETENTION_TASK_NAME


# ---------------------------------------------------------------------------
# Task body sweeps policies and dispatches per-namespace GC
# ---------------------------------------------------------------------------


async def test_async_sweep_dispatches_gc_per_policy(async_session, monkeypatch):
    """The sweep enqueues run_gc for every namespace that has a retention policy."""
    user = User(
        username="ret-user",
        email="ret@example.com",
        hashed_password="x",
        is_active=True,
    )
    async_session.add(user)
    await async_session.flush()

    ns_with = Namespace(name="ns-with-policy", owner_id=user.id)
    ns_without = Namespace(name="ns-no-policy", owner_id=user.id)
    async_session.add_all([ns_with, ns_without])
    await async_session.flush()

    async_session.add(
        RetentionPolicy(namespace_id=ns_with.id, keep_last_n=3, delete_rejected=True)
    )
    await async_session.commit()

    dispatched: list[int] = []
    monkeypatch.setattr(
        lifecycle_tasks.run_gc,
        "delay",
        lambda namespace_id: dispatched.append(namespace_id),
    )

    # Run the sweep against the in-memory test session instead of a worker session.
    @asynccontextmanager
    async def _fake_session(*args, **kwargs):
        yield async_session

    monkeypatch.setattr(lifecycle_tasks, "worker_db_session", _fake_session)

    await lifecycle_tasks._async_retention_gc()

    assert dispatched == [ns_with.id]
    assert ns_without.id not in dispatched


async def test_global_retention_sweep_prunes_audit_and_expired_public_releases(async_session, monkeypatch):
    monkeypatch.setattr(settings, "AUDIT_LOG_RETENTION_DAYS", 90)
    now = datetime.now(timezone.utc)
    user = User(username="ret-global-user", email="ret-global@example.com", hashed_password="x")
    async_session.add(user)
    await async_session.flush()
    namespace = Namespace(name="ret-global-ns", owner_id=user.id)
    async_session.add(namespace)
    await async_session.flush()
    skill = Skill(namespace_id=namespace.id, name="ret-global-skill", git_repo_path="/tmp/ret-global")
    async_session.add(skill)
    await async_session.flush()
    old_version = SkillVersion(
        skill_id=skill.id,
        tag="v1",
        commit_sha="a" * 40,
        status=SkillVersionStatus.PRODUCTION,
    )
    live_version = SkillVersion(
        skill_id=skill.id,
        tag="v2",
        commit_sha="b" * 40,
        status=SkillVersionStatus.PRODUCTION,
    )
    async_session.add_all([old_version, live_version])
    await async_session.flush()
    async_session.add_all(
        [
            AuditLog(action="old.audit", created_at=now - timedelta(days=120)),
            AuditLog(action="fresh.audit", created_at=now - timedelta(days=1)),
            PublicSkillRelease(version_id=old_version.id, expires_at=now - timedelta(days=1)),
            PublicSkillRelease(version_id=live_version.id, expires_at=now + timedelta(days=1)),
        ]
    )
    await async_session.commit()

    deleted = await lifecycle_tasks._sweep_global_retention(async_session)

    assert deleted == {"audit_logs": 1, "public_skill_releases": 1}
    actions = (await async_session.execute(select(AuditLog.action))).scalars().all()
    assert "old.audit" not in actions
    assert "fresh.audit" in actions
    assert "retention.gc.completed" in actions
    releases = (await async_session.execute(select(PublicSkillRelease.version_id))).scalars().all()
    assert old_version.id not in releases
    assert live_version.id in releases


def test_sync_task_invokes_async_sweep(monkeypatch):
    """The celery entrypoint runs the async sweep via asyncio.run.

    Kept synchronous: the real entrypoint calls ``asyncio.run`` internally, which
    cannot nest inside the test's own event loop, so we stub ``asyncio.run`` and
    assert it received the sweep coroutine.
    """
    sentinel = object()

    def _fake_async():
        return sentinel

    captured = {}

    def _fake_run(coro):
        captured["coro"] = coro
        return None

    monkeypatch.setattr(lifecycle_tasks, "_async_retention_gc", _fake_async)
    monkeypatch.setattr(lifecycle_tasks.asyncio, "run", _fake_run)

    # Call the underlying function body (bypassing the broker).
    lifecycle_tasks.run_retention_gc.run()

    assert captured["coro"] is sentinel
