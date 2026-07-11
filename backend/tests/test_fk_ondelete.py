"""DM-03: nullable FKs to runtime_instances / ai_assets are ON DELETE SET NULL.

Relies on the conftest async_session fixture enabling PRAGMA foreign_keys=ON so
SQLite actually enforces the ondelete behavior (matching MySQL).
"""
from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from app.models.control_plane import (
    AIAsset,
    AssetStatus,
    AssetType,
    CollectionJob,
    CollectionTriggerType,
    Criticality,
    JobStatus,
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
    RuntimeStatus,
    TraceType,
    WorkTrace,
)

pytestmark = pytest.mark.asyncio


async def _seed(db):
    runtime = RuntimeInstance(
        provider=RuntimeProvider.OPENCLAW,
        name="fk-runtime",
        deploy_type=RuntimeDeployType.PRIVATE,
        status=RuntimeStatus.ACTIVE,
    )
    db.add(runtime)
    await db.flush()
    asset = AIAsset(
        asset_type=AssetType.SKILL,
        name="fk-asset",
        source_provider=RuntimeProvider.OPENCLAW,
        source_runtime_id=runtime.id,
        status=AssetStatus.ACTIVE,
        criticality=Criticality.MEDIUM,
    )
    job = CollectionJob(
        runtime_id=runtime.id,
        trigger_type=CollectionTriggerType.SCHEDULED,
        status=JobStatus.PENDING,
        scope_json={},
    )
    db.add_all([asset, job])
    await db.flush()
    trace = WorkTrace(
        runtime_id=runtime.id,
        asset_id=asset.id,
        title="fk-trace",
        trace_type=TraceType.SESSION,
    )
    db.add(trace)
    await db.flush()
    return runtime, asset, job, trace


async def test_deleting_runtime_sets_dependent_fks_null(async_session):
    runtime, asset, job, trace = await _seed(async_session)
    rid, aid, jid, tid = runtime.id, asset.id, job.id, trace.id

    await async_session.execute(delete(RuntimeInstance).where(RuntimeInstance.id == rid))
    await async_session.flush()
    async_session.expire_all()

    assert (await async_session.get(CollectionJob, jid)).runtime_id is None
    assert (await async_session.get(AIAsset, aid)).source_runtime_id is None
    assert (await async_session.get(WorkTrace, tid)).runtime_id is None


async def test_deleting_asset_sets_work_trace_fk_null(async_session):
    _runtime, asset, _job, trace = await _seed(async_session)
    aid, tid = asset.id, trace.id

    await async_session.execute(delete(AIAsset).where(AIAsset.id == aid))
    await async_session.flush()
    async_session.expire_all()

    refreshed = (await async_session.execute(select(WorkTrace).where(WorkTrace.id == tid))).scalar_one()
    assert refreshed.asset_id is None
