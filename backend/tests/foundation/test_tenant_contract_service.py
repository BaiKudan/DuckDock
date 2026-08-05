from __future__ import annotations

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
from app.models.user import User
from app.services.tenant_contract_service import check_tenant_contract


async def _namespace(session, suffix: str) -> Namespace:
    owner = User(
        username=f"contract-{suffix}",
        email=f"contract-{suffix}@example.test",
        hashed_password="not-used",
    )
    session.add(owner)
    await session.flush()
    namespace = Namespace(name=f"contract-{suffix}", owner_id=owner.id)
    session.add(namespace)
    await session.flush()
    return namespace


async def test_empty_database_is_contract_ready(async_session):
    report = await check_tenant_contract(async_session)

    assert report.safe is True
    assert report.total_blockers == 0
    assert report.exit_code == 0
    assert set(report.null_counts) == {
        "runtime_instances",
        "ai_assets",
        "runtime_bindings",
        "work_traces",
        "evidence_items",
    }
    assert all(value == 0 for value in report.null_counts.values())
    assert all(value == 0 for value in report.relationship_counts.values())


async def test_contract_preflight_counts_null_and_cross_tenant_relationships(async_session):
    namespace_a = await _namespace(async_session, "a")
    namespace_b = await _namespace(async_session, "b")
    runtime = RuntimeInstance(
        namespace_id=namespace_a.id,
        provider=RuntimeProvider.CUSTOM,
        name="contract-runtime",
        deploy_type=RuntimeDeployType.PRIVATE,
        status=RuntimeStatus.ACTIVE,
    )
    asset = AIAsset(
        namespace_id=namespace_b.id,
        asset_type=AssetType.AGENT,
        name="contract-asset",
        source_provider=RuntimeProvider.CUSTOM,
        external_id="contract-asset",
    )
    null_asset = AIAsset(
        asset_type=AssetType.SKILL,
        name="contract-null-asset",
        source_provider=RuntimeProvider.CUSTOM,
        external_id="contract-null-asset",
    )
    async_session.add_all([runtime, asset, null_asset])
    await async_session.flush()
    binding = RuntimeBinding(
        namespace_id=namespace_a.id,
        runtime_id=runtime.id,
        asset_id=asset.id,
    )
    trace = WorkTrace(
        namespace_id=namespace_a.id,
        runtime_id=runtime.id,
        asset_id=asset.id,
        title="contract trace",
        trace_type=TraceType.TASK_RUN,
        sensitivity=Sensitivity.INTERNAL,
    )
    async_session.add_all([binding, trace])
    await async_session.flush()
    evidence = EvidenceItem(
        namespace_id=namespace_b.id,
        work_trace_id=trace.id,
        source_type=EvidenceSourceType.API,
        source_provider=RuntimeProvider.CUSTOM,
        summary="contract evidence",
    )
    async_session.add(evidence)
    await async_session.flush()

    report = await check_tenant_contract(async_session)

    assert report.safe is False
    assert report.exit_code == 2
    assert report.null_counts["ai_assets"] == 1
    assert report.relationship_counts == {
        "runtime_binding_runtime_missing_or_mismatch": 0,
        "runtime_binding_asset_missing_or_mismatch": 1,
        "work_trace_runtime_missing_or_mismatch": 0,
        "work_trace_asset_missing_or_mismatch": 1,
        "evidence_work_trace_missing_or_mismatch": 1,
        "runtime_binding_tenant_key_duplicates": 0,
    }
    assert report.total_blockers == 4
    assert "remediate_foundation_tenants.py" in report.remediation_command


async def test_contract_preflight_counts_duplicate_tenant_binding_keys(async_session):
    namespace = await _namespace(async_session, "duplicate-binding")
    runtime = RuntimeInstance(
        namespace_id=namespace.id,
        provider=RuntimeProvider.CUSTOM,
        name="duplicate-binding-runtime",
        deploy_type=RuntimeDeployType.PRIVATE,
        status=RuntimeStatus.ACTIVE,
    )
    asset = AIAsset(
        namespace_id=namespace.id,
        asset_type=AssetType.AGENT,
        name="duplicate-binding-asset",
        source_provider=RuntimeProvider.CUSTOM,
        external_id="duplicate-binding-asset",
    )
    async_session.add_all([runtime, asset])
    await async_session.flush()
    async_session.add_all(
        [
            RuntimeBinding(
                namespace_id=namespace.id,
                runtime_id=runtime.id,
                asset_id=asset.id,
                environment="prod",
            ),
            RuntimeBinding(
                namespace_id=namespace.id,
                runtime_id=runtime.id,
                asset_id=asset.id,
                environment="prod",
            ),
        ]
    )
    await async_session.flush()

    report = await check_tenant_contract(async_session)

    assert report.safe is False
    assert report.total_blockers == 1
    assert (
        report.relationship_counts["runtime_binding_tenant_key_duplicates"]
        == 1
    )
