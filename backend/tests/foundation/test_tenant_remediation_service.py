from __future__ import annotations

from sqlalchemy import func, select

import pytest

from app.core.security import hash_password
from app.models.audit import AuditLog
from app.models.control_plane import (
    AIAsset,
    AssetType,
    EvidenceItem,
    EvidenceSourceType,
    RuntimeBinding,
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
    RuntimeStatus,
    Sensitivity,
    TraceType,
    WorkTrace,
)
from app.models.namespace import Namespace
from app.models.user import SystemRole, User
from app.schemas.tenant_remediation import (
    TenantRemediationAssignment,
    TenantRemediationManifest,
)
from app.services.tenant_remediation_service import (
    TenantRemediationError,
    apply_tenant_remediation,
)
from app.services.tenant_resolution_service import TenantEntityType


async def _user(session, suffix: str, *, admin: bool = False) -> User:
    user = User(
        username=f"remediation-{suffix}",
        email=f"remediation-{suffix}@example.test",
        full_name=suffix,
        hashed_password=hash_password("password"),
        system_role=SystemRole.ADMIN if admin else SystemRole.USER,
    )
    session.add(user)
    await session.flush()
    return user


async def _namespace(session, suffix: str, owner: User) -> Namespace:
    row = Namespace(name=f"remediation-{suffix}", owner_id=owner.id)
    session.add(row)
    await session.flush()
    return row


async def _graph(session):
    admin = await _user(session, "admin", admin=True)
    namespace = await _namespace(session, "target", admin)
    runtime = RuntimeInstance(
        provider=RuntimeProvider.CUSTOM,
        name="legacy-runtime",
        deploy_type=RuntimeDeployType.PRIVATE,
        status=RuntimeStatus.ACTIVE,
    )
    asset = AIAsset(
        asset_type=AssetType.AGENT,
        name="legacy-asset",
        source_provider=RuntimeProvider.CUSTOM,
        external_id="legacy-asset",
    )
    session.add_all([runtime, asset])
    await session.flush()
    binding = RuntimeBinding(runtime_id=runtime.id, asset_id=asset.id)
    trace = WorkTrace(
        runtime_id=runtime.id,
        asset_id=asset.id,
        title="legacy trace",
        trace_type=TraceType.TASK_RUN,
        sensitivity=Sensitivity.INTERNAL,
    )
    session.add_all([binding, trace])
    await session.flush()
    evidence = EvidenceItem(
        work_trace_id=trace.id,
        source_type=EvidenceSourceType.API,
        source_provider=RuntimeProvider.CUSTOM,
        summary="legacy evidence",
    )
    session.add(evidence)
    await session.flush()
    return admin, namespace, runtime, asset, binding, trace, evidence


def _manifest(admin: User, namespace: Namespace, *rows) -> TenantRemediationManifest:
    return TenantRemediationManifest(
        schema_version=1,
        manifest_id="S1-D:test-graph",
        change_ticket="CHANGE-2026-001",
        reason="Approved recovery of explicit historical tenant ownership",
        approved_by_user_id=admin.id,
        assignments=[
            TenantRemediationAssignment(
                target_type=target_type,
                target_id=row.id,
                namespace_id=namespace.id,
            )
            for target_type, row in rows
        ],
    )


def test_manifest_rejects_unknown_fields_and_duplicate_targets():
    base = {
        "schema_version": 1,
        "manifest_id": "S1-D:test",
        "change_ticket": "CHANGE-1",
        "reason": "Explicit historical ownership approval",
        "approved_by_user_id": 1,
        "assignments": [
            {"target_type": "ai_asset", "target_id": 1, "namespace_id": 1},
        ],
    }

    with pytest.raises(ValueError):
        TenantRemediationManifest.model_validate({**base, "default_namespace_id": 1})
    with pytest.raises(ValueError, match="duplicate"):
        TenantRemediationManifest.model_validate(
            {**base, "assignments": [*base["assignments"], *base["assignments"]]}
        )


async def test_manifest_dry_run_apply_and_replay_are_atomic_and_audited(async_session):
    admin, namespace, runtime, asset, binding, trace, evidence = await _graph(async_session)
    manifest = _manifest(
        admin,
        namespace,
        (TenantEntityType.AI_ASSET, asset),
        (TenantEntityType.RUNTIME_INSTANCE, runtime),
        (TenantEntityType.RUNTIME_BINDING, binding),
        (TenantEntityType.WORK_TRACE, trace),
        (TenantEntityType.EVIDENCE_ITEM, evidence),
    )

    dry_run = await apply_tenant_remediation(async_session, manifest=manifest, dry_run=True)
    assert dry_run.changed_count == 0
    assert dry_run.would_assign_count == 5
    assert {runtime.namespace_id, asset.namespace_id, binding.namespace_id, trace.namespace_id, evidence.namespace_id} == {
        None
    }
    assert (await async_session.execute(select(func.count(AuditLog.id)))).scalar_one() == 0

    applied = await apply_tenant_remediation(async_session, manifest=manifest, dry_run=False)
    assert applied.changed_count == 5
    assert applied.replayed_count == 0
    assert {runtime.namespace_id, asset.namespace_id, binding.namespace_id, trace.namespace_id, evidence.namespace_id} == {
        namespace.id
    }
    audit_rows = (
        await async_session.execute(
            select(AuditLog).where(AuditLog.action == "foundation.tenant.remediated")
        )
    ).scalars().all()
    assert len(audit_rows) == 5
    assert {row.details["manifest_sha256"] for row in audit_rows} == {applied.manifest_sha256}
    assert all(row.user_id == admin.id for row in audit_rows)

    replay = await apply_tenant_remediation(async_session, manifest=manifest, dry_run=False)
    assert replay.changed_count == 0
    assert replay.replayed_count == 5
    assert (await async_session.execute(select(func.count(AuditLog.id)))).scalar_one() == 5


async def test_manifest_rejects_non_admin_and_cross_tenant_graph_before_writes(async_session):
    admin, namespace, runtime, asset, binding, trace, evidence = await _graph(async_session)
    ordinary = await _user(async_session, "ordinary")
    other_namespace = await _namespace(async_session, "other", admin)

    unauthorized = _manifest(
        ordinary,
        namespace,
        (TenantEntityType.AI_ASSET, asset),
    )
    with pytest.raises(TenantRemediationError, match="active system admin"):
        await apply_tenant_remediation(async_session, manifest=unauthorized, dry_run=False)

    inconsistent = TenantRemediationManifest(
        schema_version=1,
        manifest_id="S1-D:cross-tenant",
        change_ticket="CHANGE-2026-002",
        reason="Fixture must be rejected before any historical assignment",
        approved_by_user_id=admin.id,
        assignments=[
            TenantRemediationAssignment(
                target_type=TenantEntityType.AI_ASSET,
                target_id=asset.id,
                namespace_id=other_namespace.id,
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
    with pytest.raises(TenantRemediationError, match="runtime_binding|bound asset"):
        await apply_tenant_remediation(async_session, manifest=inconsistent, dry_run=False)

    assert {runtime.namespace_id, asset.namespace_id, binding.namespace_id, trace.namespace_id, evidence.namespace_id} == {
        None
    }
    assert (await async_session.execute(select(func.count(AuditLog.id)))).scalar_one() == 0


async def test_manifest_rejects_conflicting_existing_assignment_without_overwrite(async_session):
    admin, namespace, runtime, asset, *_ = await _graph(async_session)
    other_namespace = await _namespace(async_session, "existing", admin)
    asset.namespace_id = other_namespace.id
    await async_session.flush()
    manifest = _manifest(
        admin,
        namespace,
        (TenantEntityType.AI_ASSET, asset),
    )

    with pytest.raises(TenantRemediationError, match="already belongs"):
        await apply_tenant_remediation(async_session, manifest=manifest, dry_run=False)

    assert asset.namespace_id == other_namespace.id
    assert (await async_session.execute(select(func.count(AuditLog.id)))).scalar_one() == 0


async def test_manifest_rejects_reverse_relationship_mismatches_before_writes(async_session):
    admin, namespace, runtime, asset, binding, trace, evidence = await _graph(async_session)
    other_namespace = await _namespace(async_session, "reverse-existing", admin)

    binding.namespace_id = other_namespace.id
    await async_session.flush()
    with pytest.raises(TenantRemediationError, match="runtime_binding"):
        await apply_tenant_remediation(
            async_session,
            manifest=_manifest(
                admin,
                namespace,
                (TenantEntityType.AI_ASSET, asset),
            ),
            dry_run=False,
        )
    binding.namespace_id = namespace.id

    asset.namespace_id = namespace.id
    trace.namespace_id = other_namespace.id
    await async_session.flush()
    with pytest.raises(TenantRemediationError, match="work_trace"):
        await apply_tenant_remediation(
            async_session,
            manifest=_manifest(
                admin,
                namespace,
                (TenantEntityType.RUNTIME_INSTANCE, runtime),
            ),
            dry_run=False,
        )
    trace.namespace_id = None

    runtime.namespace_id = namespace.id
    evidence.namespace_id = other_namespace.id
    await async_session.flush()
    with pytest.raises(TenantRemediationError, match="evidence_item"):
        await apply_tenant_remediation(
            async_session,
            manifest=_manifest(
                admin,
                namespace,
                (TenantEntityType.WORK_TRACE, trace),
            ),
            dry_run=False,
        )

    assert binding.namespace_id == namespace.id
    assert trace.namespace_id is None
    assert evidence.namespace_id == other_namespace.id
    assert (await async_session.execute(select(func.count(AuditLog.id)))).scalar_one() == 0
