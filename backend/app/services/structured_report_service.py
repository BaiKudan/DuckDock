from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from app.models.control_plane import (
    AIAsset,
    AssetOwnership,
    CollectionJob,
    CollectionTriggerType,
    JobStatus,
    MemoryCandidate,
    MemoryCandidateStatus,
    MemoryCandidateType,
    OwnerType,
    ReporterCredential,
    RuntimeBinding,
    RuntimeInstance,
    TraceType,
    WorkTrace,
)
from app.models.user import User
from app.schemas.control_plane import (
    AIAssetOut,
    CollectionJobOut,
    MemoryCandidateOut,
    StructuredReportAssetRef,
    StructuredReportMemoryInput,
    StructuredReportOut,
    StructuredReportSignalInput,
    StructuredReportSubmit,
    WorkTraceOut,
)
from app.services.report_upload_service import ReporterAuthContext
from app.services.outbox_event_service import (
    build_structured_report_recorded,
    enqueue_domain_event,
)
from app.services.tenant_write_service import (
    build_runtime_binding,
    build_work_trace,
    ensure_resource_namespace,
    require_active_namespace,
)


STRUCTURED_REPORT_SCOPE = "report.structured"


async def ingest_structured_report(
    db,
    *,
    reporter: ReporterAuthContext,
    body: StructuredReportSubmit,
) -> StructuredReportOut:
    if body.runtime_id != reporter.runtime.id:
        raise HTTPException(status_code=403, detail="Reporter credential cannot submit for this runtime")
    _require_structured_scope(reporter)
    namespace = await require_active_namespace(db, reporter.runtime.namespace_id)
    ensure_resource_namespace(reporter.runtime, namespace.id, relationship="runtime")

    actor_user_id = await _reporter_user_id(db, reporter)
    external_session_id = _external_session_id(runtime=reporter.runtime, body=body)
    existing = (
        await db.execute(
            select(WorkTrace).where(
                WorkTrace.runtime_id == reporter.runtime.id,
                WorkTrace.external_session_id == external_session_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        ensure_resource_namespace(existing, namespace.id, relationship="work_trace")
        # The original WorkTrace and its Outbox event are committed
        # atomically. A request replay must not rebuild the event from a
        # MySQL-reloaded timestamp: DATETIME precision may differ from the
        # original in-memory value and turn a valid API replay into a false
        # Outbox payload conflict.
        return await _deduped_response(db, existing)

    report_id = f"srpt_{datetime.now(timezone.utc):%Y%m%d%H%M%S}_{uuid4().hex[:12]}"
    job = CollectionJob(
        runtime_id=reporter.runtime.id,
        trigger_type=_trigger_type(body.report_type),
        status=JobStatus.SUCCEEDED,
        scope_json={
            "source": "duckdock_structured_report",
            "schema_version": body.schema_version,
            "report_id": report_id,
            "report_type": body.report_type,
            "period_start": body.period_start.isoformat() if body.period_start else None,
            "period_end": body.period_end.isoformat() if body.period_end else None,
            "idempotency_key": body.idempotency_key,
        },
        summary_json={
            "source": "structured_report",
            "report_id": report_id,
            "asset_ref_count": len(body.asset_refs),
            "memory_candidate_count": len(body.memory_candidates),
            "handover_signal_count": len(body.handover_signals),
            "project_ref_count": len(body.project_refs),
            "normalization": "schema_first",
        },
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    db.add(job)
    await db.flush()

    assets = []
    for item in body.asset_refs:
        asset = await _upsert_asset(db, runtime=reporter.runtime, actor_user_id=actor_user_id, job=job, item=item)
        assets.append(asset)

    linked_asset = assets[0] if len(assets) == 1 else None
    trace = build_work_trace(
        namespace_id=namespace.id,
        runtime=reporter.runtime,
        asset=linked_asset,
        external_session_id=external_session_id,
        actor_user_id=actor_user_id,
        title=body.title,
        summary=_report_summary(body),
        trace_type=TraceType.TASK_RUN if body.report_type in {"daily", "weekly"} else TraceType.SESSION,
        started_at=body.period_start,
        ended_at=body.period_end,
        sensitivity=body.sensitivity,
        metadata_json={
            "source": "structured_report",
            "schema_version": body.schema_version,
            "report_id": report_id,
            "report_type": body.report_type,
            "collection_job_id": job.id,
            "idempotency_key": body.idempotency_key,
            "project_refs": body.project_refs,
            "asset_external_ids": [asset.external_id for asset in assets],
            "metadata_json": body.metadata_json,
        },
    )
    db.add(trace)
    await db.flush()

    candidates = []
    candidates.extend(
        await _materialize_project_refs(
            db,
            runtime=reporter.runtime,
            job=job,
            report_id=report_id,
            project_refs=body.project_refs,
            sensitivity=body.sensitivity,
        )
    )
    for memory_item in body.memory_candidates:
        candidates.append(await _create_memory_candidate(db, runtime=reporter.runtime, job=job, report_id=report_id, item=memory_item))
    for signal_item in body.handover_signals:
        candidates.append(await _create_signal_candidate(db, runtime=reporter.runtime, job=job, report_id=report_id, item=signal_item))
    for blocker in body.blockers:
        candidates.append(
            await _create_memory_candidate(
                db,
                runtime=reporter.runtime,
                job=job,
                report_id=report_id,
                item=StructuredReportMemoryInput(
                    candidate_type=MemoryCandidateType.RISK_SIGNAL,
                    subject_type="report",
                    subject_key=report_id,
                    title=f"Blocker: {blocker[:200]}",
                    summary=blocker,
                    sensitivity=body.sensitivity,
                    confidence=0.7,
                ),
            )
        )

    reporter.runtime.last_sync_at = datetime.now(timezone.utc)
    await enqueue_domain_event(
        db,
        build_structured_report_recorded(trace),
    )
    await db.flush()
    return StructuredReportOut(
        report_id=report_id,
        status="succeeded",
        job=CollectionJobOut.model_validate(job),
        work_trace=WorkTraceOut.model_validate(trace),
        assets=[AIAssetOut.model_validate(asset) for asset in assets],
        memory_candidates=[MemoryCandidateOut.model_validate(candidate) for candidate in candidates],
        warnings=[],
    )


def _require_structured_scope(reporter: ReporterAuthContext) -> None:
    token = reporter.token
    if not isinstance(token, ReporterCredential):
        return
    scopes = token.scopes or []
    if STRUCTURED_REPORT_SCOPE not in scopes:
        raise HTTPException(status_code=403, detail="Reporter credential missing report.structured scope")


async def _reporter_user_id(db, reporter: ReporterAuthContext) -> int | None:
    if isinstance(reporter.token, ReporterCredential) and reporter.token.user_id is not None:
        return int(reporter.token.user_id)
    metadata = reporter.runtime.metadata_json if isinstance(reporter.runtime.metadata_json, dict) else {}
    enrollment = (metadata.get("reporter") or {}).get("enrollment") if isinstance(metadata.get("reporter"), dict) else None
    if not isinstance(enrollment, dict):
        return None
    raw_user_id = enrollment.get("user_id")
    if isinstance(raw_user_id, int):
        exists = (await db.execute(select(User.id).where(User.id == raw_user_id).limit(1))).scalar_one_or_none()
        if exists is not None:
            return int(exists)
    username = enrollment.get("username")
    if isinstance(username, str) and username.strip():
        return (await db.execute(select(User.id).where(User.username == username.strip()).limit(1))).scalar_one_or_none()
    return None


def _external_session_id(*, runtime: RuntimeInstance, body: StructuredReportSubmit) -> str:
    if body.idempotency_key:
        return f"structured-report:{runtime.id}:{body.idempotency_key}"
    return f"structured-report:{runtime.id}:{uuid4().hex}"


async def _deduped_response(db, trace: WorkTrace) -> StructuredReportOut:
    metadata = trace.metadata_json or {}
    report_id = str(metadata.get("report_id") or trace.external_session_id or f"trace-{trace.id}")
    job_id = metadata.get("collection_job_id")
    job = None
    if isinstance(job_id, int):
        job = await db.get(CollectionJob, job_id)
    if job is None:
        job = CollectionJob(
            runtime_id=trace.runtime_id,
            trigger_type=CollectionTriggerType.SCHEDULED,
            status=JobStatus.SUCCEEDED,
            scope_json={"source": "duckdock_structured_report", "report_id": report_id, "deduped": True},
            summary_json={"source": "structured_report", "report_id": report_id, "deduped": True},
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
        db.add(job)
        await db.flush()
    candidate_rows = (
        await db.execute(select(MemoryCandidate).where(MemoryCandidate.runtime_id == trace.runtime_id))
    ).scalars().all()
    candidates = [
        row
        for row in candidate_rows
        if isinstance(row.payload_json, dict) and row.payload_json.get("report_id") == report_id
    ]
    assets = []
    asset_ids = [value for value in metadata.get("asset_external_ids") or [] if isinstance(value, str)]
    if trace.runtime_id is not None and asset_ids:
        assets = (
            await db.execute(
                select(AIAsset).where(
                    AIAsset.source_runtime_id == trace.runtime_id,
                    AIAsset.external_id.in_(asset_ids),
                )
            )
        ).scalars().all()
    return StructuredReportOut(
        report_id=report_id,
        status="deduped",
        job=CollectionJobOut.model_validate(job),
        work_trace=WorkTraceOut.model_validate(trace),
        assets=[AIAssetOut.model_validate(asset) for asset in assets],
        memory_candidates=[MemoryCandidateOut.model_validate(candidate) for candidate in candidates],
        warnings=["idempotency_key matched an existing structured report"],
    )


async def _upsert_asset(
    db,
    *,
    runtime: RuntimeInstance,
    actor_user_id: int | None,
    job: CollectionJob,
    item: StructuredReportAssetRef,
) -> AIAsset:
    external_id = item.external_id or f"structured:{runtime.id}:{item.asset_type.value}:{item.name}"
    row = (
        await db.execute(
            select(AIAsset).where(
                AIAsset.source_provider == runtime.provider,
                AIAsset.source_runtime_id == runtime.id,
                AIAsset.external_id == external_id,
            )
        )
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if row is None:
        row = AIAsset(
            namespace_id=runtime.namespace_id,
            asset_type=item.asset_type,
            name=item.name,
            source_provider=runtime.provider,
            source_runtime_id=runtime.id,
            external_id=external_id,
            first_seen_at=now,
        )
        db.add(row)
    else:
        ensure_resource_namespace(
            row,
            runtime.namespace_id,
            relationship="asset",
        )
    row.asset_type = item.asset_type
    row.name = item.name
    row.description = item.description
    row.status = item.status
    row.criticality = item.criticality
    row.content_hash = item.content_hash
    row.metadata_json = {
        **(item.metadata_json or {}),
        "source": "structured_report",
        "collection_job_id": job.id,
    }
    row.last_seen_at = now
    await db.flush()
    await _ensure_runtime_binding(db, runtime=runtime, asset=row, external_ref=external_id)
    if actor_user_id is not None:
        await _ensure_asset_ownership(db, asset=row, user_id=actor_user_id)
    return row


async def _ensure_runtime_binding(db, *, runtime: RuntimeInstance, asset: AIAsset, external_ref: str) -> None:
    existing = (
        await db.execute(
            select(RuntimeBinding).where(
                RuntimeBinding.runtime_id == runtime.id,
                RuntimeBinding.asset_id == asset.id,
                RuntimeBinding.external_ref == external_ref,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            build_runtime_binding(
                namespace_id=runtime.namespace_id,
                runtime=runtime,
                asset=asset,
                external_ref=external_ref,
                environment="structured_report",
                usage_status="active",
                metadata_json={"source": "structured_report"},
            )
        )
    else:
        ensure_resource_namespace(
            existing,
            runtime.namespace_id,
            relationship="runtime_binding",
        )
        ensure_resource_namespace(asset, runtime.namespace_id, relationship="asset")
        existing.usage_status = "active"
        existing.last_used_at = datetime.now(timezone.utc)


async def _ensure_asset_ownership(db, *, asset: AIAsset, user_id: int) -> None:
    existing = (
        await db.execute(
            select(AssetOwnership).where(
                AssetOwnership.asset_id == asset.id,
                AssetOwnership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            AssetOwnership(
                namespace_id=asset.namespace_id,
                asset_id=asset.id,
                user_id=user_id,
                owner_type=OwnerType.CREATOR,
                confidence=0.8,
                is_primary=True,
            )
        )
    else:
        ensure_resource_namespace(
            existing,
            asset.namespace_id,
            relationship="asset_ownership",
        )
        existing.confidence = max(float(existing.confidence or 0), 0.8)


async def _materialize_project_refs(
    db,
    *,
    runtime: RuntimeInstance,
    job: CollectionJob,
    report_id: str,
    project_refs: list[str],
    sensitivity,
) -> list[MemoryCandidate]:
    rows = []
    for project in project_refs:
        if not project.strip():
            continue
        rows.append(
            await _create_memory_candidate(
                db,
                runtime=runtime,
                job=job,
                report_id=report_id,
                item=StructuredReportMemoryInput(
                    candidate_type=MemoryCandidateType.PROJECT_CONTEXT,
                    subject_type="project",
                    subject_key=project.strip(),
                    title=f"Project context: {project.strip()}",
                    summary=f"Structured report references project/context '{project.strip()}'.",
                    confidence=0.65,
                    sensitivity=sensitivity,
                ),
            )
        )
    return rows


async def _create_memory_candidate(
    db,
    *,
    runtime: RuntimeInstance,
    job: CollectionJob,
    report_id: str,
    item: StructuredReportMemoryInput,
) -> MemoryCandidate:
    row = MemoryCandidate(
        runtime_id=runtime.id,
        candidate_type=item.candidate_type,
        status=MemoryCandidateStatus.CANDIDATE,
        subject_type=item.subject_type,
        subject_key=item.subject_key,
        title=item.title,
        summary=item.summary,
        confidence=item.confidence,
        sensitivity=item.sensitivity,
        source_object_uri=None,
        payload_json={
            **(item.payload_json or {}),
            "source": "structured_report",
            "report_id": report_id,
            "collection_job_id": job.id,
        },
    )
    db.add(row)
    await db.flush()
    return row


async def _create_signal_candidate(
    db,
    *,
    runtime: RuntimeInstance,
    job: CollectionJob,
    report_id: str,
    item: StructuredReportSignalInput,
) -> MemoryCandidate:
    candidate_type = MemoryCandidateType.RISK_SIGNAL if item.signal_type == "risk" else MemoryCandidateType.HANDOVER_SIGNAL
    return await _create_memory_candidate(
        db,
        runtime=runtime,
        job=job,
        report_id=report_id,
        item=StructuredReportMemoryInput(
            candidate_type=candidate_type,
            subject_type=item.subject_type,
            subject_key=item.subject_key,
            title=item.title,
            summary=item.summary,
            confidence=item.confidence,
            sensitivity=item.sensitivity,
            payload_json={**(item.payload_json or {}), "signal_type": item.signal_type},
        ),
    )


def _report_summary(body: StructuredReportSubmit) -> str:
    sections = [body.summary.strip()]
    sections.extend(_section("Highlights", body.highlights))
    sections.extend(_section("Blockers", body.blockers))
    sections.extend(_section("Next actions", body.next_actions))
    sections.extend(_section("Projects", body.project_refs))
    return "\n\n".join(part for part in sections if part).strip()


def _section(title: str, values: list[str]) -> list[str]:
    cleaned = [value.strip() for value in values if value.strip()]
    if not cleaned:
        return []
    return [f"## {title}\n" + "\n".join(f"- {item}" for item in cleaned)]


def _trigger_type(report_type: str) -> CollectionTriggerType:
    if report_type.lower() in {"daily", "weekly", "status"}:
        return CollectionTriggerType.SCHEDULED
    if "handover" in report_type.lower():
        return CollectionTriggerType.PROJECT_HANDOVER
    return CollectionTriggerType.MANUAL
