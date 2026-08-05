"""FND-020..023/028 red-first tests for immutable deployment inventory."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.audit import AuditLog
from app.models.control_plane import (
    AIAsset,
    AssetType,
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
)
from app.models.deployment import (
    AgentDeployment,
    AgentDeploymentStatus,
    DeploymentComponent,
    DeploymentComponentRole,
)
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.skill import Skill, SkillVersion
from app.models.user import SystemRole, User
from app.schemas.deployment import (
    AgentDeploymentCreate,
    DeploymentComponentConfiguration,
    DeploymentComponentCreate,
)
from app.services.deployment_service import (
    DeploymentImmutableError,
    DuplicateDeploymentError,
    DeploymentReferenceError,
    DeploymentStateError,
    DeploymentTenantMismatchError,
    activate_deployment,
    fail_deployment,
    register_deployment,
    replace_registered_components,
    retire_deployment,
    update_registered_deployment,
)


async def _user(db, suffix: str) -> User:
    user = User(
        username=f"deployment-{suffix}",
        email=f"deployment-{suffix}@example.test",
        hashed_password="unused",
        system_role=SystemRole.USER,
    )
    db.add(user)
    await db.flush()
    return user


async def _namespace(db, suffix: str, owner: User) -> Namespace:
    namespace = Namespace(name=f"deployment-{suffix}", owner_id=owner.id)
    db.add(namespace)
    await db.flush()
    db.add(
        NamespaceMember(
            namespace_id=namespace.id,
            user_id=owner.id,
            role=NamespaceRole.ADMIN,
        )
    )
    await db.flush()
    return namespace


async def _runtime(db, suffix: str, namespace_id: int) -> RuntimeInstance:
    runtime = RuntimeInstance(
        namespace_id=namespace_id,
        provider=RuntimeProvider.CUSTOM,
        name=f"deployment-runtime-{suffix}",
        deploy_type=RuntimeDeployType.PRIVATE,
    )
    db.add(runtime)
    await db.flush()
    return runtime


async def _asset(db, suffix: str, namespace_id: int) -> AIAsset:
    asset = AIAsset(
        namespace_id=namespace_id,
        asset_type=AssetType.AGENT,
        name=f"deployment-asset-{suffix}",
        source_provider=RuntimeProvider.CUSTOM,
    )
    db.add(asset)
    await db.flush()
    return asset


async def _skill_version(db, suffix: str, namespace_id: int) -> SkillVersion:
    skill = Skill(
        namespace_id=namespace_id,
        name=f"deployment-skill-{suffix}",
        git_repo_path=f"https://example.test/{suffix}.git",
    )
    db.add(skill)
    await db.flush()
    version = SkillVersion(
        skill_id=skill.id,
        tag="1.0.0",
        commit_sha="a" * 40,
        content_fingerprint="b" * 64,
    )
    db.add(version)
    await db.flush()
    return version


def _component(
    *,
    key: str = "agent",
    role: DeploymentComponentRole = DeploymentComponentRole.AGENT,
    asset_id: int | None = None,
    skill_version_id: int | None = None,
) -> DeploymentComponentCreate:
    return DeploymentComponentCreate(
        component_key=key,
        component_role=role,
        ai_asset_id=asset_id,
        skill_version_id=skill_version_id,
        content_digest="c" * 64 if asset_id is None and skill_version_id is None else None,
        configuration=DeploymentComponentConfiguration(
            provider="custom",
            model="governed-model",
            timeout_ms=30_000,
        ),
    )


def _request(
    namespace_id: int,
    runtime_id: int,
    *,
    revision: str = "1",
    agent_asset_id: int | None = None,
    components: list[DeploymentComponentCreate] | None = None,
) -> AgentDeploymentCreate:
    return AgentDeploymentCreate(
        namespace_id=namespace_id,
        runtime_id=runtime_id,
        agent_asset_id=agent_asset_id,
        external_deployment_id="agent-loop-main",
        environment="dev",
        revision=revision,
        configuration_digest="d" * 64,
        components=components or [_component(asset_id=agent_asset_id)],
    )


async def test_registered_active_retired_and_failed_state_machines(async_session) -> None:
    actor = await _user(async_session, "lifecycle")
    namespace = await _namespace(async_session, "lifecycle", actor)
    runtime = await _runtime(async_session, "lifecycle", namespace.id)
    asset = await _asset(async_session, "lifecycle", namespace.id)

    deployment = await register_deployment(
        async_session,
        request=_request(namespace.id, runtime.id, agent_asset_id=asset.id),
        actor=actor,
    )
    assert deployment.status == AgentDeploymentStatus.REGISTERED
    assert deployment.activated_at is None
    assert deployment.retired_at is None

    deployment = await activate_deployment(async_session, deployment.id, actor=actor)
    assert deployment.status == AgentDeploymentStatus.ACTIVE
    assert isinstance(deployment.activated_at, datetime)

    deployment = await retire_deployment(async_session, deployment.id, actor=actor)
    assert deployment.status == AgentDeploymentStatus.RETIRED
    assert isinstance(deployment.retired_at, datetime)

    with pytest.raises(DeploymentStateError, match="RETIRED"):
        await activate_deployment(async_session, deployment.id, actor=actor)

    failed = await register_deployment(
        async_session,
        request=_request(
            namespace.id,
            runtime.id,
            revision="failed",
            agent_asset_id=asset.id,
        ),
        actor=actor,
    )
    failed = await fail_deployment(
        async_session,
        failed.id,
        actor=actor,
        reason_code="activation_rejected",
    )
    assert failed.status == AgentDeploymentStatus.FAILED
    with pytest.raises(DeploymentStateError, match="FAILED"):
        await retire_deployment(async_session, failed.id, actor=actor)

    actions = (
        await async_session.execute(
            select(AuditLog.action)
            .where(AuditLog.resource_type == "agent_deployment")
            .order_by(AuditLog.id)
        )
    ).scalars().all()
    assert actions == [
        "deployment.registered",
        "deployment.activated",
        "deployment.retired",
        "deployment.registered",
        "deployment.failed",
    ]


async def test_active_and_retired_deployments_are_immutable(async_session) -> None:
    actor = await _user(async_session, "immutable")
    namespace = await _namespace(async_session, "immutable", actor)
    runtime = await _runtime(async_session, "immutable", namespace.id)
    asset = await _asset(async_session, "immutable", namespace.id)

    registered = await register_deployment(
        async_session,
        request=_request(namespace.id, runtime.id, agent_asset_id=asset.id),
        actor=actor,
    )
    await update_registered_deployment(
        async_session,
        registered.id,
        revision="1-rc2",
        configuration_digest="e" * 64,
        actor=actor,
    )
    await replace_registered_components(
        async_session,
        registered.id,
        [_component(key="agent-rc2", asset_id=asset.id)],
        actor=actor,
    )

    active = await activate_deployment(async_session, registered.id, actor=actor)
    with pytest.raises(DeploymentImmutableError, match="ACTIVE"):
        await update_registered_deployment(
            async_session,
            active.id,
            revision="mutated",
            actor=actor,
        )
    with pytest.raises(DeploymentImmutableError, match="ACTIVE"):
        await replace_registered_components(
            async_session,
            active.id,
            [_component(key="mutated", asset_id=asset.id)],
            actor=actor,
        )

    retired = await retire_deployment(async_session, active.id, actor=actor)
    with pytest.raises(DeploymentImmutableError, match="RETIRED"):
        await update_registered_deployment(
            async_session,
            retired.id,
            configuration_digest="f" * 64,
            actor=actor,
        )


async def test_revision_one_lifecycle_then_revision_two_registration(
    async_session,
) -> None:
    """FND-028: a material change produces a new immutable revision."""
    actor = await _user(async_session, "e2e")
    namespace = await _namespace(async_session, "e2e", actor)
    runtime = await _runtime(async_session, "e2e", namespace.id)
    asset = await _asset(async_session, "e2e", namespace.id)

    revision_one = await register_deployment(
        async_session,
        request=_request(
            namespace.id,
            runtime.id,
            revision="1",
            agent_asset_id=asset.id,
        ),
        actor=actor,
    )
    revision_one = await activate_deployment(
        async_session,
        revision_one.id,
        actor=actor,
    )
    with pytest.raises(DeploymentImmutableError):
        await replace_registered_components(
            async_session,
            revision_one.id,
            [_component(key="changed", asset_id=asset.id)],
            actor=actor,
        )

    revision_two = await register_deployment(
        async_session,
        request=_request(
            namespace.id,
            runtime.id,
            revision="2",
            agent_asset_id=asset.id,
            components=[
                _component(key="agent", asset_id=asset.id),
                _component(
                    key="model",
                    role=DeploymentComponentRole.MODEL,
                ),
            ],
        ),
        actor=actor,
    )
    assert revision_two.id != revision_one.id
    assert revision_two.status == AgentDeploymentStatus.REGISTERED

    revision_one = await retire_deployment(
        async_session,
        revision_one.id,
        actor=actor,
    )
    assert revision_one.status == AgentDeploymentStatus.RETIRED
    assert revision_two.status == AgentDeploymentStatus.REGISTERED


async def test_registration_rejects_cross_tenant_references(async_session) -> None:
    actor = await _user(async_session, "tenant")
    first = await _namespace(async_session, "tenant-first", actor)
    second = await _namespace(async_session, "tenant-second", actor)
    runtime_first = await _runtime(async_session, "tenant-first", first.id)
    runtime_second = await _runtime(async_session, "tenant-second", second.id)
    asset_first = await _asset(async_session, "tenant-first", first.id)
    asset_second = await _asset(async_session, "tenant-second", second.id)
    version_second = await _skill_version(async_session, "tenant-second", second.id)

    with pytest.raises(DeploymentTenantMismatchError, match="runtime"):
        await register_deployment(
            async_session,
            request=_request(first.id, runtime_second.id, agent_asset_id=asset_first.id),
            actor=actor,
        )
    with pytest.raises(DeploymentTenantMismatchError, match="agent asset"):
        await register_deployment(
            async_session,
            request=_request(first.id, runtime_first.id, agent_asset_id=asset_second.id),
            actor=actor,
        )
    with pytest.raises(DeploymentTenantMismatchError, match="component asset"):
        await register_deployment(
            async_session,
            request=_request(
                first.id,
                runtime_first.id,
                agent_asset_id=asset_first.id,
                components=[_component(asset_id=asset_second.id)],
            ),
            actor=actor,
        )
    with pytest.raises(DeploymentTenantMismatchError, match="SkillVersion"):
        await register_deployment(
            async_session,
            request=_request(
                first.id,
                runtime_first.id,
                agent_asset_id=asset_first.id,
                components=[
                    _component(
                        key="skill",
                        role=DeploymentComponentRole.SKILL,
                        skill_version_id=version_second.id,
                    )
                ],
            ),
            actor=actor,
        )


async def test_registration_rejects_missing_or_duplicate_component_identity(
    async_session,
) -> None:
    with pytest.raises(ValidationError, match="version reference or content digest"):
        DeploymentComponentCreate(
            component_key="unversioned",
            component_role=DeploymentComponentRole.TOOL,
        )

    actor = await _user(async_session, "component-key")
    namespace = await _namespace(async_session, "component-key", actor)
    runtime = await _runtime(async_session, "component-key", namespace.id)
    with pytest.raises(DeploymentReferenceError, match="component_key"):
        await register_deployment(
            async_session,
            request=_request(
                namespace.id,
                runtime.id,
                components=[
                    _component(key="duplicate"),
                    _component(key="duplicate"),
                ],
            ),
            actor=actor,
        )


@pytest.mark.parametrize(
    "forbidden",
    [
        {"prompt": "raw prompt"},
        {"messages": "raw conversation"},
        {"tool_arguments": '{"secret": true}'},
        {"api_key": "not-allowed"},
        {"password": "not-allowed"},
    ],
)
def test_component_configuration_is_allowlisted_and_secret_free(
    forbidden: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        DeploymentComponentConfiguration.model_validate(forbidden)


def test_deployment_model_has_tenant_uniqueness_and_inventory_indexes() -> None:
    deployment_table = AgentDeployment.__table__
    component_table = DeploymentComponent.__table__

    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in deployment_table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert (
        "namespace_id",
        "runtime_id",
        "external_deployment_id",
        "revision",
    ) in unique_columns
    assert ("public_id",) in unique_columns
    assert any(
        tuple(column.name for column in index.columns)
        == ("namespace_id", "environment", "status", "created_at")
        for index in deployment_table.indexes
    )

    component_unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in component_table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("deployment_id", "component_key") in component_unique_columns


@pytest.mark.mysql
async def test_concurrent_duplicate_registration_has_one_winner(
    async_session_mysql,
) -> None:
    actor = await _user(async_session_mysql, "concurrent")
    namespace = await _namespace(async_session_mysql, "concurrent", actor)
    runtime = await _runtime(async_session_mysql, "concurrent", namespace.id)
    asset = await _asset(async_session_mysql, "concurrent", namespace.id)
    actor_id = actor.id
    request = _request(
        namespace.id,
        runtime.id,
        agent_asset_id=asset.id,
    )
    await async_session_mysql.commit()

    session_factory = async_sessionmaker(
        async_session_mysql.bind,
        expire_on_commit=False,
    )

    async def attempt() -> str:
        async with session_factory() as db:
            concurrent_actor = await db.get(User, actor_id)
            assert concurrent_actor is not None
            try:
                await register_deployment(
                    db,
                    request=request,
                    actor=concurrent_actor,
                )
                await db.commit()
                return "created"
            except DuplicateDeploymentError:
                await db.rollback()
                return "duplicate"

    assert sorted(await asyncio.gather(attempt(), attempt())) == [
        "created",
        "duplicate",
    ]

    rows = (
        await async_session_mysql.execute(
            select(AgentDeployment).where(
                AgentDeployment.namespace_id == namespace.id,
                AgentDeployment.runtime_id == runtime.id,
                AgentDeployment.external_deployment_id
                == request.external_deployment_id,
                AgentDeployment.revision == request.revision,
            )
        )
    ).scalars().all()
    assert len(rows) == 1
