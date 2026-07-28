"""上报会话状态机(specs/001 T036 尾巴 · 宪法原则 II/III)。

report_upload_service 的 FSM:PENDING → UPLOADED → INGESTING → SUCCEEDED/FAILED。
归一化/落库/幂等已由 test_report_collection_flow 覆盖;本测试聚焦**状态转移与守卫**:
  · create:新建 PENDING + 签 PUT URL;幂等(同 idempotency_key 复用 PENDING / 终态直接返回);超限 413;
  · finalize:校验 size/sha256(head/hash 对象)→ UPLOADED;不匹配 422;跨 runtime 403;终态幂等;存储异常→FAILED;
  · ingest:读对象 → 归一化 → 落库 → SUCCEEDED;坏包→FAILED + AdapterError 留痕并抛出。
全程 mock MinIO(artifact_service),不连真实存储。
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

import app.services.report_upload_service as rus
from app.models.control_plane import (
    AdapterError,
    AnalysisJobStatus,
    CollectionJob,
    JobStatus,
    ReportAnalysisJob,
    ReportUploadStatus,
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
)
from app.models.namespace import Namespace
from app.models.user import SystemRole, User
from app.services.artifact_service import ArtifactStorageError, artifact_service

_AWARE = datetime(2030, 1, 1, tzinfo=timezone.utc)


def _sample_pack() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"schema_version": "duckdock-pack-v1", "report_id": "rpt_x", "provider": "openclaw"}))
        zf.writestr("inventory/skills.json", json.dumps([{"id": "skill/a", "name": "Skill A", "summary": "s"}]))
        zf.writestr("summaries/w.md", "# weekly\n\nok")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _mock_minio(monkeypatch):
    """默认 happy 替身:PUT 签名、对象大小/摘要、对象字节;按需在用例内覆盖。"""
    monkeypatch.setattr(
        artifact_service,
        "generate_presigned_put_url",
        lambda **kw: {"upload_url": "https://minio.local/put", "expires_in": 900, "expires_at": _AWARE},
    )
    monkeypatch.setattr(artifact_service, "head_object", lambda key: {"size_bytes": 1024})
    monkeypatch.setattr(artifact_service, "hash_object", lambda key: {"sha256": "b" * 64})
    monkeypatch.setattr(artifact_service, "read_object_bytes", lambda key, max_bytes=None: _sample_pack())

    async def _noop_analysis_job(db, session):
        return None

    monkeypatch.setattr(rus, "create_report_analysis_job_for_session", _noop_analysis_job)


async def _runtime(session) -> RuntimeInstance:
    suffix = uuid4().hex[:8]
    owner = User(
        username=f"report-fsm-{suffix}",
        email=f"report-fsm-{suffix}@example.test",
        hashed_password="unused",
        system_role=SystemRole.USER,
    )
    session.add(owner)
    await session.flush()
    namespace = Namespace(name=f"report-fsm-{suffix}", owner_id=owner.id)
    session.add(namespace)
    await session.flush()
    rt = RuntimeInstance(
        namespace_id=namespace.id,
        provider=RuntimeProvider.OPENCLAW,
        name="rt",
        deploy_type=RuntimeDeployType.SAAS,
    )
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


# ── create ──────────────────────────────────────────────────────

async def test_create_starts_pending_with_job_and_signed_url(async_session):
    rt = await _runtime(async_session)
    sess, signed = await _create(async_session, rt)
    assert sess.status == ReportUploadStatus.PENDING
    assert signed["upload_url"]
    job = (await async_session.execute(select_job(sess.collection_job_id))).scalar_one()
    assert job.status == JobStatus.PENDING


async def test_create_idempotent_reuses_pending_session(async_session):
    rt = await _runtime(async_session)
    first, _ = await _create(async_session, rt, idempotency_key="k1")
    second, _ = await _create(async_session, rt, idempotency_key="k1")
    assert second.id == first.id  # 复用,不新建


async def test_create_idempotent_returns_terminal_session_without_new(async_session):
    rt = await _runtime(async_session)
    first, _ = await _create(async_session, rt, idempotency_key="k2")
    first.status = ReportUploadStatus.SUCCEEDED
    await async_session.flush()
    second, signed = await _create(async_session, rt, idempotency_key="k2")
    assert second.id == first.id
    assert signed is None  # 终态不再签新 URL


async def test_create_rejects_oversize(async_session):
    rt = await _runtime(async_session)
    huge = rus._max_upload_bytes() + 1
    with pytest.raises(HTTPException) as exc:
        await _create(async_session, rt, expected_size_bytes=huge)
    assert exc.value.status_code == 413


# ── finalize ────────────────────────────────────────────────────

async def test_finalize_pending_to_uploaded(async_session):
    rt = await _runtime(async_session)
    sess, _ = await _create(async_session, rt, expected_size_bytes=1024, expected_sha256="b" * 64)
    out = await rus.finalize_report_upload_session(
        async_session, report_id=sess.report_id, runtime=rt, sha256="b" * 64, size_bytes=1024, manifest={"k": 1}
    )
    assert out.status == ReportUploadStatus.UPLOADED
    assert out.actual_size_bytes == 1024 and out.actual_sha256 == "b" * 64
    assert out.finalized_at is not None


async def test_finalize_always_creates_analysis_job_not_direct_ingest(async_session, monkeypatch):
    rt = await _runtime(async_session)
    sess, _ = await _create(async_session, rt, expected_size_bytes=1024, expected_sha256="b" * 64)

    def _unexpected_enqueue(report_id, session):  # noqa: ANN001
        raise AssertionError("finalize must not enqueue direct report-pack ingestion")

    async def _create_analysis_job(db, session):  # noqa: ANN001
        job = ReportAnalysisJob(
            report_upload_session_id=session.id,
            runtime_id=session.runtime_id,
            status=AnalysisJobStatus.PENDING,
            input_bucket=session.bucket,
            input_object_key=session.object_key,
            input_sha256=session.actual_sha256,
            input_size_bytes=session.actual_size_bytes,
            summary_json={"source": "test"},
        )
        db.add(job)
        await db.flush()
        return job

    monkeypatch.setattr(rus.settings, "REPORT_UPLOAD_DIRECT_INGEST_ENABLED", True)
    monkeypatch.setattr(rus, "_enqueue_report_ingestion", _unexpected_enqueue)
    monkeypatch.setattr(rus, "create_report_analysis_job_for_session", _create_analysis_job)

    out = await rus.finalize_report_upload_session(
        async_session,
        report_id=sess.report_id,
        runtime=rt,
        sha256="b" * 64,
        size_bytes=1024,
        manifest={"schema_version": "duckdock-pack-v1"},
    )

    assert out.status == ReportUploadStatus.UPLOADED
    job = (
        await async_session.execute(
            select_analysis_job(sess.id)
        )
    ).scalar_one()
    assert job.status == AnalysisJobStatus.PENDING
    assert job.input_object_key == sess.object_key


async def test_finalize_size_mismatch_422(async_session, monkeypatch):
    rt = await _runtime(async_session)
    sess, _ = await _create(async_session, rt, expected_size_bytes=2048)
    monkeypatch.setattr(artifact_service, "head_object", lambda key: {"size_bytes": 1024})  # != 2048
    with pytest.raises(HTTPException) as exc:
        await rus.finalize_report_upload_session(
            async_session, report_id=sess.report_id, runtime=rt, sha256=None, size_bytes=None, manifest=None
        )
    assert exc.value.status_code == 422


async def test_finalize_sha_mismatch_422(async_session, monkeypatch):
    rt = await _runtime(async_session)
    sess, _ = await _create(async_session, rt, expected_sha256="a" * 64)
    monkeypatch.setattr(artifact_service, "hash_object", lambda key: {"sha256": "c" * 64})  # != expected
    with pytest.raises(HTTPException) as exc:
        await rus.finalize_report_upload_session(
            async_session, report_id=sess.report_id, runtime=rt, sha256=None, size_bytes=None, manifest=None
        )
    assert exc.value.status_code == 422


async def test_finalize_wrong_runtime_403(async_session):
    rt = await _runtime(async_session)
    other = await _runtime(async_session)
    sess, _ = await _create(async_session, rt)
    with pytest.raises(HTTPException) as exc:
        await rus.finalize_report_upload_session(
            async_session, report_id=sess.report_id, runtime=other, sha256=None, size_bytes=None, manifest=None
        )
    assert exc.value.status_code == 403


async def test_finalize_terminal_is_idempotent_noop(async_session):
    rt = await _runtime(async_session)
    sess, _ = await _create(async_session, rt)
    sess.status = ReportUploadStatus.SUCCEEDED
    await async_session.flush()
    out = await rus.finalize_report_upload_session(
        async_session, report_id=sess.report_id, runtime=rt, sha256=None, size_bytes=None, manifest=None
    )
    assert out.status == ReportUploadStatus.SUCCEEDED  # 直接返回,不重新校验


async def test_finalize_storage_error_marks_failed(async_session, monkeypatch):
    rt = await _runtime(async_session)
    sess, _ = await _create(async_session, rt)

    def _boom(key):
        raise ArtifactStorageError("object missing")

    monkeypatch.setattr(artifact_service, "head_object", _boom)
    with pytest.raises(HTTPException) as exc:
        await rus.finalize_report_upload_session(
            async_session, report_id=sess.report_id, runtime=rt, sha256=None, size_bytes=None, manifest=None
        )
    assert exc.value.status_code == 422
    assert sess.status == ReportUploadStatus.FAILED
    assert sess.error_message


# ── ingest ──────────────────────────────────────────────────────

async def test_ingest_uploaded_to_succeeded(async_session):
    rt = await _runtime(async_session)
    sess, _ = await _create(async_session, rt)
    sess.status = ReportUploadStatus.UPLOADED
    await async_session.flush()

    out = await rus.ingest_report_upload_session(async_session, report_id=sess.report_id)
    assert out.status == ReportUploadStatus.SUCCEEDED
    job = (await async_session.execute(select_job(sess.collection_job_id))).scalar_one()
    assert job.status == JobStatus.SUCCEEDED
    assert job.summary_json["counts"]["assets"] == 1


async def test_ingest_bad_pack_marks_failed_and_records_error(async_session, monkeypatch):
    rt = await _runtime(async_session)
    sess, _ = await _create(async_session, rt)
    sess.status = ReportUploadStatus.UPLOADED
    await async_session.flush()
    monkeypatch.setattr(artifact_service, "read_object_bytes", lambda key, max_bytes=None: b"not-a-zip")

    with pytest.raises(Exception):  # noqa: B017 - 归一化失败应向上抛(任务层据此重试)
        await rus.ingest_report_upload_session(async_session, report_id=sess.report_id)

    assert sess.status == ReportUploadStatus.FAILED
    assert sess.error_message
    err = (await async_session.execute(select_adapter_error(sess.collection_job_id))).scalars().first()
    assert err is not None


async def test_ingest_failure_persists_failed_state_before_reraising(async_session, monkeypatch):
    rt = await _runtime(async_session)
    sess, _ = await _create(async_session, rt)
    sess.status = ReportUploadStatus.UPLOADED
    report_id = sess.report_id
    await async_session.commit()
    monkeypatch.setattr(artifact_service, "read_object_bytes", lambda key, max_bytes=None: b"not-a-zip")

    with pytest.raises(Exception):  # noqa: B017 - worker retry must still see a persisted failure trace
        await rus.ingest_report_upload_session(async_session, report_id=sess.report_id)
    await async_session.rollback()

    persisted = await rus.get_report_upload_session(async_session, report_id=report_id)
    err = (await async_session.execute(select_adapter_error(persisted.collection_job_id))).scalars().first()
    assert persisted.status == ReportUploadStatus.FAILED
    assert persisted.error_message
    assert err is not None


# ── tiny query helpers ──────────────────────────────────────────

def select_job(job_id):
    from sqlalchemy import select

    return select(CollectionJob).where(CollectionJob.id == job_id)


def select_adapter_error(job_id):
    from sqlalchemy import select

    return select(AdapterError).where(AdapterError.collection_job_id == job_id)


def select_analysis_job(session_id):
    from sqlalchemy import select

    return select(ReportAnalysisJob).where(ReportAnalysisJob.report_upload_session_id == session_id)
