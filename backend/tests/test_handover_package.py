"""精简对外交接包(FR-012 · [FR-012-OUTBOUND-PACKAGE])。

断言:
  · COMPLETED case → build_package 聚合 HandoverItem(资产名/类型/重要性 + 建议动作 + 回执)、
    关联 WorkTrace 摘要、EvidenceItem 引用进 manifest.json(schema_version + case metadata + items[]
    + generated_at),打 zip、算 sha256、put_object 到 handovers/case-{id}/pack-{ts}.zip,
    并落一条 EvidenceItem(object_uri / sha256 / summary / source_type);
  · 端点 POST /package 要求 case.status ∈ {VERIFYING, COMPLETED},否则 409;
  · GET /package/download-link 复用证据签名下载(限时 URL + 权限 + 审计 + 通用文案不泄漏桶名);
  · 跨租户/越权(敏感包证据无 evidence.sensitive.read)→ 403 且留痕;
  · 包内**不含**明文凭证,敏感工作历史仅摘要遮蔽。
"""
from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.v1.endpoints.control_plane import (
    create_handover_package,
    download_handover_package_link,
)
from app.models.audit import AuditLog
from app.models.control_plane import (
    AIAsset,
    AssetType,
    Criticality,
    EvidenceItem,
    EvidenceSourceType,
    EvidenceVisibility,
    ExecutionAction,
    ExecutionStatus,
    HandoverAction,
    HandoverCase,
    HandoverCaseType,
    HandoverItem,
    HandoverItemStatus,
    HandoverStatus,
    RuntimeProvider,
    Sensitivity,
    WorkTrace,
    TraceType,
)
from app.models.iam import Role, RoleBinding
from app.models.namespace import Namespace
from app.models.user import SystemRole, User
from app.schemas.control_plane import EvidenceDownloadLinkRequest
from app.services import handover_package_service
from app.services.artifact_service import artifact_service
from app.services.iam_service import ensure_builtin_rbac

BUCKET = artifact_service.bucket
_SECRET = "sk-LEAKED-credential-should-never-appear"


class _FakeStore:
    """替身存储:截获 put_object,记录 (key, body),避免真连 MinIO。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, object_key, body, content_type=None, metadata=None):
        self.objects[object_key] = body
        return {"bucket": BUCKET, "object_key": object_key}


@pytest.fixture
def fake_store(monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr(artifact_service, "put_object", store.put_object, raising=False)
    return store


def _fake_presign(*, object_key, expires_in=None):
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


async def _seed_asset(
    session,
    *,
    namespace_id: int,
    name,
    criticality=Criticality.HIGH,
    asset_type=AssetType.SKILL,
):
    asset = AIAsset(
        asset_type=asset_type,
        name=name,
        source_provider=RuntimeProvider.OPENCLAW,
        criticality=criticality,
        namespace_id=namespace_id,
    )
    session.add(asset)
    await session.flush()
    return asset


async def _seed_completed_case_with_items(session, admin, *, status=HandoverStatus.COMPLETED):
    namespace = Namespace(name=f"package-{uuid4().hex[:8]}", owner_id=admin.id)
    session.add(namespace)
    await session.flush()
    case = HandoverCase(
        case_type=HandoverCaseType.EMPLOYEE_OFFBOARDING,
        title="张三离职交接",
        status=status,
        created_by=admin.id,
        namespace_id=namespace.id,
        summary_json={"runtime_ids": [1]},
    )
    session.add(case)
    await session.flush()

    asset = await _seed_asset(
        session, namespace_id=namespace.id, name="离职交接摘要 Skill"
    )
    # 关联证据(item 引用)
    ev = EvidenceItem(
        source_type=EvidenceSourceType.LLM_ANALYSIS,
        source_provider=RuntimeProvider.OPENCLAW,
        summary="分析建议:转移 owner",
        sha256="b" * 64,
        created_by=admin.id,
        namespace_id=namespace.id,
    )
    session.add(ev)
    await session.flush()
    item = HandoverItem(
        handover_case_id=case.id,
        asset_id=asset.id,
        recommended_action=HandoverAction.TRANSFER_OWNER,
        receiver_user_id=None,
        risk_reason="高重要性资产",
        status=HandoverItemStatus.DONE,
        evidence_id=ev.id,
    )
    session.add(item)
    await session.flush()
    # 执行回执(receipt/result)
    action = ExecutionAction(
        handover_case_id=case.id,
        handover_item_id=item.id,
        action_type=item.recommended_action.value,
        provider=RuntimeProvider.CUSTOM,
        status=ExecutionStatus.SUCCEEDED,
        result_json={"note": "已在厂商平台转移"},
    )
    session.add(action)
    # 关联 WorkTrace —— 一条普通,一条敏感(敏感的明文摘要必须遮蔽)
    session.add(
        WorkTrace(
            asset_id=asset.id,
            title="日常会话",
            summary="普通工作摘要",
            trace_type=TraceType.SESSION,
            sensitivity=Sensitivity.INTERNAL,
            namespace_id=namespace.id,
        )
    )
    session.add(
        WorkTrace(
            asset_id=asset.id,
            title="敏感处置会话",
            summary=f"机密细节 包含凭证 {_SECRET}",
            trace_type=TraceType.SESSION,
            sensitivity=Sensitivity.RESTRICTED,
            namespace_id=namespace.id,
            metadata_json={"token": _SECRET},
        )
    )
    await session.flush()
    return case, asset, item, ev


# ── service: build_package ─────────────────────────────────────────

async def test_build_package_aggregates_manifest_and_persists_evidence(async_session, fake_store):
    admin = await _make_user(async_session, username="pkg-admin", system_role=SystemRole.ADMIN)
    case, asset, item, ev = await _seed_completed_case_with_items(async_session, admin)

    evidence = await handover_package_service.build_package(async_session, case, created_by=admin.id)

    # 持久化了一条可下载的 EvidenceItem
    assert evidence.id is not None
    assert evidence.object_uri and evidence.object_uri.startswith(f"s3://{BUCKET}/handovers/case-{case.id}/")
    assert evidence.object_uri.endswith(".zip")
    assert evidence.sha256 and len(evidence.sha256) == 64
    persisted = (
        await async_session.execute(select(EvidenceItem).where(EvidenceItem.id == evidence.id))
    ).scalar_one()
    assert persisted.object_uri == evidence.object_uri

    # 对象确实被写到了截获的存储里,且和 EvidenceItem 指向同一 key
    key = evidence.object_uri[len(f"s3://{BUCKET}/"):]
    assert key in fake_store.objects
    zip_bytes = fake_store.objects[key]

    # 解 zip:必须含 manifest.json + 每条 item 的 json
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        names = archive.namelist()
        assert "manifest.json" in names
        manifest_raw = archive.read("manifest.json")
        item_jsons = [n for n in names if n.startswith("items/") and n.endswith(".json")]
        assert len(item_jsons) == 1
        item_raw = archive.read(item_jsons[0])

    import json

    manifest = json.loads(manifest_raw)
    assert manifest["schema_version"] == "duckdock-handover-pack-v1"
    assert "generated_at" in manifest
    assert manifest["case"]["id"] == case.id
    assert manifest["case"]["status"] == HandoverStatus.COMPLETED.value
    assert len(manifest["items"]) == 1

    entry = manifest["items"][0]
    assert entry["asset"]["name"] == "离职交接摘要 Skill"
    assert entry["asset"]["asset_type"] == AssetType.SKILL.value
    assert entry["asset"]["criticality"] == Criticality.HIGH.value
    assert entry["recommended_action"] == HandoverAction.TRANSFER_OWNER.value
    # 回执/结果
    assert entry["execution"]["result"] == ExecutionStatus.SUCCEEDED.value
    # 关联证据引用
    assert any(ref.get("id") == ev.id for ref in entry.get("evidence", []))
    # work trace 摘要
    assert entry.get("work_traces")

    # sha256 = 对 zip 字节本身
    import hashlib

    assert evidence.sha256 == hashlib.sha256(zip_bytes).hexdigest()

    # per-item json 与 manifest 内 item 一致(含资产名)
    assert json.loads(item_raw)["asset"]["name"] == "离职交接摘要 Skill"


async def test_build_package_masks_sensitive_trace_and_no_plaintext_credentials(async_session, fake_store):
    admin = await _make_user(async_session, username="pkg-mask", system_role=SystemRole.ADMIN)
    case, *_ = await _seed_completed_case_with_items(async_session, admin)

    evidence = await handover_package_service.build_package(async_session, case, created_by=admin.id)
    key = evidence.object_uri[len(f"s3://{BUCKET}/"):]
    zip_bytes = fake_store.objects[key]

    # 整包(含所有内部 json)绝不出现明文凭证
    assert _SECRET.encode("utf-8") not in zip_bytes

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        blob = b"".join(archive.read(n) for n in archive.namelist())
    assert _SECRET.encode("utf-8") not in blob


# 非敏感自由文本字段里的凭证样串:全部应被模式遮蔽(L0-SEC-PKG-MASK)
_EV_SECRET = "AKIAIOSFODNN7EXAMPLE secret AKIA-leak"
_NOTE_SECRET = "Bearer abcdefghijklmnopqrstuvwxyz0123456789ABCDEF token leaked"
_RISK_SECRET = "password=SuperSecretP@ssw0rd-do-not-leak"
_TRACE_SECRET = "api_key=sk-live-0123456789abcdef0123456789abcdef leaked"
_NAME_SECRET = "sk-live-feedface0123456789abcdef01234567"
_FREETEXT_SECRETS = [
    "AKIAIOSFODNN7EXAMPLE",
    "abcdefghijklmnopqrstuvwxyz0123456789ABCDEF",
    "SuperSecretP@ssw0rd-do-not-leak",
    "sk-live-0123456789abcdef0123456789abcdef",
    _NAME_SECRET,
]


async def _seed_case_with_freetext_secrets(session, admin):
    """种入 evidence.summary / execution note / item risk_reason / 非敏感 trace.summary 各含凭证样串。"""
    namespace = Namespace(name=f"package-secret-{uuid4().hex[:8]}", owner_id=admin.id)
    session.add(namespace)
    await session.flush()
    case = HandoverCase(
        case_type=HandoverCaseType.EMPLOYEE_OFFBOARDING,
        title="自由文本凭证泄漏用例",
        status=HandoverStatus.COMPLETED,
        created_by=admin.id,
        namespace_id=namespace.id,
        summary_json={"runtime_ids": [1]},
    )
    session.add(case)
    await session.flush()

    asset = await _seed_asset(
        session, namespace_id=namespace.id, name=f"生产管线 {_NAME_SECRET}"
    )
    ev = EvidenceItem(
        source_type=EvidenceSourceType.LLM_ANALYSIS,
        source_provider=RuntimeProvider.OPENCLAW,
        summary=f"分析建议:转移 owner，附带 {_EV_SECRET}",
        sha256="c" * 64,
        created_by=admin.id,
        namespace_id=namespace.id,
    )
    session.add(ev)
    await session.flush()
    item = HandoverItem(
        handover_case_id=case.id,
        asset_id=asset.id,
        recommended_action=HandoverAction.TRANSFER_OWNER,
        receiver_user_id=None,
        risk_reason=f"高重要性资产; {_RISK_SECRET}",
        status=HandoverItemStatus.DONE,
        evidence_id=ev.id,
    )
    session.add(item)
    await session.flush()
    session.add(
        ExecutionAction(
            handover_case_id=case.id,
            handover_item_id=item.id,
            action_type=item.recommended_action.value,
            provider=RuntimeProvider.CUSTOM,
            status=ExecutionStatus.SUCCEEDED,
            result_json={"note": f"已在厂商平台转移; {_NOTE_SECRET}"},
        )
    )
    # 非敏感 trace:摘要原文进包(不遮蔽),但其中的凭证样串必须被模式遮蔽
    session.add(
        WorkTrace(
            asset_id=asset.id,
            title="日常会话",
            summary=f"普通工作摘要 {_TRACE_SECRET}",
            trace_type=TraceType.SESSION,
            sensitivity=Sensitivity.INTERNAL,
            namespace_id=namespace.id,
        )
    )
    await session.flush()
    return case


async def test_build_package_redacts_credentials_in_all_freetext_fields(async_session, fake_store):
    admin = await _make_user(async_session, username="pkg-freetext", system_role=SystemRole.ADMIN)
    case = await _seed_case_with_freetext_secrets(async_session, admin)

    evidence = await handover_package_service.build_package(async_session, case, created_by=admin.id)
    key = evidence.object_uri[len(f"s3://{BUCKET}/"):]
    zip_bytes = fake_store.objects[key]

    import json

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        item_name = next(n for n in names if n.startswith("items/") and n.endswith(".json"))
        item_entry = json.loads(archive.read(item_name))
        blob = b"".join(archive.read(n) for n in names)

    # 整包字节里绝不出现任何凭证样串
    for secret in _FREETEXT_SECRETS:
        assert secret.encode("utf-8") not in blob, f"leaked: {secret}"

    entry = manifest["items"][0]
    # evidence.summary / risk_reason / execution note / 非敏感 trace.summary 均被遮蔽
    assert _EV_SECRET.split()[0] not in entry["evidence"][0]["summary"]
    assert "SuperSecretP@ssw0rd-do-not-leak" not in entry["risk_reason"]
    assert "abcdefghijklmnopqrstuvwxyz0123456789ABCDEF" not in entry["execution"]["note"]
    trace_summary = entry["work_traces"][0]["summary"]
    assert "sk-live-0123456789abcdef0123456789abcdef" not in trace_summary
    # 非敏感 trace 仍保留非凭证文字(只遮蔽凭证片段，整段不应被整体 redact)
    assert "普通工作摘要" in trace_summary
    # asset.name 也是来源/用户文本,凭证样串必须被遮蔽(finding 4)
    assert _NAME_SECRET not in entry["asset"]["name"]
    assert "生产管线" in entry["asset"]["name"]
    # per-item json 与 manifest 内一致
    assert item_entry["risk_reason"] == entry["risk_reason"]


async def test_build_package_offloads_blocking_put_via_to_thread(async_session, fake_store, monkeypatch):
    """L0-PERF-PKG-IO: 阻塞的对象上传必须经 asyncio.to_thread 派发，不阻塞事件循环。"""
    import asyncio

    calls: list[object] = []
    real_to_thread = asyncio.to_thread

    async def _spy_to_thread(func, /, *args, **kwargs):
        calls.append(func)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(handover_package_service.asyncio, "to_thread", _spy_to_thread)

    admin = await _make_user(async_session, username="pkg-tothread", system_role=SystemRole.ADMIN)
    case, *_ = await _seed_completed_case_with_items(async_session, admin)
    evidence = await handover_package_service.build_package(async_session, case, created_by=admin.id)

    assert evidence.object_uri
    # 上传通过 to_thread 派发(派发的函数即模块内的 _put_object)
    assert handover_package_service._put_object in calls


# ── endpoint: POST /handovers/{case_id}/package ────────────────────

async def test_create_package_endpoint_builds_and_audits(async_session, fake_store):
    admin = await _make_user(async_session, username="pkg-ep", system_role=SystemRole.ADMIN)
    case, *_ = await _seed_completed_case_with_items(async_session, admin)

    out = await create_handover_package(case.id, async_session, admin)
    assert out.object_uri and out.object_uri.endswith(".zip")
    assert out.sha256 and len(out.sha256) == 64

    log = (
        await async_session.execute(
            select(AuditLog).where(AuditLog.action == "handover.package.built")
        )
    ).scalar_one()
    assert log.resource_id == case.id


@pytest.mark.parametrize(
    "status",
    [HandoverStatus.VERIFYING, HandoverStatus.COMPLETED],
)
async def test_create_package_allowed_in_terminalish_states(async_session, fake_store, status):
    admin = await _make_user(async_session, username=f"pkg-ok-{status.value}", system_role=SystemRole.ADMIN)
    case, *_ = await _seed_completed_case_with_items(async_session, admin, status=status)
    out = await create_handover_package(case.id, async_session, admin)
    assert out.object_uri


@pytest.mark.parametrize(
    "status",
    [
        HandoverStatus.DRAFT,
        HandoverStatus.COLLECTING,
        HandoverStatus.PENDING_APPROVAL,
        HandoverStatus.APPROVED,
        HandoverStatus.EXECUTING,
    ],
)
async def test_create_package_blocked_before_approval(async_session, fake_store, status):
    admin = await _make_user(async_session, username=f"pkg-bad-{status.value}", system_role=SystemRole.ADMIN)
    case, *_ = await _seed_completed_case_with_items(async_session, admin, status=status)
    with pytest.raises(HTTPException) as exc:
        await create_handover_package(case.id, async_session, admin)
    assert exc.value.status_code == 409


async def test_create_package_unknown_case_404(async_session, fake_store):
    admin = await _make_user(async_session, username="pkg-404", system_role=SystemRole.ADMIN)
    with pytest.raises(HTTPException) as exc:
        await create_handover_package(99999, async_session, admin)
    assert exc.value.status_code == 404


# ── endpoint: GET /handovers/{case_id}/package/download-link ───────

async def test_download_link_returns_time_limited_url(async_session, fake_store):
    admin = await _make_user(async_session, username="pkg-dl", system_role=SystemRole.ADMIN)
    case, *_ = await _seed_completed_case_with_items(async_session, admin)
    await create_handover_package(case.id, async_session, admin)

    out = await download_handover_package_link(
        case.id, EvidenceDownloadLinkRequest(reason="对外交付交接包"), async_session, admin
    )
    assert out.download_url.startswith("https://minio.local/")
    assert out.expires_in > 0
    assert out.expires_at is not None
    assert out.sha256 and len(out.sha256) == 64

    log = (
        await async_session.execute(
            select(AuditLog).where(AuditLog.action == "evidence.download_link.issued")
        )
    ).scalar_one()
    assert log.details["reason"] == "对外交付交接包"


async def test_download_link_without_package_returns_404(async_session, fake_store):
    admin = await _make_user(async_session, username="pkg-nopack", system_role=SystemRole.ADMIN)
    case, *_ = await _seed_completed_case_with_items(async_session, admin)
    with pytest.raises(HTTPException) as exc:
        await download_handover_package_link(
            case.id, EvidenceDownloadLinkRequest(reason="尚未生成包就下载"), async_session, admin
        )
    assert exc.value.status_code == 404


async def test_download_link_denied_for_sensitive_package_without_permission_and_audited(
    async_session, fake_store
):
    """跨租户/越权:别人建的敏感包,非创建者且无 evidence.sensitive.read → 403 且留痕。"""
    owner = await _make_user(async_session, username="pkg-owner", system_role=SystemRole.ADMIN)
    case, *_ = await _seed_completed_case_with_items(async_session, owner)
    # 用敏感可见性生成包(归 owner 所有)
    evidence = await handover_package_service.build_package(
        async_session, case, created_by=owner.id, visibility=EvidenceVisibility.RESTRICTED
    )
    assert evidence.visibility == EvidenceVisibility.RESTRICTED

    intruder = await _make_user(async_session, username="pkg-intruder")
    with pytest.raises(HTTPException) as exc:
        await download_handover_package_link(
            case.id, EvidenceDownloadLinkRequest(reason="越权窥探他人交接包"), async_session, intruder
        )
    assert exc.value.status_code == 403

    denied = (
        await async_session.execute(
            select(AuditLog).where(AuditLog.action == "evidence.download_link.denied")
        )
    ).scalar_one()
    assert denied.details["reason"] == "越权窥探他人交接包"


async def test_download_link_error_text_does_not_leak_bucket_name(async_session, fake_store, monkeypatch):
    """不可签名引用 → 422,通用文案不得泄漏内部桶名(原则 V)。"""
    from app.services import evidence_service
    from app.services.evidence_service import EvidenceObjectError

    admin = await _make_user(async_session, username="pkg-leak", system_role=SystemRole.ADMIN)
    case, *_ = await _seed_completed_case_with_items(async_session, admin)
    await create_handover_package(case.id, async_session, admin)

    def _reject(*, object_uri, expires_in=None):
        # 模拟服务层因引用落在受管桶外而拒签(异常文案本身不含桶名)
        raise EvidenceObjectError("evidence object is stored outside the managed artifact bucket")

    monkeypatch.setattr(evidence_service, "build_download_link", _reject)

    with pytest.raises(HTTPException) as exc:
        await download_handover_package_link(
            case.id, EvidenceDownloadLinkRequest(reason="不可签名引用应拒签"), async_session, admin
        )
    assert exc.value.status_code == 422
    assert BUCKET not in str(exc.value.detail)
