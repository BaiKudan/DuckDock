from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models.audit import AuditLog
from app.models.control_plane import (
    AIAsset,
    AssetType,
    RuntimeBinding,
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
    RuntimeStatus,
)
from app.models.namespace import Namespace
from app.models.user import SystemRole, User
from app.schemas.tenant_remediation import (
    TenantRemediationAssignment,
    TenantRemediationManifest,
)
from app.services.tenant_contract_service import check_tenant_contract
from app.services.tenant_remediation_service import apply_tenant_remediation
from app.services.tenant_resolution_service import TenantEntityType


@pytest.mark.mysql
async def test_mysql_remediation_apply_is_atomic_and_replay_safe(async_session_mysql):
    admin = User(
        username="mysql-remediation-admin",
        email="mysql-remediation-admin@example.test",
        hashed_password="not-used",
        system_role=SystemRole.ADMIN,
    )
    async_session_mysql.add(admin)
    await async_session_mysql.flush()
    namespace = Namespace(name="mysql-remediation", owner_id=admin.id)
    runtime = RuntimeInstance(
        provider=RuntimeProvider.CUSTOM,
        name="mysql-remediation-runtime",
        deploy_type=RuntimeDeployType.PRIVATE,
        status=RuntimeStatus.ACTIVE,
    )
    asset = AIAsset(
        asset_type=AssetType.AGENT,
        name="mysql-remediation-asset",
        source_provider=RuntimeProvider.CUSTOM,
        external_id="mysql-remediation-asset",
    )
    async_session_mysql.add_all([namespace, runtime, asset])
    await async_session_mysql.flush()
    binding = RuntimeBinding(runtime_id=runtime.id, asset_id=asset.id)
    async_session_mysql.add(binding)
    await async_session_mysql.flush()
    manifest = TenantRemediationManifest(
        schema_version=1,
        manifest_id="S1-D:mysql",
        change_ticket="MYSQL-CHANGE-1",
        reason="Approved real MySQL tenant remediation fixture",
        approved_by_user_id=admin.id,
        assignments=[
            TenantRemediationAssignment(
                target_type=TenantEntityType.AI_ASSET,
                target_id=asset.id,
                namespace_id=namespace.id,
            ),
            TenantRemediationAssignment(
                target_type=TenantEntityType.RUNTIME_INSTANCE,
                target_id=runtime.id,
                namespace_id=namespace.id,
            ),
            TenantRemediationAssignment(
                target_type=TenantEntityType.RUNTIME_BINDING,
                target_id=binding.id,
                namespace_id=namespace.id,
            ),
        ],
    )

    first = await apply_tenant_remediation(
        async_session_mysql,
        manifest=manifest,
        dry_run=False,
    )
    await async_session_mysql.commit()
    second = await apply_tenant_remediation(
        async_session_mysql,
        manifest=manifest,
        dry_run=False,
    )
    await async_session_mysql.commit()

    assert first.changed_count == 3
    assert second.changed_count == 0
    assert second.replayed_count == 3
    assert (await async_session_mysql.execute(select(func.count(AuditLog.id)))).scalar_one() == 3
    contract = await check_tenant_contract(async_session_mysql)
    assert contract.safe is True
    assert contract.total_blockers == 0
