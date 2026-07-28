"""精简对外交接包构建([FR-012-OUTBOUND-PACKAGE] · FR-012)。

把一个交接 case 的成果打成一份**精简、可对外交付**的归档:

  · 聚合 case 下每条 :class:`HandoverItem`(资产名/类型/重要性 + 建议动作 + 执行回执/结果)、
    相关 :class:`WorkTrace` 摘要、关联 :class:`EvidenceItem` 引用 → ``manifest.json``
    (schema_version ``duckdock-handover-pack-v1`` + case 元数据 + ``items[]`` + ``generated_at``);
  · zip(``manifest.json`` + 每条 item 一份 json),算 sha256;
  · 写到 ``handovers/case-{id}/pack-{ts}.zip``,落一条可签名下载的 :class:`EvidenceItem`
    (object_uri / sha256 / summary / source_type)。

**安全**(宪法原则 V):包内对凭证/密钥做**双层防护**——
  · *选择性遮蔽*:敏感级(CONFIDENTIAL/RESTRICTED)工作历史只放占位摘要、绝不外泄 metadata
    与原文,与 ``control_plane._trace_summary_out`` 同形态;
  · *模式红act*:所有进 manifest/items 的自由文本(证据摘要、执行回执 note、item 风险说明、
    非敏感 trace 原文摘要)统一过 :func:`_sanitize_freetext`,把 API key / bearer token /
    AWS 风格密钥 / ``password=…`` / 私钥块 / 长高熵 hex·base64 串替换为占位符。
结构化字段(资产名/类型/枚举/id)按设计不含明文凭证,跳过红act。
对象写入复用 :data:`artifact_service`(优先其 ``put_object`` 钩子,否则回落底层 data_client),
阻塞的对象上传经 :func:`asyncio.to_thread` 派发,避免阻塞事件循环。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.control_plane import (
    AIAsset,
    EvidenceItem,
    EvidenceSourceType,
    EvidenceVisibility,
    ExecutionAction,
    HandoverCase,
    HandoverItem,
    RuntimeProvider,
    Sensitivity,
    WorkTrace,
)
from app.services.artifact_service import artifact_service
from app.services.tenant_write_service import (
    build_evidence_item,
    ensure_resource_namespace,
    require_active_namespace,
)

SCHEMA_VERSION = "duckdock-handover-pack-v1"

# 与 control_plane._SENSITIVE_TRACE_LEVELS 对齐:这些级别的 trace 摘要原文需遮蔽,
# 包里只放占位提示,完整内容仍走受控的 reveal 通道。
_SENSITIVE_TRACE_LEVELS = {Sensitivity.CONFIDENTIAL, Sensitivity.RESTRICTED}
_MASKED_SUMMARY = "[redacted — sensitive work history available via controlled reveal]"

_REDACTED = "[redacted-secret]"

# 凭证/密钥的自由文本红act 模式(原则 V)。先匹配带前缀的强信号串(API key / bearer /
# password= / 私钥块),再兜底长高熵 hex·base64。次序敏感:更具体的模式排前面。
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # PEM 私钥块(含 BEGIN…END,跨行)
    re.compile(
        r"-----BEGIN[ A-Z]*PRIVATE KEY-----.*?-----END[ A-Z]*PRIVATE KEY-----",
        re.S,
    ),
    # AWS access key id(AKIA/ASIA + 16 大写字母数字)及其 secret access key 习惯写法
    re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA|ASCA)[0-9A-Z]{12,}\b"),
    # OpenAI/Anthropic 风格 sk- 前缀 token
    re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{16,}\b"),
    # bearer / authorization token
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    # password= / secret= / token= / api_key= 等 key=value(值到空白/引号止)
    re.compile(
        r"(?i)\b(?:pass(?:word|wd)?|secret|token|api[_-]?key|access[_-]?key|"
        r"private[_-]?key|client[_-]?secret)\b\s*[:=]\s*[\"']?[^\s\"']{6,}"
    ),
    # 兜底:长高熵 hex(>=32)或 base64-ish(>=40)连续串
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"),
)


def _sanitize_freetext(s: str | None) -> str | None:
    """红act 自由文本中的凭证/密钥样片段,返回遮蔽后的同形态文本。

    仅替换匹配到的凭证片段(非整段丢弃),保留其余说明文字以便人工判读。``None`` 原样返回。
    """
    if not s:
        return s
    out = s
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub(_REDACTED, out)
    return out


def _put_object(*, object_key: str, body: bytes, content_type: str, metadata: dict[str, str]) -> None:
    """写对象到受管制品桶。

    优先调用 ``artifact_service.put_object``(若存在 / 被测试替身覆盖),否则回落到底层
    boto data_client(与 ``publish_version_artifacts`` 同路径),保证生产可用且测试可截获。
    """
    put = getattr(artifact_service, "put_object", None)
    if callable(put):
        put(object_key=object_key, body=body, content_type=content_type, metadata=metadata)
        return
    artifact_service._ensure_bucket()
    artifact_service.data_client.put_object(
        Bucket=artifact_service.bucket,
        Key=object_key,
        Body=body,
        ContentType=content_type,
        Metadata=metadata,
    )


def _trace_summary(trace: WorkTrace) -> dict[str, Any]:
    """单条 WorkTrace 的精简摘要;敏感级遮蔽原文摘要,绝不外泄 metadata;
    非敏感原文摘要仍过自由文本红act,剔除其中凭证样片段。"""
    sensitive = trace.sensitivity in _SENSITIVE_TRACE_LEVELS
    return {
        "id": trace.id,
        "title": _sanitize_freetext(trace.title),
        "trace_type": trace.trace_type.value,
        "sensitivity": trace.sensitivity.value,
        "summary": _MASKED_SUMMARY if sensitive else _sanitize_freetext(trace.summary),
        "started_at": trace.started_at.isoformat() if trace.started_at else None,
    }


def _evidence_ref(evidence: EvidenceItem) -> dict[str, Any]:
    """证据**引用**(非内容):仅元数据 + sha256 + 摘要,不含 object_uri 内部结构;摘要过红act。"""
    return {
        "id": evidence.id,
        "source_type": evidence.source_type.value,
        "sha256": evidence.sha256,
        "summary": _sanitize_freetext(evidence.summary),
        "visibility": evidence.visibility.value,
    }


async def _build_item_entries(
    db: AsyncSession,
    case: HandoverCase,
    *,
    namespace_id: int,
) -> list[dict[str, Any]]:
    items = (
        await db.execute(
            select(HandoverItem)
            .where(HandoverItem.handover_case_id == case.id)
            .order_by(HandoverItem.id)
        )
    ).scalars().all()

    entries: list[dict[str, Any]] = []
    for item in items:
        asset = await db.get(AIAsset, item.asset_id)
        if asset is not None:
            ensure_resource_namespace(asset, namespace_id, relationship="asset")

        evidence_refs: list[dict[str, Any]] = []
        if item.evidence_id is not None:
            evidence = await db.get(EvidenceItem, item.evidence_id)
            if evidence is not None:
                ensure_resource_namespace(evidence, namespace_id, relationship="evidence")
                evidence_refs.append(_evidence_ref(evidence))

        action = (
            await db.execute(
                select(ExecutionAction)
                .where(ExecutionAction.handover_item_id == item.id)
                .order_by(ExecutionAction.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        execution = None
        if action is not None:
            note = (action.result_json or {}).get("note") if action.result_json else None
            execution = {
                "result": action.status.value,
                "action_type": action.action_type,
                "note": _sanitize_freetext(note),
            }

        traces = (
            await db.execute(
                select(WorkTrace)
                .where(WorkTrace.asset_id == item.asset_id)
                .order_by(WorkTrace.started_at.desc(), WorkTrace.created_at.desc())
                .limit(20)
            )
        ).scalars().all()
        for trace in traces:
            ensure_resource_namespace(trace, namespace_id, relationship="work_trace")

        entries.append(
            {
                "item_id": item.id,
                "asset": {
                    "id": item.asset_id,
                    "name": _sanitize_freetext(asset.name) if asset else None,
                    "asset_type": asset.asset_type.value if asset else None,
                    "criticality": asset.criticality.value if asset else None,
                },
                "recommended_action": item.recommended_action.value,
                "status": item.status.value,
                "risk_reason": _sanitize_freetext(item.risk_reason),
                "receiver_user_id": item.receiver_user_id,
                "execution": execution,
                "evidence": evidence_refs,
                "work_traces": [_trace_summary(trace) for trace in traces],
            }
        )
    return entries


def _build_archive(manifest: dict[str, Any], entries: list[dict[str, Any]]) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        )
        for entry in entries:
            archive.writestr(
                f"items/item-{entry['item_id']}.json",
                json.dumps(entry, ensure_ascii=False, indent=2, sort_keys=True),
            )
    return buf.getvalue()


async def build_package(
    db: AsyncSession,
    case: HandoverCase,
    *,
    created_by: int | None = None,
    visibility: EvidenceVisibility = EvidenceVisibility.NORMAL,
) -> EvidenceItem:
    """构建并落地一个 case 的精简对外交接包,返回可签名下载的 EvidenceItem。

    包内: ``manifest.json`` + 每条 item 一份 json;sha256 = 对 zip 字节本身。
    对象 key: ``handovers/case-{id}/pack-{ts}.zip``;EvidenceItem.object_uri =
    ``s3://{bucket}/{key}``(与 evidence 下载链路一致,供 build_download_link 签名)。
    """
    namespace = await require_active_namespace(db, case.namespace_id)
    generated_at = datetime.now(timezone.utc)
    entries = await _build_item_entries(db, case, namespace_id=namespace.id)
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "case": {
            "id": case.id,
            "case_type": case.case_type.value,
            "title": _sanitize_freetext(case.title),
            "status": case.status.value,
            "risk_level": case.risk_level.value,
            "subject_user_id": case.subject_user_id,
            "receiver_user_id": case.receiver_user_id,
        },
        "item_count": len(entries),
        "items": entries,
    }

    # zip 构建 + sha256 都是 CPU/同步 IO,连同对象上传一起 offload 到线程,避免阻塞事件循环。
    archive_bytes = await asyncio.to_thread(_build_archive, manifest, entries)
    sha256 = hashlib.sha256(archive_bytes).hexdigest()
    object_key = f"handovers/case-{case.id}/pack-{generated_at:%Y%m%dT%H%M%S}-{sha256[:12]}.zip"
    await asyncio.to_thread(
        _put_object,
        object_key=object_key,
        body=archive_bytes,
        content_type="application/zip",
        metadata={"handover_case_id": str(case.id), "sha256": sha256},
    )

    evidence = build_evidence_item(
        namespace_id=namespace.id,
        source_type=EvidenceSourceType.BACKUP_PACKAGE,
        source_provider=RuntimeProvider.CUSTOM,
        object_uri=f"s3://{artifact_service.bucket}/{object_key}",
        sha256=sha256,
        summary=f"对外交接包(case {case.id}「{_sanitize_freetext(case.title)}」,{len(entries)} 项)。",
        visibility=visibility,
        created_by=created_by,
    )
    db.add(evidence)
    await db.flush()
    return evidence
