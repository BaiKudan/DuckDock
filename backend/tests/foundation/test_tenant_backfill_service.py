"""Red-first contract for the Foundation tenant backfill/audit service.

The expand migration deliberately leaves five ``namespace_id`` columns
nullable.  FND-017 must therefore be safe to retry, bounded, resumable and
honest about records that the deterministic FND-016 resolver cannot resolve.
These tests lock that operational contract without inventing a relationship
for ``EvidenceItem`` (the current model has no typed WorkTrace link).
"""
from __future__ import annotations

import importlib
import inspect
import json
from typing import Any

import pytest
from sqlalchemy import update

from app.models.control_plane import (
    AIAsset,
    AssetType,
    EvidenceItem,
    EvidenceSourceType,
    RuntimeBinding,
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
    TraceType,
    WorkTrace,
)
from app.models.namespace import Namespace
from app.models.user import User


EXPECTED_TARGET_ORDER = (
    ("ai_asset", "ai_assets"),
    ("runtime_instance", "runtime_instances"),
    ("runtime_binding", "runtime_bindings"),
    ("work_trace", "work_traces"),
    ("evidence_item", "evidence_items"),
)


def _contracts():
    """Load the two S1-B contracts while keeping the test module collectable."""

    try:
        backfill = importlib.import_module("app.services.tenant_backfill_service")
        resolution = importlib.import_module("app.services.tenant_resolution_service")
    except ModuleNotFoundError as exc:
        pytest.fail(
            "FND-017 is intentionally red until tenant_backfill_service and "
            f"its FND-016 resolver dependency exist: {exc}",
            pytrace=False,
        )
    return backfill, resolution


class StubResolver:
    """Small resolver double using the public FND-016 ``resolve`` seam."""

    def __init__(self, results: dict[tuple[str, int], Any]) -> None:
        self.results = results
        self.calls: list[tuple[str, int]] = []

    async def resolve(self, db, *, target_type, target_id: int):  # noqa: ANN001
        key = (target_type.value, target_id)
        self.calls.append(key)
        result = self.results[key]
        if callable(result):
            result = result(db, target_type, target_id)
        if inspect.isawaitable(result):
            result = await result
        return result


async def _namespace(db, suffix: str) -> Namespace:  # noqa: ANN001
    owner = User(
        username=f"backfill-owner-{suffix}",
        email=f"backfill-owner-{suffix}@example.com",
        hashed_password="x",
    )
    db.add(owner)
    await db.flush()
    namespace = Namespace(name=f"backfill-ns-{suffix}", owner_id=owner.id)
    db.add(namespace)
    await db.flush()
    return namespace


async def _asset(db, suffix: str, *, namespace_id: int | None = None) -> AIAsset:  # noqa: ANN001
    row = AIAsset(
        namespace_id=namespace_id,
        asset_type=AssetType.SKILL,
        name=f"backfill-asset-{suffix}",
        source_provider=RuntimeProvider.CUSTOM,
        external_id=f"backfill-asset-{suffix}",
    )
    db.add(row)
    await db.flush()
    return row


async def _runtime(db, suffix: str, *, namespace_id: int | None = None) -> RuntimeInstance:  # noqa: ANN001
    row = RuntimeInstance(
        namespace_id=namespace_id,
        provider=RuntimeProvider.CUSTOM,
        name=f"backfill-runtime-{suffix}",
        deploy_type=RuntimeDeployType.PRIVATE,
    )
    db.add(row)
    await db.flush()
    return row


async def _target_graph(db, suffix: str) -> dict[str, Any]:  # noqa: ANN001
    runtime = await _runtime(db, suffix)
    asset = await _asset(db, suffix)
    binding = RuntimeBinding(asset_id=asset.id, runtime_id=runtime.id)
    trace = WorkTrace(
        runtime_id=runtime.id,
        asset_id=asset.id,
        title=f"backfill-trace-{suffix}",
        trace_type=TraceType.SESSION,
    )
    # There is intentionally no fabricated EvidenceItem -> WorkTrace link.
    evidence = EvidenceItem(
        source_type=EvidenceSourceType.USER_CONFIRM,
        source_provider=RuntimeProvider.CUSTOM,
        summary=f"backfill-evidence-{suffix}",
    )
    db.add_all([binding, trace, evidence])
    await db.flush()
    return {
        "ai_asset": asset,
        "runtime_instance": runtime,
        "runtime_binding": binding,
        "work_trace": trace,
        "evidence_item": evidence,
    }


def _result(
    resolution,
    *,
    target_type,
    target_id: int,
    status,
    namespace_id: int | None = None,
    rule=None,
    candidates: tuple[int, ...] = (),
    reason: str | None = None,
):
    return resolution.TenantResolutionResult(
        target_type=target_type,
        target_id=target_id,
        status=status,
        namespace_id=namespace_id,
        rule=rule,
        candidate_namespace_ids=candidates,
        reason=reason,
    )


async def test_backfill_visits_exactly_five_targets_in_dependency_order(async_session):
    backfill, resolution = _contracts()
    rows = await _target_graph(async_session, "order")
    results = {}
    for target_name, _table_name in EXPECTED_TARGET_ORDER:
        target_type = resolution.TenantEntityType(target_name)
        row = rows[target_name]
        reason = (
            "no approved typed WorkTrace relation"
            if target_type is resolution.TenantEntityType.EVIDENCE_ITEM
            else "no supported namespace evidence"
        )
        results[(target_name, row.id)] = _result(
            resolution,
            target_type=target_type,
            target_id=row.id,
            status=resolution.TenantResolutionStatus.UNRESOLVED,
            reason=reason,
        )
    resolver = StubResolver(results)

    report = await backfill.run_tenant_backfill(
        async_session,
        resolver=resolver,
        batch_size=20,
    )

    assert [target for target, _record_id in resolver.calls] == [
        target for target, _table in EXPECTED_TARGET_ORDER
    ]
    payload = report.to_dict()
    assert payload["schema_version"] == 1
    assert [(finding["target"], finding["table"]) for finding in payload["findings"]] == list(
        EXPECTED_TARGET_ORDER
    )
    assert report.scanned_count == 5
    assert report.exit_code != 0


async def test_backfill_only_updates_null_namespace_and_does_not_clobber_race(async_session):
    backfill, resolution = _contracts()
    namespace_a = await _namespace(async_session, "null-guard-a")
    namespace_b = await _namespace(async_session, "null-guard-b")
    already_owned = await _asset(async_session, "already-owned", namespace_id=namespace_a.id)
    raced = await _asset(async_session, "raced")

    async def assign_during_resolution(db, target_type, target_id: int):  # noqa: ANN001
        await db.execute(
            update(AIAsset)
            .where(AIAsset.id == target_id)
            .values(namespace_id=namespace_b.id)
        )
        return _result(
            resolution,
            target_type=target_type,
            target_id=target_id,
            status=resolution.TenantResolutionStatus.RESOLVED,
            namespace_id=namespace_a.id,
            rule=resolution.TenantResolutionRule.ASSET_OWNERSHIP,
            candidates=(namespace_a.id,),
        )

    resolver = StubResolver({("ai_asset", raced.id): assign_during_resolution})
    report = await backfill.run_tenant_backfill(
        async_session,
        resolver=resolver,
        batch_size=20,
    )
    await async_session.refresh(already_owned)
    await async_session.refresh(raced)

    assert resolver.calls == [("ai_asset", raced.id)]
    assert already_owned.namespace_id == namespace_a.id
    assert raced.namespace_id == namespace_b.id, (
        "the write must be a conditional UPDATE ... WHERE namespace_id IS NULL"
    )
    assert report.updated_count == 0


async def test_backfill_obeys_total_batch_limit_and_resumes_from_checkpoint(async_session):
    backfill, resolution = _contracts()
    assets = [await _asset(async_session, f"checkpoint-{index}") for index in range(3)]
    target_type = resolution.TenantEntityType.AI_ASSET
    results = {
        (target_type.value, row.id): _result(
            resolution,
            target_type=target_type,
            target_id=row.id,
            status=resolution.TenantResolutionStatus.UNRESOLVED,
            reason="awaiting explicit ownership",
        )
        for row in assets
    }
    resolver = StubResolver(results)

    first = await backfill.run_tenant_backfill(
        async_session,
        resolver=resolver,
        batch_size=2,
        dry_run=True,
    )
    assert first.scanned_count == 2
    assert resolver.calls == [("ai_asset", assets[0].id), ("ai_asset", assets[1].id)]
    assert first.checkpoint.target_type is target_type
    assert first.checkpoint.last_id == assets[1].id

    second = await backfill.run_tenant_backfill(
        async_session,
        resolver=resolver,
        batch_size=2,
        dry_run=True,
        checkpoint=first.checkpoint,
    )
    assert second.scanned_count == 1
    assert resolver.calls[-1] == ("ai_asset", assets[2].id)
    assert second.checkpoint.target_type is target_type
    assert second.checkpoint.last_id == assets[2].id


async def test_dry_run_reports_resolved_rule_without_writing(async_session):
    backfill, resolution = _contracts()
    namespace = await _namespace(async_session, "dry-run")
    asset = await _asset(async_session, "dry-run")
    target_type = resolution.TenantEntityType.AI_ASSET
    resolver = StubResolver(
        {
            (target_type.value, asset.id): _result(
                resolution,
                target_type=target_type,
                target_id=asset.id,
                status=resolution.TenantResolutionStatus.RESOLVED,
                namespace_id=namespace.id,
                rule=resolution.TenantResolutionRule.ASSET_OWNERSHIP,
                candidates=(namespace.id,),
            )
        }
    )

    report = await backfill.run_tenant_backfill(
        async_session,
        resolver=resolver,
        batch_size=10,
        dry_run=True,
    )
    await async_session.refresh(asset)

    assert asset.namespace_id is None
    assert report.dry_run is True
    assert report.resolved_count == 1
    assert report.updated_count == 0
    finding = report.to_dict()["findings"][0]
    assert finding["resolution_rule"] == "asset_ownership"
    assert finding["namespace_id"] == namespace.id
    assert finding["updated"] is False


async def test_backfill_is_idempotent_when_repeated_without_checkpoint(async_session):
    backfill, resolution = _contracts()
    namespace = await _namespace(async_session, "idempotent")
    asset = await _asset(async_session, "idempotent")
    target_type = resolution.TenantEntityType.AI_ASSET
    resolver = StubResolver(
        {
            (target_type.value, asset.id): _result(
                resolution,
                target_type=target_type,
                target_id=asset.id,
                status=resolution.TenantResolutionStatus.RESOLVED,
                namespace_id=namespace.id,
                rule=resolution.TenantResolutionRule.ASSET_OWNERSHIP,
                candidates=(namespace.id,),
            )
        }
    )

    first = await backfill.run_tenant_backfill(async_session, resolver=resolver, batch_size=10)
    second = await backfill.run_tenant_backfill(async_session, resolver=resolver, batch_size=10)
    await async_session.refresh(asset)

    assert asset.namespace_id == namespace.id
    assert first.updated_count == 1
    assert first.exit_code == 0
    assert second.scanned_count == 0
    assert second.updated_count == 0
    assert second.exit_code == 0
    assert resolver.calls == [("ai_asset", asset.id)]


async def test_unresolved_and_conflict_report_is_deterministic_and_nonzero(async_session):
    backfill, resolution = _contracts()
    namespace_a = await _namespace(async_session, "report-a")
    namespace_b = await _namespace(async_session, "report-b")
    asset = await _asset(async_session, "report-conflict")
    runtime = await _runtime(async_session, "report-unresolved")
    asset_type = resolution.TenantEntityType.AI_ASSET
    runtime_type = resolution.TenantEntityType.RUNTIME_INSTANCE
    resolver = StubResolver(
        {
            (asset_type.value, asset.id): _result(
                resolution,
                target_type=asset_type,
                target_id=asset.id,
                status=resolution.TenantResolutionStatus.CONFLICT,
                rule=resolution.TenantResolutionRule.ASSET_OWNERSHIP,
                candidates=tuple(sorted((namespace_b.id, namespace_a.id))),
                reason="ownership candidates disagree",
            ),
            (runtime_type.value, runtime.id): _result(
                resolution,
                target_type=runtime_type,
                target_id=runtime.id,
                status=resolution.TenantResolutionStatus.UNRESOLVED,
                reason="no resolved bound assets",
            ),
        }
    )

    first = await backfill.run_tenant_backfill(
        async_session,
        resolver=resolver,
        batch_size=10,
        dry_run=True,
    )
    second = await backfill.run_tenant_backfill(
        async_session,
        resolver=resolver,
        batch_size=10,
        dry_run=True,
    )

    assert first.to_json() == second.to_json()
    assert first.to_human() == second.to_human()
    assert first.exit_code != 0
    assert first.unresolved_count == 1
    assert first.conflict_count == 1

    payload = json.loads(first.to_json())
    findings = payload["findings"]
    expected_conflict = {
        "target": "ai_asset",
        "table": "ai_assets",
        "id": asset.id,
        "status": "conflict",
        "candidates": sorted((namespace_a.id, namespace_b.id)),
        "reason": "ownership candidates disagree",
    }
    assert {key: findings[0][key] for key in expected_conflict} == expected_conflict
    expected_unresolved = {
        "target": "runtime_instance",
        "table": "runtime_instances",
        "id": runtime.id,
        "status": "unresolved",
        "candidates": [],
        "reason": "no resolved bound assets",
    }
    assert {key: findings[1][key] for key in expected_unresolved} == expected_unresolved
    human = first.to_human()
    assert "unresolved=1" in human
    assert "conflict=1" in human
    assert "ai_assets" in human
    assert "runtime_instances" in human


async def test_invalid_batch_and_checkpoint_fail_before_resolution(async_session):
    backfill, resolution = _contracts()
    resolver = StubResolver({})

    with pytest.raises(ValueError, match="batch_size"):
        await backfill.run_tenant_backfill(async_session, resolver=resolver, batch_size=0)
    with pytest.raises(ValueError, match="batch_size"):
        await backfill.run_tenant_backfill(async_session, resolver=resolver, batch_size=-1)
    with pytest.raises(ValueError, match="last_id"):
        backfill.BackfillCheckpoint(
            target_type=resolution.TenantEntityType.AI_ASSET,
            last_id=-1,
        )

    assert resolver.calls == []
