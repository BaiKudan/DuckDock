from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.models.control_plane import (
    AIAsset,
    AnalysisResultArtifact,
    AnalysisResultArtifactKind,
    AssetOwnership,
    AssetStatus,
    AssetType,
    Criticality,
    MemoryCandidate,
    MemoryCandidateStatus,
    MemoryCandidateType,
    OwnerType,
    ReportAnalysisJob,
    ReportUploadSession,
    RuntimeBinding,
    RuntimeInstance,
    Sensitivity,
    TraceType,
    WorkTrace,
)
from app.models.user import User
from app.services.artifact_service import ArtifactStorageError, artifact_service


MATERIALIZED_COUNT_KEYS = (
    "asset_cards",
    "asset_ownerships",
    "runtime_bindings",
    "worktrace_summaries",
    "memory_candidates",
    "handover_signals",
    "risk_signals",
)


class ArtifactReadError(Exception):
    """Raised when an artifact cannot be read or parsed.

    This deliberately distinguishes a genuine read/parse failure (the bytes
    were missing, unreadable, or malformed) from an artifact that was read
    successfully but simply carried no usable data. FR-005 (PARTIAL_FAILED)
    relies on the difference: a read/parse failure on one artifact must mark
    the job partially failed, whereas an empty-but-valid artifact must not.
    """


async def materialize_analysis_result(
    db,
    *,
    job: ReportAnalysisJob,
    session: ReportUploadSession,
    runtime: RuntimeInstance,
    artifacts: list[AnalysisResultArtifact],
) -> dict[str, Any]:
    counts = _empty_materialization_counts()
    asset_map: dict[str, AIAsset] = {}
    for artifact in artifacts:
        try:
            if artifact.kind == AnalysisResultArtifactKind.ASSET_CARDS:
                cards = _load_json_items(artifact.object_key)
                asset_counts = await _materialize_asset_cards(
                    db,
                    job=job,
                    runtime=runtime,
                    artifact=artifact,
                    cards=cards,
                    asset_map=asset_map,
                )
                _merge_materialization_counts(counts, asset_counts)
            elif artifact.kind == AnalysisResultArtifactKind.WORKTRACE_SUMMARY:
                trace_counts = await _materialize_worktrace_summary(
                    db,
                    job=job,
                    session=session,
                    runtime=runtime,
                    artifact=artifact,
                )
                _merge_materialization_counts(counts, trace_counts)
            elif artifact.kind == AnalysisResultArtifactKind.MEMORY_CANDIDATES:
                items = _load_json_items(artifact.object_key)
                candidate_counts = await _materialize_memory_candidates(
                    db,
                    job=job,
                    session=session,
                    artifact=artifact,
                    items=items,
                    default_type=MemoryCandidateType.KNOWLEDGE_NOTE,
                )
                _merge_materialization_counts(counts, candidate_counts)
            elif artifact.kind == AnalysisResultArtifactKind.HANDOVER_SIGNALS:
                items = _load_json_items(artifact.object_key)
                signal_counts = await _materialize_handover_signals(
                    db,
                    job=job,
                    session=session,
                    artifact=artifact,
                    items=items,
                )
                _merge_materialization_counts(counts, signal_counts)
            else:
                # Kinds we do not materialize (e.g. ANALYSIS_RESULT) are not
                # counted as a materialization success and never fail here.
                continue
        except ArtifactReadError as exc:
            counts["failures"].append(
                {
                    "kind": artifact.kind.value,
                    "object_key": artifact.object_key,
                    "error": str(exc),
                }
            )
            continue
        counts["succeeded_artifacts"] += 1
    await db.flush()
    return counts


def _empty_count_bucket() -> dict[str, int]:
    return {key: 0 for key in MATERIALIZED_COUNT_KEYS}


def _empty_materialization_counts() -> dict[str, Any]:
    counts: dict[str, Any] = {
        "succeeded_artifacts": 0,
        "failures": [],
        "parsed_counts": _empty_count_bucket(),
        "inserted_counts": _empty_count_bucket(),
        "updated_counts": _empty_count_bucket(),
        "deduped_counts": _empty_count_bucket(),
        "skipped_counts": _empty_count_bucket(),
    }
    for key in MATERIALIZED_COUNT_KEYS:
        counts[key] = 0
    return counts


def _merge_materialization_counts(target: dict[str, Any], source: dict[str, Any]) -> None:
    for bucket_name in ("parsed_counts", "inserted_counts", "updated_counts", "deduped_counts", "skipped_counts"):
        target_bucket = target[bucket_name]
        source_bucket = source.get(bucket_name)
        if not isinstance(source_bucket, dict):
            continue
        for key in MATERIALIZED_COUNT_KEYS:
            target_bucket[key] += int(source_bucket.get(key) or 0)
    inserted = target["inserted_counts"]
    for key in MATERIALIZED_COUNT_KEYS:
        target[key] = inserted[key]


def _single_count_bucket(bucket_name: str, key: str, amount: int = 1) -> dict[str, Any]:
    counts = _empty_materialization_counts()
    counts[bucket_name][key] += amount
    for legacy_key in MATERIALIZED_COUNT_KEYS:
        counts[legacy_key] = counts["inserted_counts"][legacy_key]
    return counts


async def _materialize_asset_cards(
    db,
    *,
    job: ReportAnalysisJob,
    runtime: RuntimeInstance,
    artifact: AnalysisResultArtifact,
    cards: list[dict[str, Any]],
    asset_map: dict[str, AIAsset],
) -> dict[str, Any]:
    counts = _empty_materialization_counts()
    for card in cards:
        name = _first_text(card, "name", "title", "asset_name")
        if not name:
            counts["skipped_counts"]["asset_cards"] += 1
            continue
        counts["parsed_counts"]["asset_cards"] += 1
        external_id = _asset_external_id(card, name)
        row = (
            await db.execute(
                select(AIAsset).where(
                    AIAsset.source_provider == runtime.provider,
                    AIAsset.source_runtime_id == runtime.id,
                    AIAsset.external_id == external_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = AIAsset(
                asset_type=_asset_type(card),
                name=name,
                source_provider=runtime.provider,
                source_runtime_id=runtime.id,
                external_id=external_id,
            )
            db.add(row)
            counts["inserted_counts"]["asset_cards"] += 1
        else:
            counts["updated_counts"]["asset_cards"] += 1
        row.asset_type = _asset_type(card)
        row.name = name
        row.description = _first_text(card, "description", "summary")
        row.status = _asset_status(card)
        row.criticality = _criticality(card)
        row.content_hash = _first_text(card, "content_hash", "sha256", "hash") or artifact.sha256
        row.metadata_json = _compact_metadata(
            card,
            source="analysis_result",
            analysis_job_id=job.id,
            source_object_uri=f"s3://{artifact.bucket}/{artifact.object_key}",
        )
        row.last_seen_at = _now()
        await db.flush()
        user_id = await _resolve_user_id_from_hints(db, _principal_hints(card))
        if user_id is not None:
            owner_result = await _ensure_asset_owner(
                db,
                asset_id=row.id,
                user_id=user_id,
                owner_type=_owner_type(card),
                confidence=_float(card.get("ownership_confidence"), default=0.9),
            )
            if owner_result == "inserted":
                counts["inserted_counts"]["asset_ownerships"] += 1
            elif owner_result == "updated":
                counts["updated_counts"]["asset_ownerships"] += 1
        asset_map[external_id] = row
        binding_result = await _ensure_runtime_binding(db, runtime=runtime, asset=row, external_ref=external_id)
        if binding_result == "inserted":
            counts["inserted_counts"]["runtime_bindings"] += 1
        elif binding_result == "updated":
            counts["updated_counts"]["runtime_bindings"] += 1
    for key in MATERIALIZED_COUNT_KEYS:
        counts[key] = counts["inserted_counts"][key]
    return counts


async def _ensure_runtime_binding(db, *, runtime: RuntimeInstance, asset: AIAsset, external_ref: str) -> str:
    existing = (
        await db.execute(
            select(RuntimeBinding).where(
                RuntimeBinding.asset_id == asset.id,
                RuntimeBinding.runtime_id == runtime.id,
                RuntimeBinding.external_ref == external_ref,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            RuntimeBinding(
                asset_id=asset.id,
                runtime_id=runtime.id,
                external_ref=external_ref,
                environment="analysis",
                usage_status="active",
                metadata_json={"source": "analysis_result"},
            )
        )
        return "inserted"
    existing.usage_status = "active"
    existing.last_used_at = _now()
    return "updated"


async def _materialize_worktrace_summary(
    db,
    *,
    job: ReportAnalysisJob,
    session: ReportUploadSession,
    runtime: RuntimeInstance,
    artifact: AnalysisResultArtifact,
) -> dict[str, Any]:
    counts = _single_count_bucket("parsed_counts", "worktrace_summaries")
    external_session_id = f"analysis-summary:{session.report_id}:{job.id}"
    existing = (
        await db.execute(
            select(WorkTrace).where(
                WorkTrace.runtime_id == runtime.id,
                WorkTrace.external_session_id == external_session_id,
            )
        )
    ).scalar_one_or_none()
    summary = _load_text(artifact.object_key)
    if not summary.strip():
        counts["skipped_counts"]["worktrace_summaries"] += 1
        return counts
    title = _summary_title(summary) or f"{session.report_type} analysis summary"
    actor_user_id = await _resolve_user_id_from_hints(
        db,
        [
            *_principal_hints(artifact.summary_json),
            *_principal_hints(session.metadata_json),
            *_principal_hints(session.manifest_json),
        ],
    )
    if actor_user_id is None:
        actor_user_id = await _resolve_runtime_enrollment_user_id(db, runtime)
    if existing is None:
        db.add(
            WorkTrace(
                runtime_id=runtime.id,
                external_session_id=external_session_id,
                actor_user_id=actor_user_id,
                title=title,
                summary=_truncate(summary, 8000),
                trace_type=TraceType.SESSION,
                started_at=session.period_start,
                ended_at=session.period_end,
                sensitivity=Sensitivity.INTERNAL,
                metadata_json={
                    "source": "analysis_result",
                    "analysis_job_id": job.id,
                    "report_id": session.report_id,
                    "source_object_uri": f"s3://{artifact.bucket}/{artifact.object_key}",
                },
            )
        )
        counts["inserted_counts"]["worktrace_summaries"] += 1
        counts["worktrace_summaries"] = counts["inserted_counts"]["worktrace_summaries"]
        return counts
    existing.title = title
    existing.summary = _truncate(summary, 8000)
    existing.actor_user_id = actor_user_id
    existing.started_at = session.period_start
    existing.ended_at = session.period_end
    existing.metadata_json = {
        **(existing.metadata_json or {}),
        "source": "analysis_result",
        "analysis_job_id": job.id,
        "report_id": session.report_id,
        "source_object_uri": f"s3://{artifact.bucket}/{artifact.object_key}",
    }
    counts["updated_counts"]["worktrace_summaries"] += 1
    return counts


async def _ensure_asset_owner(
    db,
    *,
    asset_id: int,
    user_id: int,
    owner_type: OwnerType,
    confidence: float,
) -> str:
    existing = (
        await db.execute(
            select(AssetOwnership).where(
                AssetOwnership.asset_id == asset_id,
                AssetOwnership.user_id == user_id,
                AssetOwnership.owner_type == owner_type,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.confidence = max(existing.confidence, confidence)
        return "updated"
    has_primary = (
        await db.execute(
            select(AssetOwnership.id).where(
                AssetOwnership.asset_id == asset_id,
                AssetOwnership.is_primary.is_(True),
            )
        )
    ).scalar_one_or_none()
    db.add(
        AssetOwnership(
            asset_id=asset_id,
            user_id=user_id,
            owner_type=owner_type,
            confidence=confidence,
            is_primary=has_primary is None,
        )
    )
    return "inserted"


async def _materialize_memory_candidates(
    db,
    *,
    job: ReportAnalysisJob,
    session: ReportUploadSession,
    artifact: AnalysisResultArtifact,
    items: list[dict[str, Any]],
    default_type: MemoryCandidateType,
) -> dict[str, Any]:
    counts = _empty_materialization_counts()
    for item in items:
        counts["parsed_counts"]["memory_candidates"] += 1
        result = await _ensure_memory_candidate(
            db,
            job=job,
            session=session,
            artifact=artifact,
            item=item,
            candidate_type=_memory_type(item, default_type),
        )
        if result == "inserted":
            counts["inserted_counts"]["memory_candidates"] += 1
        elif result == "deduped":
            counts["deduped_counts"]["memory_candidates"] += 1
        else:
            counts["skipped_counts"]["memory_candidates"] += 1
    counts["memory_candidates"] = counts["inserted_counts"]["memory_candidates"]
    return counts


async def _materialize_handover_signals(
    db,
    *,
    job: ReportAnalysisJob,
    session: ReportUploadSession,
    artifact: AnalysisResultArtifact,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    counts = _empty_materialization_counts()
    for item in items:
        signal_type = str(item.get("signal_type") or item.get("type") or "").lower()
        candidate_type = MemoryCandidateType.RISK_SIGNAL if "risk" in signal_type else MemoryCandidateType.HANDOVER_SIGNAL
        key = "risk_signals" if candidate_type == MemoryCandidateType.RISK_SIGNAL else "handover_signals"
        counts["parsed_counts"][key] += 1
        result = await _ensure_memory_candidate(
            db,
            job=job,
            session=session,
            artifact=artifact,
            item=item,
            candidate_type=candidate_type,
        )
        if result == "inserted":
            counts["inserted_counts"][key] += 1
        elif result == "deduped":
            counts["deduped_counts"][key] += 1
        else:
            counts["skipped_counts"][key] += 1
    counts["handover_signals"] = counts["inserted_counts"]["handover_signals"]
    counts["risk_signals"] = counts["inserted_counts"]["risk_signals"]
    return counts


async def _ensure_memory_candidate(
    db,
    *,
    job: ReportAnalysisJob,
    session: ReportUploadSession,
    artifact: AnalysisResultArtifact,
    item: dict[str, Any],
    candidate_type: MemoryCandidateType,
) -> str:
    title = _first_text(item, "title", "name", "signal", "summary")
    if not title:
        return "skipped"
    subject_type = _first_text(item, "subject_type", "asset_type", "type") or "runtime"
    subject_key = _first_text(item, "subject_key", "asset_key", "id", "external_id")
    existing = (
        await db.execute(
            select(MemoryCandidate).where(
                MemoryCandidate.analysis_job_id == job.id,
                MemoryCandidate.candidate_type == candidate_type,
                MemoryCandidate.subject_type == subject_type,
                MemoryCandidate.subject_key == subject_key,
                MemoryCandidate.title == title,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return "deduped"
    db.add(
        MemoryCandidate(
            analysis_job_id=job.id,
            report_upload_session_id=session.id,
            runtime_id=job.runtime_id,
            candidate_type=candidate_type,
            status=MemoryCandidateStatus.CANDIDATE,
            subject_type=subject_type[:64],
            subject_key=subject_key[:255] if subject_key else None,
            title=title[:255],
            summary=_first_text(item, "summary", "description", "reason"),
            confidence=_float(item.get("confidence"), default=0.8),
            sensitivity=_sensitivity(item),
            source_object_uri=f"s3://{artifact.bucket}/{artifact.object_key}",
            source_sha256=artifact.sha256,
            payload_json=_compact_metadata(item, source="analysis_result", analysis_job_id=job.id),
        )
    )
    return "inserted"


def _load_json_items(object_key: str) -> list[dict[str, Any]]:
    # Distinguish "read/parse failed" (raise ArtifactReadError) from "no data"
    # (return []). A missing/unreadable object or malformed JSON is a failure that
    # must surface in the job's partial-failure accounting; a well-formed but empty
    # document is genuinely-absent data and must not fail the job.
    try:
        payload = artifact_service.read_object_bytes(object_key, max_bytes=16 * 1024 * 1024)
    except ArtifactStorageError as exc:
        raise ArtifactReadError(f"Could not read artifact {object_key}: {exc}") from exc
    try:
        value = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactReadError(f"Could not parse JSON artifact {object_key}: {exc}") from exc
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    for key in ("items", "assets", "asset_cards", "memory_candidates", "signals", "handover_signals"):
        nested = value.get(key)
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
    return [value]


def _load_text(object_key: str) -> str:
    # See _load_json_items: a read/decode failure raises ArtifactReadError so the
    # caller can record it as a partial failure, rather than being swallowed as "".
    try:
        payload = artifact_service.read_object_bytes(object_key, max_bytes=16 * 1024 * 1024)
    except ArtifactStorageError as exc:
        raise ArtifactReadError(f"Could not read artifact {object_key}: {exc}") from exc
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ArtifactReadError(f"Could not decode text artifact {object_key}: {exc}") from exc


def _asset_external_id(card: dict[str, Any], name: str) -> str:
    explicit = _first_text(card, "external_id", "id", "key", "asset_key")
    if explicit:
        return explicit[:255]
    digest = hashlib.sha256(
        json.dumps({"name": name, "type": _first_text(card, "type", "asset_type")}, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return f"analysis_asset:{digest}"


def _asset_type(card: dict[str, Any]) -> AssetType:
    value = _first_text(card, "asset_type", "type", "kind")
    if value:
        normalized = value.lower().replace("-", "_")
        for item in AssetType:
            if item.value == normalized:
                return item
    return AssetType.OTHER


def _asset_status(card: dict[str, Any]) -> AssetStatus:
    value = _first_text(card, "status")
    if value:
        normalized = value.lower().replace("-", "_")
        for item in AssetStatus:
            if item.value == normalized:
                return item
    return AssetStatus.ACTIVE


def _criticality(card: dict[str, Any]) -> Criticality:
    value = _first_text(card, "criticality", "risk_level", "priority")
    if value:
        normalized = value.lower().replace("-", "_")
        for item in Criticality:
            if item.value == normalized:
                return item
    return Criticality.MEDIUM


def _memory_type(item: dict[str, Any], default: MemoryCandidateType) -> MemoryCandidateType:
    value = _first_text(item, "candidate_type", "memory_type", "type")
    if value:
        normalized = value.lower().replace("-", "_")
        for candidate_type in MemoryCandidateType:
            if candidate_type.value == normalized:
                return candidate_type
    return default


def _owner_type(card: dict[str, Any]) -> OwnerType:
    value = _first_text(card, "owner_type", "ownership_type", "role")
    if value:
        normalized = value.lower().replace("-", "_")
        for owner_type in OwnerType:
            if owner_type.value == normalized:
                return owner_type
    if _first_text(card, "maintainer"):
        return OwnerType.MAINTAINER
    return OwnerType.CREATOR


def _sensitivity(item: dict[str, Any]) -> Sensitivity:
    value = _first_text(item, "sensitivity")
    if value:
        normalized = value.lower().replace("-", "_")
        for sensitivity in Sensitivity:
            if sensitivity.value == normalized:
                return sensitivity
    return Sensitivity.INTERNAL


def _summary_title(summary: str) -> str | None:
    for line in summary.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()[:255] or None
        if stripped:
            return stripped[:80]
    return None


def _first_text(value: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
        if item is not None and not isinstance(item, (dict, list)):
            text = str(item).strip()
            if text:
                return text
    return None


def _float(value: Any, *, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, result))


def _principal_hints(value: dict[str, Any] | None) -> list[str]:
    if not isinstance(value, dict):
        return []
    hints: list[str] = []
    for key in (
        "owner",
        "owner_hint",
        "owner_external_id",
        "owner_username",
        "owner_email",
        "created_by",
        "created_by_username",
        "created_by_email",
        "creator",
        "creator_id",
        "creator_username",
        "creator_email",
        "maintainer",
        "maintainer_username",
        "maintainer_email",
        "actor",
        "actor_username",
        "actor_email",
        "user",
        "username",
        "email",
    ):
        _collect_principal_hint(value.get(key), hints)
    return hints


def _collect_principal_hint(value: Any, hints: list[str]) -> None:
    if value is None:
        return
    if isinstance(value, str):
        text = value.strip()
        if text:
            hints.append(text)
        return
    if isinstance(value, dict):
        for key in ("email", "username", "name", "id", "external_id"):
            _collect_principal_hint(value.get(key), hints)
        return
    if isinstance(value, list):
        for item in value:
            _collect_principal_hint(item, hints)
        return
    text = str(value).strip()
    if text:
        hints.append(text)


async def _resolve_user_id_from_hints(db, hints: list[str]) -> int | None:
    seen: set[str] = set()
    for hint in hints:
        normalized = hint.strip()
        if not normalized or normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        if "@" in normalized:
            stmt = select(User.id).where(User.email == normalized).limit(1)
        else:
            stmt = select(User.id).where((User.username == normalized) | (User.email == normalized)).limit(1)
        user_id = (await db.execute(stmt)).scalar_one_or_none()
        if user_id is not None:
            return int(user_id)
    return None


async def _resolve_runtime_enrollment_user_id(db, runtime: RuntimeInstance) -> int | None:
    metadata = runtime.metadata_json if isinstance(runtime.metadata_json, dict) else {}
    reporter = metadata.get("reporter") if isinstance(metadata, dict) else None
    enrollment = reporter.get("enrollment") if isinstance(reporter, dict) else None
    if not isinstance(enrollment, dict):
        return None
    raw_user_id = enrollment.get("user_id")
    if isinstance(raw_user_id, int):
        exists = (await db.execute(select(User.id).where(User.id == raw_user_id).limit(1))).scalar_one_or_none()
        if exists is not None:
            return int(exists)
    return await _resolve_user_id_from_hints(db, _principal_hints(enrollment))


def _compact_metadata(value: dict[str, Any], **extra: Any) -> dict[str, Any]:
    allowed = {
        "project",
        "project_id",
        "project_name",
        "workspace",
        "workspace_name",
        "namespace",
        "repository",
        "repo",
        "tags",
        "owner",
        "owner_hint",
        "created_by",
        "maintainer",
        "confidence",
        "criticality",
        "risk_level",
        "risk_reason",
        "handover_status",
        "handover_action",
        "source_files",
        "related_assets",
    }
    compact = {key: value[key] for key in allowed if key in value}
    return {**compact, **extra}


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _now() -> datetime:
    return datetime.now(timezone.utc)
