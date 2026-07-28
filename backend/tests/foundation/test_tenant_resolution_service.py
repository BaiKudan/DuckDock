"""FND-011 red tests for deterministic tenant resolution.

The resolver is deliberately provider-neutral: callers identify one of the
five legacy control-plane entity types and receive a stable, auditable result.
Only direct keys and typed database relationships are admissible evidence.
Names, membership, provider strings, URLs and free-form metadata are never
tenant evidence.

These tests intentionally import the not-yet-implemented service at test time
so the whole contract remains collectable while the first run is genuinely
red.
"""

from __future__ import annotations

import importlib
import inspect
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

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
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.user import User


def _resolution_api() -> SimpleNamespace:
    module = importlib.import_module("app.services.tenant_resolution_service")
    return SimpleNamespace(
        service_type=module.TenantResolutionService,
        entity_type=module.TenantEntityType,
        status=module.TenantResolutionStatus,
        rule=module.TenantResolutionRule,
        result_type=module.TenantResolutionResult,
    )


async def _make_user(db, suffix: str) -> User:
    user = User(
        username=f"resolver-{suffix}",
        email=f"resolver-{suffix}@example.test",
        hashed_password="not-used",
    )
    db.add(user)
    await db.flush()
    return user


async def _make_namespace(db, suffix: str, *, owner: User | None = None) -> Namespace:
    owner = owner or await _make_user(db, f"owner-{suffix}")
    namespace = Namespace(name=f"resolver-{suffix}", owner_id=owner.id)
    db.add(namespace)
    await db.flush()
    return namespace


async def _make_runtime(
    db,
    suffix: str,
    *,
    namespace_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> RuntimeInstance:
    runtime = RuntimeInstance(
        namespace_id=namespace_id,
        provider=RuntimeProvider.CUSTOM,
        name=f"runtime-{suffix}",
        base_url=f"https://{suffix}.runtime.invalid",
        deploy_type=RuntimeDeployType.PRIVATE,
        status=RuntimeStatus.ACTIVE,
        metadata_json=metadata,
    )
    db.add(runtime)
    await db.flush()
    return runtime


async def _make_asset(
    db,
    suffix: str,
    *,
    namespace_id: int | None = None,
    source_runtime_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> AIAsset:
    asset = AIAsset(
        namespace_id=namespace_id,
        asset_type=AssetType.AGENT,
        name=f"asset-{suffix}",
        source_provider=RuntimeProvider.CUSTOM,
        source_runtime_id=source_runtime_id,
        external_id=f"asset-{suffix}",
        metadata_json=metadata,
    )
    db.add(asset)
    await db.flush()
    return asset


async def _own_asset(
    db,
    *,
    asset: AIAsset,
    namespace_id: int | None,
    owner_type: OwnerType = OwnerType.CREATOR,
    user_id: int | None = None,
    evidence_id: int | None = None,
) -> AssetOwnership:
    ownership = AssetOwnership(
        asset_id=asset.id,
        namespace_id=namespace_id,
        owner_type=owner_type,
        user_id=user_id,
        evidence_id=evidence_id,
    )
    db.add(ownership)
    await db.flush()
    return ownership


async def _bind(
    db,
    *,
    runtime: RuntimeInstance,
    asset: AIAsset,
    namespace_id: int | None = None,
    suffix: str = "binding",
    metadata: dict[str, Any] | None = None,
) -> RuntimeBinding:
    binding = RuntimeBinding(
        namespace_id=namespace_id,
        runtime_id=runtime.id,
        asset_id=asset.id,
        external_ref=suffix,
        metadata_json=metadata,
    )
    db.add(binding)
    await db.flush()
    return binding


async def _make_trace(
    db,
    suffix: str,
    *,
    namespace_id: int | None = None,
    runtime_id: int | None = None,
    asset_id: int | None = None,
    actor_user_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> WorkTrace:
    trace = WorkTrace(
        namespace_id=namespace_id,
        runtime_id=runtime_id,
        asset_id=asset_id,
        actor_user_id=actor_user_id,
        title=f"trace-{suffix}",
        trace_type=TraceType.SESSION,
        sensitivity=Sensitivity.INTERNAL,
        metadata_json=metadata,
    )
    db.add(trace)
    await db.flush()
    return trace


async def _make_evidence(
    db,
    suffix: str,
    *,
    namespace_id: int | None = None,
    work_trace_id: int | None = None,
    collection_job_id: int | None = None,
    created_by: int | None = None,
) -> EvidenceItem:
    evidence = EvidenceItem(
        namespace_id=namespace_id,
        work_trace_id=work_trace_id,
        source_type=EvidenceSourceType.API,
        source_provider=RuntimeProvider.CUSTOM,
        collection_job_id=collection_job_id,
        object_uri=f"s3://resolver/{suffix}",
        summary=f"evidence-{suffix}",
        created_by=created_by,
    )
    db.add(evidence)
    await db.flush()
    return evidence


async def _make_collection_job(db, runtime: RuntimeInstance) -> CollectionJob:
    job = CollectionJob(
        runtime_id=runtime.id,
        trigger_type=CollectionTriggerType.MANUAL,
        status=JobStatus.SUCCEEDED,
    )
    db.add(job)
    await db.flush()
    return job


def _fingerprint(result) -> tuple[Any, ...]:
    return (
        result.target_type,
        result.target_id,
        result.status,
        result.namespace_id,
        result.rule,
        result.candidate_namespace_ids,
        result.reason,
    )


def _assert_resolved(api, result, *, target_type, target_id: int, namespace_id: int, rule) -> None:
    assert isinstance(result, api.result_type)
    assert result.target_type is target_type
    assert result.target_id == target_id
    assert result.status is api.status.RESOLVED
    assert result.namespace_id == namespace_id
    assert result.rule is rule
    assert result.candidate_namespace_ids == (namespace_id,)


def _assert_unresolved(api, result, *, target_type, target_id: int) -> None:
    assert isinstance(result, api.result_type)
    assert result.target_type is target_type
    assert result.target_id == target_id
    assert result.status is api.status.UNRESOLVED
    assert result.namespace_id is None
    assert result.rule is None
    assert result.candidate_namespace_ids == ()
    assert isinstance(result.reason, str) and result.reason.strip()


def _assert_conflict(
    api,
    result,
    *,
    target_type,
    target_id: int,
    rule,
    candidates: tuple[int, ...],
) -> None:
    assert isinstance(result, api.result_type)
    assert result.target_type is target_type
    assert result.target_id == target_id
    assert result.status is api.status.CONFLICT
    assert result.namespace_id is None
    assert result.rule is rule
    assert result.candidate_namespace_ids == tuple(sorted(set(candidates)))
    assert isinstance(result.reason, str) and result.reason.strip()


def test_public_contract_is_async_explicit_and_provider_neutral() -> None:
    api = _resolution_api()

    assert {item.name: item.value for item in api.entity_type} == {
        "RUNTIME_INSTANCE": "runtime_instance",
        "AI_ASSET": "ai_asset",
        "RUNTIME_BINDING": "runtime_binding",
        "WORK_TRACE": "work_trace",
        "EVIDENCE_ITEM": "evidence_item",
    }
    assert {item.name: item.value for item in api.status} == {
        "RESOLVED": "resolved",
        "UNRESOLVED": "unresolved",
        "CONFLICT": "conflict",
    }
    assert {item.name: item.value for item in api.rule} == {
        "DIRECT_NAMESPACE": "direct_namespace",
        "VERIFIED_TYPED_SOURCE": "verified_typed_source",
        "ASSET_OWNERSHIP": "asset_ownership",
        "RUNTIME_BOUND_ASSETS": "runtime_bound_assets",
        "BINDING_RELATIONS": "binding_relations",
        "WORK_TRACE_ASSET": "work_trace_asset",
        "WORK_TRACE_RUNTIME": "work_trace_runtime",
        "WORK_TRACE_RELATIONS": "work_trace_relations",
        "EVIDENCE_WORK_TRACE": "evidence_work_trace",
    }
    signature = inspect.signature(api.service_type.resolve)
    assert inspect.iscoroutinefunction(api.service_type.resolve)
    assert list(signature.parameters) == ["self", "db", "target_type", "target_id"]
    assert signature.parameters["target_type"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["target_id"].kind is inspect.Parameter.KEYWORD_ONLY


async def test_direct_namespace_resolves_all_five_target_entity_types(async_session) -> None:
    api = _resolution_api()
    namespace = await _make_namespace(async_session, "direct")
    runtime = await _make_runtime(async_session, "direct", namespace_id=namespace.id)
    asset = await _make_asset(async_session, "direct", namespace_id=namespace.id)
    binding = await _bind(
        async_session,
        runtime=runtime,
        asset=asset,
        namespace_id=namespace.id,
        suffix="direct",
    )
    trace = await _make_trace(
        async_session,
        "direct",
        namespace_id=namespace.id,
        runtime_id=runtime.id,
        asset_id=asset.id,
    )
    evidence = await _make_evidence(async_session, "direct", namespace_id=namespace.id)

    targets = (
        (api.entity_type.RUNTIME_INSTANCE, runtime.id),
        (api.entity_type.AI_ASSET, asset.id),
        (api.entity_type.RUNTIME_BINDING, binding.id),
        (api.entity_type.WORK_TRACE, trace.id),
        (api.entity_type.EVIDENCE_ITEM, evidence.id),
    )
    for target_type, target_id in targets:
        result = await api.service_type().resolve(
            async_session,
            target_type=target_type,
            target_id=target_id,
        )
        _assert_resolved(
            api,
            result,
            target_type=target_type,
            target_id=target_id,
            namespace_id=namespace.id,
            rule=api.rule.DIRECT_NAMESPACE,
        )


async def test_direct_namespace_has_priority_and_repeat_resolution_is_stable(async_session) -> None:
    api = _resolution_api()
    direct = await _make_namespace(async_session, "direct-priority")
    lower_priority = await _make_namespace(async_session, "lower-priority")
    asset = await _make_asset(async_session, "priority", namespace_id=direct.id)
    await _own_asset(async_session, asset=asset, namespace_id=lower_priority.id)

    first = await api.service_type().resolve(
        async_session,
        target_type=api.entity_type.AI_ASSET,
        target_id=asset.id,
    )
    second = await api.service_type().resolve(
        async_session,
        target_type=api.entity_type.AI_ASSET,
        target_id=asset.id,
    )

    _assert_resolved(
        api,
        first,
        target_type=api.entity_type.AI_ASSET,
        target_id=asset.id,
        namespace_id=direct.id,
        rule=api.rule.DIRECT_NAMESPACE,
    )
    assert _fingerprint(first) == _fingerprint(second)


async def test_soft_deleted_namespace_remains_a_valid_historical_identity(async_session) -> None:
    api = _resolution_api()
    namespace = await _make_namespace(async_session, "soft-deleted-history")
    namespace.deleted_at = datetime.now(timezone.utc)
    direct_asset = await _make_asset(
        async_session,
        "soft-deleted-direct",
        namespace_id=namespace.id,
    )
    owned_asset = await _make_asset(async_session, "soft-deleted-ownership")
    await _own_asset(async_session, asset=owned_asset, namespace_id=namespace.id)
    await async_session.flush()

    direct = await api.service_type().resolve(
        async_session,
        target_type=api.entity_type.AI_ASSET,
        target_id=direct_asset.id,
    )
    ownership = await api.service_type().resolve(
        async_session,
        target_type=api.entity_type.AI_ASSET,
        target_id=owned_asset.id,
    )

    _assert_resolved(
        api,
        direct,
        target_type=api.entity_type.AI_ASSET,
        target_id=direct_asset.id,
        namespace_id=namespace.id,
        rule=api.rule.DIRECT_NAMESPACE,
    )
    _assert_resolved(
        api,
        ownership,
        target_type=api.entity_type.AI_ASSET,
        target_id=owned_asset.id,
        namespace_id=namespace.id,
        rule=api.rule.ASSET_OWNERSHIP,
    )


async def test_asset_ownership_resolves_exactly_one_distinct_namespace(async_session) -> None:
    api = _resolution_api()
    namespace = await _make_namespace(async_session, "ownership-one")
    asset = await _make_asset(async_session, "ownership-one")
    await _own_asset(async_session, asset=asset, namespace_id=namespace.id)
    await _own_asset(
        async_session,
        asset=asset,
        namespace_id=namespace.id,
        owner_type=OwnerType.MAINTAINER,
    )

    result = await api.service_type().resolve(
        async_session,
        target_type=api.entity_type.AI_ASSET,
        target_id=asset.id,
    )

    _assert_resolved(
        api,
        result,
        target_type=api.entity_type.AI_ASSET,
        target_id=asset.id,
        namespace_id=namespace.id,
        rule=api.rule.ASSET_OWNERSHIP,
    )


async def test_asset_ownership_with_two_namespaces_is_a_sorted_stable_conflict(async_session) -> None:
    api = _resolution_api()
    first_namespace = await _make_namespace(async_session, "ownership-conflict-a")
    second_namespace = await _make_namespace(async_session, "ownership-conflict-b")
    asset = await _make_asset(async_session, "ownership-conflict")
    # Reverse insertion order: result order must not depend on database row order.
    await _own_asset(async_session, asset=asset, namespace_id=second_namespace.id)
    await _own_asset(
        async_session,
        asset=asset,
        namespace_id=first_namespace.id,
        owner_type=OwnerType.MAINTAINER,
    )

    first = await api.service_type().resolve(
        async_session,
        target_type=api.entity_type.AI_ASSET,
        target_id=asset.id,
    )
    second = await api.service_type().resolve(
        async_session,
        target_type=api.entity_type.AI_ASSET,
        target_id=asset.id,
    )

    _assert_conflict(
        api,
        first,
        target_type=api.entity_type.AI_ASSET,
        target_id=asset.id,
        rule=api.rule.ASSET_OWNERSHIP,
        candidates=(first_namespace.id, second_namespace.id),
    )
    assert _fingerprint(first) == _fingerprint(second)


async def test_runtime_resolves_only_when_all_bound_assets_agree(async_session) -> None:
    api = _resolution_api()
    namespace = await _make_namespace(async_session, "runtime-agree")
    runtime = await _make_runtime(async_session, "runtime-agree")
    first_asset = await _make_asset(async_session, "runtime-agree-a", namespace_id=namespace.id)
    second_asset = await _make_asset(async_session, "runtime-agree-b", namespace_id=namespace.id)
    await _bind(async_session, runtime=runtime, asset=first_asset, suffix="runtime-agree-a")
    await _bind(async_session, runtime=runtime, asset=second_asset, suffix="runtime-agree-b")

    result = await api.service_type().resolve(
        async_session,
        target_type=api.entity_type.RUNTIME_INSTANCE,
        target_id=runtime.id,
    )

    _assert_resolved(
        api,
        result,
        target_type=api.entity_type.RUNTIME_INSTANCE,
        target_id=runtime.id,
        namespace_id=namespace.id,
        rule=api.rule.RUNTIME_BOUND_ASSETS,
    )


async def test_runtime_bound_to_assets_from_two_namespaces_is_conflict(async_session) -> None:
    api = _resolution_api()
    first_namespace = await _make_namespace(async_session, "runtime-conflict-a")
    second_namespace = await _make_namespace(async_session, "runtime-conflict-b")
    runtime = await _make_runtime(async_session, "runtime-conflict")
    first_asset = await _make_asset(async_session, "runtime-conflict-a", namespace_id=first_namespace.id)
    second_asset = await _make_asset(async_session, "runtime-conflict-b", namespace_id=second_namespace.id)
    await _bind(async_session, runtime=runtime, asset=first_asset, suffix="runtime-conflict-a")
    await _bind(async_session, runtime=runtime, asset=second_asset, suffix="runtime-conflict-b")

    result = await api.service_type().resolve(
        async_session,
        target_type=api.entity_type.RUNTIME_INSTANCE,
        target_id=runtime.id,
    )

    _assert_conflict(
        api,
        result,
        target_type=api.entity_type.RUNTIME_INSTANCE,
        target_id=runtime.id,
        rule=api.rule.RUNTIME_BOUND_ASSETS,
        candidates=(first_namespace.id, second_namespace.id),
    )


async def test_runtime_conflict_lists_candidates_from_conflicting_and_resolved_assets(
    async_session,
) -> None:
    api = _resolution_api()
    first_namespace = await _make_namespace(async_session, "runtime-nested-conflict-a")
    second_namespace = await _make_namespace(async_session, "runtime-nested-conflict-b")
    third_namespace = await _make_namespace(async_session, "runtime-nested-conflict-c")
    runtime = await _make_runtime(async_session, "runtime-nested-conflict")
    ambiguous_asset = await _make_asset(async_session, "runtime-nested-conflict-ambiguous")
    resolved_asset = await _make_asset(
        async_session,
        "runtime-nested-conflict-resolved",
        namespace_id=third_namespace.id,
    )
    await _own_asset(async_session, asset=ambiguous_asset, namespace_id=second_namespace.id)
    await _own_asset(
        async_session,
        asset=ambiguous_asset,
        namespace_id=first_namespace.id,
        owner_type=OwnerType.MAINTAINER,
    )
    await _bind(
        async_session,
        runtime=runtime,
        asset=ambiguous_asset,
        suffix="runtime-nested-conflict-ambiguous",
    )
    await _bind(
        async_session,
        runtime=runtime,
        asset=resolved_asset,
        suffix="runtime-nested-conflict-resolved",
    )

    result = await api.service_type().resolve(
        async_session,
        target_type=api.entity_type.RUNTIME_INSTANCE,
        target_id=runtime.id,
    )

    _assert_conflict(
        api,
        result,
        target_type=api.entity_type.RUNTIME_INSTANCE,
        target_id=runtime.id,
        rule=api.rule.RUNTIME_BOUND_ASSETS,
        candidates=(first_namespace.id, second_namespace.id, third_namespace.id),
    )


async def test_binding_resolves_only_when_runtime_and_asset_agree(async_session) -> None:
    api = _resolution_api()
    namespace = await _make_namespace(async_session, "binding-agree")
    runtime = await _make_runtime(async_session, "binding-agree", namespace_id=namespace.id)
    asset = await _make_asset(async_session, "binding-agree", namespace_id=namespace.id)
    binding = await _bind(async_session, runtime=runtime, asset=asset, suffix="binding-agree")

    result = await api.service_type().resolve(
        async_session,
        target_type=api.entity_type.RUNTIME_BINDING,
        target_id=binding.id,
    )

    _assert_resolved(
        api,
        result,
        target_type=api.entity_type.RUNTIME_BINDING,
        target_id=binding.id,
        namespace_id=namespace.id,
        rule=api.rule.BINDING_RELATIONS,
    )


async def test_binding_rejects_disagreement_and_does_not_accept_one_sided_evidence(async_session) -> None:
    api = _resolution_api()
    first_namespace = await _make_namespace(async_session, "binding-conflict-a")
    second_namespace = await _make_namespace(async_session, "binding-conflict-b")
    runtime = await _make_runtime(async_session, "binding-conflict", namespace_id=first_namespace.id)
    conflicting_asset = await _make_asset(
        async_session,
        "binding-conflict",
        namespace_id=second_namespace.id,
    )
    unresolved_asset = await _make_asset(async_session, "binding-unresolved")
    conflicting = await _bind(
        async_session,
        runtime=runtime,
        asset=conflicting_asset,
        suffix="binding-conflict",
    )
    one_sided = await _bind(
        async_session,
        runtime=runtime,
        asset=unresolved_asset,
        suffix="binding-one-sided",
    )

    conflict_result = await api.service_type().resolve(
        async_session,
        target_type=api.entity_type.RUNTIME_BINDING,
        target_id=conflicting.id,
    )
    unresolved_result = await api.service_type().resolve(
        async_session,
        target_type=api.entity_type.RUNTIME_BINDING,
        target_id=one_sided.id,
    )

    _assert_conflict(
        api,
        conflict_result,
        target_type=api.entity_type.RUNTIME_BINDING,
        target_id=conflicting.id,
        rule=api.rule.BINDING_RELATIONS,
        candidates=(first_namespace.id, second_namespace.id),
    )
    _assert_unresolved(
        api,
        unresolved_result,
        target_type=api.entity_type.RUNTIME_BINDING,
        target_id=one_sided.id,
    )


async def test_work_trace_prefers_asset_and_falls_back_to_runtime(async_session) -> None:
    api = _resolution_api()
    namespace = await _make_namespace(async_session, "trace-precedence")
    runtime = await _make_runtime(async_session, "trace-precedence", namespace_id=namespace.id)
    asset = await _make_asset(async_session, "trace-precedence", namespace_id=namespace.id)
    asset_trace = await _make_trace(
        async_session,
        "trace-asset",
        runtime_id=runtime.id,
        asset_id=asset.id,
    )
    runtime_trace = await _make_trace(
        async_session,
        "trace-runtime",
        runtime_id=runtime.id,
    )

    asset_result = await api.service_type().resolve(
        async_session,
        target_type=api.entity_type.WORK_TRACE,
        target_id=asset_trace.id,
    )
    runtime_result = await api.service_type().resolve(
        async_session,
        target_type=api.entity_type.WORK_TRACE,
        target_id=runtime_trace.id,
    )

    _assert_resolved(
        api,
        asset_result,
        target_type=api.entity_type.WORK_TRACE,
        target_id=asset_trace.id,
        namespace_id=namespace.id,
        rule=api.rule.WORK_TRACE_ASSET,
    )
    _assert_resolved(
        api,
        runtime_result,
        target_type=api.entity_type.WORK_TRACE,
        target_id=runtime_trace.id,
        namespace_id=namespace.id,
        rule=api.rule.WORK_TRACE_RUNTIME,
    )


async def test_work_trace_asset_priority_must_not_hide_runtime_conflict(async_session) -> None:
    api = _resolution_api()
    asset_namespace = await _make_namespace(async_session, "trace-conflict-asset")
    runtime_namespace = await _make_namespace(async_session, "trace-conflict-runtime")
    runtime = await _make_runtime(
        async_session,
        "trace-conflict",
        namespace_id=runtime_namespace.id,
    )
    asset = await _make_asset(
        async_session,
        "trace-conflict",
        namespace_id=asset_namespace.id,
    )
    trace = await _make_trace(
        async_session,
        "trace-conflict",
        runtime_id=runtime.id,
        asset_id=asset.id,
    )

    result = await api.service_type().resolve(
        async_session,
        target_type=api.entity_type.WORK_TRACE,
        target_id=trace.id,
    )

    _assert_conflict(
        api,
        result,
        target_type=api.entity_type.WORK_TRACE,
        target_id=trace.id,
        rule=api.rule.WORK_TRACE_RELATIONS,
        candidates=(asset_namespace.id, runtime_namespace.id),
    )


async def test_work_trace_does_not_fallback_when_its_linked_asset_is_unresolved(async_session) -> None:
    api = _resolution_api()
    namespace = await _make_namespace(async_session, "trace-unresolved-asset")
    runtime = await _make_runtime(
        async_session,
        "trace-unresolved-asset",
        namespace_id=namespace.id,
    )
    asset = await _make_asset(async_session, "trace-unresolved-asset")
    trace = await _make_trace(
        async_session,
        "trace-unresolved-asset",
        runtime_id=runtime.id,
        asset_id=asset.id,
    )

    result = await api.service_type().resolve(
        async_session,
        target_type=api.entity_type.WORK_TRACE,
        target_id=trace.id,
    )

    _assert_unresolved(
        api,
        result,
        target_type=api.entity_type.WORK_TRACE,
        target_id=trace.id,
    )


async def test_evidence_without_typed_work_trace_link_ignores_legacy_relationships(async_session) -> None:
    api = _resolution_api()
    owner = await _make_user(async_session, "evidence-legacy-owner")
    namespace = await _make_namespace(async_session, "evidence-legacy", owner=owner)
    runtime = await _make_runtime(async_session, "evidence-legacy", namespace_id=namespace.id)
    job = await _make_collection_job(async_session, runtime)
    evidence = await _make_evidence(
        async_session,
        "evidence-legacy",
        collection_job_id=job.id,
        created_by=owner.id,
    )
    asset = await _make_asset(
        async_session,
        "evidence-legacy",
        namespace_id=namespace.id,
    )
    await _own_asset(
        async_session,
        asset=asset,
        namespace_id=namespace.id,
        evidence_id=evidence.id,
    )

    result = await api.service_type().resolve(
        async_session,
        target_type=api.entity_type.EVIDENCE_ITEM,
        target_id=evidence.id,
    )

    _assert_unresolved(
        api,
        result,
        target_type=api.entity_type.EVIDENCE_ITEM,
        target_id=evidence.id,
    )


async def test_evidence_resolves_only_from_its_typed_work_trace(async_session) -> None:
    api = _resolution_api()
    namespace = await _make_namespace(async_session, "evidence-typed-trace")
    runtime = await _make_runtime(
        async_session,
        "evidence-typed-trace",
        namespace_id=namespace.id,
    )
    trace = await _make_trace(
        async_session,
        "evidence-typed-trace",
        runtime_id=runtime.id,
    )
    evidence = await _make_evidence(
        async_session,
        "evidence-typed-trace",
        work_trace_id=trace.id,
    )

    result = await api.service_type().resolve(
        async_session,
        target_type=api.entity_type.EVIDENCE_ITEM,
        target_id=evidence.id,
    )

    _assert_resolved(
        api,
        result,
        target_type=api.entity_type.EVIDENCE_ITEM,
        target_id=evidence.id,
        namespace_id=namespace.id,
        rule=api.rule.EVIDENCE_WORK_TRACE,
    )


async def test_no_supported_evidence_is_unresolved_for_all_five_entity_types(async_session) -> None:
    api = _resolution_api()
    runtime = await _make_runtime(async_session, "unresolved")
    asset = await _make_asset(async_session, "unresolved")
    binding = await _bind(async_session, runtime=runtime, asset=asset, suffix="unresolved")
    trace = await _make_trace(async_session, "unresolved")
    evidence = await _make_evidence(async_session, "unresolved")

    targets = (
        (api.entity_type.RUNTIME_INSTANCE, runtime.id),
        (api.entity_type.AI_ASSET, asset.id),
        (api.entity_type.RUNTIME_BINDING, binding.id),
        (api.entity_type.WORK_TRACE, trace.id),
        (api.entity_type.EVIDENCE_ITEM, evidence.id),
    )
    for target_type, target_id in targets:
        result = await api.service_type().resolve(
            async_session,
            target_type=target_type,
            target_id=target_id,
        )
        _assert_unresolved(api, result, target_type=target_type, target_id=target_id)


async def test_membership_names_provider_urls_and_metadata_are_never_tenant_evidence(async_session) -> None:
    api = _resolution_api()
    member = await _make_user(async_session, "weak-evidence-member")
    namespace = await _make_namespace(async_session, "weak-evidence", owner=member)
    async_session.add(
        NamespaceMember(
            namespace_id=namespace.id,
            user_id=member.id,
            role=NamespaceRole.ADMIN,
        )
    )
    weak_metadata = {
        "namespace_id": namespace.id,
        "namespace": namespace.name,
        "tenant": namespace.name,
    }
    runtime = await _make_runtime(async_session, namespace.name, metadata=weak_metadata)
    runtime.name = namespace.name
    runtime.base_url = f"https://{namespace.name}.example.test"
    asset = await _make_asset(
        async_session,
        namespace.name,
        source_runtime_id=runtime.id,
        metadata=weak_metadata,
    )
    asset.name = namespace.name
    await _own_asset(
        async_session,
        asset=asset,
        namespace_id=None,
        user_id=member.id,
    )
    binding = await _bind(
        async_session,
        runtime=runtime,
        asset=asset,
        suffix=namespace.name,
        metadata=weak_metadata,
    )
    trace = await _make_trace(
        async_session,
        namespace.name,
        actor_user_id=member.id,
        metadata=weak_metadata,
    )
    trace.title = namespace.name
    evidence = await _make_evidence(
        async_session,
        namespace.name,
        created_by=member.id,
    )
    evidence.summary = namespace.name
    evidence.object_uri = f"https://{namespace.name}.example.test/evidence"
    await async_session.flush()

    targets = (
        (api.entity_type.RUNTIME_INSTANCE, runtime.id),
        (api.entity_type.AI_ASSET, asset.id),
        (api.entity_type.RUNTIME_BINDING, binding.id),
        (api.entity_type.WORK_TRACE, trace.id),
        (api.entity_type.EVIDENCE_ITEM, evidence.id),
    )
    for target_type, target_id in targets:
        result = await api.service_type().resolve(
            async_session,
            target_type=target_type,
            target_id=target_id,
        )
        _assert_unresolved(api, result, target_type=target_type, target_id=target_id)
