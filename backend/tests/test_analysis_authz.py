from __future__ import annotations

import typing
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.v1.endpoints import analysis
from app.models.audit import AuditLog
from app.models.control_plane import (
    AnalysisJobStatus,
    AnalysisResultArtifact,
    AnalysisResultArtifactKind,
    CollectionJob,
    CollectionTriggerType,
    JobStatus,
    ReportAnalysisJob,
    ReportUploadSession,
    ReportUploadStatus,
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
    RuntimeStatus,
)
from app.models.iam import Role, RoleBinding
from app.models.user import SystemRole, User
from app.services.iam_service import ensure_builtin_rbac


async def _make_user(session, *, username, system_role=SystemRole.USER):
    user = User(
        username=username,
        email=f"{username}@duckdock-ai.com",
        hashed_password="x",
        system_role=system_role,
    )
    session.add(user)
    await session.flush()
    return user


async def _bind_role(session, *, user, role_key):
    await ensure_builtin_rbac(session)
    role = (await session.execute(select(Role).where(Role.key == role_key))).scalar_one()
    session.add(RoleBinding(role_id=role.id, user_id=user.id, namespace_id=None))
    await session.flush()


def _robot_user():
    robot = User(id=-1, username="robot:analysis", email="", hashed_password="", is_active=True)
    robot._robot = object()  # type: ignore[attr-defined]
    return robot


async def _seed_analysis_artifact(session):
    now = datetime.now(timezone.utc)
    runtime = RuntimeInstance(
        provider=RuntimeProvider.OPENCLAW,
        name="analysis-authz-runtime",
        deploy_type=RuntimeDeployType.PRIVATE,
        status=RuntimeStatus.ACTIVE,
    )
    session.add(runtime)
    await session.flush()
    collection = CollectionJob(
        runtime_id=runtime.id,
        trigger_type=CollectionTriggerType.SCHEDULED,
        status=JobStatus.SUCCEEDED,
        scope_json={},
    )
    session.add(collection)
    await session.flush()
    upload = ReportUploadSession(
        report_id="rpt_analysis_authz",
        runtime_id=runtime.id,
        collection_job_id=collection.id,
        status=ReportUploadStatus.SUCCEEDED,
        schema_version="duckdock-pack-v1",
        report_type="weekly",
        bucket="duckdock",
        object_key="reports/analysis-authz/pack.zip",
        filename="pack.zip",
        content_type="application/zip",
        upload_expires_at=now + timedelta(minutes=15),
        created_via="test",
    )
    session.add(upload)
    await session.flush()
    job = ReportAnalysisJob(
        report_upload_session_id=upload.id,
        runtime_id=runtime.id,
        status=AnalysisJobStatus.SUCCEEDED,
        priority=100,
        attempts=1,
        max_attempts=3,
        input_bucket="duckdock",
        input_object_key=upload.object_key,
        input_size_bytes=128,
        result_bucket="duckdock",
        result_object_key="analysis/authz/analysis-result.json",
        result_content_type="application/json",
        result_sha256="a" * 64,
        result_size_bytes=64,
        summary_json={},
        started_at=now,
        finished_at=now,
    )
    session.add(job)
    await session.flush()
    artifact = AnalysisResultArtifact(
        analysis_job_id=job.id,
        report_upload_session_id=upload.id,
        runtime_id=runtime.id,
        kind=AnalysisResultArtifactKind.ANALYSIS_RESULT,
        bucket="duckdock",
        object_key=job.result_object_key,
        filename="analysis-result.json",
        content_type="application/json",
        sha256="a" * 64,
        size_bytes=64,
    )
    session.add(artifact)
    await session.flush()
    return artifact


def _dependency_name(endpoint, parameter: str) -> str:
    hints = typing.get_type_hints(endpoint, include_extras=True)
    return hints[parameter].__metadata__[0].dependency.__name__


def test_analysis_read_endpoints_declare_permission_gates():
    assert _dependency_name(analysis.list_analysis_jobs, "current_user") == "require_analysis_reader"
    assert _dependency_name(analysis.list_analysis_job_artifacts, "current_user") == "require_analysis_reader"
    assert _dependency_name(analysis.list_memory_candidates, "current_user") == "require_analysis_reader"
    assert _dependency_name(analysis.get_analysis_artifact_download_link, "access") == (
        "require_analysis_artifact_reader"
    )


async def test_analysis_reader_denies_plain_user_and_robot(async_session):
    plain = await _make_user(async_session, username="analysis-plain")

    for user in [plain, _robot_user()]:
        with pytest.raises(HTTPException) as exc:
            await analysis.require_analysis_reader(user, async_session)
        assert exc.value.status_code == 403


async def test_analysis_reader_allows_asset_reader(async_session):
    user = await _make_user(async_session, username="analysis-reader")
    await _bind_role(async_session, user=user, role_key="enterprise-admin")

    assert await analysis.require_analysis_reader(user, async_session) is user


async def test_analysis_artifact_download_denial_is_audited(async_session):
    plain = await _make_user(async_session, username="analysis-download-plain")
    artifact = await _seed_analysis_artifact(async_session)

    with pytest.raises(HTTPException) as exc:
        await analysis.require_analysis_artifact_reader(artifact.id, async_session, plain)
    logs = (
        await async_session.execute(
            select(AuditLog).where(AuditLog.action == "analysis_artifact.download.denied")
        )
    ).scalars().all()

    assert exc.value.status_code == 403
    assert len(logs) == 1
    assert logs[0].resource_id == artifact.id
    assert logs[0].details["runtime_id"] == artifact.runtime_id


async def test_analysis_artifact_download_success_is_audited(async_session, monkeypatch):
    user = await _make_user(async_session, username="analysis-download-reader")
    await _bind_role(async_session, user=user, role_key="enterprise-admin")
    artifact = await _seed_analysis_artifact(async_session)

    def fake_presign(*, object_key, expires_in=None):
        return {
            "download_url": f"https://minio.local/duckdock/{object_key}?sig=fake",
            "expires_in": expires_in or 900,
            "expires_at": datetime(2030, 1, 1, tzinfo=timezone.utc),
        }

    monkeypatch.setattr(analysis.artifact_service, "generate_presigned_get_url", fake_presign)

    access = await analysis.require_analysis_artifact_reader(artifact.id, async_session, user)
    out = await analysis.get_analysis_artifact_download_link(access, async_session)
    logs = (
        await async_session.execute(
            select(AuditLog).where(AuditLog.action == "analysis_artifact.download.issued")
        )
    ).scalars().all()

    assert out.filename == "analysis-result.json"
    assert out.content_type == "application/json"
    assert "analysis/authz/analysis-result.json" in out.download_url
    assert len(logs) == 1
    assert logs[0].resource_id == artifact.id
    assert logs[0].details["runtime_id"] == artifact.runtime_id
    assert logs[0].details["object_key"] == artifact.object_key
