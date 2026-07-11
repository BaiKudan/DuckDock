"""skill.publish namespace authz regressions (P3-13)."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.v1.endpoints import skills as skills_endpoint
from app.api.v1.endpoints.skills import publish_version
from app.workers import scan_tasks
from app.models.iam import Role, RoleBinding
from app.models.namespace import Namespace
from app.models.skill import Skill
from app.models.user import SystemRole, User
from app.schemas.skill import PublishVersionRequest
from app.services.iam_service import ensure_builtin_rbac


async def test_enterprise_admin_without_namespace_binding_cannot_publish(async_session):
    owner = User(username="publish-owner", email="publish-owner@duckdock-ai.com", hashed_password="x")
    publisher = User(
        username="publish-enterprise-admin",
        email="publish-enterprise-admin@duckdock-ai.com",
        hashed_password="x",
        system_role=SystemRole.USER,
    )
    async_session.add_all([owner, publisher])
    await async_session.flush()
    namespace = Namespace(name="publish-authz", owner_id=owner.id)
    async_session.add(namespace)
    await async_session.flush()
    skill = Skill(namespace_id=namespace.id, name="guarded-skill", git_repo_path="/tmp/guarded-skill.git")
    async_session.add(skill)
    await async_session.flush()
    await ensure_builtin_rbac(async_session)
    role = (await async_session.execute(select(Role).where(Role.key == "enterprise-admin"))).scalar_one()
    async_session.add(RoleBinding(role_id=role.id, user_id=publisher.id, namespace_id=None))
    await async_session.flush()

    with pytest.raises(HTTPException) as exc:
        await publish_version(
            namespace.name,
            skill.name,
            PublishVersionRequest(tag="v1.0.0", skill_md="# guarded skill\n"),
            async_session,
            publisher,
        )

    assert exc.value.status_code == 403


async def test_enterprise_admin_with_namespace_developer_binding_can_publish(async_session, monkeypatch):
    owner = User(username="publish-owner-ok", email="publish-owner-ok@duckdock-ai.com", hashed_password="x")
    publisher = User(
        username="publish-enterprise-admin-ok",
        email="publish-enterprise-admin-ok@duckdock-ai.com",
        hashed_password="x",
        system_role=SystemRole.USER,
    )
    async_session.add_all([owner, publisher])
    await async_session.flush()
    namespace = Namespace(name="publish-authz-ok", owner_id=owner.id)
    async_session.add(namespace)
    await async_session.flush()
    skill = Skill(namespace_id=namespace.id, name="guarded_skill_ok", git_repo_path="/tmp/guarded-skill-ok.git")
    async_session.add(skill)
    await async_session.flush()
    await ensure_builtin_rbac(async_session)
    enterprise_role = (await async_session.execute(select(Role).where(Role.key == "enterprise-admin"))).scalar_one()
    developer_role = (await async_session.execute(select(Role).where(Role.key == "namespace-developer"))).scalar_one()
    async_session.add_all(
        [
            RoleBinding(role_id=enterprise_role.id, user_id=publisher.id, namespace_id=None),
            RoleBinding(role_id=developer_role.id, user_id=publisher.id, namespace_id=namespace.id),
        ]
    )
    await async_session.flush()

    monkeypatch.setattr(skills_endpoint.git_service, "publish_version", lambda **_kwargs: "abc123")
    monkeypatch.setattr(skills_endpoint.git_service, "version_fingerprint", lambda *_args, **_kwargs: "fingerprint")
    monkeypatch.setattr(skills_endpoint.artifact_service, "publish_version_artifacts", lambda **_kwargs: None)
    monkeypatch.setattr(scan_tasks.trigger_scan, "delay", lambda *_args, **_kwargs: None)

    async def _noop_async(*_args, **_kwargs):
        return None

    monkeypatch.setattr(skills_endpoint, "dispatch_event", _noop_async)
    monkeypatch.setattr(skills_endpoint, "run_on_publish_replication", _noop_async)

    version = await publish_version(
        namespace.name,
        skill.name,
        PublishVersionRequest(
            tag="v1.0.0",
            skill_md=(
                "---\n"
                "name: guarded_skill_ok\n"
                "description: Guarded skill\n"
                "version: 1.0.0\n"
                "---\n"
                "# Guarded skill\n"
            ),
        ),
        async_session,
        publisher,
    )

    assert version.tag == "v1.0.0"
