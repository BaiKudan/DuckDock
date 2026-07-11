"""PUSH-05 回归:无 external_session_id 的工作历程按内容去重(原则 II 幂等)。

缺少 `external_session_id` 的工作历程此前无法去重——每次上报都新增一行,
重复上报会累积重复记录。修复后:当 `external_session_id` 缺失时,改用
基于内容的去重键(runtime_id + title + started_at + payload 哈希),与
`raw_records` 的去重方式对齐。本测试覆盖:
  1. 两条无 external_session_id、内容完全相同的工作历程 → 去重为一行;
  2. 内容不同(标题/起始时间/摘要任一不同)→ 保留两行;
  3. 带 external_session_id 的工作历程 → 维持原 upsert 行为。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

import app.models  # noqa: F401 - 注册模型以便 create_all
from app.models.control_plane import (
    CollectionJob,
    CollectionTriggerType,
    JobStatus,
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
    Sensitivity,
    TraceType,
    WorkTrace,
)
from app.services.adapter_collection_service import persist_collection_result
from app.services.adapters.contracts import (
    AdapterCapabilities,
    AdapterCollectionResult,
    NormalizedWorkTrace,
)


def _make_runtime() -> RuntimeInstance:
    return RuntimeInstance(
        provider=RuntimeProvider.OPENCLAW,
        name="dev-openclaw",
        deploy_type=RuntimeDeployType.SAAS,
    )


async def _seed_runtime_job(session) -> tuple[RuntimeInstance, CollectionJob]:
    runtime = _make_runtime()
    session.add(runtime)
    await session.flush()
    job = CollectionJob(
        runtime_id=runtime.id,
        trigger_type=CollectionTriggerType.WEBHOOK,
        status=JobStatus.RUNNING,
    )
    session.add(job)
    await session.flush()
    return runtime, job


def _capabilities() -> AdapterCapabilities:
    return AdapterCapabilities(
        provider=RuntimeProvider.OPENCLAW,
        adapter_name="duckdock_reporter",
        asset_sync=True,
        principal_sync=True,
        worktrace_sync=True,
        artifact_sync=True,
        backup_import=True,
        backup_create=False,
        restore=False,
        browser_fallback=False,
    )


def _result(*traces: NormalizedWorkTrace) -> AdapterCollectionResult:
    return AdapterCollectionResult(
        adapter_name="duckdock_reporter",
        provider=RuntimeProvider.OPENCLAW,
        capabilities=_capabilities(),
        work_traces=list(traces),
    )


def _trace(**overrides) -> NormalizedWorkTrace:
    base = dict(
        title="Validated upload path",
        external_session_id=None,
        summary="Ran the upload + finalize + ingestion sequence.",
        trace_type=TraceType.SESSION,
        started_at=datetime(2026, 6, 22, 9, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 6, 22, 9, 30, tzinfo=timezone.utc),
        sensitivity=Sensitivity.INTERNAL,
        metadata={"source": "report_pack"},
    )
    base.update(overrides)
    return NormalizedWorkTrace(**base)


async def _trace_count(session) -> int:
    return (await session.execute(select(func.count()).select_from(WorkTrace))).scalar_one()


async def test_identical_traces_without_session_id_are_deduped(async_session):
    """两条无 external_session_id、内容完全相同的工作历程应去重为一行。"""
    runtime, job = await _seed_runtime_job(async_session)

    await persist_collection_result(async_session, job=job, runtime=runtime, result=_result(_trace()))
    await async_session.flush()
    await persist_collection_result(async_session, job=job, runtime=runtime, result=_result(_trace()))
    await async_session.flush()

    assert await _trace_count(async_session) == 1, "相同内容的工作历程应按内容去重,不重复落库"


async def test_identical_traces_in_same_batch_are_deduped(async_session):
    """同一批次内两条相同内容的工作历程也应去重为一行。"""
    runtime, job = await _seed_runtime_job(async_session)

    await persist_collection_result(
        async_session, job=job, runtime=runtime, result=_result(_trace(), _trace())
    )
    await async_session.flush()

    assert await _trace_count(async_session) == 1


async def test_differing_traces_without_session_id_are_kept(async_session):
    """内容不同(标题/起始时间/摘要任一不同)的工作历程应分别保留。"""
    runtime, job = await _seed_runtime_job(async_session)

    await persist_collection_result(async_session, job=job, runtime=runtime, result=_result(_trace()))
    await async_session.flush()
    # 标题不同
    await persist_collection_result(
        async_session, job=job, runtime=runtime, result=_result(_trace(title="Other path"))
    )
    # 起始时间不同
    await persist_collection_result(
        async_session,
        job=job,
        runtime=runtime,
        result=_result(_trace(started_at=datetime(2026, 6, 22, 10, 0, tzinfo=timezone.utc))),
    )
    # 摘要不同(payload 哈希区分)
    await persist_collection_result(
        async_session, job=job, runtime=runtime, result=_result(_trace(summary="Different summary text."))
    )
    await async_session.flush()

    assert await _trace_count(async_session) == 4, "内容不同的工作历程不应被误判为重复"


async def test_traces_with_session_id_keep_upsert_behavior(async_session):
    """带 external_session_id 的工作历程维持原 upsert 行为:重复 id 更新而非新增。"""
    runtime, job = await _seed_runtime_job(async_session)

    await persist_collection_result(
        async_session,
        job=job,
        runtime=runtime,
        result=_result(_trace(external_session_id="session-1")),
    )
    await async_session.flush()
    # 同一 session id 再次上报,标题变化 → upsert 更新现有行
    await persist_collection_result(
        async_session,
        job=job,
        runtime=runtime,
        result=_result(_trace(external_session_id="session-1", title="Updated title")),
    )
    await async_session.flush()

    rows = (await async_session.execute(select(WorkTrace))).scalars().all()
    assert len(rows) == 1, "带 external_session_id 的工作历程应按 id upsert,不重复"
    assert rows[0].title == "Updated title", "upsert 应更新现有行字段"
