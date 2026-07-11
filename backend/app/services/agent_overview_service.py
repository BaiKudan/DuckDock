from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import HTTPException
from sqlalchemy import select

from app.core.config import resolve_llm, settings
from app.models.control_plane import (
    AIAsset,
    AgentInsightJob,
    AgentInsightStatus,
    AssetOwnership,
    CollectionJob,
    MemoryCandidate,
    MemoryCandidateType,
    ReporterCredential,
    ReportAnalysisJob,
    ReportUploadSession,
    RuntimeInstance,
    WorkTrace,
)
from app.models.user import User
from app.schemas.agent_overview import (
    AgentInsightJobOut,
    AgentOverviewMetricsOut,
    AgentOverviewOut,
    AgentOverviewReporterOut,
    AgentOverviewUserOut,
    AgentReportTimelineItemOut,
)
from app.schemas.control_plane import AIAssetOut, MemoryCandidateOut, RuntimeInstanceOut, WorkTraceOut
from app.services.llm_http import LLMConfigError, validate_llm_base_url


PROMPT_VERSION = "agent-overview-v1"


async def build_agent_overview(db, *, user_id: int, runtime_id: int) -> AgentOverviewOut:
    user = await _get_user(db, user_id)
    runtime = await _get_runtime(db, runtime_id)
    assets = await _load_assets(db, user_id=user.id, runtime_id=runtime.id)
    asset_ids = [asset.id for asset in assets]
    traces = await _load_traces(db, user_id=user.id, runtime_id=runtime.id, asset_ids=asset_ids)
    candidates = await _load_memory_candidates(db, runtime_id=runtime.id)
    upload_sessions = await _load_upload_sessions(db, runtime_id=runtime.id)
    analysis_jobs_by_session = await _load_analysis_jobs_by_session(db, upload_sessions)
    collection_jobs_by_id = await _load_collection_jobs_by_id(db, traces=traces, upload_sessions=upload_sessions)
    timeline = _build_timeline(
        traces=traces,
        upload_sessions=upload_sessions,
        analysis_jobs_by_session=analysis_jobs_by_session,
        collection_jobs_by_id=collection_jobs_by_id,
        candidates=candidates,
    )
    reporter = await _build_reporter(db, user_id=user.id, runtime=runtime, timeline=timeline)
    latest_insight = (
        await db.execute(
            select(AgentInsightJob)
            .where(AgentInsightJob.user_id == user.id, AgentInsightJob.runtime_id == runtime.id)
            .order_by(AgentInsightJob.created_at.desc(), AgentInsightJob.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return AgentOverviewOut(
        user=AgentOverviewUserOut(id=user.id, username=user.username, email=user.email, full_name=user.full_name),
        runtime=RuntimeInstanceOut.model_validate(runtime),
        reporter=reporter,
        metrics=_build_metrics(assets=assets, traces=traces, candidates=candidates, timeline=timeline),
        timeline=timeline,
        assets=[AIAssetOut.model_validate(asset) for asset in assets],
        work_traces=[WorkTraceOut.model_validate(trace) for trace in traces[:50]],
        memory_candidates=[MemoryCandidateOut.model_validate(candidate) for candidate in candidates[:50]],
        latest_insight=AgentInsightJobOut.model_validate(latest_insight) if latest_insight is not None else None,
    )


async def create_agent_insight_job(db, *, user_id: int, runtime_id: int, requested_by: int | None) -> AgentInsightJob:
    await _get_user(db, user_id)
    await _get_runtime(db, runtime_id)
    overview = await build_agent_overview(db, user_id=user_id, runtime_id=runtime_id)
    snapshot = _snapshot_for_llm(overview)
    job = AgentInsightJob(
        user_id=user_id,
        runtime_id=runtime_id,
        requested_by=requested_by,
        status=AgentInsightStatus.PENDING,
        prompt_version=PROMPT_VERSION,
        input_hash=_hash_snapshot(snapshot),
        ai_assist_json={"mode": "baseline", "degraded": True, "reason": "queued"},
    )
    db.add(job)
    await db.flush()
    return job


async def get_agent_insight_job(db, *, job_id: int) -> AgentInsightJob:
    job = (await db.execute(select(AgentInsightJob).where(AgentInsightJob.id == job_id))).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Agent insight job not found")
    return job


async def run_agent_insight_job(db, *, job_id: int) -> AgentInsightJob:
    job = await get_agent_insight_job(db, job_id=job_id)
    if job.status == AgentInsightStatus.SUCCEEDED:
        return job
    job.status = AgentInsightStatus.RUNNING
    job.started_at = datetime.now(timezone.utc)
    await db.flush()

    overview = await build_agent_overview(db, user_id=job.user_id, runtime_id=job.runtime_id)
    snapshot = _snapshot_for_llm(overview)
    job.input_hash = _hash_snapshot(snapshot)
    try:
        result, ai_assist, model = await _analyze_snapshot(snapshot)
        job.result_json = result
        job.ai_assist_json = ai_assist
        job.model = model
        job.error_message = None
        job.status = AgentInsightStatus.SUCCEEDED
    except Exception as exc:
        job.result_json = _baseline_result(snapshot, reason="analysis_error")
        job.ai_assist_json = {"mode": "baseline", "degraded": True, "reason": f"analysis_error: {exc}"[:240]}
        job.error_message = str(exc)[:1000]
        job.status = AgentInsightStatus.SUCCEEDED
    job.finished_at = datetime.now(timezone.utc)
    await db.flush()
    return job


async def _get_user(db, user_id: int) -> User:
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


async def _get_runtime(db, runtime_id: int) -> RuntimeInstance:
    runtime = (await db.execute(select(RuntimeInstance).where(RuntimeInstance.id == runtime_id))).scalar_one_or_none()
    if runtime is None:
        raise HTTPException(status_code=404, detail="Runtime not found")
    return runtime


async def _load_assets(db, *, user_id: int, runtime_id: int) -> list[AIAsset]:
    owned_ids = (
        await db.execute(
            select(AssetOwnership.asset_id).where(AssetOwnership.user_id == user_id)
        )
    ).scalars().all()
    stmt = select(AIAsset).where(AIAsset.source_runtime_id == runtime_id)
    if owned_ids:
        stmt = stmt.where(AIAsset.id.in_(list(owned_ids)))
    else:
        stmt = stmt.where(AIAsset.id == -1)
    return (await db.execute(stmt.order_by(AIAsset.last_seen_at.desc(), AIAsset.id.desc()))).scalars().all()


async def _load_traces(db, *, user_id: int, runtime_id: int, asset_ids: list[int]) -> list[WorkTrace]:
    stmt = select(WorkTrace).where(WorkTrace.runtime_id == runtime_id)
    if asset_ids:
        stmt = stmt.where((WorkTrace.actor_user_id == user_id) | (WorkTrace.asset_id.in_(asset_ids)))
    else:
        stmt = stmt.where(WorkTrace.actor_user_id == user_id)
    return (await db.execute(stmt.order_by(WorkTrace.created_at.desc(), WorkTrace.id.desc()).limit(100))).scalars().all()


async def _load_memory_candidates(db, *, runtime_id: int) -> list[MemoryCandidate]:
    return (
        await db.execute(
            select(MemoryCandidate)
            .where(MemoryCandidate.runtime_id == runtime_id)
            .order_by(MemoryCandidate.created_at.desc(), MemoryCandidate.id.desc())
            .limit(200)
        )
    ).scalars().all()


async def _load_upload_sessions(db, *, runtime_id: int) -> list[ReportUploadSession]:
    return (
        await db.execute(
            select(ReportUploadSession)
            .where(ReportUploadSession.runtime_id == runtime_id)
            .order_by(ReportUploadSession.created_at.desc(), ReportUploadSession.id.desc())
            .limit(50)
        )
    ).scalars().all()


async def _load_analysis_jobs_by_session(db, sessions: list[ReportUploadSession]) -> dict[int, ReportAnalysisJob]:
    session_ids = [row.id for row in sessions]
    if not session_ids:
        return {}
    jobs = (
        await db.execute(select(ReportAnalysisJob).where(ReportAnalysisJob.report_upload_session_id.in_(session_ids)))
    ).scalars().all()
    return {job.report_upload_session_id: job for job in jobs}


async def _load_collection_jobs_by_id(
    db,
    *,
    traces: list[WorkTrace],
    upload_sessions: list[ReportUploadSession],
) -> dict[int, CollectionJob]:
    ids: set[int] = set()
    for trace in traces:
        metadata = trace.metadata_json if isinstance(trace.metadata_json, dict) else {}
        raw_id = metadata.get("collection_job_id")
        if isinstance(raw_id, int):
            ids.add(raw_id)
    for session in upload_sessions:
        if session.collection_job_id is not None:
            ids.add(session.collection_job_id)
    if not ids:
        return {}
    rows = (await db.execute(select(CollectionJob).where(CollectionJob.id.in_(ids)))).scalars().all()
    return {row.id: row for row in rows}


async def _build_reporter(
    db,
    *,
    user_id: int,
    runtime: RuntimeInstance,
    timeline: list[AgentReportTimelineItemOut],
) -> AgentOverviewReporterOut:
    credential = (
        await db.execute(
            select(ReporterCredential)
            .where(
                ReporterCredential.runtime_id == runtime.id,
                ReporterCredential.user_id == user_id,
                ReporterCredential.is_active.is_(True),
                ReporterCredential.revoked_at.is_(None),
            )
            .order_by(ReporterCredential.last_heartbeat_at.desc(), ReporterCredential.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    latest_report_at = max((item.created_at for item in timeline), default=None)
    heartbeat_at = credential.last_heartbeat_at if credential is not None else None
    healthy_cutoff = datetime.now(timezone.utc) - timedelta(days=8)
    if heartbeat_at is not None and _ensure_aware(heartbeat_at) >= healthy_cutoff:
        state, message = "healthy", "Reporter heartbeat is recent."
    elif latest_report_at is not None:
        state, message = "waiting", "Reports exist but reporter heartbeat is stale or unavailable."
    elif credential is not None:
        state, message = "waiting", "Reporter credential is active; waiting for the first report."
    else:
        state, message = "unknown", "No active Reporter credential for this user/runtime."
    return AgentOverviewReporterOut(
        credential_id=credential.id if credential is not None else None,
        token_prefix=credential.token_prefix if credential is not None else None,
        state=state,  # type: ignore[arg-type]
        message=message,
        last_heartbeat_at=heartbeat_at,
        last_used_at=credential.last_used_at if credential is not None else None,
        heartbeat_json=credential.heartbeat_json if credential is not None else None,
    )


def _build_timeline(
    *,
    traces: list[WorkTrace],
    upload_sessions: list[ReportUploadSession],
    analysis_jobs_by_session: dict[int, ReportAnalysisJob],
    collection_jobs_by_id: dict[int, CollectionJob],
    candidates: list[MemoryCandidate],
) -> list[AgentReportTimelineItemOut]:
    items: list[AgentReportTimelineItemOut] = []
    for trace in traces:
        metadata = trace.metadata_json if isinstance(trace.metadata_json, dict) else {}
        if metadata.get("source") != "structured_report":
            continue
        report_id = str(metadata.get("report_id") or trace.external_session_id or f"trace-{trace.id}")
        collection_job_id = metadata.get("collection_job_id") if isinstance(metadata.get("collection_job_id"), int) else None
        collection_job = collection_jobs_by_id.get(collection_job_id) if collection_job_id is not None else None
        candidate_counts = _candidate_counts(candidates, report_id=report_id)
        items.append(
            AgentReportTimelineItemOut(
                report_id=report_id,
                source="structured_report",
                report_type=str(metadata.get("report_type") or "structured"),
                status=collection_job.status.value if collection_job is not None else "succeeded",
                title=trace.title,
                summary=trace.summary,
                period_start=trace.started_at,
                period_end=trace.ended_at,
                created_at=trace.created_at,
                collection_job_id=collection_job_id,
                work_trace_id=trace.id,
                highlights=_metadata_list(trace.summary, "Highlights"),
                blockers=_metadata_list(trace.summary, "Blockers"),
                next_actions=_metadata_list(trace.summary, "Next actions"),
                project_refs=_metadata_string_list(metadata.get("project_refs")),
                asset_count=len(_metadata_string_list(metadata.get("asset_external_ids"))),
                memory_candidate_count=candidate_counts["memory"],
                risk_signal_count=candidate_counts["risk"],
                handover_signal_count=candidate_counts["handover"],
            )
        )
    for session in upload_sessions:
        job = analysis_jobs_by_session.get(session.id)
        summary = job.summary_json if job is not None and isinstance(job.summary_json, dict) else {}
        items.append(
            AgentReportTimelineItemOut(
                report_id=session.report_id,
                source="report_pack",
                report_type=session.report_type,
                status=job.status.value if job is not None else session.status.value,
                title=f"{session.report_type} pack",
                summary=_pack_summary(summary, session),
                period_start=session.period_start,
                period_end=session.period_end,
                created_at=session.created_at,
                collection_job_id=session.collection_job_id,
                upload_session_id=session.id,
                analysis_job_id=job.id if job is not None else None,
                asset_count=int(summary.get("asset_card_count") or summary.get("asset_cards") or 0),
                memory_candidate_count=int(summary.get("memory_candidate_count") or 0),
                risk_signal_count=int(summary.get("risk_signal_count") or 0),
                handover_signal_count=int(summary.get("handover_signal_count") or 0),
            )
        )
    items.sort(key=lambda item: item.created_at, reverse=True)
    return items[:80]


def _candidate_counts(candidates: list[MemoryCandidate], *, report_id: str) -> dict[str, int]:
    rows = [
        row
        for row in candidates
        if isinstance(row.payload_json, dict) and row.payload_json.get("report_id") == report_id
    ]
    return {
        "memory": len(rows),
        "risk": sum(1 for row in rows if row.candidate_type == MemoryCandidateType.RISK_SIGNAL),
        "handover": sum(1 for row in rows if row.candidate_type == MemoryCandidateType.HANDOVER_SIGNAL),
    }


def _build_metrics(
    *,
    assets: list[AIAsset],
    traces: list[WorkTrace],
    candidates: list[MemoryCandidate],
    timeline: list[AgentReportTimelineItemOut],
) -> AgentOverviewMetricsOut:
    latest_report_at = max((item.created_at for item in timeline), default=None)
    latest_trace_at = max((trace.created_at for trace in traces), default=None)
    latest_asset_at = max((asset.last_seen_at for asset in assets), default=None)
    latest_activity_at = max([item for item in [latest_report_at, latest_trace_at, latest_asset_at] if item is not None], default=None)
    return AgentOverviewMetricsOut(
        asset_count=len(assets),
        work_trace_count=len(traces),
        report_count=len(timeline),
        structured_report_count=sum(1 for item in timeline if item.source == "structured_report"),
        pack_report_count=sum(1 for item in timeline if item.source == "report_pack"),
        risk_signal_count=sum(1 for row in candidates if row.candidate_type == MemoryCandidateType.RISK_SIGNAL),
        handover_signal_count=sum(1 for row in candidates if row.candidate_type == MemoryCandidateType.HANDOVER_SIGNAL),
        blocker_count=sum(len(item.blockers) for item in timeline),
        latest_report_at=latest_report_at,
        latest_activity_at=latest_activity_at,
    )


def _snapshot_for_llm(overview: AgentOverviewOut) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "user": overview.user.model_dump(mode="json"),
        "runtime": overview.runtime.model_dump(mode="json"),
        "reporter": overview.reporter.model_dump(mode="json"),
        "metrics": overview.metrics.model_dump(mode="json"),
        "timeline": [item.model_dump(mode="json") for item in overview.timeline[:30]],
        "assets": [
            {
                "id": item.id,
                "name": item.name,
                "asset_type": item.asset_type.value if hasattr(item.asset_type, "value") else item.asset_type,
                "criticality": item.criticality.value if hasattr(item.criticality, "value") else item.criticality,
                "status": item.status.value if hasattr(item.status, "value") else item.status,
                "last_seen_at": item.last_seen_at.isoformat(),
            }
            for item in overview.assets[:50]
        ],
        "signals": [
            {
                "candidate_type": item.candidate_type.value if hasattr(item.candidate_type, "value") else item.candidate_type,
                "title": item.title,
                "summary": item.summary,
                "confidence": item.confidence,
                "created_at": item.created_at.isoformat(),
            }
            for item in overview.memory_candidates[:50]
            if item.candidate_type in {MemoryCandidateType.RISK_SIGNAL, MemoryCandidateType.HANDOVER_SIGNAL}
        ],
    }
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    max_chars = max(settings.DUCKDOCK_LLM_MAX_INPUT_CHARS, 2000)
    if len(encoded) <= max_chars:
        return snapshot
    timeline = snapshot.get("timeline")
    assets = snapshot.get("assets")
    signals = snapshot.get("signals")
    snapshot["timeline"] = timeline[:12] if isinstance(timeline, list) else []
    snapshot["assets"] = assets[:25] if isinstance(assets, list) else []
    snapshot["signals"] = signals[:25] if isinstance(signals, list) else []
    snapshot["truncated"] = True
    return snapshot


def _hash_snapshot(snapshot: dict[str, Any]) -> str:
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def _analyze_snapshot(snapshot: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    api_key, base_url, model = resolve_llm("DUCKDOCK_LLM")
    if not api_key or not base_url or not model:
        return _baseline_result(snapshot, reason="llm_unconfigured"), {
            "mode": "baseline",
            "degraded": True,
            "reason": "DUCKDOCK_LLM_API_KEY is not configured",
        }, model or None
    try:
        checked_base_url = validate_llm_base_url(base_url).rstrip("/")
    except LLMConfigError as exc:
        return _baseline_result(snapshot, reason="invalid_llm_base_url"), {
            "mode": "baseline",
            "degraded": True,
            "reason": str(exc),
        }, model
    prompt = _build_prompt(snapshot)
    payload = {
        "model": model,
        "temperature": 0.1,
        "max_tokens": 1600,
        "response_format": {"type": "json_object"},
        "enable_thinking": settings.DUCKDOCK_LLM_ENABLE_THINKING,
        "messages": [
            {
                "role": "system",
                "content": "You are DuckDock's enterprise AI agent timeline analyst. Return compact JSON only.",
            },
            {"role": "user", "content": prompt},
        ],
    }
    async with httpx.AsyncClient(timeout=settings.DUCKDOCK_LLM_TIMEOUT_SECONDS) as client:
        body, compat_retry = await _post_chat_completion(
            client,
            f"{checked_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            payload=payload,
        )
    content = body["choices"][0]["message"]["content"]
    parsed = _extract_json_object(content)
    result = _normalize_result(parsed)
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else None
    ai_assist = {
        "mode": "llm",
        "degraded": False,
        "reason": "compat_retry_without_response_format" if compat_retry else None,
        "usage": usage,
    }
    return result, ai_assist, model


async def _post_chat_completion(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    try:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json(), False
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code not in {500, 502, 503, 504}:
            raise
        retry_payload = dict(payload)
        retry_payload.pop("response_format", None)
        retry_payload.pop("enable_thinking", None)
        response = await client.post(url, headers=headers, json=retry_payload)
        response.raise_for_status()
        return response.json(), True


def _build_prompt(snapshot: dict[str, Any]) -> str:
    return (
        "基于 DuckDock 已入库的结构化摘要,为管理员生成这个员工/agent 的时间线概览。"
        "不要编造未出现的事实,不要要求读取原始聊天或密钥。"
        "只返回 JSON 对象,字段为: executive_summary(str), recent_activity(str), "
        "risks(list[str]), handover_readiness(str), recommendations(list[str]), timeline_highlights(list[str]).\n"
        f"输入快照:\n{json.dumps(snapshot, ensure_ascii=False, default=str)}"
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("LLM response did not contain a JSON object")
    value = json.loads(stripped[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("LLM response JSON must be an object")
    return value


def _normalize_result(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "executive_summary": _text(value.get("executive_summary"), "暂无概览。"),
        "recent_activity": _text(value.get("recent_activity"), "暂无近期活动摘要。"),
        "risks": _text_list(value.get("risks"))[:8],
        "handover_readiness": _text(value.get("handover_readiness"), "需要继续积累报告和资产确认。"),
        "recommendations": _text_list(value.get("recommendations"))[:8],
        "timeline_highlights": _text_list(value.get("timeline_highlights"))[:8],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "prompt_version": PROMPT_VERSION,
    }


def _baseline_result(snapshot: dict[str, Any], *, reason: str) -> dict[str, Any]:
    raw_metrics = snapshot.get("metrics")
    metrics: dict[str, Any] = raw_metrics if isinstance(raw_metrics, dict) else {}
    raw_timeline = snapshot.get("timeline")
    timeline: list[Any] = raw_timeline if isinstance(raw_timeline, list) else []
    raw_signals = snapshot.get("signals")
    signals: list[Any] = raw_signals if isinstance(raw_signals, list) else []
    risks = [
        str(item.get("title") or item.get("summary"))
        for item in signals
        if isinstance(item, dict) and item.get("candidate_type") == "risk_signal"
    ][:5]
    recommendations = []
    if int(metrics.get("report_count") or 0) == 0:
        recommendations.append("先让 Reporter 提交一次结构化日报/周报。")
    if int(metrics.get("handover_signal_count") or 0) > 0:
        recommendations.append("检查交接信号,为关键自动化任务指定 fallback owner。")
    if int(metrics.get("blocker_count") or 0) > 0:
        recommendations.append("优先处理最近报告中的 blockers。")
    if not recommendations:
        recommendations.append("保持 Reporter 周期任务开启,继续积累时间线。")
    return {
        "executive_summary": (
            f"该员工/agent 当前关联 {metrics.get('asset_count', 0)} 个资产、"
            f"{metrics.get('work_trace_count', 0)} 条工作历程、{metrics.get('report_count', 0)} 份报告。"
        ),
        "recent_activity": f"最近时间线节点: {len(timeline)} 条; 最新报告时间 {metrics.get('latest_report_at') or '暂无'}。",
        "risks": risks,
        "handover_readiness": "baseline 根据已入库索引判断:可用于初步盘点,关键资产仍需管理员人工复核。",
        "recommendations": recommendations,
        "timeline_highlights": [
            str(item.get("title") or item.get("report_id"))
            for item in timeline[:5]
            if isinstance(item, dict)
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "prompt_version": PROMPT_VERSION,
        "baseline_reason": reason,
    }


def _metadata_list(summary: str | None, section: str) -> list[str]:
    if not summary:
        return []
    marker = f"## {section}\n"
    if marker not in summary:
        return []
    after = summary.split(marker, 1)[1]
    block = after.split("\n\n", 1)[0]
    return [line[2:].strip() for line in block.splitlines() if line.startswith("- ") and line[2:].strip()]


def _metadata_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _pack_summary(summary: dict[str, Any], session: ReportUploadSession) -> str | None:
    raw_worker_metadata = summary.get("worker_metadata")
    worker_metadata: dict[str, Any] = raw_worker_metadata if isinstance(raw_worker_metadata, dict) else {}
    mode = worker_metadata.get("analysis_mode") or summary.get("analysis_mode")
    if mode:
        return f"Analysis worker processed {session.filename} in {mode} mode."
    if session.error_message:
        return session.error_message
    return f"Uploaded {session.filename}."


def _text(value: Any, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()[:4000]
    return default


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:1000] for item in value if str(item).strip()]


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
