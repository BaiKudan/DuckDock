"""Push 上报链路:归一化 + 落库 + 幂等(specs/001 T036 · 宪法原则 II)。

2026-06-12 Push-only 决策后,采集主链 = Reporter 上报报告包 → `normalize_duckdock_report_pack`
归一化为 `AdapterCollectionResult` → `persist_collection_result` 落库(资产/工作历程/证据/主体)。
本测试在内存 SQLite 上覆盖:
  1. 归一化:报告包 ZIP → 主体/资产/工作历程/证据 正确解析;
  2. 落库:`persist_collection_result` 建行 + 返回计数;
  3. 幂等(原则 II):同一报告包重复落库,资产/主体/工作历程按 external_id upsert 不重复;
  4. 非法 schema 版本被拒(ReportPackError)。
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest
from sqlalchemy import func, select

import app.models  # noqa: F401 - 注册模型以便 create_all
from app.models.control_plane import (
    AIAsset,
    CollectionJob,
    CollectionTriggerType,
    EvidenceItem,
    JobStatus,
    ProviderPrincipal,
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
    WorkTrace,
)
from app.services.adapter_collection_service import persist_collection_result
from app.services.report_pack_service import ReportPackError, normalize_duckdock_report_pack

OBJECT_URI = "minio://duckdock/reports/rpt_test.zip"


def _sample_pack(report_id: str = "rpt_test") -> bytes:
    """构造一个最小但完整的 DuckDock 报告包(manifest + 主体/资产/会话/证据 + 摘要)。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "schema_version": "duckdock-pack-v1",
                    "report_id": report_id,
                    "provider": "openclaw",
                    "actor_username": "employee",
                }
            ),
        )
        zf.writestr(
            "runtime.json",
            json.dumps({"provider": "openclaw", "runtime_id": "runtime-1", "workspace": "duckdock-demo"}),
        )
        zf.writestr(
            "inventory/principals.json",
            json.dumps(
                [
                    {"id": "acct-emp", "display_name": "Employee One", "username": "employee", "email": "employee@duckdock-ai.com"},
                ]
            ),
        )
        zf.writestr(
            "inventory/skills.json",
            json.dumps(
                [
                    {
                        "id": "skill/upload-check",
                        "name": "DuckDock Upload Check Skill",
                        "summary": "Validates report upload sessions.",
                        "criticality": "high",
                        "owner_external_id": "acct-emp",
                    }
                ]
            ),
        )
        zf.writestr(
            "inventory/sessions.ndjson",
            json.dumps(
                {
                    "id": "session-1",
                    "summary": "Validated upload + finalize + ingestion path.",
                    "asset_external_id": "skill/upload-check",
                    "actor_external_id": "acct-emp",
                }
            )
            + "\n",
        )
        zf.writestr(
            "inventory/artifacts.json",
            json.dumps([{"id": "ev-1", "summary": "redaction log", "sha256": "a" * 64}]),
        )
        zf.writestr(
            "summaries/weekly.md",
            "# DuckDock upload path dry-run\n\nValidated report upload session and ingestion.",
        )
    return buf.getvalue()


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


def test_normalize_rejects_unknown_schema():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"schema_version": "not-a-real-schema", "report_id": "x"}))
    with pytest.raises(ReportPackError):
        normalize_duckdock_report_pack(content=buf.getvalue(), runtime=_make_runtime(), object_uri=OBJECT_URI)


async def test_normalize_parses_pack_into_collection_result():
    runtime = _make_runtime()
    _, result = normalize_duckdock_report_pack(content=_sample_pack(), runtime=runtime, object_uri=OBJECT_URI)

    assert result.adapter_name == "duckdock_reporter"
    assert result.provider == RuntimeProvider.OPENCLAW
    # 一个主体、一个 skill 资产、一个会话工作历程
    assert {p.external_id for p in result.principals} == {"acct-emp"}
    assert {a.external_id for a in result.assets} == {"skill/upload-check"}
    assert [t.external_session_id for t in result.work_traces] == ["session-1"]
    # 证据 = artifacts.json 行 + summaries/*.md
    assert len(result.evidence) >= 2
    # 工作历程关联回资产与主体的 external_id(落库时解析成内部 id)
    trace = result.work_traces[0]
    assert trace.asset_external_id == "skill/upload-check"
    assert trace.actor_external_id == "acct-emp"


async def test_persist_collection_result_writes_rows_and_counts(async_session):
    runtime, job = await _seed_runtime_job(async_session)
    _, result = normalize_duckdock_report_pack(content=_sample_pack(), runtime=runtime, object_uri=OBJECT_URI)

    counts = await persist_collection_result(async_session, job=job, runtime=runtime, result=result)
    await async_session.flush()

    assert counts["principals"] == 1
    assert counts["assets"] == 1
    assert counts["work_traces"] == 1
    assert counts["evidence"] >= 2

    assets = (await async_session.execute(select(AIAsset))).scalars().all()
    assert {a.external_id for a in assets} == {"skill/upload-check"}
    assert assets[0].source_runtime_id == runtime.id
    assert assets[0].source_provider == RuntimeProvider.OPENCLAW

    # 工作历程落库后应解析出内部 asset_id(归属推断生效)
    trace = (await async_session.execute(select(WorkTrace))).scalar_one()
    assert trace.asset_id == assets[0].id
    assert trace.external_session_id == "session-1"

    # job.summary_json 记录归一化计数(可追溯,原则 IV)
    assert job.summary_json["counts"]["assets"] == 1


async def test_persist_is_idempotent_on_repeated_pack(async_session):
    """同一报告包重复上报落库:主体/资产/工作历程按 external_id upsert,不产生重复行(原则 II)。"""
    runtime, job = await _seed_runtime_job(async_session)

    _, first = normalize_duckdock_report_pack(content=_sample_pack(), runtime=runtime, object_uri=OBJECT_URI)
    await persist_collection_result(async_session, job=job, runtime=runtime, result=first)
    await async_session.flush()

    _, second = normalize_duckdock_report_pack(content=_sample_pack(), runtime=runtime, object_uri=OBJECT_URI)
    await persist_collection_result(async_session, job=job, runtime=runtime, result=second)
    await async_session.flush()

    asset_count = (await async_session.execute(select(func.count()).select_from(AIAsset))).scalar_one()
    principal_count = (await async_session.execute(select(func.count()).select_from(ProviderPrincipal))).scalar_one()
    trace_count = (await async_session.execute(select(func.count()).select_from(WorkTrace))).scalar_one()
    assert asset_count == 1, "资产应 upsert 不重复"
    assert principal_count == 1, "主体应 upsert 不重复"
    assert trace_count == 1, "工作历程应按 external_session_id upsert 不重复"

    # 证据为 append-only(每次采集留痕),重复上报会累积——这是预期(原则 IV 证据不可绕过)
    evidence_count = (await async_session.execute(select(func.count()).select_from(EvidenceItem))).scalar_one()
    assert evidence_count >= 4
