"""Compatibility baselines for the pre-v2 tenant resolution paths.

The control-plane entities do not yet all carry a direct namespace key.  These
tests intentionally exercise the boundaries that are already enforceable:

* namespace-owned assets provide the legacy relational path for runtimes,
  bindings, traces, and collection evidence;
* the self workspace is scoped by asset ownership and trace actor;
* handover analysis filters writes through the case namespace; and
* sensitive trace/evidence reads keep another user's rows out of the result.

They do not assert that a future nullable ``namespace_id`` column is absent.
"""

from __future__ import annotations

from fastapi import HTTPException, Response
from sqlalchemy import select

from app.api.v1.endpoints import control_plane as cp
from app.core.deps import require_asset_reader
from app.models.control_plane import (
    AIAsset,
    AssetOwnership,
    AssetType,
    CollectionJob,
    CollectionTriggerType,
    EvidenceItem,
    EvidenceSourceType,
    EvidenceVisibility,
    HandoverCase,
    HandoverCaseType,
    HandoverItem,
    HandoverStatus,
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
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.user import SystemRole, User


async def _make_user(session, name: str, *, admin: bool = False) -> User:
    user = User(
        username=name,
        email=f"{name}@tenant-path.test",
        hashed_password="x",
        system_role=SystemRole.ADMIN if admin else SystemRole.USER,
    )
    session.add(user)
    await session.flush()
    return user


async def _make_namespace(session, name: str, owner: User) -> Namespace:
    namespace = Namespace(name=name, owner_id=owner.id)
    session.add(namespace)
    await session.flush()
    session.add(
        NamespaceMember(
            namespace_id=namespace.id,
            user_id=owner.id,
            role=NamespaceRole.ADMIN,
        )
    )
    await session.flush()
    return namespace


async def _make_runtime(session, name: str) -> RuntimeInstance:
    runtime = RuntimeInstance(
        provider=RuntimeProvider.OPENCLAW,
        name=name,
        deploy_type=RuntimeDeployType.PRIVATE,
        status=RuntimeStatus.ACTIVE,
    )
    session.add(runtime)
    await session.flush()
    return runtime


async def _make_asset_graph(
    session,
    *,
    namespace: Namespace,
    owner: User,
    runtime: RuntimeInstance,
    suffix: str,
) -> tuple[AIAsset, RuntimeBinding, WorkTrace, EvidenceItem]:
    asset = AIAsset(
        asset_type=AssetType.AGENT,
        name=f"agent-{suffix}",
        source_provider=RuntimeProvider.OPENCLAW,
        source_runtime_id=runtime.id,
        external_id=f"asset-{suffix}",
    )
    session.add(asset)
    await session.flush()
    session.add(
        AssetOwnership(
            asset_id=asset.id,
            owner_type=OwnerType.CREATOR,
            user_id=owner.id,
            namespace_id=namespace.id,
            is_primary=True,
        )
    )
    binding = RuntimeBinding(
        asset_id=asset.id,
        runtime_id=runtime.id,
        external_ref=f"binding-{suffix}",
    )
    trace = WorkTrace(
        runtime_id=runtime.id,
        asset_id=asset.id,
        actor_user_id=owner.id,
        external_session_id=f"session-{suffix}",
        title=f"trace-{suffix}",
        trace_type=TraceType.SESSION,
        sensitivity=Sensitivity.INTERNAL,
    )
    job = CollectionJob(
        runtime_id=runtime.id,
        trigger_type=CollectionTriggerType.SCHEDULED,
        status=JobStatus.SUCCEEDED,
    )
    session.add_all([binding, trace, job])
    await session.flush()
    evidence = EvidenceItem(
        source_type=EvidenceSourceType.API,
        source_provider=RuntimeProvider.OPENCLAW,
        collection_job_id=job.id,
        summary=f"evidence-{suffix}",
        created_by=owner.id,
    )
    session.add(evidence)
    await session.flush()
    return asset, binding, trace, evidence


async def test_namespace_membership_does_not_escalate_to_system_control_plane(async_session):
    tenant_admin = await _make_user(async_session, "tenant-only-admin")
    await _make_namespace(async_session, "tenant-only", tenant_admin)

    try:
        await require_asset_reader(tenant_admin, async_session)
    except HTTPException as exc:
        assert exc.status_code == 403
    else:  # pragma: no cover - makes an accidental permission expansion explicit
        raise AssertionError("namespace membership must not grant system-scope asset.read")


async def test_legacy_namespace_graph_partitions_runtime_asset_binding_trace_and_evidence(async_session):
    owner_a = await _make_user(async_session, "legacy-owner-a")
    owner_b = await _make_user(async_session, "legacy-owner-b")
    namespace_a = await _make_namespace(async_session, "legacy-tenant-a", owner_a)
    namespace_b = await _make_namespace(async_session, "legacy-tenant-b", owner_b)
    runtime_a = await _make_runtime(async_session, "legacy-runtime-a")
    runtime_b = await _make_runtime(async_session, "legacy-runtime-b")
    asset_a, binding_a, trace_a, evidence_a = await _make_asset_graph(
        async_session,
        namespace=namespace_a,
        owner=owner_a,
        runtime=runtime_a,
        suffix="a",
    )
    asset_b, binding_b, trace_b, evidence_b = await _make_asset_graph(
        async_session,
        namespace=namespace_b,
        owner=owner_b,
        runtime=runtime_b,
        suffix="b",
    )

    asset_ids = set(
        (
            await async_session.execute(
                select(AIAsset.id)
                .join(AssetOwnership, AssetOwnership.asset_id == AIAsset.id)
                .where(AssetOwnership.namespace_id == namespace_a.id)
            )
        ).scalars()
    )
    binding_ids = set(
        (
            await async_session.execute(
                select(RuntimeBinding.id)
                .join(AIAsset, AIAsset.id == RuntimeBinding.asset_id)
                .join(AssetOwnership, AssetOwnership.asset_id == AIAsset.id)
                .where(AssetOwnership.namespace_id == namespace_a.id)
            )
        ).scalars()
    )
    trace_ids = set(
        (
            await async_session.execute(
                select(WorkTrace.id)
                .join(AIAsset, AIAsset.id == WorkTrace.asset_id)
                .join(AssetOwnership, AssetOwnership.asset_id == AIAsset.id)
                .where(AssetOwnership.namespace_id == namespace_a.id)
            )
        ).scalars()
    )
    runtime_ids = set(
        (
            await async_session.execute(
                select(RuntimeInstance.id)
                .join(RuntimeBinding, RuntimeBinding.runtime_id == RuntimeInstance.id)
                .join(AIAsset, AIAsset.id == RuntimeBinding.asset_id)
                .join(AssetOwnership, AssetOwnership.asset_id == AIAsset.id)
                .where(AssetOwnership.namespace_id == namespace_a.id)
            )
        ).scalars()
    )
    evidence_ids = set(
        (
            await async_session.execute(
                select(EvidenceItem.id)
                .join(CollectionJob, CollectionJob.id == EvidenceItem.collection_job_id)
                .join(RuntimeInstance, RuntimeInstance.id == CollectionJob.runtime_id)
                .join(RuntimeBinding, RuntimeBinding.runtime_id == RuntimeInstance.id)
                .join(AIAsset, AIAsset.id == RuntimeBinding.asset_id)
                .join(AssetOwnership, AssetOwnership.asset_id == AIAsset.id)
                .where(AssetOwnership.namespace_id == namespace_a.id)
            )
        ).scalars()
    )

    assert asset_ids == {asset_a.id}
    assert binding_ids == {binding_a.id}
    assert trace_ids == {trace_a.id}
    assert runtime_ids == {runtime_a.id}
    assert evidence_ids == {evidence_a.id}
    assert asset_b.id not in asset_ids
    assert binding_b.id not in binding_ids
    assert trace_b.id not in trace_ids
    assert runtime_b.id not in runtime_ids
    assert evidence_b.id not in evidence_ids


async def test_self_workspace_excludes_other_tenant_asset_trace_and_runtime(async_session):
    owner_a = await _make_user(async_session, "workspace-owner-a")
    owner_b = await _make_user(async_session, "workspace-owner-b")
    namespace_a = await _make_namespace(async_session, "workspace-tenant-a", owner_a)
    namespace_b = await _make_namespace(async_session, "workspace-tenant-b", owner_b)
    runtime_a = await _make_runtime(async_session, "workspace-runtime-a")
    runtime_b = await _make_runtime(async_session, "workspace-runtime-b")
    asset_a, _, trace_a, _ = await _make_asset_graph(
        async_session,
        namespace=namespace_a,
        owner=owner_a,
        runtime=runtime_a,
        suffix="workspace-a",
    )
    asset_b, _, trace_b, _ = await _make_asset_graph(
        async_session,
        namespace=namespace_b,
        owner=owner_b,
        runtime=runtime_b,
        suffix="workspace-b",
    )

    workspace = await cp.my_ai_workspace(async_session, owner_a)

    assert {row.id for row in workspace.assets} == {asset_a.id}
    assert {row.id for row in workspace.work_traces} == {trace_a.id}
    assert {row.runtime.id for row in workspace.reporter_statuses} == {runtime_a.id}
    assert asset_b.id not in {row.id for row in workspace.assets}
    assert trace_b.id not in {row.id for row in workspace.work_traces}
    assert runtime_b.id not in {row.runtime.id for row in workspace.reporter_statuses}


async def test_handover_analysis_writes_only_case_namespace_assets_and_linked_evidence(async_session):
    admin = await _make_user(async_session, "handover-tenant-admin", admin=True)
    owner_a = await _make_user(async_session, "handover-owner-a")
    owner_b = await _make_user(async_session, "handover-owner-b")
    namespace_a = await _make_namespace(async_session, "handover-tenant-a", owner_a)
    namespace_b = await _make_namespace(async_session, "handover-tenant-b", owner_b)
    runtime_a = await _make_runtime(async_session, "handover-runtime-a")
    runtime_b = await _make_runtime(async_session, "handover-runtime-b")
    asset_a, _, _, _ = await _make_asset_graph(
        async_session,
        namespace=namespace_a,
        owner=owner_a,
        runtime=runtime_a,
        suffix="handover-a",
    )
    asset_b, _, _, _ = await _make_asset_graph(
        async_session,
        namespace=namespace_b,
        owner=owner_b,
        runtime=runtime_b,
        suffix="handover-b",
    )
    # S1-C handover analysis reads the new direct tenant ownership.  The other
    # tests in this module deliberately keep exercising the legacy join path.
    runtime_a.namespace_id = namespace_a.id
    runtime_b.namespace_id = namespace_b.id
    asset_a.namespace_id = namespace_a.id
    asset_b.namespace_id = namespace_b.id
    await async_session.flush()
    case = HandoverCase(
        case_type=HandoverCaseType.PROJECT_HANDOVER,
        title="tenant-a handover",
        namespace_id=namespace_a.id,
        status=HandoverStatus.DRAFT,
        created_by=admin.id,
    )
    async_session.add(case)
    await async_session.flush()

    items = await cp.analyze_handover(case.id, async_session, admin, response=Response())

    assert {item.asset_id for item in items} == {asset_a.id}
    assert asset_b.id not in {item.asset_id for item in items}
    assert all(item.evidence_id is not None for item in items)
    evidence_ids = {item.evidence_id for item in items}
    evidence_rows = (
        await async_session.execute(select(EvidenceItem).where(EvidenceItem.id.in_(evidence_ids)))
    ).scalars().all()
    assert {row.id for row in evidence_rows} == evidence_ids
    assert {row.namespace_id for row in evidence_rows} == {namespace_a.id}
    persisted_items = (
        await async_session.execute(select(HandoverItem).where(HandoverItem.handover_case_id == case.id))
    ).scalars().all()
    assert {item.asset_id for item in persisted_items} == {asset_a.id}


async def test_sensitive_trace_and_evidence_reads_exclude_other_user_rows(async_session):
    owner_a = await _make_user(async_session, "sensitive-owner-a")
    owner_b = await _make_user(async_session, "sensitive-owner-b")
    runtime = await _make_runtime(async_session, "sensitive-runtime")
    trace_a = WorkTrace(
        runtime_id=runtime.id,
        actor_user_id=owner_a.id,
        title="owner-a restricted trace",
        trace_type=TraceType.SESSION,
        sensitivity=Sensitivity.RESTRICTED,
        metadata_json={"secret": "a"},
    )
    trace_b = WorkTrace(
        runtime_id=runtime.id,
        actor_user_id=owner_b.id,
        title="owner-b restricted trace",
        trace_type=TraceType.SESSION,
        sensitivity=Sensitivity.RESTRICTED,
        metadata_json={"secret": "b"},
    )
    evidence_a = EvidenceItem(
        source_type=EvidenceSourceType.USER_CONFIRM,
        source_provider=RuntimeProvider.CUSTOM,
        summary="owner-a restricted evidence",
        visibility=EvidenceVisibility.RESTRICTED,
        created_by=owner_a.id,
    )
    evidence_b = EvidenceItem(
        source_type=EvidenceSourceType.USER_CONFIRM,
        source_provider=RuntimeProvider.CUSTOM,
        summary="owner-b restricted evidence",
        visibility=EvidenceVisibility.RESTRICTED,
        created_by=owner_b.id,
    )
    async_session.add_all([trace_a, trace_b, evidence_a, evidence_b])
    await async_session.flush()

    traces = await cp.list_work_traces(async_session, owner_a)
    evidence = await cp.list_evidence(async_session, owner_a)

    assert {row.id for row in traces} == {trace_a.id}
    assert traces[0].metadata_json is None
    assert {row.id for row in evidence} == {evidence_a.id}
