from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import event

from app.api.v1.endpoints import skills as skills_endpoints
from app.api.v1.endpoints.registry import registry_download_link
from app.api.v1.endpoints.skills import list_versions
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.governance import NamespaceGovernancePolicy
from app.models.public_release import PublicReleaseApprovalStatus, PublicSkillRelease
from app.models.skill import Skill, SkillVersion, SkillVersionStatus
from app.models.user import User
from app.services.public_release_service import is_public_release_expired, set_public_release


def test_public_release_expiry_normalizes_naive_past_and_future():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    expired = PublicSkillRelease(
        version_id=1,
        approval_status=PublicReleaseApprovalStatus.APPROVED,
        expires_at=now - timedelta(days=1),
    )
    active = PublicSkillRelease(
        version_id=2,
        approval_status=PublicReleaseApprovalStatus.APPROVED,
        expires_at=now + timedelta(days=1),
    )

    assert is_public_release_expired(expired) is True
    assert is_public_release_expired(active) is False


async def _seed_public_skill(session, *, expires_at: datetime, suffix: str) -> tuple[str, str, str]:
    owner = User(username=f"pub-owner-{suffix}", email=f"pub-owner-{suffix}@duckdock-ai.com", hashed_password="x")
    session.add(owner)
    await session.flush()
    namespace = Namespace(name=f"pub-ns-{suffix}", owner_id=owner.id)
    session.add(namespace)
    await session.flush()
    skill = Skill(namespace_id=namespace.id, name=f"pub-skill-{suffix}", git_repo_path=f"/tmp/pub-{suffix}.git")
    session.add(skill)
    await session.flush()
    version = SkillVersion(
        skill_id=skill.id,
        tag="v1",
        commit_sha="a" * 40,
        status=SkillVersionStatus.PRODUCTION,
    )
    session.add(version)
    await session.flush()
    session.add(
        PublicSkillRelease(
            version_id=version.id,
            approval_status=PublicReleaseApprovalStatus.APPROVED,
            expires_at=expires_at,
        )
    )
    await session.flush()
    return namespace.name, skill.name, version.tag


async def _assert_expired_public_download_link_is_controlled(session):
    ns_name, skill_name, tag = await _seed_public_skill(
        session,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1),
        suffix="expired",
    )

    with pytest.raises(HTTPException) as exc:
        await registry_download_link(ns_name, skill_name, tag, session, None)
    assert exc.value.status_code == 404


async def test_public_download_link_handles_naive_expired_release(async_session):
    await _assert_expired_public_download_link_is_controlled(async_session)


@pytest.mark.mysql
async def test_public_download_link_handles_naive_expired_release_mysql(async_session_mysql):
    await _assert_expired_public_download_link_is_controlled(async_session_mysql)


def _count_public_release_selects(session) -> list[int]:
    """Attach a cursor listener that counts public_skill_releases SELECTs."""
    counter = [0]
    bind = session.get_bind()

    def _on_execute(conn, cursor, statement, parameters, context, executemany):
        normalized = statement.lower()
        if "select" in normalized and "public_skill_releases" in normalized:
            counter[0] += 1

    event.listen(bind, "before_cursor_execute", _on_execute)
    return counter


async def test_list_versions_batches_public_release_lookup(async_session, monkeypatch):
    """list_versions must not issue a per-version PublicSkillRelease query (CORR-07)."""
    owner = User(username="batch-owner", email="batch-owner@duckdock-ai.com", hashed_password="x")
    async_session.add(owner)
    await async_session.flush()
    namespace = Namespace(name="batch-ns", owner_id=owner.id)
    async_session.add(namespace)
    await async_session.flush()
    async_session.add(
        NamespaceMember(namespace_id=namespace.id, user_id=owner.id, role=NamespaceRole.ADMIN)
    )
    skill = Skill(namespace_id=namespace.id, name="batch-skill", git_repo_path="/tmp/batch.git")
    async_session.add(skill)
    await async_session.flush()

    # Three versions; only v1 and v3 are publicly shared (v2 has no release).
    versions = []
    for idx in range(1, 4):
        version = SkillVersion(
            skill_id=skill.id,
            tag=f"v{idx}",
            commit_sha=str(idx) * 40,
            status=SkillVersionStatus.PRODUCTION,
        )
        async_session.add(version)
        await async_session.flush()
        versions.append(version)
    shared_tags = {"v1", "v3"}
    for version in versions:
        if version.tag in shared_tags:
            async_session.add(
                PublicSkillRelease(
                    version_id=version.id,
                    approval_status=PublicReleaseApprovalStatus.APPROVED,
                )
            )
    await async_session.flush()

    # The batch path must not fall back to the per-version helper.
    def _fail(*args, **kwargs):  # pragma: no cover - only hit on regression
        raise AssertionError("get_public_release should not be called per version")

    monkeypatch.setattr(skills_endpoints, "get_public_release", _fail)

    counter = _count_public_release_selects(async_session)
    result = await list_versions(namespace.name, skill.name, async_session, owner)

    # Exactly one release query for the whole list, regardless of version count.
    assert counter[0] == 1

    # Response shape/content unchanged: each version reports correct sharing state.
    by_tag = {item.tag: item for item in result}
    assert set(by_tag) == {"v1", "v2", "v3"}
    assert by_tag["v1"].is_public_shared is True
    assert by_tag["v1"].public_shared_at is not None
    assert by_tag["v2"].is_public_shared is False
    assert by_tag["v2"].public_shared_at is None
    assert by_tag["v3"].is_public_shared is True
    assert by_tag["v3"].public_shared_at is not None


async def test_reopened_public_share_requires_fresh_approval(async_session):
    owner = User(username="reopen-owner", email="reopen-owner@duckdock-ai.com", hashed_password="x")
    async_session.add(owner)
    await async_session.flush()
    namespace = Namespace(name="reopen-ns", owner_id=owner.id)
    async_session.add(namespace)
    await async_session.flush()
    skill = Skill(namespace_id=namespace.id, name="reopen-skill", git_repo_path="/tmp/reopen.git")
    async_session.add(skill)
    await async_session.flush()
    version = SkillVersion(
        skill_id=skill.id,
        tag="v1",
        commit_sha="d" * 40,
        status=SkillVersionStatus.PRODUCTION,
    )
    async_session.add(version)
    await async_session.flush()
    policy = NamespaceGovernancePolicy(namespace_id=namespace.id, public_sharing_requires_approval=True)

    approved = await set_public_release(
        async_session,
        version=version,
        shared=True,
        shared_by=owner.id,
        policy=policy,
        approval_status=PublicReleaseApprovalStatus.APPROVED,
        acting_user_id=owner.id,
    )
    assert approved is not None
    assert approved.approval_status == PublicReleaseApprovalStatus.APPROVED

    disabled = await set_public_release(
        async_session,
        version=version,
        shared=False,
        shared_by=owner.id,
        policy=policy,
        acting_user_id=owner.id,
    )
    assert disabled is None

    reopened = await set_public_release(
        async_session,
        version=version,
        shared=True,
        shared_by=owner.id,
        policy=policy,
        acting_user_id=owner.id,
    )

    assert reopened is not None
    assert reopened.approval_status == PublicReleaseApprovalStatus.PENDING
    assert reopened.approved_at is None
