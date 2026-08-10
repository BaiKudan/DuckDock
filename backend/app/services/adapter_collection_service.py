from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select

from app.models.control_plane import (
    AIAsset,
    AdapterCursor,
    AdapterRunStep,
    AdapterStepStatus,
    AssetOwnership,
    CollectionJob,
    CollectionTriggerType,
    JobStatus,
    OwnerType,
    ProviderPrincipal,
    RawCollectionRecord,
    RawRecordStream,
    RuntimeCapabilitySnapshot,
    RuntimeInstance,
    WorkTrace,
)
from app.models.user import User
from app.services.adapters.contracts import AdapterCollectionResult, NormalizedAsset, NormalizedWorkTrace
from app.services.adapters.openclaw import normalize_openclaw_payload, parse_openclaw_backup_bytes
from app.services.tenant_write_service import (
    TenantWriteError,
    build_evidence_item,
    build_work_trace,
    ensure_resource_namespace,
    require_active_namespace,
)

# 主动外联采集已退役。这里只保留可信备份上传导入与归一化落库，
# 并与 Reporter 推送链路共用相同的数据写入流程。


async def ingest_openclaw_backup(db, *, runtime: RuntimeInstance, content: bytes, filename: str | None = None) -> CollectionJob:
    await require_active_namespace(db, runtime.namespace_id)
    payload = parse_openclaw_backup_bytes(content, filename)
    job = CollectionJob(
        runtime_id=runtime.id,
        trigger_type=CollectionTriggerType.MANUAL,
        status=JobStatus.RUNNING,
        scope_json={"source": "openclaw_backup_import", "filename": filename},
        started_at=_now(),
    )
    db.add(job)
    await db.flush()
    result = normalize_openclaw_payload(payload, source="backup_package")
    await _run_step(db, job, "backup.parse", {"filename": filename, "payload_keys": sorted(payload.keys())})
    await persist_collection_result(db, job=job, runtime=runtime, result=result)
    job.status = JobStatus.SUCCEEDED
    job.finished_at = _now()
    return job


async def persist_collection_result(
    db,
    *,
    job: CollectionJob,
    runtime: RuntimeInstance,
    result: AdapterCollectionResult,
) -> dict[str, int]:
    namespace = await require_active_namespace(db, runtime.namespace_id)
    ensure_resource_namespace(runtime, namespace.id, relationship="runtime")
    await _persist_capability_snapshot(db, job, runtime, result.adapter_name, result.capabilities.to_json())
    principal_map: dict[str, ProviderPrincipal] = {}
    asset_map: dict[str, AIAsset] = {}

    for principal in result.principals:
        row = (
            await db.execute(
                select(ProviderPrincipal).where(
                    ProviderPrincipal.runtime_id == runtime.id,
                    ProviderPrincipal.external_id == principal.external_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = ProviderPrincipal(
                runtime_id=runtime.id,
                provider=runtime.provider,
                external_id=principal.external_id,
                principal_type=principal.principal_type,
            )
            db.add(row)
        row.display_name = principal.display_name
        row.username = principal.username
        row.email = principal.email
        row.metadata_json = principal.metadata
        row.last_seen_at = _now()
        row.user_id = await _resolve_user_id(db, email=principal.email, username=principal.username)
        principal_map[principal.external_id] = row

    await db.flush()

    for asset in result.assets:
        row = await _get_existing_asset(db, runtime=runtime, asset=asset)
        if row is None:
            row = AIAsset(
                namespace_id=namespace.id,
                asset_type=asset.asset_type,
                name=asset.name,
                description=asset.description,
                source_provider=runtime.provider,
                source_runtime_id=runtime.id,
                external_id=asset.external_id,
                status=asset.status,
                criticality=asset.criticality,
                metadata_json=asset.metadata,
                content_hash=asset.content_hash,
            )
            db.add(row)
        else:
            ensure_resource_namespace(
                row,
                namespace.id,
                relationship="asset",
            )
            row.asset_type = asset.asset_type
            row.name = asset.name
            row.description = asset.description
            row.status = asset.status
            row.criticality = asset.criticality
            row.metadata_json = asset.metadata
            row.content_hash = asset.content_hash
            row.last_seen_at = _now()
        if asset.external_id:
            asset_map[asset.external_id] = row
        await db.flush()
        if asset.owner_external_id and asset.owner_external_id in principal_map:
            principal = principal_map[asset.owner_external_id]
            if principal.user_id is not None:
                await _ensure_asset_owner(db, asset=row, user_id=principal.user_id)

    await db.flush()

    seen_trace_hashes: set[str] = set()
    trace_map: dict[str, WorkTrace] = {}
    for trace in result.work_traces:
        asset_id = asset_map.get(trace.asset_external_id).id if trace.asset_external_id in asset_map else None
        actor = principal_map.get(trace.actor_external_id) if trace.actor_external_id else None
        actor_user_id = actor.user_id if actor else None
        existing = None
        if trace.external_session_id:
            existing = (
                await db.execute(
                    select(WorkTrace).where(
                        WorkTrace.runtime_id == runtime.id,
                        WorkTrace.external_session_id == trace.external_session_id,
                    )
                )
            ).scalar_one_or_none()
        else:
            # 缺少 external_session_id 时改用基于内容的去重键(runtime_id + title +
            # started_at + payload 哈希),与 raw_records 的去重方式对齐(原则 II 幂等)。
            trace_hash = _hash_work_trace(trace)
            if trace_hash in seen_trace_hashes:
                continue
            candidates = (
                await db.execute(
                    select(WorkTrace).where(
                        WorkTrace.runtime_id == runtime.id,
                        WorkTrace.external_session_id.is_(None),
                        WorkTrace.title == trace.title,
                        WorkTrace.started_at == trace.started_at,
                    )
                )
            ).scalars().all()
            existing = next((row for row in candidates if _hash_work_trace_row(row) == trace_hash), None)
            seen_trace_hashes.add(trace_hash)
        if existing is None:
            linked_asset = asset_map.get(trace.asset_external_id) if trace.asset_external_id else None
            existing = build_work_trace(
                namespace_id=namespace.id,
                runtime=runtime,
                asset=linked_asset,
                external_session_id=trace.external_session_id,
                actor_user_id=actor_user_id,
                title=trace.title,
                summary=trace.summary,
                trace_type=trace.trace_type,
                started_at=trace.started_at,
                ended_at=trace.ended_at,
                sensitivity=trace.sensitivity,
                metadata_json=trace.metadata,
            )
            db.add(existing)
        else:
            ensure_resource_namespace(
                existing,
                namespace.id,
                relationship="work_trace",
            )
            if asset_id is not None:
                linked_asset = asset_map.get(trace.asset_external_id) if trace.asset_external_id else None
                if linked_asset is not None:
                    ensure_resource_namespace(linked_asset, namespace.id, relationship="asset")
            existing.asset_id = asset_id
            existing.actor_user_id = actor_user_id
            existing.title = trace.title
            existing.summary = trace.summary
            existing.trace_type = trace.trace_type
            existing.started_at = trace.started_at
            existing.ended_at = trace.ended_at
            existing.sensitivity = trace.sensitivity
            existing.metadata_json = trace.metadata

        if trace.external_session_id:
            trace_map[trace.external_session_id] = existing

    await db.flush()

    for evidence in result.evidence:
        linked_trace = None
        if evidence.work_trace_external_session_id is not None:
            linked_trace = trace_map.get(evidence.work_trace_external_session_id)
            if linked_trace is None:
                raise TenantWriteError(
                    "Evidence typed WorkTrace reference was not found in the normalized collection"
                )
        db.add(
            build_evidence_item(
                namespace_id=namespace.id,
                work_trace=linked_trace,
                source_type=evidence.source_type,
                source_provider=runtime.provider,
                collection_job_id=job.id,
                object_uri=evidence.object_uri,
                sha256=evidence.sha256,
                summary=evidence.summary,
                confidence=evidence.confidence,
                visibility=evidence.visibility,
            )
        )

    for raw in result.raw_records:
        record_hash = _hash_payload(raw.payload)
        exists = (
            await db.execute(
                select(RawCollectionRecord).where(
                    RawCollectionRecord.runtime_id == runtime.id,
                    RawCollectionRecord.stream == raw.stream,
                    RawCollectionRecord.external_id == raw.external_id,
                    RawCollectionRecord.record_hash == record_hash,
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            db.add(
                RawCollectionRecord(
                    collection_job_id=job.id,
                    runtime_id=runtime.id,
                    provider=runtime.provider,
                    adapter_name=result.adapter_name,
                    stream=raw.stream,
                    external_id=raw.external_id,
                    record_hash=record_hash,
                    payload_json=raw.payload,
                    normalized_type=raw.normalized_type,
                )
            )

    for stream, cursor in result.cursor_updates.items():
        await _upsert_cursor(db, runtime=runtime, adapter_name=result.adapter_name, stream=stream, cursor=cursor)

    counts = {
        "principals": len(result.principals),
        "assets": len(result.assets),
        "work_traces": len(result.work_traces),
        "evidence": len(result.evidence),
        "raw_records": len(result.raw_records),
    }
    job.summary_json = {"adapter": result.adapter_name, "provider": runtime.provider.value, "counts": counts}
    await _run_step(db, job, "persisting", counts)
    return counts


async def _run_step(db, job: CollectionJob, step_name: str, summary: dict[str, Any] | None = None):
    row = AdapterRunStep(
        collection_job_id=job.id,
        step_name=step_name,
        status=AdapterStepStatus.SUCCEEDED,
        summary_json=summary,
        started_at=_now(),
        finished_at=_now(),
    )
    db.add(row)
    await db.flush()
    return row


async def _get_existing_asset(db, *, runtime: RuntimeInstance, asset: NormalizedAsset) -> AIAsset | None:
    if asset.external_id:
        row = (
            await db.execute(
                select(AIAsset).where(
                    AIAsset.source_provider == runtime.provider,
                    AIAsset.source_runtime_id == runtime.id,
                    AIAsset.external_id == asset.external_id,
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            return row
    if asset.content_hash:
        return (
            await db.execute(
                select(AIAsset).where(
                    AIAsset.source_provider == runtime.provider,
                    AIAsset.source_runtime_id == runtime.id,
                    AIAsset.content_hash == asset.content_hash,
                )
            )
        ).scalar_one_or_none()
    return None


async def _persist_capability_snapshot(
    db,
    job: CollectionJob,
    runtime: RuntimeInstance,
    adapter_name: str,
    capabilities: dict[str, Any],
):
    """记录上报来源声明的能力快照(Push 链经 persist_collection_result 共用)。"""
    runtime.capabilities = capabilities
    db.add(
        RuntimeCapabilitySnapshot(
            runtime_id=runtime.id,
            provider=runtime.provider,
            adapter_name=adapter_name,
            status="ok",
            source="report",
            version=capabilities.get("version"),
            capabilities_json=capabilities,
        )
    )
    await db.flush()


async def _resolve_user_id(db, *, email: str | None, username: str | None) -> int | None:
    filters = []
    if email:
        filters.append(User.email == email)
    if username:
        filters.append(User.username == username)
    if not filters:
        return None
    return (await db.execute(select(User.id).where(or_(*filters)).limit(1))).scalar_one_or_none()


async def _ensure_asset_owner(db, *, asset: AIAsset, user_id: int):
    existing = (
        await db.execute(
            select(AssetOwnership).where(
                AssetOwnership.asset_id == asset.id,
                AssetOwnership.user_id == user_id,
                AssetOwnership.owner_type == OwnerType.CREATOR,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            AssetOwnership(
                namespace_id=asset.namespace_id,
                asset_id=asset.id,
                owner_type=OwnerType.CREATOR,
                user_id=user_id,
                confidence=0.95,
                is_primary=True,
            )
        )
    else:
        ensure_resource_namespace(
            existing,
            asset.namespace_id,
            relationship="asset_ownership",
        )


async def _upsert_cursor(
    db,
    *,
    runtime: RuntimeInstance,
    adapter_name: str,
    stream: RawRecordStream,
    cursor: dict[str, Any],
):
    row = (
        await db.execute(
            select(AdapterCursor).where(
                AdapterCursor.runtime_id == runtime.id,
                AdapterCursor.stream == stream,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = AdapterCursor(
            runtime_id=runtime.id,
            provider=runtime.provider,
            adapter_name=adapter_name,
            stream=stream,
        )
        db.add(row)
    row.cursor_json = cursor
    row.high_watermark = _string_or_none(cursor.get("high_watermark") or cursor.get("last_collected_at"))
    row.last_success_at = _now()


def _hash_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_dt(value: datetime | None) -> str | None:
    """归一化时间到 tz-naive UTC ISO 字符串,使去重哈希不受存储后端 tz 行为影响。

    SQLite 读回的 DateTime 不带 tzinfo,MySQL 行为亦不保证一致;统一折算到 UTC
    并去掉 tzinfo,确保上报侧(tz-aware)与已落库行(可能 tz-naive)算出同一哈希。
    """
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat()


def _trace_content_payload(
    *,
    title: str,
    summary: str | None,
    trace_type: Any,
    started_at: datetime | None,
    ended_at: datetime | None,
    sensitivity: Any,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    """构造工作历程的去重内容载荷(标题/摘要/类型/时间/敏感度/元数据)。"""
    return {
        "title": title,
        "summary": summary,
        "trace_type": getattr(trace_type, "value", trace_type),
        "started_at": _canonical_dt(started_at),
        "ended_at": _canonical_dt(ended_at),
        "sensitivity": getattr(sensitivity, "value", sensitivity),
        "metadata": metadata,
    }


def _hash_work_trace(trace: NormalizedWorkTrace) -> str:
    return _hash_payload(
        _trace_content_payload(
            title=trace.title,
            summary=trace.summary,
            trace_type=trace.trace_type,
            started_at=trace.started_at,
            ended_at=trace.ended_at,
            sensitivity=trace.sensitivity,
            metadata=trace.metadata,
        )
    )


def _hash_work_trace_row(row: WorkTrace) -> str:
    return _hash_payload(
        _trace_content_payload(
            title=row.title,
            summary=row.summary,
            trace_type=row.trace_type,
            started_at=row.started_at,
            ended_at=row.ended_at,
            sensitivity=row.sensitivity,
            metadata=row.metadata_json,
        )
    )


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _now() -> datetime:
    return datetime.now(timezone.utc)
