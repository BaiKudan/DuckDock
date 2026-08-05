"""FND-026/027 management API and strict-schema tests."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.deployments import (
    activate_agent_deployment,
    create_agent_deployment,
    get_agent_deployment,
    list_agent_deployments,
    retire_agent_deployment,
)
from app.models.control_plane import (
    AIAsset,
    AssetType,
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
)
from app.models.deployment import AgentDeploymentStatus, DeploymentComponentRole
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.user import SystemRole, User
from app.schemas.deployment import (
    AgentDeploymentCreate,
    DeploymentComponentCreate,
)


async def _seed(db):
    owner = User(
        username="deployment-api-owner",
        email="deployment-api-owner@example.test",
        hashed_password="unused",
        system_role=SystemRole.USER,
    )
    outsider = User(
        username="deployment-api-outsider",
        email="deployment-api-outsider@example.test",
        hashed_password="unused",
        system_role=SystemRole.USER,
    )
    db.add_all([owner, outsider])
    await db.flush()
    namespace = Namespace(name="deployment-api", owner_id=owner.id)
    db.add(namespace)
    await db.flush()
    db.add(
        NamespaceMember(
            namespace_id=namespace.id,
            user_id=owner.id,
            role=NamespaceRole.ADMIN,
        )
    )
    runtime = RuntimeInstance(
        namespace_id=namespace.id,
        provider=RuntimeProvider.CUSTOM,
        name="deployment-api-runtime",
        deploy_type=RuntimeDeployType.PRIVATE,
    )
    asset = AIAsset(
        namespace_id=namespace.id,
        asset_type=AssetType.AGENT,
        name="deployment-api-agent",
        source_provider=RuntimeProvider.CUSTOM,
    )
    db.add_all([runtime, asset])
    await db.flush()
    request = AgentDeploymentCreate(
        namespace_id=namespace.id,
        runtime_id=runtime.id,
        agent_asset_id=asset.id,
        external_deployment_id="api-deployment",
        environment="staging",
        revision="1",
        configuration_digest="a" * 64,
        components=[
            DeploymentComponentCreate(
                component_key="agent",
                component_role=DeploymentComponentRole.AGENT,
                ai_asset_id=asset.id,
            )
        ],
    )
    return owner, outsider, namespace, request


async def test_deployment_management_api_is_namespace_scoped(async_session) -> None:
    owner, outsider, namespace, request = await _seed(async_session)

    with pytest.raises(HTTPException) as denied:
        await create_agent_deployment(request, async_session, outsider)
    assert denied.value.status_code == 403

    created = await create_agent_deployment(request, async_session, owner)
    assert created.namespace_id == namespace.id
    assert created.status == AgentDeploymentStatus.REGISTERED
    assert created.public_id.startswith("dep_")

    listed = await list_agent_deployments(
        namespace_id=namespace.id,
        db=async_session,
        current_user=owner,
        runtime_id=None,
        environment=None,
        deployment_status=None,
        limit=100,
    )
    assert [item.public_id for item in listed] == [created.public_id]

    detail = await get_agent_deployment(created.public_id, async_session, owner)
    assert detail.components[0].component_key == "agent"

    with pytest.raises(HTTPException) as hidden:
        await get_agent_deployment(created.public_id, async_session, outsider)
    assert hidden.value.status_code == 404

    active = await activate_agent_deployment(created.public_id, async_session, owner)
    assert active.status == AgentDeploymentStatus.ACTIVE
    retired = await retire_agent_deployment(created.public_id, async_session, owner)
    assert retired.status == AgentDeploymentStatus.RETIRED


async def test_deployment_api_rejects_duplicate_revision(async_session) -> None:
    owner, _, _, request = await _seed(async_session)
    await create_agent_deployment(request, async_session, owner)

    with pytest.raises(HTTPException) as duplicate:
        await create_agent_deployment(request, async_session, owner)
    assert duplicate.value.status_code == 409
    assert "already registered" in str(duplicate.value.detail)
