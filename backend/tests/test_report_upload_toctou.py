"""上报包摄取 TOCTOU 防护(PUSH-06 · 宪法原则 II)。

finalize 记录的 actual_sha256 与 ingest 时实际读到的字节之间存在 TOCTOU 窗口:
对象可能被替换。摄取前必须对**实际读到的字节**重算 sha256 并与 session.actual_sha256
比对;不一致则会话 FAILED + 明确错误,且**不解析**(不调用 normalize_duckdock_report_pack);
失败态须在抛出前 commit(复用 CORR-03 行为),worker 重试仍能看到已落盘的失败痕迹。
全程 mock MinIO(artifact_service),不连真实存储。
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone

import pytest

import app.services.report_upload_service as rus
from app.models.control_plane import (
    AdapterError,
    ReportUploadStatus,
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
)

_AWARE = datetime(2030, 1, 1, tzinfo=timezone.utc)


def _sample_pack() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps({"schema_version": "duckdock-pack-v1", "report_id": "rpt_x", "provider": "openclaw"}),
        )
        zf.writestr("inventory/skills.json", json.dumps([{"id": "skill/a", "name": "Skill A", "summary": "s"}]))
        zf.writestr("summaries/w.md", "# weekly\n\nok")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _mock_minio(monkeypatch):
    """Happy 替身:PUT 签名 + 对象字节读取;hash/size 校验在 finalize 阶段已被其它套件覆盖。"""
    monkeypatch.setattr(
        rus.artifact_service,
        "generate_presigned_put_url",
        lambda **kw: {"upload_url": "https://minio.local/put", "expires_in": 900, "expires_at": _AWARE},
    )
    monkeypatch.setattr(rus.artifact_service, "read_object_bytes", lambda key, max_bytes=None: _sample_pack())

    async def _noop_analysis_job(db, session):
        return None

    monkeypatch.setattr(rus, "create_report_analysis_job_for_session", _noop_analysis_job)


async def _runtime(session) -> RuntimeInstance:
    rt = RuntimeInstance(provider=RuntimeProvider.OPENCLAW, name="rt", deploy_type=RuntimeDeployType.SAAS)
    session.add(rt)
    await session.flush()
    return rt


async def _create(session, runtime, **over):
    kwargs = dict(
        schema_version="duckdock-pack-v1",
        report_type="weekly",
        period_start=None,
        period_end=None,
        filename="pack.zip",
        content_type="application/zip",
        expected_size_bytes=None,
        expected_sha256=None,
        idempotency_key=None,
        metadata_json=None,
        created_by=None,
        created_via="reporter",
    )
    kwargs.update(over)
    return await rus.create_report_upload_session(session, runtime=runtime, **kwargs)


def select_adapter_error(job_id):
    from sqlalchemy import select

    return select(AdapterError).where(AdapterError.collection_job_id == job_id)


async def test_ingest_hash_mismatch_fails_without_parsing(async_session, monkeypatch):
    """读到的字节 sha256 != session.actual_sha256 → FAILED,绝不进入归一化。"""
    rt = await _runtime(async_session)
    sess, _ = await _create(async_session, rt)
    sess.status = ReportUploadStatus.UPLOADED
    # 记录的哈希与对象实际字节不符(模拟 finalize 后对象被替换)。
    sess.actual_sha256 = "d" * 64
    report_id = sess.report_id
    await async_session.commit()

    parsed = {"called": False}

    def _record_parse(**kwargs):
        parsed["called"] = True
        return {}, None

    monkeypatch.setattr(rus, "normalize_duckdock_report_pack", _record_parse)

    with pytest.raises(Exception) as exc:  # noqa: B017 - 失败须向上抛,任务层据此处理
        await rus.ingest_report_upload_session(async_session, report_id=report_id)
    assert parsed["called"] is False, "parser must not run when recorded hash mismatches read bytes"
    assert "hash" in str(exc.value).lower() or "sha256" in str(exc.value).lower()
    await async_session.rollback()

    persisted = await rus.get_report_upload_session(async_session, report_id=report_id)
    assert persisted.status == ReportUploadStatus.FAILED
    assert persisted.error_message
    err = (await async_session.execute(select_adapter_error(persisted.collection_job_id))).scalars().first()
    assert err is not None


async def test_ingest_hash_match_proceeds(async_session):
    """记录哈希等于实际字节 sha256 → 正常摄取到 SUCCEEDED。"""
    rt = await _runtime(async_session)
    sess, _ = await _create(async_session, rt)
    sess.status = ReportUploadStatus.UPLOADED
    sess.actual_sha256 = hashlib.sha256(_sample_pack()).hexdigest()
    await async_session.flush()

    out = await rus.ingest_report_upload_session(async_session, report_id=sess.report_id)
    assert out.status == ReportUploadStatus.SUCCEEDED


async def test_ingest_no_recorded_hash_proceeds(async_session):
    """未记录哈希(actual_sha256 为 None)时不应被新检查阻断(向后兼容旧会话)。"""
    rt = await _runtime(async_session)
    sess, _ = await _create(async_session, rt)
    sess.status = ReportUploadStatus.UPLOADED
    sess.actual_sha256 = None
    await async_session.flush()

    out = await rus.ingest_report_upload_session(async_session, report_id=sess.report_id)
    assert out.status == ReportUploadStatus.SUCCEEDED
