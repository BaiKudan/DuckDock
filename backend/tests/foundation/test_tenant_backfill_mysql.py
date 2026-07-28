"""Real-MySQL fixtures for the Foundation tenant backfill.

These tests exercise MySQL persistence and the real resolver/backfill pair.
They are skipped by ``async_session_mysql`` unless ``TEST_MYSQL_URL`` points
to an explicitly allowed test database.
"""

from __future__ import annotations

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.control_plane import (
    AIAsset,
    AssetOwnership,
    AssetType,
    CollectionJob,
    CollectionTriggerType,
    EvidenceItem,
    EvidenceSourceType,
    JobStatus,
    OwnerType,
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
from app.services.tenant_backfill_service import run_tenant_backfill
from app.services.tenant_resolution_service import (
    TenantEntityType,
    TenantResolutionResult,
    TenantResolutionRule,
    TenantResolutionStatus,
)


class _ConcurrentAssignmentResolver:
    def __init__(
        self,
        *,
        other_session_factory,
        assigned_namespace_id: int,
        resolved_namespace_id: int,
    ) -> None:
        self.other_session_factory = other_session_factory
        self.assigned_namespace_id = assigned_namespace_id
        self.resolved_namespace_id = resolved_namespace_id

    async def resolve(self, db, *, target_type, target_id: int):  # noqa: ANN001
        async with self.other_session_factory() as other_session:
            await other_session.execute(
                update(AIAsset)
                .where(AIAsset.id == target_id, AIAsset.namespace_id.is_(None))
                .values(namespace_id=self.assigned_namespace_id)
            )
            await other_session.commit()
        return TenantResolutionResult(
            target_type=target_type,
            target_id=target_id,
            status=TenantResolutionStatus.RESOLVED,
            namespace_id=self.resolved_namespace_id,
            rule=TenantResolutionRule.ASSET_OWNERSHIP,
            candidate_namespace_ids=(self.resolved_namespace_id,),
            reason="fixture_resolution_before_concurrent_assignment",
        )


async def _make_user(db: AsyncSession, suffix: str) -> User:
    user = User(
        username=f"mysql-backfill-{suffix}",
        email=f"mysql-backfill-{suffix}@example.test",
        hashed_password="not-used",
    )
    db.add(user)
    await db.flush()
    return user


async def _make_namespace(
    db: AsyncSession,
    suffix: str,
    *,
    owner: User | None = None,
) -> Namespace:
    owner = owner or await _make_user(db, f"owner-{suffix}")
    namespace = Namespace(name=f"mysql-backfill-{suffix}", owner_id=owner.id)
    db.add(namespace)
    await db.flush()
    return namespace


async def _make_asset(
    db: AsyncSession,
    suffix: str,
    *,
    namespace_id: int | None = None,
) -> AIAsset:
    asset = AIAsset(
        namespace_id=namespace_id,
        asset_type=AssetType.AGENT,
        name=f"mysql-backfill-asset-{suffix}",
        source_provider=RuntimeProvider.CUSTOM,
        external_id=f"mysql-backfill-asset-{suffix}",
    )
    db.add(asset)
    await db.flush()
    return asset


async def _make_runtime(
    db: AsyncSession,
    suffix: str,
    *,
    namespace_id: int | None = None,
) -> RuntimeInstance:
    runtime = RuntimeInstance(
        namespace_id=namespace_id,
        provider=RuntimeProvider.CUSTOM,
        name=f"mysql-backfill-runtime-{suffix}",
        deploy_type=RuntimeDeployType.PRIVATE,
        status=RuntimeStatus.ACTIVE,
    )
    db.add(runtime)
    await db.flush()
    return runtime


async def _own_asset(
    db: AsyncSession,
    *,
    asset: AIAsset,
    namespace: Namespace,
    owner_type: OwnerType = OwnerType.CREATOR,
    user_id: int | None = None,
    evidence_id: int | None = None,
) -> AssetOwnership:
    ownership = AssetOwnership(
        asset_id=asset.id,
        namespace_id=namespace.id,
        owner_type=owner_type,
        user_id=user_id,
        evidence_id=evidence_id,
    )
    db.add(ownership)
    await db.flush()
    return ownership


@pytest.mark.mysql
async def test_mysql_empty_database_backfill_is_a_clean_noop(async_session_mysql) -> None:
    report = await run_tenant_backfill(async_session_mysql, batch_size=100)

    assert report.scanned_count == 0
    assert report.updated_count == 0
    assert report.resolved_count == 0
    assert report.unresolved_count == 0
    assert report.conflict_count == 0
    assert report.exit_code == 0
    assert report.checkpoint is None
    assert report.findings == ()


@pytest.mark.mysql
async def test_mysql_deterministic_asset_runtime_binding_trace_backfill_is_idempotent(
    async_session_mysql,
) -> None:
    namespace = await _make_namespace(async_session_mysql, "deterministic")
    runtime = await _make_runtime(async_session_mysql, "deterministic")
    asset = await _make_asset(async_session_mysql, "deterministic")
    await _own_asset(
        async_session_mysql,
        asset=asset,
        namespace=namespace,
    )
    binding = RuntimeBinding(
        asset_id=asset.id,
        runtime_id=runtime.id,
        external_ref="mysql-backfill-deterministic",
    )
    trace = WorkTrace(
        runtime_id=runtime.id,
        asset_id=asset.id,
        title="mysql deterministic tenant backfill",
        trace_type=TraceType.TASK_RUN,
        sensitivity=Sensitivity.INTERNAL,
    )
    async_session_mysql.add_all([binding, trace])
    await async_session_mysql.flush()

    first = await run_tenant_backfill(async_session_mysql, batch_size=100)
    await async_session_mysql.commit()
    for row in (asset, runtime, binding, trace):
        await async_session_mysql.refresh(row)

    assert first.scanned_count == 4
    assert first.updated_count == 4
    assert first.resolved_count == 4
    assert first.unresolved_count == 0
    assert first.conflict_count == 0
    assert first.exit_code == 0
    assert [finding.target_type for finding in first.findings] == [
        TenantEntityType.AI_ASSET,
        TenantEntityType.RUNTIME_INSTANCE,
        TenantEntityType.RUNTIME_BINDING,
        TenantEntityType.WORK_TRACE,
    ]
    assert [finding.resolution_rule for finding in first.findings] == [
        TenantResolutionRule.ASSET_OWNERSHIP,
        TenantResolutionRule.RUNTIME_BOUND_ASSETS,
        TenantResolutionRule.BINDING_RELATIONS,
        TenantResolutionRule.WORK_TRACE_ASSET,
    ]
    assert {asset.namespace_id, runtime.namespace_id, binding.namespace_id, trace.namespace_id} == {
        namespace.id
    }

    second = await run_tenant_backfill(async_session_mysql, batch_size=100)
    await async_session_mysql.commit()
    for row in (asset, runtime, binding, trace):
        await async_session_mysql.refresh(row)

    assert second.scanned_count == 0
    assert second.updated_count == 0
    assert second.exit_code == 0
    assert second.findings == ()
    assert {asset.namespace_id, runtime.namespace_id, binding.namespace_id, trace.namespace_id} == {
        namespace.id
    }


@pytest.mark.mysql
async def test_mysql_evidence_without_typed_work_trace_link_stays_unresolved(
    async_session_mysql,
) -> None:
    owner = await _make_user(async_session_mysql, "evidence-owner")
    namespace = await _make_namespace(
        async_session_mysql,
        "evidence",
        owner=owner,
    )
    runtime = await _make_runtime(
        async_session_mysql,
        "evidence",
        namespace_id=namespace.id,
    )
    job = CollectionJob(
        runtime_id=runtime.id,
        trigger_type=CollectionTriggerType.MANUAL,
        status=JobStatus.SUCCEEDED,
    )
    evidence = EvidenceItem(
        source_type=EvidenceSourceType.API,
        source_provider=RuntimeProvider.CUSTOM,
        collection_job_id=None,
        summary="mysql evidence without a typed WorkTrace link",
        created_by=owner.id,
    )
    async_session_mysql.add_all([job, evidence])
    await async_session_mysql.flush()
    evidence.collection_job_id = job.id
    asset = await _make_asset(
        async_session_mysql,
        "evidence",
        namespace_id=namespace.id,
    )
    await _own_asset(
        async_session_mysql,
        asset=asset,
        namespace=namespace,
        user_id=owner.id,
        evidence_id=evidence.id,
    )
    await async_session_mysql.flush()

    first = await run_tenant_backfill(async_session_mysql, batch_size=100)
    await async_session_mysql.commit()
    await async_session_mysql.refresh(evidence)

    assert first.scanned_count == 1
    assert first.updated_count == 0
    assert first.resolved_count == 0
    assert first.unresolved_count == 1
    assert first.conflict_count == 0
    assert first.exit_code != 0
    assert first.findings[0].target_type is TenantEntityType.EVIDENCE_ITEM
    assert first.findings[0].status is TenantResolutionStatus.UNRESOLVED
    assert first.findings[0].reason == "no_typed_work_trace_link"
    assert evidence.namespace_id is None

    second = await run_tenant_backfill(async_session_mysql, batch_size=100)
    await async_session_mysql.commit()
    await async_session_mysql.refresh(evidence)

    assert second.to_json() == first.to_json()
    assert evidence.namespace_id is None


@pytest.mark.mysql
async def test_mysql_two_namespace_asset_and_runtime_conflicts_stay_null(
    async_session_mysql,
) -> None:
    namespace_a = await _make_namespace(async_session_mysql, "conflict-a")
    namespace_b = await _make_namespace(async_session_mysql, "conflict-b")

    ambiguous_asset = await _make_asset(async_session_mysql, "ambiguous")
    await _own_asset(
        async_session_mysql,
        asset=ambiguous_asset,
        namespace=namespace_b,
    )
    await _own_asset(
        async_session_mysql,
        asset=ambiguous_asset,
        namespace=namespace_a,
        owner_type=OwnerType.MAINTAINER,
    )

    runtime = await _make_runtime(async_session_mysql, "conflicting-runtime")
    asset_a = await _make_asset(async_session_mysql, "runtime-a")
    asset_b = await _make_asset(async_session_mysql, "runtime-b")
    await _own_asset(async_session_mysql, asset=asset_a, namespace=namespace_a)
    await _own_asset(async_session_mysql, asset=asset_b, namespace=namespace_b)
    binding_a = RuntimeBinding(
        asset_id=asset_a.id,
        runtime_id=runtime.id,
        external_ref="mysql-backfill-runtime-a",
    )
    binding_b = RuntimeBinding(
        asset_id=asset_b.id,
        runtime_id=runtime.id,
        external_ref="mysql-backfill-runtime-b",
    )
    async_session_mysql.add_all([binding_a, binding_b])
    await async_session_mysql.flush()

    report = await run_tenant_backfill(async_session_mysql, batch_size=100)
    await async_session_mysql.commit()
    for row in (ambiguous_asset, asset_a, asset_b, runtime, binding_a, binding_b):
        await async_session_mysql.refresh(row)

    assert report.scanned_count == 6
    assert report.updated_count == 2
    assert report.resolved_count == 2
    assert report.unresolved_count == 0
    assert report.conflict_count == 4
    assert report.exit_code != 0
    assert asset_a.namespace_id == namespace_a.id
    assert asset_b.namespace_id == namespace_b.id
    assert ambiguous_asset.namespace_id is None
    assert runtime.namespace_id is None
    assert binding_a.namespace_id is None
    assert binding_b.namespace_id is None

    findings = {(finding.target_type, finding.target_id): finding for finding in report.findings}
    expected_candidates = tuple(sorted((namespace_a.id, namespace_b.id)))
    asset_conflict = findings[(TenantEntityType.AI_ASSET, ambiguous_asset.id)]
    assert asset_conflict.status is TenantResolutionStatus.CONFLICT
    assert asset_conflict.resolution_rule is TenantResolutionRule.ASSET_OWNERSHIP
    assert asset_conflict.candidates == expected_candidates
    runtime_conflict = findings[(TenantEntityType.RUNTIME_INSTANCE, runtime.id)]
    assert runtime_conflict.status is TenantResolutionStatus.CONFLICT
    assert runtime_conflict.resolution_rule is TenantResolutionRule.RUNTIME_BOUND_ASSETS
    assert runtime_conflict.candidates == expected_candidates


@pytest.mark.mysql
async def test_mysql_cas_race_reads_current_value_and_reports_conflict(
    async_session_mysql,
) -> None:
    resolved_namespace = await _make_namespace(async_session_mysql, "cas-resolved")
    concurrently_assigned = await _make_namespace(async_session_mysql, "cas-concurrent")
    asset = await _make_asset(async_session_mysql, "cas-race")
    await async_session_mysql.commit()

    other_session_factory = async_sessionmaker(
        async_session_mysql.bind,
        expire_on_commit=False,
    )
    resolver = _ConcurrentAssignmentResolver(
        other_session_factory=other_session_factory,
        assigned_namespace_id=concurrently_assigned.id,
        resolved_namespace_id=resolved_namespace.id,
    )

    report = await run_tenant_backfill(
        async_session_mysql,
        resolver=resolver,
        batch_size=10,
    )
    await async_session_mysql.commit()
    await async_session_mysql.refresh(asset)

    assert report.scanned_count == 1
    assert report.updated_count == 0
    assert report.resolved_count == 0
    assert report.conflict_count == 1
    assert report.exit_code != 0
    assert asset.namespace_id == concurrently_assigned.id
    assert report.findings[0].status is TenantResolutionStatus.CONFLICT
    assert report.findings[0].candidates == tuple(
        sorted((resolved_namespace.id, concurrently_assigned.id))
    )
