"""证据签名下载 URL(specs/001 T013 · FR-009 · 宪法原则 V)。

断言:
  · 解析器把 ``s3://bucket/key#frag`` 正确拆成 (bucket, key, archive_path),非受管引用拒签;
  · 端点为证据签发限时 URL,**每次签发进审计**(action + reason + visibility);
  · 敏感级证据(SENSITIVE/RESTRICTED)在 evidence.read 之上额外要求 evidence.sensitive.read;
  · 报告包内部证据签名所在归档对象,回传 archive 内路径,且**不泄漏**原始 object_uri/桶名;
  · object_uri 缺失 → 404,非受管/不可签名引用 → 422,reason 强制 ≥5 字。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from app.api.v1.endpoints.control_plane import issue_evidence_download_link
from app.models.audit import AuditLog
from app.models.control_plane import (
    EvidenceItem,
    EvidenceSourceType,
    EvidenceVisibility,
    RuntimeProvider,
)
from app.models.iam import Role, RoleBinding
from app.models.user import SystemRole, User
from app.schemas.control_plane import EvidenceDownloadLinkRequest
from app.services.artifact_service import artifact_service
from app.services.evidence_service import (
    EvidenceObjectError,
    build_download_link,
    parse_storage_reference,
)
from app.services.iam_service import ensure_builtin_rbac

BUCKET = artifact_service.bucket


def _fake_presign(*, object_key, expires_in=None):
    """替身:回显被签名的 object_key,避免真连 MinIO。"""
    return {
        "download_url": f"https://minio.local/{BUCKET}/{object_key}?sig=fake",
        "expires_in": expires_in or 900,
        "expires_at": datetime(2030, 1, 1, tzinfo=timezone.utc),
    }


@pytest.fixture(autouse=True)
def _patch_presign(monkeypatch):
    monkeypatch.setattr(artifact_service, "generate_presigned_get_url", _fake_presign)


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


async def _bind_enterprise_admin(session, user):
    await ensure_builtin_rbac(session)
    role = (await session.execute(select(Role).where(Role.key == "enterprise-admin"))).scalar_one()
    session.add(RoleBinding(role_id=role.id, user_id=user.id, namespace_id=None))
    await session.flush()


async def _seed_evidence(session, *, object_uri, visibility=EvidenceVisibility.NORMAL, sha256="a" * 64, created_by=None):
    ev = EvidenceItem(
        source_type=EvidenceSourceType.BACKUP_PACKAGE,
        source_provider=RuntimeProvider.OPENCLAW,
        summary="redaction log",
        object_uri=object_uri,
        sha256=sha256,
        visibility=visibility,
        created_by=created_by,
    )
    session.add(ev)
    await session.flush()
    return ev


# ── 解析器(纯函数)─────────────────────────────────────────────

def test_parse_plain_object_reference():
    parsed = parse_storage_reference(f"s3://{BUCKET}/reports/r1/pack.zip")
    assert parsed.bucket == BUCKET
    assert parsed.key == "reports/r1/pack.zip"
    assert parsed.archive_path is None


def test_parse_archive_member_fragment():
    parsed = parse_storage_reference(f"minio://{BUCKET}/reports/r1/pack.zip#inventory/foo.json")
    assert parsed.bucket == BUCKET
    assert parsed.key == "reports/r1/pack.zip"
    assert parsed.archive_path == "inventory/foo.json"


def test_parse_preserves_percent_encoded_key():
    """S3 Key 在 object_uri 里已是转义原文;解析必须**原样保留**,不能 unquote,
    否则 'my%20pack.zip' 会被解码成 'my pack.zip' → 签出指向不存在对象的死链。"""
    parsed = parse_storage_reference(f"s3://{BUCKET}/reports/r1/my%20pack%23v2.zip")
    assert parsed.key == "reports/r1/my%20pack%23v2.zip"  # 原样,未解码
    assert "%20" in parsed.key and "%23" in parsed.key


@pytest.mark.parametrize("uri", ["https://example.com/x", "/local/path", "ftp://h/x"])
def test_parse_rejects_unsignable_scheme(uri):
    with pytest.raises(EvidenceObjectError):
        parse_storage_reference(uri)


def test_parse_rejects_missing_key():
    with pytest.raises(EvidenceObjectError):
        parse_storage_reference("s3://bucket-only")


def test_build_download_link_rejects_unmanaged_bucket():
    with pytest.raises(EvidenceObjectError):
        build_download_link(object_uri="s3://some-other-bucket/key")


# ── 端点行为 ────────────────────────────────────────────────────

async def test_normal_evidence_issues_link_and_audits(async_session):
    user = await _make_user(async_session, username="ev-normal")
    ev = await _seed_evidence(async_session, object_uri=f"s3://{BUCKET}/reports/r1/pack.zip")

    out = await issue_evidence_download_link(
        ev.id, EvidenceDownloadLinkRequest(reason="离职交接证据核对"), async_session, user
    )

    assert out.evidence_id == ev.id
    assert out.is_archive_member is False
    assert out.archive_path is None
    assert out.sha256 == "a" * 64
    assert "reports/r1/pack.zip" in out.download_url

    log = (
        await async_session.execute(
            select(AuditLog).where(AuditLog.action == "evidence.download_link.issued")
        )
    ).scalar_one()
    assert log.resource_id == ev.id
    assert log.resource_type == "evidence_item"
    assert log.details["reason"] == "离职交接证据核对"
    assert log.details["visibility"] == "normal"


async def test_archive_member_evidence_signs_container_and_returns_inner_path(async_session):
    user = await _make_user(async_session, username="ev-frag")
    ev = await _seed_evidence(
        async_session, object_uri=f"s3://{BUCKET}/reports/r1/pack.zip#inventory/redaction.json"
    )

    out = await issue_evidence_download_link(
        ev.id, EvidenceDownloadLinkRequest(reason="审计抽查归档内条目"), async_session, user
    )

    # 签名的是整个归档对象,inner 路径单独回传(片段无法直接签名)
    assert out.is_archive_member is True
    assert out.archive_path == "inventory/redaction.json"
    assert out.download_url.endswith("pack.zip?sig=fake")
    # 不泄漏原始 object_uri / 桶名结构以外的内部细节
    assert "object_uri" not in out.model_dump()


async def test_sensitive_evidence_denied_without_sensitive_permission_and_audited(async_session):
    user = await _make_user(async_session, username="ev-sens-deny")
    restricted = await _seed_evidence(
        async_session,
        object_uri=f"s3://{BUCKET}/reports/r1/pack.zip",
        visibility=EvidenceVisibility.RESTRICTED,
    )
    with pytest.raises(HTTPException) as exc:
        await issue_evidence_download_link(
            restricted.id, EvidenceDownloadLinkRequest(reason="想看敏感证据"), async_session, user
        )
    assert exc.value.status_code == 403

    # 越权尝试必须留痕(denied 审计),且 reason 进 details
    denied = (
        await async_session.execute(
            select(AuditLog).where(AuditLog.action == "evidence.download_link.denied")
        )
    ).scalar_one()
    assert denied.resource_id == restricted.id
    assert denied.details["reason"] == "想看敏感证据"
    assert denied.details["visibility"] == "restricted"

    # 同一个无敏感权限的用户,对 NORMAL 证据仍可签发(拒绝是按可见性、非一刀切)
    normal = await _seed_evidence(
        async_session, object_uri=f"s3://{BUCKET}/reports/r1/normal.zip", visibility=EvidenceVisibility.NORMAL
    )
    out = await issue_evidence_download_link(
        normal.id, EvidenceDownloadLinkRequest(reason="普通证据正常下载"), async_session, user
    )
    assert out.evidence_id == normal.id


async def test_creator_can_download_own_sensitive_evidence_without_permission(async_session):
    """FR-002:创建者本人可取自己的敏感证据,无需 evidence.sensitive.read(与 worktrace reveal 对齐)。"""
    me = await _make_user(async_session, username="ev-owner-self")
    ev = await _seed_evidence(
        async_session,
        object_uri=f"s3://{BUCKET}/reports/r1/pack.zip",
        visibility=EvidenceVisibility.RESTRICTED,
        created_by=me.id,
    )
    out = await issue_evidence_download_link(
        ev.id, EvidenceDownloadLinkRequest(reason="核对本人敏感证据"), async_session, me
    )
    assert out.evidence_id == ev.id
    assert out.visibility == EvidenceVisibility.RESTRICTED


async def test_sensitive_evidence_allowed_for_enterprise_admin_binding(async_session):
    auditor = await _make_user(async_session, username="ev-sens-ok")
    await _bind_enterprise_admin(async_session, auditor)  # 含 evidence.sensitive.read
    ev = await _seed_evidence(
        async_session,
        object_uri=f"s3://{BUCKET}/reports/r1/pack.zip",
        visibility=EvidenceVisibility.SENSITIVE,
    )

    out = await issue_evidence_download_link(
        ev.id, EvidenceDownloadLinkRequest(reason="安全合规审计"), async_session, auditor
    )
    assert out.visibility == EvidenceVisibility.SENSITIVE
    log = (
        await async_session.execute(
            select(AuditLog).where(AuditLog.action == "evidence.download_link.issued")
        )
    ).scalar_one()
    assert log.details["visibility"] == "sensitive"


async def test_system_admin_bypasses_sensitive_gate(async_session):
    admin = await _make_user(async_session, username="ev-admin", system_role=SystemRole.ADMIN)
    ev = await _seed_evidence(
        async_session,
        object_uri=f"s3://{BUCKET}/reports/r1/pack.zip",
        visibility=EvidenceVisibility.RESTRICTED,
    )
    out = await issue_evidence_download_link(
        ev.id, EvidenceDownloadLinkRequest(reason="管理员直查"), async_session, admin
    )
    assert out.evidence_id == ev.id


async def test_missing_object_uri_returns_404(async_session):
    user = await _make_user(async_session, username="ev-noobj")
    ev = await _seed_evidence(async_session, object_uri=None)
    with pytest.raises(HTTPException) as exc:
        await issue_evidence_download_link(
            ev.id, EvidenceDownloadLinkRequest(reason="尝试下载无对象证据"), async_session, user
        )
    assert exc.value.status_code == 404


async def test_unsignable_object_uri_returns_422(async_session):
    user = await _make_user(async_session, username="ev-bad")
    ev = await _seed_evidence(async_session, object_uri="https://example.com/leaked")
    with pytest.raises(HTTPException) as exc:
        await issue_evidence_download_link(
            ev.id, EvidenceDownloadLinkRequest(reason="非受管引用应拒签"), async_session, user
        )
    assert exc.value.status_code == 422


async def test_unmanaged_bucket_returns_422_without_leaking_bucket_name(async_session):
    user = await _make_user(async_session, username="ev-otherbucket")
    ev = await _seed_evidence(async_session, object_uri="s3://some-other-bucket/secret/key.zip")
    with pytest.raises(HTTPException) as exc:
        await issue_evidence_download_link(
            ev.id, EvidenceDownloadLinkRequest(reason="跨桶引用应拒签"), async_session, user
        )
    assert exc.value.status_code == 422
    # 文案不得泄漏内部桶名(原则 V):既不含受管桶、也不含来路桶
    assert BUCKET not in exc.value.detail
    assert "some-other-bucket" not in exc.value.detail


async def test_storage_failure_maps_to_502(async_session, monkeypatch):
    from botocore.exceptions import EndpointConnectionError

    def _boom(*, object_key, expires_in=None):
        raise EndpointConnectionError(endpoint_url="http://minio:9000")

    monkeypatch.setattr(artifact_service, "generate_presigned_get_url", _boom)
    user = await _make_user(async_session, username="ev-502")
    ev = await _seed_evidence(async_session, object_uri=f"s3://{BUCKET}/reports/r1/pack.zip")
    with pytest.raises(HTTPException) as exc:
        await issue_evidence_download_link(
            ev.id, EvidenceDownloadLinkRequest(reason="存储不可用应转 502"), async_session, user
        )
    assert exc.value.status_code == 502


async def test_evidence_not_found_returns_404(async_session):
    user = await _make_user(async_session, username="ev-404")
    with pytest.raises(HTTPException) as exc:
        await issue_evidence_download_link(
            999, EvidenceDownloadLinkRequest(reason="不存在的证据 id"), async_session, user
        )
    assert exc.value.status_code == 404


def test_reason_is_mandatory_min_length():
    with pytest.raises(ValidationError):
        EvidenceDownloadLinkRequest(reason="短")
