from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.control_plane import AIAsset, RuntimeInstance
from app.models.deployment import (
    AgentDeployment,
    AgentDeploymentStatus,
    DeploymentComponent,
)
from app.models.skill import Skill, SkillVersion
from app.models.release import ReleaseCandidateEvaluationBinding
from app.models.release_control import ReleaseCandidate
from app.models.package_registry import AgentPackageVersion, AgentPackageVersionStatus
from app.models.user import User
from app.schemas.deployment import (
    AgentDeploymentCreate,
    DeploymentComponentConfiguration,
    DeploymentComponentCreate,
)
from app.services.audit_service import audit
from app.services.tenant_write_service import require_active_namespace


class DeploymentError(ValueError):
    pass


class DeploymentNotFoundError(DeploymentError):
    pass


class DuplicateDeploymentError(DeploymentError):
    pass


class DeploymentReferenceError(DeploymentError):
    pass


class DeploymentTenantMismatchError(DeploymentReferenceError):
    pass


class DeploymentStateError(DeploymentError):
    pass


class DeploymentImmutableError(DeploymentStateError):
    pass


def _public_id() -> str:
    return f"dep_{uuid.uuid4().hex}"


def _is_unique_violation(exc: IntegrityError) -> bool:
    original = exc.orig
    args = getattr(original, "args", ())
    if args and args[0] == 1062:
        return True
    return "unique constraint failed" in str(original).lower()


def _configuration_json(component: DeploymentComponentCreate) -> dict | None:
    if component.configuration is None:
        return None
    return component.configuration.model_dump(exclude_none=True, mode="json")


def _component_row(component: DeploymentComponentCreate) -> DeploymentComponent:
    return DeploymentComponent(
        component_key=component.component_key,
        component_role=component.component_role,
        ai_asset_id=component.ai_asset_id,
        skill_version_id=component.skill_version_id,
        external_version=component.external_version,
        content_digest=component.content_digest,
        configuration_json=_configuration_json(component),
    )


def _assert_unique_component_keys(
    components: Sequence[DeploymentComponentCreate],
) -> None:
    keys = [component.component_key for component in components]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise DeploymentReferenceError(
            f"component_key must be unique within a deployment: {duplicates}"
        )


async def _validate_references(
    db: AsyncSession,
    *,
    namespace_id: int,
    runtime_id: int,
    agent_asset_id: int | None,
    package_version_public_id: str | None,
    components: Sequence[DeploymentComponentCreate],
) -> AgentPackageVersion | None:
    await require_active_namespace(db, namespace_id)
    runtime = await db.get(RuntimeInstance, runtime_id)
    if runtime is None:
        raise DeploymentReferenceError("runtime was not found")
    if runtime.namespace_id != namespace_id:
        raise DeploymentTenantMismatchError(
            "runtime must belong to the deployment Namespace"
        )

    if agent_asset_id is not None:
        agent_asset = await db.get(AIAsset, agent_asset_id)
        if agent_asset is None:
            raise DeploymentReferenceError("agent asset was not found")
        if agent_asset.namespace_id != namespace_id:
            raise DeploymentTenantMismatchError(
                "agent asset must belong to the deployment Namespace"
            )

    package_version: AgentPackageVersion | None = None
    if package_version_public_id is not None:
        package_version = (
            await db.execute(
                select(AgentPackageVersion)
                .options(selectinload(AgentPackageVersion.package))
                .where(AgentPackageVersion.public_id == package_version_public_id)
            )
        ).scalar_one_or_none()
        if package_version is None:
            raise DeploymentReferenceError("PackageVersion was not found")
        if package_version.namespace_id != namespace_id:
            raise DeploymentTenantMismatchError(
                "PackageVersion must belong to the deployment Namespace"
            )
        if package_version.status != AgentPackageVersionStatus.VERIFIED:
            raise DeploymentReferenceError("PackageVersion must be VERIFIED")
        if (
            agent_asset_id is not None
            and package_version.package.agent_asset_id != agent_asset_id
        ):
            raise DeploymentReferenceError(
                "PackageVersion Agent must match the deployment Agent asset"
            )

    _assert_unique_component_keys(components)
    asset_ids = {
        component.ai_asset_id
        for component in components
        if component.ai_asset_id is not None
    }
    if asset_ids:
        assets = (
            await db.execute(select(AIAsset).where(AIAsset.id.in_(asset_ids)))
        ).scalars().all()
        assets_by_id = {asset.id: asset for asset in assets}
        missing = sorted(asset_ids - assets_by_id.keys())
        if missing:
            raise DeploymentReferenceError(
                f"component assets were not found: {missing}"
            )
        mismatched = sorted(
            asset_id
            for asset_id, asset in assets_by_id.items()
            if asset.namespace_id != namespace_id
        )
        if mismatched:
            raise DeploymentTenantMismatchError(
                f"component assets must belong to the deployment Namespace: {mismatched}"
            )
    skill_version_ids = {
        component.skill_version_id
        for component in components
        if component.skill_version_id is not None
    }
    if skill_version_ids:
        rows = (
            await db.execute(
                select(SkillVersion.id, Skill.namespace_id)
                .join(Skill, Skill.id == SkillVersion.skill_id)
                .where(SkillVersion.id.in_(skill_version_ids))
            )
        ).all()
        namespace_by_version: dict[int, int] = {
            int(row[0]): int(row[1])
            for row in rows
        }
        missing = sorted(skill_version_ids - namespace_by_version.keys())
        if missing:
            raise DeploymentReferenceError(
                f"SkillVersion references were not found: {missing}"
            )
        mismatched = sorted(
            version_id
            for version_id, version_namespace_id in namespace_by_version.items()
            if version_namespace_id != namespace_id
        )
        if mismatched:
            raise DeploymentTenantMismatchError(
                "SkillVersion components must belong to the deployment "
                f"Namespace: {mismatched}"
            )
    return package_version


async def _find_duplicate(
    db: AsyncSession,
    request: AgentDeploymentCreate,
) -> AgentDeployment | None:
    return (
        await db.execute(
            select(AgentDeployment)
            .options(selectinload(AgentDeployment.components))
            .where(
                AgentDeployment.namespace_id == request.namespace_id,
                AgentDeployment.runtime_id == request.runtime_id,
                AgentDeployment.external_deployment_id
                == request.external_deployment_id,
                AgentDeployment.revision == request.revision,
            )
        )
    ).scalar_one_or_none()


async def register_deployment(
    db: AsyncSession,
    *,
    request: AgentDeploymentCreate,
    actor: User,
) -> AgentDeployment:
    package_version = await _validate_references(
        db,
        namespace_id=request.namespace_id,
        runtime_id=request.runtime_id,
        agent_asset_id=request.agent_asset_id,
        package_version_public_id=request.package_version_public_id,
        components=request.components,
    )
    if await _find_duplicate(db, request) is not None:
        raise DuplicateDeploymentError(
            "deployment revision is already registered in this Namespace/Runtime"
        )

    deployment = AgentDeployment(
        public_id=_public_id(),
        namespace_id=request.namespace_id,
        runtime_id=request.runtime_id,
        agent_asset_id=request.agent_asset_id,
        package_version_id=package_version.id if package_version is not None else None,
        external_deployment_id=request.external_deployment_id,
        environment=request.environment,
        revision=request.revision,
        configuration_digest=request.configuration_digest,
        status=AgentDeploymentStatus.REGISTERED,
        created_by_user_id=actor.id,
        components=[_component_row(component) for component in request.components],
    )
    deployment.package_version = package_version
    try:
        async with db.begin_nested():
            db.add(deployment)
            await db.flush()
    except IntegrityError as exc:
        # Under MySQL REPEATABLE READ the losing transaction may not see the
        # winner in its original snapshot even though the unique index has
        # already rejected it. Classify the database error itself.
        if _is_unique_violation(exc):
            raise DuplicateDeploymentError(
                "deployment revision is already registered in this Namespace/Runtime"
            ) from exc
        raise

    await audit(
        db,
        user=actor,
        action="deployment.registered",
        resource_type="agent_deployment",
        resource_id=deployment.id,
        namespace_id=deployment.namespace_id,
        details={
            "public_id": deployment.public_id,
            "runtime_id": deployment.runtime_id,
            "environment": deployment.environment,
            "revision": deployment.revision,
            "package_version_public_id": (
                package_version.public_id if package_version is not None else None
            ),
            "component_count": len(deployment.components),
        },
    )
    await db.flush()
    return deployment


async def get_deployment(
    db: AsyncSession,
    public_id: str,
) -> AgentDeployment:
    deployment = (
        await db.execute(
            select(AgentDeployment)
            .options(selectinload(AgentDeployment.components))
            .where(AgentDeployment.public_id == public_id)
        )
    ).scalar_one_or_none()
    if deployment is None:
        raise DeploymentNotFoundError("deployment was not found")
    return deployment


async def list_deployments(
    db: AsyncSession,
    *,
    namespace_id: int,
    runtime_id: int | None = None,
    environment: str | None = None,
    status: AgentDeploymentStatus | None = None,
    limit: int = 100,
) -> list[AgentDeployment]:
    statement = (
        select(AgentDeployment)
        .options(selectinload(AgentDeployment.components))
        .where(AgentDeployment.namespace_id == namespace_id)
        .order_by(AgentDeployment.created_at.desc(), AgentDeployment.id.desc())
        .limit(limit)
    )
    if runtime_id is not None:
        statement = statement.where(AgentDeployment.runtime_id == runtime_id)
    if environment is not None:
        statement = statement.where(AgentDeployment.environment == environment)
    if status is not None:
        statement = statement.where(AgentDeployment.status == status)
    return list((await db.execute(statement)).scalars().all())


async def _locked_deployment(
    db: AsyncSession,
    deployment_id: int,
) -> AgentDeployment:
    deployment = (
        await db.execute(
            select(AgentDeployment)
            .options(selectinload(AgentDeployment.components))
            .where(AgentDeployment.id == deployment_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if deployment is None:
        raise DeploymentNotFoundError("deployment was not found")
    return deployment


async def _revalidate_snapshot(
    db: AsyncSession,
    deployment: AgentDeployment,
) -> None:
    components = [
        DeploymentComponentCreate(
            component_key=component.component_key,
            component_role=component.component_role,
            ai_asset_id=component.ai_asset_id,
            skill_version_id=component.skill_version_id,
            external_version=component.external_version,
            content_digest=component.content_digest,
            configuration=(
                DeploymentComponentConfiguration.model_validate(
                    component.configuration_json
                )
                if component.configuration_json is not None
                else None
            ),
        )
        for component in deployment.components
    ]
    await _validate_references(
        db,
        namespace_id=deployment.namespace_id,
        runtime_id=deployment.runtime_id,
        agent_asset_id=deployment.agent_asset_id,
        package_version_public_id=deployment.package_version_public_id,
        components=components,
    )


async def activate_deployment(
    db: AsyncSession,
    deployment_id: int,
    *,
    actor: User,
) -> AgentDeployment:
    deployment = await _locked_deployment(db, deployment_id)
    if deployment.status != AgentDeploymentStatus.REGISTERED:
        raise DeploymentStateError(
            f"cannot activate deployment in {deployment.status.value} state"
        )
    await _revalidate_snapshot(db, deployment)
    deployment.status = AgentDeploymentStatus.ACTIVE
    deployment.activated_at = datetime.now(timezone.utc)
    await audit(
        db,
        user=actor,
        action="deployment.activated",
        resource_type="agent_deployment",
        resource_id=deployment.id,
        namespace_id=deployment.namespace_id,
        details={"public_id": deployment.public_id, "revision": deployment.revision},
    )
    await db.flush()
    return deployment


async def retire_deployment(
    db: AsyncSession,
    deployment_id: int,
    *,
    actor: User,
) -> AgentDeployment:
    deployment = await _locked_deployment(db, deployment_id)
    if deployment.status != AgentDeploymentStatus.ACTIVE:
        raise DeploymentStateError(
            f"cannot retire deployment in {deployment.status.value} state"
        )
    deployment.status = AgentDeploymentStatus.RETIRED
    deployment.retired_at = datetime.now(timezone.utc)
    await audit(
        db,
        user=actor,
        action="deployment.retired",
        resource_type="agent_deployment",
        resource_id=deployment.id,
        namespace_id=deployment.namespace_id,
        details={"public_id": deployment.public_id, "revision": deployment.revision},
    )
    await db.flush()
    return deployment


async def fail_deployment(
    db: AsyncSession,
    deployment_id: int,
    *,
    actor: User,
    reason_code: str,
) -> AgentDeployment:
    deployment = await _locked_deployment(db, deployment_id)
    if deployment.status != AgentDeploymentStatus.REGISTERED:
        raise DeploymentStateError(
            f"cannot fail deployment in {deployment.status.value} state"
        )
    deployment.status = AgentDeploymentStatus.FAILED
    await audit(
        db,
        user=actor,
        action="deployment.failed",
        resource_type="agent_deployment",
        resource_id=deployment.id,
        namespace_id=deployment.namespace_id,
        details={
            "public_id": deployment.public_id,
            "revision": deployment.revision,
            "reason_code": reason_code,
        },
    )
    await db.flush()
    return deployment


def _require_registered_mutability(deployment: AgentDeployment) -> None:
    if deployment.status != AgentDeploymentStatus.REGISTERED:
        raise DeploymentImmutableError(
            f"deployment in {deployment.status.value} state is immutable"
        )


async def _require_not_release_bound(
    db: AsyncSession,
    deployment: AgentDeployment,
) -> None:
    binding_id = await db.scalar(
        select(ReleaseCandidateEvaluationBinding.id).where(
            ReleaseCandidateEvaluationBinding.deployment_id
            == deployment.id
        ).limit(1)
    )
    if binding_id is not None:
        raise DeploymentImmutableError(
            "deployment is immutable after release evaluation evidence "
            "has been bound"
        )
    candidate_id = await db.scalar(
        select(ReleaseCandidate.id).where(
            ReleaseCandidate.deployment_id == deployment.id
        ).limit(1)
    )
    if candidate_id is not None:
        raise DeploymentImmutableError(
            "deployment is immutable after a ReleaseCandidate has been created"
        )


async def update_registered_deployment(
    db: AsyncSession,
    deployment_id: int,
    *,
    actor: User,
    runtime_id: int | None = None,
    external_deployment_id: str | None = None,
    environment: str | None = None,
    revision: str | None = None,
    configuration_digest: str | None = None,
) -> AgentDeployment:
    deployment = await _locked_deployment(db, deployment_id)
    _require_registered_mutability(deployment)
    await _require_not_release_bound(db, deployment)
    if runtime_id is not None:
        runtime = await db.get(RuntimeInstance, runtime_id)
        if runtime is None:
            raise DeploymentReferenceError("runtime was not found")
        if runtime.namespace_id != deployment.namespace_id:
            raise DeploymentTenantMismatchError(
                "runtime must belong to the deployment Namespace"
            )
        deployment.runtime_id = runtime_id
    if external_deployment_id is not None:
        deployment.external_deployment_id = external_deployment_id
    if environment is not None:
        deployment.environment = environment
    if revision is not None:
        deployment.revision = revision
    if configuration_digest is not None:
        deployment.configuration_digest = configuration_digest
    await audit(
        db,
        user=actor,
        action="deployment.registration.updated",
        resource_type="agent_deployment",
        resource_id=deployment.id,
        namespace_id=deployment.namespace_id,
        details={"public_id": deployment.public_id},
    )
    await db.flush()
    return deployment


async def replace_registered_components(
    db: AsyncSession,
    deployment_id: int,
    components: Sequence[DeploymentComponentCreate],
    *,
    actor: User,
) -> AgentDeployment:
    deployment = await _locked_deployment(db, deployment_id)
    _require_registered_mutability(deployment)
    await _require_not_release_bound(db, deployment)
    await _validate_references(
        db,
        namespace_id=deployment.namespace_id,
        runtime_id=deployment.runtime_id,
        agent_asset_id=deployment.agent_asset_id,
        package_version_public_id=deployment.package_version_public_id,
        components=components,
    )
    deployment.components = [_component_row(component) for component in components]
    await audit(
        db,
        user=actor,
        action="deployment.components.replaced",
        resource_type="agent_deployment",
        resource_id=deployment.id,
        namespace_id=deployment.namespace_id,
        details={
            "public_id": deployment.public_id,
            "component_count": len(components),
        },
    )
    await db.flush()
    return deployment
