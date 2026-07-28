from __future__ import annotations

from collections.abc import Sequence
import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.deps import (
    AdminUser,
    AssetManagerUser,
    AssetReaderUser,
    CurrentUser,
    DB,
    EvidenceReaderUser,
    HandoverManagerUser,
    HandoverReaderUser,
    RuntimeManagerUser,
    RuntimeReaderUser,
    WorktraceReaderUser,
    require_namespace_writer,
)
from app.services.iam_service import ensure_builtin_rbac, has_permission
from app.services.credential_service import DB_REF_PREFIX, make_db_ref, store_credential
from app.models.control_plane import EvidenceSourceType, EvidenceVisibility, Sensitivity
from app.services import evidence_service
from app.services.artifact_service import ArtifactStorageError, artifact_service
from app.services.evidence_service import EvidenceObjectError
from app.services.handover_advisor_service import (
    AssetContext,
    build_ai_assist,
    build_handover_advisor,
)
from app.services import handover_package_service
from app.schemas.control_plane import (
    EvidenceDownloadLinkOut,
    EvidenceDownloadLinkRequest,
    ExecutionReceipt,
    HandoverVerifyRequest,
    WorkArtifactOut,
    WorkTraceDetailOut,
    WorkTraceRevealRequest,
)
from app.core.config import settings
from app.core.security import hash_password
from app.models.user import SystemRole
from app.models.control_plane import (
    AIAsset,
    AdapterCursor,
    AdapterError,
    AdapterRunStep,
    ApprovalStatus,
    ApprovalTask,
    AssetOwnership,
    CollectionJob,
    CollectionTriggerType,
    Criticality,
    ExecutionAction,
    ExecutionMode,
    ExecutionStatus,
    HandoverAction,
    HandoverCase,
    HandoverItem,
    HandoverItemStatus,
    HandoverStatus,
    JobStatus,
    ReporterCredential,
    ReportUploadSession,
    ReportUploadStatus,
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
    RuntimeReportToken,
    RuntimeStatus,
    ProviderPrincipal,
    RawCollectionRecord,
    RuntimeCapabilitySnapshot,
    RuntimeBinding,
    WorkTrace,
    WorkArtifact,
    EvidenceItem,
)
from app.models.iam import EmploymentStatus, UserHandoverProfile
from app.schemas.control_plane import (
    AIAssetCreate,
    AIAssetFeedbackCreate,
    AIAssetOut,
    AIAssetUpdate,
    AdapterCursorOut,
    AdapterErrorOut,
    AdapterRunStepOut,
    AdapterCapabilityOut,
    ApprovalDecision,
    ApprovalTaskCreate,
    ApprovalTaskOut,
    AssetOwnershipCreate,
    AssetOwnershipOut,
    CollectionJobOut,
    DemoDataCleanupOut,
    ExecuteHandoverRequest,
    ExecutionActionOut,
    EvidenceItemOut,
    HandoverCaseCreate,
    HandoverCaseOut,
    HandoverCaseUpdate,
    HandoverItemCreate,
    HandoverItemOut,
    MyAIWorkspaceOut,
    MyWorkspaceProjectContextOut,
    MyWorkspaceReadinessOut,
    MyWorkspaceReporterStatusOut,
    RuntimeConnectionTestOut,
    ReporterCredentialCreatedOut,
    ReporterCredentialOut,
    ReporterCredentialRevoke,
    ReporterCredentialRotate,
    ReporterEnrollmentCreate,
    ReporterEnrollmentOut,
    ReporterHeartbeat,
    ReporterHeartbeatOut,
    RuntimeReportTokenCreate,
    RuntimeReportTokenCreatedOut,
    RuntimeReportTokenOut,
    ProviderPrincipalOut,
    RawCollectionRecordOut,
    ReportUploadSessionCreate,
    ReportUploadSessionFinalize,
    ReportUploadSessionOut,
    RuntimeCapabilitySnapshotOut,
    RuntimeInstanceCreate,
    RuntimeInstanceOut,
    RuntimeInstanceUpdate,
    StructuredReportOut,
    StructuredReportSubmit,
    WorkTraceOut,
)
from app.services.audit_service import audit
from app.services.adapter_collection_service import ingest_openclaw_backup
from app.services.report_upload_service import (
    ReporterAuthContext,
    authenticate_runtime_report_token,
    create_report_upload_session,
    finalize_report_upload_session,
    generate_runtime_report_token,
    get_report_upload_session,
    ingest_report_upload_session,
)
from app.services.structured_report_service import ingest_structured_report
from app.services.tenant_write_service import (
    InactiveNamespaceError,
    MissingNamespaceError,
    TenantMismatchError,
    build_evidence_item,
    ensure_resource_namespace,
    require_active_namespace,
)


router = APIRouter(tags=["control-plane"])
reporter_bearer = HTTPBearer()


def _tenant_write_http_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, TenantMismatchError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (MissingNamespaceError, InactiveNamespaceError)):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=422, detail="Tenant-scoped write rejected")


def _capability(provider: RuntimeProvider) -> AdapterCapabilityOut:
    matrix: dict[RuntimeProvider, dict[str, bool | str]] = {
        RuntimeProvider.OPENCLAW: {
            "asset_sync": True,
            "worktrace_sync": True,
            "artifact_sync": True,
            "backup_create": True,
            "restore": "manual",
            "browser_fallback": False,
        },
        RuntimeProvider.JVS: {
            "asset_sync": True,
            "worktrace_sync": True,
            "artifact_sync": True,
            "backup_create": False,
            "restore": False,
            "browser_fallback": False,
        },
        RuntimeProvider.ARKCLAW: {
            "asset_sync": "provider_confirmation_required",
            "worktrace_sync": "provider_confirmation_required",
            "artifact_sync": "provider_confirmation_required",
            "backup_create": "provider_confirmation_required",
            "restore": "provider_confirmation_required",
            "browser_fallback": "fallback_only",
        },
        RuntimeProvider.WORKBUDDY: {
            "asset_sync": "browser_or_private_api",
            "worktrace_sync": "browser_or_private_api",
            "artifact_sync": "browser_or_private_api",
            "backup_create": False,
            "restore": False,
            "browser_fallback": True,
        },
        RuntimeProvider.CUSTOM: {
            "asset_sync": "adapter_required",
            "worktrace_sync": "adapter_required",
            "artifact_sync": "adapter_required",
            "backup_create": "adapter_required",
            "restore": "adapter_required",
            "browser_fallback": "adapter_required",
        },
    }
    return AdapterCapabilityOut(provider=provider, **matrix[provider])


async def _get_runtime(db: DB, runtime_id: int) -> RuntimeInstance:
    runtime = (await db.execute(select(RuntimeInstance).where(RuntimeInstance.id == runtime_id))).scalar_one_or_none()
    if runtime is None:
        raise HTTPException(status_code=404, detail="Runtime not found")
    return runtime


async def _get_asset(db: DB, asset_id: int) -> AIAsset:
    asset = (await db.execute(select(AIAsset).where(AIAsset.id == asset_id))).scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=404, detail="AI asset not found")
    return asset


async def _get_handover(db: DB, case_id: int, *, for_update: bool = False) -> HandoverCase:
    stmt = select(HandoverCase).where(HandoverCase.id == case_id)
    if for_update:
        stmt = stmt.with_for_update()
    case = (await db.execute(stmt)).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Handover case not found")
    return case


def _assert_transition(case: HandoverCase, allowed_from: set[HandoverStatus]) -> None:
    """HANDOVER-04 前台状态机闸门:仅当 case.status 属于 allowed_from 才放行,否则 409。

    FSM:draft → collecting → analyzing → pending_approval → approved → executing
    → verifying → completed(rejected/cancelled 为终态)。collect/analyze/submit
    缺少来源态校验会让任意态(含终态/执行中)被重新拉回前台,绕过审批闸门。
    """
    if case.status not in allowed_from:
        allowed = ", ".join(sorted(item.value for item in allowed_from))
        raise HTTPException(
            status_code=409,
            detail=f"Handover case status {case.status.value} is not in allowed source states ({allowed})",
        )


SENSITIVITY_REQUIRES_EVIDENCE = {Sensitivity.INTERNAL, Sensitivity.CONFIDENTIAL, Sensitivity.RESTRICTED}
CRITICALITY_REQUIRES_EVIDENCE = {Criticality.HIGH, Criticality.CRITICAL}
SENSITIVITY_RANK = {
    Sensitivity.PUBLIC: 0,
    Sensitivity.INTERNAL: 1,
    Sensitivity.CONFIDENTIAL: 2,
    Sensitivity.RESTRICTED: 3,
}


def _dedupe_ids(values: list[int]) -> list[int]:
    seen: set[int] = set()
    result: list[int] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _safe_object_segment(value: str | None, fallback: str) -> str:
    segment = (value or fallback).strip().replace("/", "_").replace("\\", "_") or fallback
    return quote(segment[:160], safe="-_.~")


def _receipt_payload_hash(*, result: ExecutionStatus, note: str, evidence_ids: list[int]) -> str:
    payload = json.dumps(
        {
            # Hash the evidence *set* (order/dupes irrelevant) so a legitimate replay
            # that lists the same ids in a different order is not falsely rejected.
            "evidence_ids": sorted(set(evidence_ids)),
            "note": note,
            "result": result.value,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _max_sensitivity(values: Sequence[Sensitivity]) -> Sensitivity | None:
    if not values:
        return None
    return max(values, key=lambda item: SENSITIVITY_RANK[item])


async def _execution_action_context(
    db: DB, action: ExecutionAction
) -> tuple[HandoverItem | None, Criticality | None, Sensitivity | None]:
    if action.handover_item_id is None:
        return None, None, None
    item = await db.get(HandoverItem, action.handover_item_id)
    if item is None:
        return None, None, None
    asset = await db.get(AIAsset, item.asset_id)
    trace_sensitivities = (
        (
            await db.execute(
                select(WorkTrace.sensitivity).where(
                    WorkTrace.asset_id == item.asset_id,
                )
            )
        )
        .scalars()
        .all()
    )
    return item, asset.criticality if asset else None, _max_sensitivity(trace_sensitivities)


def _receipt_requires_evidence(
    *,
    asset_criticality: Criticality | None,
    trace_sensitivity: Sensitivity | None,
) -> bool:
    return asset_criticality in CRITICALITY_REQUIRES_EVIDENCE or trace_sensitivity in SENSITIVITY_REQUIRES_EVIDENCE


async def _handover_item_requires_evidence(db: DB, item: HandoverItem) -> bool:
    asset = await db.get(AIAsset, item.asset_id)
    trace_sensitivities = (
        (
            await db.execute(
                select(WorkTrace.sensitivity).where(
                    WorkTrace.asset_id == item.asset_id,
                )
            )
        )
        .scalars()
        .all()
    )
    return _receipt_requires_evidence(
        asset_criticality=asset.criticality if asset else None,
        trace_sensitivity=_max_sensitivity(trace_sensitivities),
    )


async def _annotate_handover_items(db: DB, items: Sequence[HandoverItem]) -> None:
    for item in items:
        setattr(item, "requires_evidence", await _handover_item_requires_evidence(db, item))


async def _annotate_execution_actions(db: DB, actions: Sequence[ExecutionAction]) -> None:
    for action in actions:
        _, asset_criticality, trace_sensitivity = await _execution_action_context(db, action)
        setattr(
            action,
            "requires_evidence",
            _receipt_requires_evidence(
                asset_criticality=asset_criticality,
                trace_sensitivity=trace_sensitivity,
            ),
        )


async def _case_evidence_ids(
    db: DB,
    case_id: int,
    namespace_id: int,
    requested_ids: list[int],
) -> set[int]:
    if not requested_ids:
        return set()
    item_evidence_ids = (
        (
            await db.execute(
                select(HandoverItem.evidence_id).where(
                    HandoverItem.handover_case_id == case_id,
                    HandoverItem.evidence_id.in_(requested_ids),
                    HandoverItem.evidence_id.in_(
                        select(EvidenceItem.id).where(EvidenceItem.namespace_id == namespace_id)
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    case_evidence_ids = {evidence_id for evidence_id in item_evidence_ids if evidence_id is not None}
    action_evidence_rows = (
        (
            await db.execute(
                select(ExecutionAction.evidence_ids).where(
                    ExecutionAction.handover_case_id == case_id,
                    ExecutionAction.evidence_ids.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for evidence_ids in action_evidence_rows:
        case_evidence_ids.update(evidence_id for evidence_id in (evidence_ids or []) if evidence_id in requested_ids)
    package_prefix = f"s3://{artifact_service.bucket}/handovers/case-{case_id}/"
    case_evidence_ids.update(
        (
            await db.execute(
                select(EvidenceItem.id).where(
                    EvidenceItem.id.in_(requested_ids),
                    EvidenceItem.namespace_id == namespace_id,
                    EvidenceItem.object_uri.like(f"{package_prefix}%"),
                )
            )
        )
        .scalars()
        .all()
    )
    if not case_evidence_ids:
        return set()
    return set(
        (
            await db.execute(
                select(EvidenceItem.id).where(
                    EvidenceItem.id.in_(case_evidence_ids),
                    EvidenceItem.namespace_id == namespace_id,
                )
            )
        )
        .scalars()
        .all()
    )


async def _assert_case_evidence_ids(
    db: DB,
    *,
    case_id: int,
    namespace_id: int,
    evidence_ids: list[int],
) -> None:
    if not evidence_ids:
        return
    allowed_ids = await _case_evidence_ids(db, case_id, namespace_id, evidence_ids)
    missing = sorted(set(evidence_ids) - allowed_ids)
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Evidence items must exist and belong to this handover case: {missing}",
        )


async def _assert_handover_item_namespaces(
    db: DB,
    *,
    namespace_id: int,
    items: Sequence[HandoverItem],
) -> None:
    try:
        for item in items:
            asset = await db.get(AIAsset, item.asset_id)
            if asset is None:
                raise HTTPException(status_code=422, detail="Handover item asset was not found")
            ensure_resource_namespace(asset, namespace_id, relationship="asset")
            if item.evidence_id is not None:
                evidence = await db.get(EvidenceItem, item.evidence_id)
                if evidence is None:
                    raise HTTPException(status_code=422, detail="Handover item evidence was not found")
                ensure_resource_namespace(evidence, namespace_id, relationship="evidence")
    except (MissingNamespaceError, TenantMismatchError) as exc:
        raise _tenant_write_http_exception(exc) from exc


async def _assert_handover_runtime_namespaces(
    db: DB,
    *,
    case: HandoverCase,
    namespace_id: int,
) -> None:
    raw_runtime_ids = (case.summary_json or {}).get("runtime_ids", [])
    if not isinstance(raw_runtime_ids, list) or any(
        not isinstance(runtime_id, int) or isinstance(runtime_id, bool)
        for runtime_id in raw_runtime_ids
    ):
        raise HTTPException(status_code=422, detail="Handover runtime_ids are invalid")
    runtime_ids = set(raw_runtime_ids)
    if not runtime_ids:
        return
    runtimes = (
        await db.execute(select(RuntimeInstance).where(RuntimeInstance.id.in_(runtime_ids)))
    ).scalars().all()
    if {runtime.id for runtime in runtimes} != runtime_ids:
        raise HTTPException(status_code=422, detail="One or more handover runtimes were not found")
    try:
        for runtime in runtimes:
            ensure_resource_namespace(runtime, namespace_id, relationship="runtime")
    except (MissingNamespaceError, TenantMismatchError) as exc:
        raise _tenant_write_http_exception(exc) from exc


async def _existing_execution_actions_for_idempotency(
    db: DB,
    *,
    case_id: int,
    idempotency_key: str | None,
) -> list[ExecutionAction] | None:
    if not idempotency_key:
        return None
    existing_action_id = (
        await db.execute(
            select(ExecutionAction.id)
            .where(
                ExecutionAction.handover_case_id == case_id,
                ExecutionAction.idempotency_key == idempotency_key,
            )
            .with_for_update()
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing_action_id is None:
        return None
    rows = (
        await db.execute(
            select(ExecutionAction)
            .where(ExecutionAction.handover_case_id == case_id)
            .with_for_update()
            .order_by(ExecutionAction.id)
        )
    ).scalars().all()
    return list(rows)


async def _get_reporter_context(
    db: DB,
    credentials: HTTPAuthorizationCredentials = Depends(reporter_bearer),
) -> ReporterAuthContext:
    return await authenticate_runtime_report_token(db, credentials.credentials)


async def _require_handover_namespace_write(db: DB, current_user, case: HandoverCase) -> int:
    namespace_id = await _active_handover_namespace_id(db, case)
    await require_namespace_writer(current_user, namespace_id, db)
    return namespace_id


async def _active_handover_namespace_id(db: DB, case: HandoverCase) -> int:
    try:
        namespace = await require_active_namespace(db, case.namespace_id)
    except (MissingNamespaceError, InactiveNamespaceError) as exc:
        raise _tenant_write_http_exception(exc) from exc
    return namespace.id


async def _require_runtime_namespace_write(db: DB, current_user, runtime: RuntimeInstance) -> int:
    try:
        namespace = await require_active_namespace(db, runtime.namespace_id)
    except (MissingNamespaceError, InactiveNamespaceError) as exc:
        raise _tenant_write_http_exception(exc) from exc
    await require_namespace_writer(current_user, namespace.id, db)
    return namespace.id


def _report_session_out(
    session: ReportUploadSession,
    signed: dict | None = None,
) -> ReportUploadSessionOut:
    payload = ReportUploadSessionOut.model_validate(session).model_dump()
    if signed:
        payload.update(
            {
                "upload_url": signed.get("upload_url"),
                "expires_in": signed.get("expires_in"),
            }
        )
    payload["max_size_mb"] = settings.REPORT_UPLOAD_MAX_SIZE_MB
    return ReportUploadSessionOut(**payload)


def _runtime_reporter_metadata(
    *,
    current_user,
    body: ReporterEnrollmentCreate,
    enrolled_at: datetime,
) -> dict[str, Any]:
    metadata = dict(body.metadata_json or {})
    metadata["reporter"] = {
        "enrollment": {
            "mode": "self_service",
            "user_id": current_user.id,
            "username": current_user.username,
            "device_id": body.device_id,
            "agent_kind": body.agent_kind,
            "reporter_version": body.reporter_version,
            "schedule_json": body.schedule_json,
            "enrolled_at": enrolled_at.isoformat(),
        },
        "last_seen_at": None,
        "heartbeat": None,
    }
    return metadata


def _reporter_credential_metadata(body: ReporterEnrollmentCreate) -> dict[str, Any]:
    return {
        "agent_kind": body.agent_kind,
        "reporter_version": body.reporter_version,
        "schedule_json": body.schedule_json,
        "enrollment": "self_service",
    }


def _reporter_scopes(existing: list | None = None) -> list[str]:
    scopes = [item for item in (existing or []) if isinstance(item, str)]
    for required in ("report.upload", "report.structured", "report.heartbeat"):
        if required not in scopes:
            scopes.append(required)
    return scopes


def _reporter_credential_created(row: ReporterCredential, token: str) -> ReporterCredentialCreatedOut:
    payload = ReporterCredentialOut.model_validate(row).model_dump()
    return ReporterCredentialCreatedOut(**payload, token=token)


async def _get_reporter_credential_for_user(db: DB, credential_id: int, current_user) -> ReporterCredential:
    credential = (
        await db.execute(select(ReporterCredential).where(ReporterCredential.id == credential_id))
    ).scalar_one_or_none()
    if credential is None:
        raise HTTPException(status_code=404, detail="Reporter credential not found")
    if current_user.system_role != SystemRole.ADMIN and credential.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only manage your own reporter credentials")
    return credential


def _is_demo_metadata(value: dict | None) -> bool:
    return isinstance(value, dict) and value.get("demo") is True


def _asset_user_feedback(asset: AIAsset, user_id: int) -> dict | None:
    feedback = (asset.metadata_json or {}).get("duckdock_user_feedback")
    if not isinstance(feedback, dict):
        return None
    value = feedback.get(str(user_id))
    return value if isinstance(value, dict) else None


def _project_names_from_metadata(metadata: dict | None) -> set[str]:
    if not isinstance(metadata, dict):
        return set()
    names: set[str] = set()
    keys = (
        "project",
        "project_name",
        "project_id",
        "workspace",
        "workspace_name",
        "repository",
        "repo",
        "namespace",
        "team",
        "context",
    )
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            names.add(value.strip())
        elif isinstance(value, dict):
            nested = value.get("name") or value.get("title") or value.get("id")
            if isinstance(nested, str) and nested.strip():
                names.add(nested.strip())
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    names.add(item.strip())
                elif isinstance(item, dict):
                    nested = item.get("name") or item.get("title") or item.get("id")
                    if isinstance(nested, str) and nested.strip():
                        names.add(nested.strip())
    return names


def _build_project_contexts(assets: list[AIAsset], traces: list[WorkTrace]) -> list[MyWorkspaceProjectContextOut]:
    contexts: dict[str, dict] = {}

    def ensure(name: str) -> dict:
        key = name.lower()
        if key not in contexts:
            contexts[key] = {
                "key": key,
                "name": name,
                "asset_count": 0,
                "trace_count": 0,
                "last_seen_at": None,
                "sources": set(),
            }
        return contexts[key]

    for asset in assets:
        names = _project_names_from_metadata(asset.metadata_json)
        if not names:
            runtime_name = f"{asset.source_provider.value} runtime"
            names = {runtime_name}
        for name in names:
            context = ensure(name)
            context["asset_count"] += 1
            context["sources"].add("asset")
            if context["last_seen_at"] is None or asset.last_seen_at > context["last_seen_at"]:
                context["last_seen_at"] = asset.last_seen_at

    for trace in traces:
        names = _project_names_from_metadata(trace.metadata_json)
        if not names and trace.asset_id is not None:
            linked_asset = next((asset for asset in assets if asset.id == trace.asset_id), None)
            names = _project_names_from_metadata(linked_asset.metadata_json if linked_asset else None)
        if not names:
            names = {"unclassified"}
        for name in names:
            context = ensure(name)
            context["trace_count"] += 1
            context["sources"].add("work_trace")
            trace_time = trace.started_at or trace.created_at
            if trace_time and (context["last_seen_at"] is None or trace_time > context["last_seen_at"]):
                context["last_seen_at"] = trace_time

    rows = [
        MyWorkspaceProjectContextOut(
            key=value["key"],
            name=value["name"],
            asset_count=value["asset_count"],
            trace_count=value["trace_count"],
            last_seen_at=value["last_seen_at"],
            sources=sorted(value["sources"]),
        )
        for value in contexts.values()
    ]
    return sorted(rows, key=lambda item: item.last_seen_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:10]


def _build_reporter_statuses(
    runtimes: list[RuntimeInstance],
    latest_reports: dict[int, ReportUploadSession],
) -> list[MyWorkspaceReporterStatusOut]:
    now = datetime.now(timezone.utc)
    result: list[MyWorkspaceReporterStatusOut] = []
    for runtime in runtimes:
        report = latest_reports.get(runtime.id)
        if report is None:
            state = "waiting"
            message = "No report has been received for this runtime yet."
        else:
            report_updated_at = report.updated_at
            if report_updated_at.tzinfo is None:
                report_updated_at = report_updated_at.replace(tzinfo=timezone.utc)
        if report is not None and report.status == ReportUploadStatus.SUCCEEDED and report_updated_at >= now - timedelta(days=8):
            state = "healthy"
            message = "Recent report ingested successfully."
        elif report is not None and report.status in {ReportUploadStatus.FAILED, ReportUploadStatus.EXPIRED}:
            state = "failed"
            message = report.error_message or "The latest report failed or expired."
        elif report is not None:
            state = "waiting"
            message = f"Latest report is {report.status.value}."
        result.append(
            MyWorkspaceReporterStatusOut(
                runtime=runtime,
                latest_report=report,
                state=state,
                message=message,
            )
        )
    return result


def _build_readiness(
    *,
    user_id: int,
    assets: list[AIAsset],
    traces: list[WorkTrace],
    reporter_statuses: list[MyWorkspaceReporterStatusOut],
) -> MyWorkspaceReadinessOut:
    if not assets and not traces and not reporter_statuses:
        return MyWorkspaceReadinessOut(
            score=0,
            reviewed_assets=0,
            total_assets=0,
            missing_items=["DuckDock has not collected assets or work traces linked to you yet."],
            next_actions=["Ask your OpenClaw or WorkBuddy runtime to install duckdock_reporter and run a dry-run report."],
        )

    reviewed_assets = sum(1 for asset in assets if _asset_user_feedback(asset, user_id) is not None)
    high_assets = [asset for asset in assets if asset.criticality in {Criticality.HIGH, Criticality.CRITICAL}]
    reviewed_high_assets = sum(1 for asset in high_assets if _asset_user_feedback(asset, user_id) is not None)

    asset_score = reviewed_assets / len(assets) if assets else 0.0
    criticality_score = reviewed_high_assets / len(high_assets) if high_assets else (1.0 if assets else 0.0)
    reporter_score = 1.0 if any(item.state == "healthy" for item in reporter_statuses) else (0.5 if reporter_statuses else 0.0)
    trace_score = min(len(traces) / 5, 1.0)
    score = round((asset_score * 0.4 + criticality_score * 0.25 + reporter_score * 0.2 + trace_score * 0.15) * 100)

    missing_items: list[str] = []
    next_actions: list[str] = []
    if assets and reviewed_assets < len(assets):
        missing_items.append("Some assets still need your confirm, supplement, or exclude decision.")
        next_actions.append("Review the pending assets in My AI Assets.")
    if high_assets and reviewed_high_assets < len(high_assets):
        missing_items.append("High criticality assets need explicit owner feedback.")
        next_actions.append("Prioritize high or critical assets before offboarding.")
    if not any(item.state == "healthy" for item in reporter_statuses):
        missing_items.append("No healthy reporter upload was found in the last 8 days.")
        next_actions.append("Run duckdock_reporter dry-run and upload a pack from your runtime.")
    if len(traces) < 3:
        missing_items.append("Recent work trace coverage is still thin.")
        next_actions.append("Keep periodic reporting enabled so project context is not lost.")

    return MyWorkspaceReadinessOut(
        score=max(0, min(100, score)),
        reviewed_assets=reviewed_assets,
        total_assets=len(assets),
        missing_items=missing_items,
        next_actions=next_actions,
    )


def _is_demo_runtime(runtime: RuntimeInstance) -> bool:
    if _is_demo_metadata(runtime.metadata_json):
        return True
    return (
        runtime.provider == RuntimeProvider.OPENCLAW
        and runtime.base_url == "https://openclaw.example.internal"
        and (runtime.credential_ref or "").startswith("vault://duckdock/demo")
        and ("Demo Runtime" in (runtime.name or "") or (runtime.name or "").startswith("演示"))
    )


def _is_demo_asset(asset: AIAsset, demo_runtime_ids: set[int]) -> bool:
    if _is_demo_metadata(asset.metadata_json):
        return True
    external_id = asset.external_id or ""
    if external_id.startswith("demo-skill-") or external_id.startswith("skill-demo-"):
        return True
    if asset.content_hash == "demo-sha256":
        return True
    return (
        asset.source_runtime_id in demo_runtime_ids
        and asset.name == "离职交接摘要 Skill"
        and asset.source_provider == RuntimeProvider.OPENCLAW
    )


def _runtime_ids_from_summary(value: dict | None) -> set[int]:
    if not isinstance(value, dict):
        return set()
    ids = value.get("runtime_ids")
    if not isinstance(ids, list):
        return set()
    result: set[int] = set()
    for item in ids:
        try:
            result.add(int(item))
        except (TypeError, ValueError):
            continue
    return result


def _is_demo_handover(case: HandoverCase, demo_runtime_ids: set[int]) -> bool:
    if _is_demo_metadata(case.summary_json):
        return True
    title = case.title or ""
    has_demo_title = title.startswith("演示") or title.startswith("Demo") or title.startswith("婕旂ず")
    return has_demo_title and bool(_runtime_ids_from_summary(case.summary_json) & demo_runtime_ids)


async def _delete_where(db: DB, model, *conditions) -> int:
    if not conditions:
        return 0
    result = await db.execute(delete(model).where(*conditions))
    return int(result.rowcount or 0)


@router.get("/adapters/capabilities", response_model=dict[RuntimeProvider, AdapterCapabilityOut])
async def adapter_capabilities(current_user: RuntimeReaderUser):
    return {provider: _capability(provider) for provider in RuntimeProvider}


@router.get("/runtimes", response_model=list[RuntimeInstanceOut])
async def list_runtimes(db: DB, current_user: RuntimeReaderUser, provider: RuntimeProvider | None = None):
    stmt = select(RuntimeInstance).order_by(RuntimeInstance.created_at.desc())
    if provider is not None:
        stmt = stmt.where(RuntimeInstance.provider == provider)
    return (await db.execute(stmt)).scalars().all()


@router.post("/runtimes", response_model=RuntimeInstanceOut, status_code=status.HTTP_201_CREATED)
async def create_runtime(body: RuntimeInstanceCreate, db: DB, current_user: RuntimeManagerUser):
    try:
        namespace = await require_active_namespace(db, body.namespace_id)
    except (MissingNamespaceError, InactiveNamespaceError) as exc:
        raise _tenant_write_http_exception(exc) from exc
    await require_namespace_writer(current_user, namespace.id, db)
    credential_ref = body.credential_ref
    if body.credential:
        record = await store_credential(
            db,
            plaintext=body.credential,
            name=f"runtime:{body.name}",
            created_by=current_user.id,
        )
        credential_ref = make_db_ref(record)
    runtime = RuntimeInstance(
        namespace_id=namespace.id,
        provider=body.provider,
        name=body.name,
        base_url=body.base_url,
        deploy_type=body.deploy_type,
        credential_ref=credential_ref,
        metadata_json=body.metadata_json,
        capabilities=_capability(body.provider).model_dump(mode="json"),
    )
    db.add(runtime)
    await db.flush()
    await audit(
        db,
        user=current_user,
        action="runtime.created",
        resource_type="runtime",
        resource_id=runtime.id,
        details={"provider": runtime.provider.value, "name": runtime.name},
    )
    return runtime


@router.get("/runtimes/{runtime_id}", response_model=RuntimeInstanceOut)
async def get_runtime(runtime_id: int, db: DB, current_user: RuntimeReaderUser):
    return await _get_runtime(db, runtime_id)


@router.patch("/runtimes/{runtime_id}", response_model=RuntimeInstanceOut)
async def update_runtime(runtime_id: int, body: RuntimeInstanceUpdate, db: DB, current_user: RuntimeManagerUser):
    runtime = await _get_runtime(db, runtime_id)
    try:
        namespace = await require_active_namespace(db, runtime.namespace_id)
    except (MissingNamespaceError, InactiveNamespaceError) as exc:
        raise _tenant_write_http_exception(exc) from exc
    await require_namespace_writer(current_user, namespace.id, db)
    data = body.model_dump(exclude_unset=True)
    plaintext_credential = data.pop("credential", None)
    for key, value in data.items():
        setattr(runtime, key, value)
    if plaintext_credential:
        if (runtime.credential_ref or "").startswith(DB_REF_PREFIX):
            await store_credential(
                db,
                plaintext=plaintext_credential,
                record_id=int(runtime.credential_ref[len(DB_REF_PREFIX):]),
            )
        else:
            record = await store_credential(
                db,
                plaintext=plaintext_credential,
                name=f"runtime:{runtime.name}",
                created_by=current_user.id,
            )
            runtime.credential_ref = make_db_ref(record)
    await audit(
        db,
        user=current_user,
        action="runtime.updated",
        resource_type="runtime",
        resource_id=runtime.id,
        # 审计绝不携带明文凭证 — 只记是否轮换(FR-016/原则 V)
        details={
            **body.model_dump(exclude_unset=True, exclude={"credential"}, mode="json"),
            "credential_rotated": bool(plaintext_credential),
        },
    )
    return runtime


@router.post("/runtimes/{runtime_id}/test", response_model=RuntimeConnectionTestOut)
async def test_runtime(runtime_id: int, db: DB, current_user: RuntimeManagerUser):
    """Push-only(specs/001 T080):上报链路自检——不再外联厂商,检查 Reporter 凭证与最近上报。"""
    runtime = await _get_runtime(db, runtime_id)
    active_tokens = (
        await db.execute(
            select(RuntimeReportToken).where(
                RuntimeReportToken.runtime_id == runtime.id,
                RuntimeReportToken.is_active.is_(True),
            )
        )
    ).scalars().all()
    active_credentials = (
        await db.execute(
            select(ReporterCredential).where(
                ReporterCredential.runtime_id == runtime.id,
                ReporterCredential.is_active.is_(True),
                ReporterCredential.revoked_at.is_(None),
            )
        )
    ).scalars().all()
    latest = (
        await db.execute(
            select(ReportUploadSession)
            .where(ReportUploadSession.runtime_id == runtime.id)
            .order_by(ReportUploadSession.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    active_reporter_count = len(active_credentials) + len(active_tokens)
    if not active_reporter_count:
        check_status, message = "degraded", "无有效上报凭证,请通过 Reporter 自助接入或创建兜底 report token。"
    elif latest is None:
        check_status, message = "waiting", f"{active_reporter_count} 个上报凭证就绪,尚未收到任何上报。"
    else:
        check_status, message = "ok", f"最近上报状态 {latest.status.value}(report {latest.report_id})。"
    return RuntimeConnectionTestOut(
        status=check_status,
        provider=runtime.provider,
        capabilities=_capability(runtime.provider),
        message=message,
    )


@router.post("/reporters/enroll", response_model=ReporterEnrollmentOut, status_code=status.HTTP_201_CREATED)
async def enroll_reporter_endpoint(body: ReporterEnrollmentCreate, db: DB, current_user: CurrentUser):
    """Self-service Reporter enrollment.

    This creates a runtime endpoint plus a long-lived, revocable dkr_report_* credential
    for the authenticated employee/agent. Object uploads still use short-lived
    presigned URLs obtained later through /reports/upload-sessions.
    """

    try:
        namespace = await require_active_namespace(db, body.namespace_id)
    except (MissingNamespaceError, InactiveNamespaceError) as exc:
        raise _tenant_write_http_exception(exc) from exc
    await require_namespace_writer(current_user, namespace.id, db)
    now = datetime.now(timezone.utc)
    agent_label = body.agent_kind or body.provider.value
    runtime_name = body.runtime_name or f"{current_user.username} {agent_label} reporter ({body.device_id})"
    runtime = RuntimeInstance(
        namespace_id=namespace.id,
        provider=body.provider,
        name=runtime_name,
        deploy_type=RuntimeDeployType.PRIVATE,
        status=RuntimeStatus.ACTIVE,
        metadata_json=_runtime_reporter_metadata(current_user=current_user, body=body, enrolled_at=now),
    )
    db.add(runtime)
    await db.flush()

    prefix, secret, token = generate_runtime_report_token()
    credential = ReporterCredential(
        runtime_id=runtime.id,
        user_id=current_user.id,
        device_id=body.device_id,
        name=f"DuckDock Reporter ({body.device_id})",
        token_prefix=prefix,
        token_hash=hash_password(secret),
        scopes=_reporter_scopes(),
        expires_at=body.expires_at,
        metadata_json=_reporter_credential_metadata(body),
    )
    db.add(credential)
    await db.flush()
    await audit(
        db,
        user=current_user,
        action="reporter.enrolled",
        resource_type="runtime",
        resource_id=runtime.id,
        details={
            "runtime_id": runtime.id,
            "provider": runtime.provider.value,
            "device_id": body.device_id,
            "credential_id": credential.id,
            "token_prefix": credential.token_prefix,
        },
    )
    runtime_payload = RuntimeInstanceOut.model_validate(runtime)
    return ReporterEnrollmentOut(
        runtime=runtime_payload,
        credential=_reporter_credential_created(credential, token),
    )


@router.post("/reporters/heartbeat", response_model=ReporterHeartbeatOut)
async def reporter_heartbeat_endpoint(
    body: ReporterHeartbeat,
    db: DB,
    reporter: ReporterAuthContext = Depends(_get_reporter_context),
):
    """Lightweight liveness update for long-running agent-side Reporters."""

    try:
        namespace = await require_active_namespace(db, reporter.runtime.namespace_id)
        ensure_resource_namespace(
            reporter.runtime,
            namespace.id,
            relationship="runtime",
        )
    except (MissingNamespaceError, InactiveNamespaceError, TenantMismatchError) as exc:
        raise _tenant_write_http_exception(exc) from exc

    now = datetime.now(timezone.utc)
    metadata = dict(reporter.runtime.metadata_json or {})
    reporter_metadata = dict(metadata.get("reporter") or {})
    heartbeat = {
        "status": body.status,
        "device_id": body.device_id,
        "reporter_version": body.reporter_version,
        "agent_version": body.agent_version,
        "next_run_at": body.next_run_at.isoformat() if body.next_run_at else None,
        "schedule_json": body.schedule_json,
        "capabilities_json": body.capabilities_json,
        "metadata_json": body.metadata_json,
        "last_seen_at": now.isoformat(),
    }
    reporter_metadata["heartbeat"] = heartbeat
    reporter_metadata["last_seen_at"] = now.isoformat()
    metadata["reporter"] = reporter_metadata
    reporter.runtime.metadata_json = metadata
    reporter.runtime.status = RuntimeStatus.ACTIVE if body.status == "ok" else RuntimeStatus.DEGRADED
    reporter.token.last_used_at = now
    if isinstance(reporter.token, ReporterCredential):
        reporter.token.last_heartbeat_at = now
        reporter.token.heartbeat_json = heartbeat

    latest_report = (
        await db.execute(
            select(ReportUploadSession)
            .where(ReportUploadSession.runtime_id == reporter.runtime.id)
            .order_by(ReportUploadSession.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    upload_recommended = latest_report is None or latest_report.status in {
        ReportUploadStatus.FAILED,
        ReportUploadStatus.EXPIRED,
    }
    return ReporterHeartbeatOut(
        runtime_id=reporter.runtime.id,
        token_id=reporter.token.id,
        credential_source=reporter.source,
        status=body.status,
        last_seen_at=now,
        server_time=now,
        upload_recommended=upload_recommended,
    )


@router.get("/reporter-credentials", response_model=list[ReporterCredentialOut])
async def list_reporter_credentials(
    db: DB,
    current_user: CurrentUser,
    runtime_id: int | None = None,
):
    stmt = select(ReporterCredential).order_by(ReporterCredential.created_at.desc())
    if current_user.system_role != SystemRole.ADMIN:
        stmt = stmt.where(ReporterCredential.user_id == current_user.id)
    if runtime_id is not None:
        stmt = stmt.where(ReporterCredential.runtime_id == runtime_id)
    return (await db.execute(stmt)).scalars().all()


@router.post(
    "/reporter-credentials/{credential_id}/rotate",
    response_model=ReporterCredentialCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def rotate_reporter_credential(
    credential_id: int,
    body: ReporterCredentialRotate,
    db: DB,
    current_user: CurrentUser,
):
    old = await _get_reporter_credential_for_user(db, credential_id, current_user)
    now = datetime.now(timezone.utc)
    revoke_reason = body.reason or "rotated"
    old.is_active = False
    old.revoked_at = now
    old.revoked_by = current_user.id
    old.revoked_reason = revoke_reason

    stale_rows = (
        await db.execute(
            select(ReporterCredential).where(
                ReporterCredential.runtime_id == old.runtime_id,
                ReporterCredential.user_id == old.user_id,
                ReporterCredential.device_id == old.device_id,
                ReporterCredential.is_active.is_(True),
                ReporterCredential.revoked_at.is_(None),
            )
        )
    ).scalars().all()
    for row in stale_rows:
        if row.id == old.id:
            continue
        row.is_active = False
        row.revoked_at = now
        row.revoked_by = current_user.id
        row.revoked_reason = "superseded by credential rotation"

    prefix, secret, token = generate_runtime_report_token()
    new_credential = ReporterCredential(
        runtime_id=old.runtime_id,
        user_id=old.user_id,
        device_id=old.device_id,
        name=old.name,
        token_prefix=prefix,
        token_hash=hash_password(secret),
        scopes=_reporter_scopes(old.scopes),
        expires_at=body.expires_at if body.expires_at is not None else old.expires_at,
        rotated_from_id=old.id,
        metadata_json={**(old.metadata_json or {}), "rotated_at": now.isoformat(), "rotated_by": current_user.id},
    )
    db.add(new_credential)
    await db.flush()
    await audit(
        db,
        user=current_user,
        action="reporter_credential.rotated",
        resource_type="reporter_credential",
        resource_id=new_credential.id,
        details={
            "runtime_id": new_credential.runtime_id,
            "old_credential_id": old.id,
            "device_id": new_credential.device_id,
            "token_prefix": new_credential.token_prefix,
        },
    )
    return _reporter_credential_created(new_credential, token)


@router.post("/reporter-credentials/{credential_id}/revoke", response_model=ReporterCredentialOut)
async def revoke_reporter_credential(
    credential_id: int,
    body: ReporterCredentialRevoke,
    db: DB,
    current_user: CurrentUser,
):
    credential = await _get_reporter_credential_for_user(db, credential_id, current_user)
    now = datetime.now(timezone.utc)
    credential.is_active = False
    credential.revoked_at = credential.revoked_at or now
    credential.revoked_by = current_user.id
    credential.revoked_reason = body.reason
    await audit(
        db,
        user=current_user,
        action="reporter_credential.revoked",
        resource_type="reporter_credential",
        resource_id=credential.id,
        details={
            "runtime_id": credential.runtime_id,
            "device_id": credential.device_id,
            "reason": body.reason,
            "token_prefix": credential.token_prefix,
        },
    )
    return credential


@router.get("/runtimes/{runtime_id}/report-tokens", response_model=list[RuntimeReportTokenOut])
async def list_runtime_report_tokens(runtime_id: int, db: DB, current_user: RuntimeManagerUser):
    await _get_runtime(db, runtime_id)
    return (
        await db.execute(
            select(RuntimeReportToken)
            .where(RuntimeReportToken.runtime_id == runtime_id)
            .order_by(RuntimeReportToken.created_at.desc())
        )
    ).scalars().all()


@router.post(
    "/runtimes/{runtime_id}/report-tokens",
    response_model=RuntimeReportTokenCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_runtime_report_token(
    runtime_id: int,
    body: RuntimeReportTokenCreate,
    db: DB,
    current_user: RuntimeManagerUser,
):
    runtime = await _get_runtime(db, runtime_id)
    await _require_runtime_namespace_write(db, current_user, runtime)
    prefix, secret, token = generate_runtime_report_token()
    row = RuntimeReportToken(
        runtime_id=runtime.id,
        name=body.name,
        token_prefix=prefix,
        token_hash=hash_password(secret),
        expires_at=body.expires_at,
        created_by=current_user.id,
    )
    db.add(row)
    await db.flush()
    await audit(
        db,
        user=current_user,
        action="runtime.report_token.created",
        resource_type="runtime_report_token",
        resource_id=row.id,
        details={"runtime_id": runtime.id, "token_prefix": prefix},
    )
    payload = RuntimeReportTokenOut.model_validate(row).model_dump()
    return RuntimeReportTokenCreatedOut(**payload, token=token)


@router.delete("/runtime-report-tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_runtime_report_token(token_id: int, db: DB, current_user: RuntimeManagerUser):
    row = (
        await db.execute(select(RuntimeReportToken).where(RuntimeReportToken.id == token_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Runtime report token not found")
    runtime = await _get_runtime(db, row.runtime_id)
    await _require_runtime_namespace_write(db, current_user, runtime)
    row.is_active = False
    await audit(
        db,
        user=current_user,
        action="runtime.report_token.revoked",
        resource_type="runtime_report_token",
        resource_id=row.id,
        details={"runtime_id": row.runtime_id, "token_prefix": row.token_prefix},
    )
    return None


@router.get("/collection-jobs", response_model=list[CollectionJobOut])
async def list_collection_jobs(db: DB, current_user: RuntimeReaderUser, runtime_id: int | None = None):
    stmt = select(CollectionJob).order_by(CollectionJob.created_at.desc())
    if runtime_id is not None:
        stmt = stmt.where(CollectionJob.runtime_id == runtime_id)
    return (await db.execute(stmt)).scalars().all()


@router.get("/reports/upload-sessions", response_model=list[ReportUploadSessionOut])
async def list_report_upload_sessions(
    db: DB,
    current_user: RuntimeReaderUser,
    runtime_id: int | None = None,
    status_filter: ReportUploadStatus | None = None,
):
    stmt = select(ReportUploadSession).order_by(ReportUploadSession.created_at.desc()).limit(200)
    if runtime_id is not None:
        stmt = stmt.where(ReportUploadSession.runtime_id == runtime_id)
    if status_filter is not None:
        stmt = stmt.where(ReportUploadSession.status == status_filter)
    rows = (await db.execute(stmt)).scalars().all()
    return [_report_session_out(row) for row in rows]


@router.post(
    "/reports/upload-sessions",
    response_model=ReportUploadSessionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_report_upload_session_endpoint(
    body: ReportUploadSessionCreate,
    db: DB,
    reporter: ReporterAuthContext = Depends(_get_reporter_context),
):
    if body.runtime_id != reporter.runtime.id:
        raise HTTPException(status_code=403, detail="Runtime report token cannot upload for this runtime")
    session, signed = await create_report_upload_session(
        db,
        runtime=reporter.runtime,
        schema_version=body.schema_version,
        report_type=body.report_type,
        period_start=body.period_start,
        period_end=body.period_end,
        filename=body.filename,
        content_type=body.content_type,
        expected_size_bytes=body.expected_size_bytes,
        expected_sha256=body.expected_sha256,
        idempotency_key=body.idempotency_key,
        metadata_json=body.metadata_json,
        created_by=None,
        created_via=f"{reporter.source}:{reporter.token.id}",
    )
    await audit(
        db,
        username=f"runtime:{reporter.runtime.id}",
        action="report_upload_session.created",
        resource_type="report_upload_session",
        resource_id=session.id,
        details={
            "runtime_id": reporter.runtime.id,
            "report_id": session.report_id,
            "report_type": session.report_type,
            "credential_source": reporter.source,
            "token_prefix": reporter.token.token_prefix,
        },
    )
    return _report_session_out(session, signed)


@router.post("/reports/structured", response_model=StructuredReportOut, status_code=status.HTTP_201_CREATED)
async def submit_structured_report_endpoint(
    body: StructuredReportSubmit,
    db: DB,
    reporter: ReporterAuthContext = Depends(_get_reporter_context),
):
    """Lightweight daily/weekly Reporter path.

    This is intentionally separate from /reports/upload-sessions:
    structured reports are schema-first timeline updates, while upload sessions
    are evidence/hand-over packs that go through MinIO and analysis-worker.
    """

    out = await ingest_structured_report(db, reporter=reporter, body=body)
    await audit(
        db,
        username=f"runtime:{reporter.runtime.id}",
        action="structured_report.submitted",
        resource_type="work_trace",
        resource_id=out.work_trace.id,
        details={
            "runtime_id": reporter.runtime.id,
            "report_id": out.report_id,
            "report_type": body.report_type,
            "status": out.status,
            "credential_source": reporter.source,
            "token_prefix": reporter.token.token_prefix,
        },
    )
    return out


@router.post("/reports/{report_id}/finalize", response_model=ReportUploadSessionOut)
async def finalize_report_upload_session_endpoint(
    report_id: str,
    body: ReportUploadSessionFinalize,
    db: DB,
    reporter: ReporterAuthContext = Depends(_get_reporter_context),
):
    session = await finalize_report_upload_session(
        db,
        report_id=report_id,
        runtime=reporter.runtime,
        sha256=body.sha256,
        size_bytes=body.size_bytes,
        manifest=body.manifest,
    )
    await audit(
        db,
        username=f"runtime:{reporter.runtime.id}",
        action="report_upload_session.finalized",
        resource_type="report_upload_session",
        resource_id=session.id,
        details={
            "runtime_id": reporter.runtime.id,
            "report_id": session.report_id,
            "status": session.status.value,
        },
    )
    return _report_session_out(session)


@router.post("/reports/{report_id}/ingest", response_model=ReportUploadSessionOut)
async def ingest_report_upload_session_endpoint(report_id: str, db: DB, current_user: RuntimeManagerUser):
    candidate = await get_report_upload_session(db, report_id=report_id)
    runtime = await _get_runtime(db, candidate.runtime_id)
    await _require_runtime_namespace_write(db, current_user, runtime)
    session = await ingest_report_upload_session(db, report_id=report_id)
    await audit(
        db,
        user=current_user,
        action="report_upload_session.ingested",
        resource_type="report_upload_session",
        resource_id=session.id,
        details={"report_id": session.report_id, "status": session.status.value},
    )
    return _report_session_out(session)


@router.delete("/demo-data", response_model=DemoDataCleanupOut)
async def cleanup_demo_data(db: DB, current_user: AdminUser):
    runtimes = (await db.execute(select(RuntimeInstance))).scalars().all()
    demo_runtime_ids = {row.id for row in runtimes if _is_demo_runtime(row)}

    assets = (await db.execute(select(AIAsset))).scalars().all()
    demo_asset_ids = {
        row.id
        for row in assets
        if _is_demo_asset(row, demo_runtime_ids)
    }
    protected_runtime_asset_ids = {
        row.id
        for row in assets
        if row.source_runtime_id in demo_runtime_ids and row.id not in demo_asset_ids
    }
    deletable_runtime_ids = {
        runtime_id
        for runtime_id in demo_runtime_ids
        if not any(row.source_runtime_id == runtime_id for row in assets if row.id in protected_runtime_asset_ids)
    }

    handovers = (await db.execute(select(HandoverCase))).scalars().all()
    demo_handover_ids = {
        row.id
        for row in handovers
        if _is_demo_handover(row, demo_runtime_ids)
    }

    jobs = (await db.execute(select(CollectionJob))).scalars().all()
    demo_job_ids = {
        row.id
        for row in jobs
        if row.runtime_id in demo_runtime_ids or _is_demo_metadata(row.scope_json)
    }

    demo_raw_asset_external_ids: set[str] = set()
    demo_raw_trace_external_ids: set[str] = set()
    if demo_job_ids:
        raw_records_for_demo_jobs = (
            await db.execute(select(RawCollectionRecord).where(RawCollectionRecord.collection_job_id.in_(demo_job_ids)))
        ).scalars().all()
        for row in raw_records_for_demo_jobs:
            payload = row.payload_json or {}
            external_id = row.external_id or payload.get("external_id") or payload.get("id")
            if not external_id:
                continue
            if row.normalized_type == "ai_asset":
                demo_raw_asset_external_ids.add(str(external_id))
            if row.normalized_type == "work_trace":
                demo_raw_trace_external_ids.add(str(external_id))

    if demo_raw_asset_external_ids:
        demo_asset_ids.update(
            row.id
            for row in assets
            if row.source_runtime_id in demo_runtime_ids and (row.external_id or "") in demo_raw_asset_external_ids
        )
        protected_runtime_asset_ids = {
            row.id
            for row in assets
            if row.source_runtime_id in demo_runtime_ids and row.id not in demo_asset_ids
        }
        deletable_runtime_ids = {
            runtime_id
            for runtime_id in demo_runtime_ids
            if not any(row.source_runtime_id == runtime_id for row in assets if row.id in protected_runtime_asset_ids)
        }

    report_sessions = (await db.execute(select(ReportUploadSession))).scalars().all()
    demo_report_session_ids = {
        row.id
        for row in report_sessions
        if row.runtime_id in demo_runtime_ids
        or row.collection_job_id in demo_job_ids
        or _is_demo_metadata(row.metadata_json)
    }

    traces = (await db.execute(select(WorkTrace))).scalars().all()
    demo_trace_ids = {
        row.id
        for row in traces
        if row.asset_id in demo_asset_ids
        or ((row.external_session_id or "") in demo_raw_trace_external_ids and row.runtime_id in demo_runtime_ids)
        or (
            row.runtime_id in demo_runtime_ids
            and (
                _is_demo_metadata(row.metadata_json)
                or (row.external_session_id or "").startswith("demo-")
                or (row.external_session_id or "").startswith("session-demo-")
            )
        )
    }
    protected_runtime_trace_ids = {
        row.id
        for row in traces
        if row.runtime_id in demo_runtime_ids and row.id not in demo_trace_ids
    }
    deletable_runtime_ids = {
        runtime_id
        for runtime_id in deletable_runtime_ids
        if not any(row.runtime_id == runtime_id for row in traces if row.id in protected_runtime_trace_ids)
    }

    handover_items = (await db.execute(select(HandoverItem))).scalars().all()
    demo_item_ids = {
        row.id
        for row in handover_items
        if row.handover_case_id in demo_handover_ids or row.asset_id in demo_asset_ids
    }

    deleted: dict[str, int] = {}
    if demo_item_ids:
        deleted["execution_actions"] = await _delete_where(
            db,
            ExecutionAction,
            ExecutionAction.handover_item_id.in_(demo_item_ids),
        )
    if demo_handover_ids:
        deleted["execution_actions"] = deleted.get("execution_actions", 0) + await _delete_where(
            db,
            ExecutionAction,
            ExecutionAction.handover_case_id.in_(demo_handover_ids),
        )
        deleted["approval_tasks"] = await _delete_where(
            db,
            ApprovalTask,
            ApprovalTask.handover_case_id.in_(demo_handover_ids),
        )
    if demo_item_ids:
        deleted["handover_items"] = await _delete_where(
            db,
            HandoverItem,
            HandoverItem.id.in_(demo_item_ids),
        )
    if demo_handover_ids:
        deleted["handover_cases"] = await _delete_where(
            db,
            HandoverCase,
            HandoverCase.id.in_(demo_handover_ids),
        )
    if demo_trace_ids:
        deleted["work_artifacts"] = await _delete_where(
            db,
            WorkArtifact,
            WorkArtifact.trace_id.in_(demo_trace_ids),
        )
    if demo_asset_ids:
        deleted["work_artifacts"] = deleted.get("work_artifacts", 0) + await _delete_where(
            db,
            WorkArtifact,
            WorkArtifact.asset_id.in_(demo_asset_ids),
        )
        deleted["runtime_bindings"] = await _delete_where(
            db,
            RuntimeBinding,
            RuntimeBinding.asset_id.in_(demo_asset_ids),
        )
        deleted["asset_ownerships"] = await _delete_where(
            db,
            AssetOwnership,
            AssetOwnership.asset_id.in_(demo_asset_ids),
        )
    if demo_trace_ids:
        deleted["work_traces"] = await _delete_where(
            db,
            WorkTrace,
            WorkTrace.id.in_(demo_trace_ids),
        )
    if demo_report_session_ids:
        deleted["report_upload_sessions"] = await _delete_where(
            db,
            ReportUploadSession,
            ReportUploadSession.id.in_(demo_report_session_ids),
        )
    if demo_job_ids:
        deleted["adapter_run_steps"] = await _delete_where(
            db,
            AdapterRunStep,
            AdapterRunStep.collection_job_id.in_(demo_job_ids),
        )
        deleted["evidence_items"] = await _delete_where(
            db,
            EvidenceItem,
            EvidenceItem.collection_job_id.in_(demo_job_ids),
        )
        deleted["raw_collection_records"] = await _delete_where(
            db,
            RawCollectionRecord,
            RawCollectionRecord.collection_job_id.in_(demo_job_ids),
        )
        deleted["adapter_errors"] = await _delete_where(
            db,
            AdapterError,
            AdapterError.collection_job_id.in_(demo_job_ids),
        )
        deleted["collection_jobs"] = await _delete_where(
            db,
            CollectionJob,
            CollectionJob.id.in_(demo_job_ids),
        )
    if demo_asset_ids:
        deleted["ai_assets"] = await _delete_where(
            db,
            AIAsset,
            AIAsset.id.in_(demo_asset_ids),
        )
    if deletable_runtime_ids:
        deleted["raw_collection_records"] = deleted.get("raw_collection_records", 0) + await _delete_where(
            db,
            RawCollectionRecord,
            RawCollectionRecord.runtime_id.in_(deletable_runtime_ids),
        )
        deleted["adapter_errors"] = deleted.get("adapter_errors", 0) + await _delete_where(
            db,
            AdapterError,
            AdapterError.runtime_id.in_(deletable_runtime_ids),
        )
        deleted["adapter_cursors"] = await _delete_where(
            db,
            AdapterCursor,
            AdapterCursor.runtime_id.in_(deletable_runtime_ids),
        )
        deleted["provider_principals"] = await _delete_where(
            db,
            ProviderPrincipal,
            ProviderPrincipal.runtime_id.in_(deletable_runtime_ids),
        )
        deleted["runtime_capability_snapshots"] = await _delete_where(
            db,
            RuntimeCapabilitySnapshot,
            RuntimeCapabilitySnapshot.runtime_id.in_(deletable_runtime_ids),
        )
        deleted["runtime_instances"] = await _delete_where(
            db,
            RuntimeInstance,
            RuntimeInstance.id.in_(deletable_runtime_ids),
        )

    await audit(
        db,
        user=current_user,
        action="control_plane.demo_data.cleaned",
        resource_type="control_plane",
        details={
            "deleted": deleted,
            "protected": {
                "runtime_instances": len(demo_runtime_ids - deletable_runtime_ids),
                "non_demo_assets_on_demo_runtimes": len(protected_runtime_asset_ids),
                "non_demo_traces_on_demo_runtimes": len(protected_runtime_trace_ids),
            },
        },
    )
    return DemoDataCleanupOut(
        deleted={key: value for key, value in deleted.items() if value},
        protected={
            "runtime_instances": len(demo_runtime_ids - deletable_runtime_ids),
            "non_demo_assets_on_demo_runtimes": len(protected_runtime_asset_ids),
            "non_demo_traces_on_demo_runtimes": len(protected_runtime_trace_ids),
        },
    )


@router.get("/collection-jobs/{job_id}/steps", response_model=list[AdapterRunStepOut])
async def list_collection_job_steps(job_id: int, db: DB, current_user: RuntimeReaderUser):
    return (
        await db.execute(
            select(AdapterRunStep)
            .where(AdapterRunStep.collection_job_id == job_id)
            .order_by(AdapterRunStep.created_at.asc(), AdapterRunStep.id.asc())
        )
    ).scalars().all()


@router.get("/collection-jobs/{job_id}/raw-records", response_model=list[RawCollectionRecordOut])
async def list_collection_job_raw_records(job_id: int, db: DB, current_user: RuntimeReaderUser):
    return (
        await db.execute(
            select(RawCollectionRecord)
            .where(RawCollectionRecord.collection_job_id == job_id)
            .order_by(RawCollectionRecord.collected_at.desc())
            .limit(200)
        )
    ).scalars().all()


@router.get("/adapter-errors", response_model=list[AdapterErrorOut])
async def list_adapter_errors(db: DB, current_user: RuntimeReaderUser, runtime_id: int | None = None):
    stmt = select(AdapterError).order_by(AdapterError.created_at.desc()).limit(200)
    if runtime_id is not None:
        stmt = stmt.where(AdapterError.runtime_id == runtime_id)
    return (await db.execute(stmt)).scalars().all()


@router.get("/runtimes/{runtime_id}/cursors", response_model=list[AdapterCursorOut])
async def list_runtime_cursors(runtime_id: int, db: DB, current_user: RuntimeReaderUser):
    await _get_runtime(db, runtime_id)
    return (
        await db.execute(
            select(AdapterCursor)
            .where(AdapterCursor.runtime_id == runtime_id)
            .order_by(AdapterCursor.stream.asc())
        )
    ).scalars().all()


@router.get("/runtimes/{runtime_id}/principals", response_model=list[ProviderPrincipalOut])
async def list_runtime_principals(runtime_id: int, db: DB, current_user: RuntimeReaderUser):
    await _get_runtime(db, runtime_id)
    return (
        await db.execute(
            select(ProviderPrincipal)
            .where(ProviderPrincipal.runtime_id == runtime_id)
            .order_by(ProviderPrincipal.last_seen_at.desc())
            .limit(200)
        )
    ).scalars().all()


@router.get("/runtimes/{runtime_id}/capability-snapshots", response_model=list[RuntimeCapabilitySnapshotOut])
async def list_runtime_capability_snapshots(runtime_id: int, db: DB, current_user: RuntimeReaderUser):
    await _get_runtime(db, runtime_id)
    return (
        await db.execute(
            select(RuntimeCapabilitySnapshot)
            .where(RuntimeCapabilitySnapshot.runtime_id == runtime_id)
            .order_by(RuntimeCapabilitySnapshot.collected_at.desc())
            .limit(50)
        )
    ).scalars().all()


@router.post("/runtimes/{runtime_id}/imports/openclaw-backup", response_model=CollectionJobOut, status_code=status.HTTP_201_CREATED)
async def import_openclaw_backup(
    runtime_id: int,
    db: DB,
    current_user: RuntimeManagerUser,
    file: UploadFile = File(...),
):
    runtime = await _get_runtime(db, runtime_id)
    try:
        namespace = await require_active_namespace(db, runtime.namespace_id)
    except (MissingNamespaceError, InactiveNamespaceError) as exc:
        raise _tenant_write_http_exception(exc) from exc
    await require_namespace_writer(current_user, namespace.id, db)
    if runtime.provider != RuntimeProvider.OPENCLAW:
        raise HTTPException(status_code=422, detail="OpenClaw backup import requires an openclaw runtime")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="Backup file is empty")
    job = await ingest_openclaw_backup(db, runtime=runtime, content=content, filename=file.filename)
    await audit(
        db,
        user=current_user,
        action="runtime.openclaw_backup.imported",
        resource_type="collection_job",
        resource_id=job.id,
        details={"runtime_id": runtime.id, "filename": file.filename},
    )
    return job


@router.get("/me/ai-workspace", response_model=MyAIWorkspaceOut)
async def my_ai_workspace(db: DB, current_user: CurrentUser):
    asset_stmt = (
        select(AIAsset)
        .join(AssetOwnership, AssetOwnership.asset_id == AIAsset.id)
        .where(AssetOwnership.user_id == current_user.id)
        .order_by(AIAsset.last_seen_at.desc(), AIAsset.created_at.desc())
        .limit(50)
    )
    assets = (await db.execute(asset_stmt)).scalars().unique().all()

    trace_stmt = (
        select(WorkTrace)
        .where(WorkTrace.actor_user_id == current_user.id)
        .order_by(WorkTrace.started_at.desc(), WorkTrace.created_at.desc())
        .limit(50)
    )
    traces = (await db.execute(trace_stmt)).scalars().all()

    handover_stmt = (
        select(HandoverCase)
        .where(
            or_(
                HandoverCase.subject_user_id == current_user.id,
                HandoverCase.receiver_user_id == current_user.id,
                HandoverCase.created_by == current_user.id,
            )
        )
        .order_by(HandoverCase.created_at.desc())
        .limit(20)
    )
    handovers = (await db.execute(handover_stmt)).scalars().all()

    runtime_ids = {
        runtime_id
        for runtime_id in [*(asset.source_runtime_id for asset in assets), *(trace.runtime_id for trace in traces)]
        if runtime_id is not None
    }
    runtimes: list[RuntimeInstance] = []
    latest_reports: dict[int, ReportUploadSession] = {}
    if runtime_ids:
        runtimes = (
            await db.execute(
                select(RuntimeInstance)
                .where(RuntimeInstance.id.in_(runtime_ids))
                .order_by(RuntimeInstance.updated_at.desc())
            )
        ).scalars().all()
        reports = (
            await db.execute(
                select(ReportUploadSession)
                .where(ReportUploadSession.runtime_id.in_(runtime_ids))
                .order_by(ReportUploadSession.created_at.desc())
                .limit(100)
            )
        ).scalars().all()
        for report in reports:
            latest_reports.setdefault(report.runtime_id, report)

    reporter_statuses = _build_reporter_statuses(runtimes, latest_reports)
    handover_profile = (
        await db.execute(select(UserHandoverProfile).where(UserHandoverProfile.user_id == current_user.id))
    ).scalar_one_or_none()
    employment_status = handover_profile.employment_status if handover_profile is not None else EmploymentStatus.ACTIVE
    offboarding_visible = employment_status in {EmploymentStatus.LEAVE, EmploymentStatus.OFFBOARDED}
    offboarding_case_count = sum(1 for item in handovers if item.case_type.value == "employee_offboarding")

    return MyAIWorkspaceOut(
        user_id=current_user.id,
        username=current_user.username,
        employment_status=employment_status,
        offboarding_visible=offboarding_visible,
        offboarding_case_count=offboarding_case_count,
        assets=assets,
        work_traces=traces,
        handovers=handovers,
        project_contexts=_build_project_contexts(assets, traces),
        reporter_statuses=reporter_statuses,
        readiness=_build_readiness(
            user_id=current_user.id,
            assets=assets,
            traces=traces,
            reporter_statuses=reporter_statuses,
        ),
    )


@router.get("/assets", response_model=list[AIAssetOut])
async def list_assets(
    db: DB,
    current_user: AssetReaderUser,
    provider: RuntimeProvider | None = None,
    status_filter: str | None = None,
):
    stmt = select(AIAsset).order_by(AIAsset.last_seen_at.desc())
    if provider is not None:
        stmt = stmt.where(AIAsset.source_provider == provider)
    if status_filter:
        stmt = stmt.where(AIAsset.status == status_filter)
    return (await db.execute(stmt)).scalars().all()


@router.post("/assets", response_model=AIAssetOut, status_code=status.HTTP_201_CREATED)
async def create_asset(body: AIAssetCreate, db: DB, current_user: AssetManagerUser):
    try:
        namespace = await require_active_namespace(db, body.namespace_id)
    except (MissingNamespaceError, InactiveNamespaceError) as exc:
        raise _tenant_write_http_exception(exc) from exc
    await require_namespace_writer(current_user, namespace.id, db)
    if body.source_runtime_id is not None:
        runtime = await _get_runtime(db, body.source_runtime_id)
        try:
            ensure_resource_namespace(runtime, namespace.id, relationship="source_runtime")
        except (MissingNamespaceError, TenantMismatchError) as exc:
            raise _tenant_write_http_exception(exc) from exc
    asset = AIAsset(**body.model_dump())
    db.add(asset)
    await db.flush()
    await audit(
        db,
        user=current_user,
        action="asset.created",
        resource_type="ai_asset",
        resource_id=asset.id,
        details={"asset_type": asset.asset_type.value, "source_provider": asset.source_provider.value},
    )
    return asset


@router.get("/assets/{asset_id}", response_model=AIAssetOut)
async def get_asset(asset_id: int, db: DB, current_user: AssetReaderUser):
    return await _get_asset(db, asset_id)


@router.patch("/assets/{asset_id}", response_model=AIAssetOut)
async def update_asset(asset_id: int, body: AIAssetUpdate, db: DB, current_user: AssetManagerUser):
    asset = await _get_asset(db, asset_id)
    try:
        namespace = await require_active_namespace(db, asset.namespace_id)
    except (MissingNamespaceError, InactiveNamespaceError) as exc:
        raise _tenant_write_http_exception(exc) from exc
    await require_namespace_writer(current_user, namespace.id, db)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(asset, key, value)
    await audit(
        db,
        user=current_user,
        action="asset.updated",
        resource_type="ai_asset",
        resource_id=asset.id,
        details=body.model_dump(exclude_unset=True, mode="json"),
    )
    return asset


@router.post("/assets/{asset_id}/feedback", response_model=AIAssetOut)
async def submit_asset_feedback(asset_id: int, body: AIAssetFeedbackCreate, db: DB, current_user: CurrentUser):
    asset = await _get_asset(db, asset_id)
    try:
        namespace = await require_active_namespace(db, asset.namespace_id)
        ensure_resource_namespace(asset, namespace.id, relationship="asset")
    except (MissingNamespaceError, InactiveNamespaceError, TenantMismatchError) as exc:
        raise _tenant_write_http_exception(exc) from exc
    if current_user.system_role != SystemRole.ADMIN:
        ownership = (
            await db.execute(
                select(AssetOwnership.id).where(
                    AssetOwnership.asset_id == asset.id,
                    AssetOwnership.user_id == current_user.id,
                    AssetOwnership.namespace_id == namespace.id,
                )
            )
        ).scalar_one_or_none()
        trace = (
            await db.execute(
                select(WorkTrace.id).where(
                    WorkTrace.asset_id == asset.id,
                    WorkTrace.actor_user_id == current_user.id,
                    WorkTrace.namespace_id == namespace.id,
                )
            )
        ).scalar_one_or_none()
        if ownership is None and trace is None:
            raise HTTPException(status_code=403, detail="You can only submit feedback for assets linked to you")

    metadata = dict(asset.metadata_json or {})
    feedback = dict(metadata.get("duckdock_user_feedback") or {})
    feedback[str(current_user.id)] = {
        "action": body.action,
        "note": body.note,
        "metadata_json": body.metadata_json,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": current_user.id,
    }
    metadata["duckdock_user_feedback"] = feedback
    asset.metadata_json = metadata
    await audit(
        db,
        user=current_user,
        action="asset.feedback.submitted",
        resource_type="ai_asset",
        resource_id=asset.id,
        details={"action": body.action},
    )
    return asset


@router.post("/assets/{asset_id}/ownership", response_model=AssetOwnershipOut, status_code=status.HTTP_201_CREATED)
async def create_asset_ownership(asset_id: int, body: AssetOwnershipCreate, db: DB, current_user: AssetManagerUser):
    asset = await _get_asset(db, asset_id)
    try:
        namespace = await require_active_namespace(db, asset.namespace_id)
        if body.namespace_id is not None and body.namespace_id != namespace.id:
            raise TenantMismatchError(
                f"asset ownership namespace_id={body.namespace_id} does not match "
                f"asset namespace_id={namespace.id}"
            )
        if body.evidence_id is not None:
            evidence = await db.get(EvidenceItem, body.evidence_id)
            if evidence is None:
                raise HTTPException(status_code=404, detail="Evidence item not found")
            ensure_resource_namespace(evidence, namespace.id, relationship="evidence")
    except (MissingNamespaceError, InactiveNamespaceError, TenantMismatchError) as exc:
        raise _tenant_write_http_exception(exc) from exc
    await require_namespace_writer(current_user, namespace.id, db)
    if body.is_primary:
        rows = (
            await db.execute(
                select(AssetOwnership).where(
                    AssetOwnership.asset_id == asset_id,
                    AssetOwnership.owner_type == body.owner_type,
                    AssetOwnership.is_primary == True,
                )
            )
        ).scalars().all()
        for row in rows:
            row.is_primary = False
    ownership_values = body.model_dump()
    ownership_values["namespace_id"] = namespace.id
    ownership = AssetOwnership(asset_id=asset_id, **ownership_values)
    db.add(ownership)
    await db.flush()
    await audit(
        db,
        user=current_user,
        action="asset.ownership.created",
        resource_type="asset_ownership",
        resource_id=ownership.id,
        details={"asset_id": asset_id, "owner_type": ownership.owner_type.value},
    )
    return ownership


@router.get("/assets/{asset_id}/ownership", response_model=list[AssetOwnershipOut])
async def list_asset_ownership(asset_id: int, db: DB, current_user: AssetReaderUser):
    await _get_asset(db, asset_id)
    return (
        await db.execute(
            select(AssetOwnership)
            .where(AssetOwnership.asset_id == asset_id)
            .order_by(AssetOwnership.is_primary.desc(), AssetOwnership.created_at.desc())
        )
    ).scalars().all()


_SENSITIVE_TRACE_LEVELS = {Sensitivity.CONFIDENTIAL, Sensitivity.RESTRICTED}
_SENSITIVE_EVIDENCE_LEVELS = {EvidenceVisibility.SENSITIVE, EvidenceVisibility.RESTRICTED}


async def _has_elevated_read(db: DB, user, permission_key: str) -> bool:
    """系统 admin 或持指定提权键者 → 不受敏感行 ownership 过滤限制(specs/003 FR-002)。

    与 reveal/decide/verify 同形态:admin 直接放行,否则确保内置 RBAC 后查 has_permission。
    范围维度 = **仅归属**(actor_user_id / created_by);org_unit 细分按 2026-06-12 单租户
    决策暂缓(SaaS 多租户 P2 时再引入),故此处不做 org_unit 过滤。
    """
    if user.system_role == SystemRole.ADMIN:
        return True
    await ensure_builtin_rbac(db)
    return await has_permission(db, user=user, permission_key=permission_key)


def _trace_summary_out(trace: WorkTrace) -> WorkTraceOut:
    """FR-008:默认仅摘要——敏感级 trace 的 metadata_json 遮蔽,完整内容走 reveal。"""
    out = WorkTraceOut.model_validate(trace)
    if trace.sensitivity in _SENSITIVE_TRACE_LEVELS:
        out.metadata_json = None
    return out


@router.get("/worktraces", response_model=list[WorkTraceOut])
async def list_work_traces(
    db: DB,
    current_user: WorktraceReaderUser,
    asset_id: int | None = None,
    actor_user_id: int | None = None,
):
    stmt = select(WorkTrace).order_by(WorkTrace.started_at.desc(), WorkTrace.created_at.desc())
    if asset_id is not None:
        stmt = stmt.where(WorkTrace.asset_id == asset_id)
    if actor_user_id is not None:
        stmt = stmt.where(WorkTrace.actor_user_id == actor_user_id)
    # FR-002:敏感级 trace 仅本人(actor)可见;持 worktrace.content.read / admin 不受限。
    # 非提权者整行排除他人的敏感 trace(含 actor 为空者)——比单纯遮蔽更强的最小暴露。
    if not await _has_elevated_read(db, current_user, "worktrace.content.read"):
        stmt = stmt.where(
            or_(
                WorkTrace.sensitivity.notin_(_SENSITIVE_TRACE_LEVELS),
                WorkTrace.actor_user_id == current_user.id,
            )
        )
    return [_trace_summary_out(trace) for trace in (await db.execute(stmt)).scalars().all()]


@router.get("/worktraces/{trace_id}", response_model=WorkTraceOut)
async def get_work_trace(trace_id: int, db: DB, current_user: WorktraceReaderUser):
    trace = (await db.execute(select(WorkTrace).where(WorkTrace.id == trace_id))).scalar_one_or_none()
    if trace is None:
        raise HTTPException(status_code=404, detail="Work trace not found")
    # FR-002:他人的敏感 trace 按 id 直取也不返回(404,不暴露存在性);提权者/admin 例外。
    if trace.sensitivity in _SENSITIVE_TRACE_LEVELS and trace.actor_user_id != current_user.id:
        if not await _has_elevated_read(db, current_user, "worktrace.content.read"):
            # FR-003:跨边界访问尝试必须留痕。显式 commit——随后抛 404 会触发 get_db rollback。
            await audit(
                db,
                user=current_user,
                action="worktrace.content.denied",
                resource_type="work_trace",
                resource_id=trace.id,
                details={"sensitivity": trace.sensitivity.value, "access": "get_by_id"},
            )
            await db.commit()
            raise HTTPException(status_code=404, detail="Work trace not found")
    return _trace_summary_out(trace)


@router.post("/worktraces/{trace_id}/reveal", response_model=WorkTraceDetailOut)
async def reveal_work_trace(trace_id: int, body: WorkTraceRevealRequest, db: DB, current_user: CurrentUser):
    """FR-008:查看完整内容必须 reason + 审计;本人(actor)或 worktrace.content.read 可揭。"""
    trace = (await db.execute(select(WorkTrace).where(WorkTrace.id == trace_id))).scalar_one_or_none()
    if trace is None:
        raise HTTPException(status_code=404, detail="Work trace not found")
    if current_user.system_role != SystemRole.ADMIN and trace.actor_user_id != current_user.id:
        await ensure_builtin_rbac(db)
        if not await has_permission(db, user=current_user, permission_key="worktrace.content.read"):
            # FR-003:越权揭示尝试必须留痕。显式 commit——随后抛 403 会触发 get_db rollback。
            await audit(
                db,
                user=current_user,
                action="worktrace.content.denied",
                resource_type="work_trace",
                resource_id=trace.id,
                details={"reason": body.reason, "sensitivity": trace.sensitivity.value, "access": "reveal"},
            )
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="worktrace.content.read required to reveal full trace content",
            )
    artifacts = (
        await db.execute(
            select(WorkArtifact).where(WorkArtifact.trace_id == trace.id).order_by(WorkArtifact.created_at.desc())
        )
    ).scalars().all()
    await audit(
        db,
        user=current_user,
        action="worktrace.content.revealed",
        resource_type="work_trace",
        resource_id=trace.id,
        details={
            "reason": body.reason,
            "sensitivity": trace.sensitivity.value,
            "artifact_count": len(artifacts),
        },
    )
    detail = WorkTraceDetailOut.model_validate(trace)
    detail.artifacts = [WorkArtifactOut.model_validate(artifact) for artifact in artifacts]
    return detail


@router.get("/evidence", response_model=list[EvidenceItemOut])
async def list_evidence(db: DB, current_user: EvidenceReaderUser, collection_job_id: int | None = None):
    stmt = select(EvidenceItem).order_by(EvidenceItem.created_at.desc())
    if collection_job_id is not None:
        stmt = stmt.where(EvidenceItem.collection_job_id == collection_job_id)
    # FR-002:敏感/受限证据仅创建者可见;持 evidence.sensitive.read / admin 不受限。
    if not await _has_elevated_read(db, current_user, "evidence.sensitive.read"):
        stmt = stmt.where(
            or_(
                EvidenceItem.visibility.notin_(_SENSITIVE_EVIDENCE_LEVELS),
                EvidenceItem.created_by == current_user.id,
            )
        )
    return (await db.execute(stmt)).scalars().all()


@router.post("/evidence/{evidence_id}/download-link", response_model=EvidenceDownloadLinkOut)
async def issue_evidence_download_link(
    evidence_id: int, body: EvidenceDownloadLinkRequest, db: DB, current_user: EvidenceReaderUser
):
    """FR-009:为证据对象签发**限时**下载 URL(原则 V:权限 + 原因 + 审计;对外签名 URL 必带过期)。

    敏感级证据(SENSITIVE/RESTRICTED)在 evidence.read 之上额外要求 evidence.sensitive.read
    (系统 admin 例外)。报告包内部证据签名的是所在归档对象,响应回传 archive 内路径。
    """
    evidence = (
        await db.execute(select(EvidenceItem).where(EvidenceItem.id == evidence_id))
    ).scalar_one_or_none()
    if evidence is None:
        raise HTTPException(status_code=404, detail="Evidence item not found")
    if not evidence.object_uri:
        raise HTTPException(status_code=404, detail="Evidence item has no downloadable object")

    if (
        evidence.visibility in {EvidenceVisibility.SENSITIVE, EvidenceVisibility.RESTRICTED}
        and current_user.system_role != SystemRole.ADMIN
        and evidence.created_by != current_user.id  # FR-002:创建者本人可取自己的敏感证据(与 worktrace reveal 对齐)
    ):
        await ensure_builtin_rbac(db)
        if not await has_permission(db, user=current_user, permission_key="evidence.sensitive.read"):
            # 敏感证据的越权尝试也留痕(原则 V:敏感访问全程可审计,授予与拒绝皆记)。
            # 必须显式 commit:随后抛 403 会触发 get_db 的 rollback,否则该审计会被回滚丢失。
            await audit(
                db,
                user=current_user,
                action="evidence.download_link.denied",
                resource_type="evidence_item",
                resource_id=evidence.id,
                details={"reason": body.reason, "visibility": evidence.visibility.value},
            )
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="evidence.sensitive.read required for sensitive/restricted evidence",
            )

    try:
        link = evidence_service.build_download_link(object_uri=evidence.object_uri, expires_in=body.expires_in)
    except EvidenceObjectError as exc:
        # 文案保持通用,不外泄内部存储结构(桶/scheme 细节);详情留服务端异常即可。
        raise HTTPException(status_code=422, detail="Evidence object cannot be issued a download link") from exc
    except ArtifactStorageError as exc:
        raise HTTPException(status_code=502, detail="Evidence storage is temporarily unavailable") from exc

    await audit(
        db,
        user=current_user,
        action="evidence.download_link.issued",
        resource_type="evidence_item",
        resource_id=evidence.id,
        details={
            "reason": body.reason,
            "visibility": evidence.visibility.value,
            "expires_in": link["expires_in"],
            "is_archive_member": link["is_archive_member"],
            "archive_path": link["archive_path"],
        },
    )
    return EvidenceDownloadLinkOut(
        evidence_id=evidence.id,
        visibility=evidence.visibility,
        download_url=link["download_url"],
        expires_in=link["expires_in"],
        expires_at=link["expires_at"],
        sha256=evidence.sha256,
        archive_path=link["archive_path"],
        is_archive_member=link["is_archive_member"],
    )


@router.get("/handovers", response_model=list[HandoverCaseOut])
async def list_handovers(db: DB, current_user: HandoverReaderUser, status_filter: HandoverStatus | None = None):
    stmt = select(HandoverCase).order_by(HandoverCase.created_at.desc())
    if status_filter is not None:
        stmt = stmt.where(HandoverCase.status == status_filter)
    return (await db.execute(stmt)).scalars().all()


@router.post("/handovers", response_model=HandoverCaseOut, status_code=status.HTTP_201_CREATED)
async def create_handover(body: HandoverCaseCreate, db: DB, current_user: HandoverManagerUser):
    try:
        namespace = await require_active_namespace(db, body.namespace_id)
    except (MissingNamespaceError, InactiveNamespaceError) as exc:
        raise _tenant_write_http_exception(exc) from exc
    await require_namespace_writer(current_user, namespace.id, db)
    if body.runtime_ids:
        runtimes = (
            await db.execute(select(RuntimeInstance).where(RuntimeInstance.id.in_(body.runtime_ids)))
        ).scalars().all()
        if len({runtime.id for runtime in runtimes}) != len(set(body.runtime_ids)):
            raise HTTPException(status_code=422, detail="One or more handover runtimes were not found")
        try:
            for runtime in runtimes:
                ensure_resource_namespace(runtime, namespace.id, relationship="runtime")
        except (MissingNamespaceError, TenantMismatchError) as exc:
            raise _tenant_write_http_exception(exc) from exc
    summary = dict(body.metadata_json or {})
    summary.update({
        "runtime_ids": body.runtime_ids,
        "collection_scope": body.collection_scope.model_dump(mode="json"),
    })
    case = HandoverCase(
        case_type=body.case_type,
        title=body.title,
        subject_user_id=body.subject_user_id,
        namespace_id=namespace.id,
        receiver_user_id=body.receiver_user_id,
        due_at=body.due_at,
        created_by=current_user.id,
        summary_json=summary,
    )
    db.add(case)
    await db.flush()
    await audit(
        db,
        user=current_user,
        action="handover.created",
        resource_type="handover_case",
        resource_id=case.id,
        details={"case_type": case.case_type.value, "title": case.title},
    )
    return case


@router.get("/handovers/{case_id}", response_model=HandoverCaseOut)
async def get_handover(case_id: int, db: DB, current_user: HandoverReaderUser):
    return await _get_handover(db, case_id)


@router.patch("/handovers/{case_id}", response_model=HandoverCaseOut)
async def update_handover(case_id: int, body: HandoverCaseUpdate, db: DB, current_user: HandoverManagerUser):
    case = await _get_handover(db, case_id, for_update=True)
    await _require_handover_namespace_write(db, current_user, case)
    if case.status in {HandoverStatus.COMPLETED, HandoverStatus.REJECTED, HandoverStatus.CANCELLED}:
        raise HTTPException(status_code=409, detail="Terminal handover cases cannot be updated")
    changed: dict[str, object | None] = {}
    fields = body.model_fields_set
    if "title" in fields:
        case.title = (body.title or "").strip()
        changed["title"] = case.title
    if "subject_user_id" in fields:
        case.subject_user_id = body.subject_user_id
        changed["subject_user_id"] = body.subject_user_id
    if "receiver_user_id" in fields:
        case.receiver_user_id = body.receiver_user_id
        changed["receiver_user_id"] = body.receiver_user_id
    if "due_at" in fields:
        case.due_at = body.due_at
        changed["due_at"] = body.due_at.isoformat() if body.due_at else None
    if changed:
        await audit(
            db,
            user=current_user,
            action="handover.updated",
            resource_type="handover_case",
            resource_id=case.id,
            details=changed,
        )
    return case


@router.post("/handovers/{case_id}/evidence", response_model=EvidenceItemOut, status_code=status.HTTP_201_CREATED)
async def upload_handover_evidence(
    case_id: int,
    db: DB,
    current_user: HandoverManagerUser,
    file: UploadFile = File(...),
    summary: str = Form(..., min_length=2, max_length=1000),
    visibility: EvidenceVisibility = Form(EvidenceVisibility.SENSITIVE),
):
    """Upload a real execution evidence object for an executing handover case.

    The object key deliberately lives under ``handovers/case-{id}/evidence/`` so the
    existing case-prefix ownership check accepts the returned EvidenceItem ID during
    execution receipt validation.
    """
    case = await _get_handover(db, case_id)
    namespace_id = await _require_handover_namespace_write(db, current_user, case)
    _assert_transition(case, {HandoverStatus.EXECUTING})
    body = await file.read()
    if not body:
        raise HTTPException(status_code=422, detail="Evidence upload file must not be empty")
    sha256 = hashlib.sha256(body).hexdigest()
    generated_at = datetime.now(timezone.utc)
    filename = _safe_object_segment(file.filename, "evidence.bin")
    object_key = f"handovers/case-{case.id}/evidence/{generated_at:%Y%m%dT%H%M%S}-{sha256[:12]}-{filename}"
    content_type = file.content_type or "application/octet-stream"
    try:
        await asyncio.to_thread(
            handover_package_service._put_object,
            object_key=object_key,
            body=body,
            content_type=content_type,
            metadata={
                "handover_case_id": str(case.id),
                "created_by": str(current_user.id),
                "sha256": sha256,
            },
        )
    except ArtifactStorageError as exc:
        raise HTTPException(status_code=502, detail="Evidence storage is temporarily unavailable") from exc

    evidence = build_evidence_item(
        namespace_id=namespace_id,
        source_type=EvidenceSourceType.USER_CONFIRM,
        source_provider=RuntimeProvider.CUSTOM,
        object_uri=f"s3://{artifact_service.bucket}/{object_key}",
        sha256=sha256,
        summary=summary,
        visibility=visibility,
        created_by=current_user.id,
    )
    db.add(evidence)
    await db.flush()
    await audit(
        db,
        user=current_user,
        action="handover.evidence.uploaded",
        resource_type="handover_case",
        resource_id=case.id,
        details={
            "evidence_id": evidence.id,
            "object_uri_prefix": f"s3://{artifact_service.bucket}/handovers/case-{case.id}/evidence/",
            "sha256": sha256,
            "visibility": visibility.value,
        },
    )
    return evidence


@router.post("/handovers/{case_id}/collect", response_model=CollectionJobOut, status_code=status.HTTP_202_ACCEPTED)
async def collect_handover(case_id: int, db: DB, current_user: HandoverManagerUser):
    case = await _get_handover(db, case_id)
    namespace_id = await _require_handover_namespace_write(db, current_user, case)
    await _assert_handover_runtime_namespaces(
        db,
        case=case,
        namespace_id=namespace_id,
    )
    _assert_transition(case, {HandoverStatus.DRAFT, HandoverStatus.COLLECTING})
    case.status = HandoverStatus.COLLECTING
    job = CollectionJob(
        runtime_id=None,
        trigger_type=CollectionTriggerType.OFFBOARDING
        if case.case_type.value == "employee_offboarding"
        else CollectionTriggerType.PROJECT_HANDOVER,
        status=JobStatus.PENDING,
        scope_json={"handover_case_id": case.id, **(case.summary_json or {})},
    )
    db.add(job)
    await db.flush()
    await audit(
        db,
        user=current_user,
        action="handover.collection.requested",
        resource_type="collection_job",
        resource_id=job.id,
        details={"handover_case_id": case.id},
    )
    return job


@router.post("/handovers/{case_id}/items", response_model=HandoverItemOut, status_code=status.HTTP_201_CREATED)
async def create_handover_item(case_id: int, body: HandoverItemCreate, db: DB, current_user: HandoverManagerUser):
    case = await _get_handover(db, case_id)
    namespace_id = await _require_handover_namespace_write(db, current_user, case)
    asset = await _get_asset(db, body.asset_id)
    try:
        ensure_resource_namespace(asset, namespace_id, relationship="asset")
        if body.evidence_id is not None:
            evidence = await db.get(EvidenceItem, body.evidence_id)
            if evidence is None:
                raise HTTPException(status_code=422, detail="Evidence item not found")
            ensure_resource_namespace(evidence, namespace_id, relationship="evidence")
    except (MissingNamespaceError, TenantMismatchError) as exc:
        raise _tenant_write_http_exception(exc) from exc
    item = HandoverItem(
        handover_case_id=case.id,
        asset_id=body.asset_id,
        recommended_action=body.recommended_action,
        receiver_user_id=body.receiver_user_id or case.receiver_user_id,
        risk_reason=body.risk_reason,
        evidence_id=body.evidence_id,
    )
    db.add(item)
    await db.flush()
    await audit(
        db,
        user=current_user,
        action="handover.item.created",
        resource_type="handover_item",
        resource_id=item.id,
        details={"handover_case_id": case.id, "asset_id": item.asset_id},
    )
    await _annotate_handover_items(db, [item])
    return item


@router.get("/handovers/{case_id}/items", response_model=list[HandoverItemOut])
async def list_handover_items(case_id: int, db: DB, current_user: HandoverReaderUser):
    await _get_handover(db, case_id)
    items = (
        await db.execute(
            select(HandoverItem)
            .where(HandoverItem.handover_case_id == case_id)
            .order_by(HandoverItem.created_at.desc())
        )
    ).scalars().all()
    await _annotate_handover_items(db, items)
    return items


class _AnalysisResult(list):  # type: ignore[type-arg]
    """analyze 结果:对 HTTP 序列化仍是 ``list[HandoverItemOut]``(向后兼容),

    但额外携带 ``ai_assist`` 共享降级指示,供进程内直接调用方读取;HTTP 侧另以
    ``X-AI-Assist`` 头暴露(让"这是启发式而非 AI"在 API 响应里可见)。
    """

    ai_assist: dict[str, Any]


@router.post("/handovers/{case_id}/analyze", response_model=list[HandoverItemOut])
async def analyze_handover(
    case_id: int, db: DB, current_user: HandoverManagerUser, response: Response = Response()
):
    # 进程内直调(测试/内部)可省 response;HTTP 走 FastAPI 时会注入每请求独立的 Response 以写头。
    case = await _get_handover(db, case_id)
    namespace_id = await _require_handover_namespace_write(db, current_user, case)
    _assert_transition(
        case,
        {HandoverStatus.DRAFT, HandoverStatus.COLLECTING, HandoverStatus.ANALYZING},
    )
    case.status = HandoverStatus.ANALYZING
    stmt = select(AIAsset)
    if case.subject_user_id is not None:
        stmt = stmt.join(AssetOwnership, AssetOwnership.asset_id == AIAsset.id).where(
            AssetOwnership.user_id == case.subject_user_id
        )
    stmt = stmt.where(AIAsset.namespace_id == namespace_id)
    assets = (await db.execute(stmt.limit(200))).scalars().all()

    created: list[HandoverItem] = []
    existing_asset_ids = set(
        (
            await db.execute(select(HandoverItem.asset_id).where(HandoverItem.handover_case_id == case.id))
        ).scalars().all()
    )
    new_assets = [asset for asset in assets if asset.id not in existing_asset_ids]

    # 顾问产出建议(可插拔:规则版默认 / LLM 版按需)。LLM 失败时返回空 → 逐项回落 MANUAL_REVIEW。
    advisor = build_handover_advisor()
    contexts = [
        AssetContext(
            asset_id=asset.id,
            name=asset.name,
            asset_type=asset.asset_type.value,
            criticality=asset.criticality.value,
            status=asset.status.value,
            description=asset.description,
        )
        for asset in new_assets
    ]
    recommendations = await advisor.recommend(contexts) if contexts else {}

    for asset in new_assets:
        rec = recommendations.get(asset.id)
        rationale = rec.rationale if rec else "未获顾问建议,回落 MANUAL_REVIEW,需人工复核。"
        recommended_action = rec.recommended_action if rec else HandoverAction.MANUAL_REVIEW
        confidence = rec.confidence if rec else 0.0
        # FR-009:每条生成的 HandoverItem 关联一条溯源证据(顾问/分析阶段产物),供审批人核对建议来源。
        evidence = build_evidence_item(
            namespace_id=namespace_id,
            source_type=EvidenceSourceType.LLM_ANALYSIS,
            source_provider=asset.source_provider,
            summary=(
                f"交接分析建议({advisor.mode}):资产「{asset.name}」→ "
                f"{recommended_action.value}(置信度 {confidence:.2f})。{rationale}"
            )[:2000],
            confidence=confidence,
            created_by=current_user.id,
        )
        db.add(evidence)
        await db.flush()  # 需要 evidence.id 才能回填 HandoverItem.evidence_id(无 ORM relationship)
        item = HandoverItem(
            handover_case_id=case.id,
            asset_id=asset.id,
            recommended_action=recommended_action,
            receiver_user_id=case.receiver_user_id,
            risk_reason=rationale,
            confidence=confidence,
            status=HandoverItemStatus.PROPOSED,
            evidence_id=evidence.id,
        )
        db.add(item)
        created.append(item)
    case.status = HandoverStatus.PENDING_APPROVAL
    await db.flush()
    # 共享降级指示:LLM 顾问真正产出建议才算 mode="llm";规则版兜底 / LLM 静默回落 → baseline+degraded。
    # 无新资产可分析时 LLM 顾问根本没被调用,不应误报降级 → 视作未降级。
    llm_produced = bool(recommendations) if new_assets else True
    ai_assist = build_ai_assist(advisor, llm_produced=llm_produced)
    await audit(
        db,
        user=current_user,
        action="handover.analyzed",
        resource_type="handover_case",
        resource_id=case.id,
        details={
            "created_items": len(created),
            "advisor_mode": advisor.mode,
            "ai_assist": ai_assist,
        },
    )
    # HTTP 响应头暴露(响应体仍为 list[HandoverItemOut],向后兼容前端/既有调用方）。
    response.headers["X-AI-Assist"] = json.dumps(ai_assist)
    result = _AnalysisResult(created)
    result.ai_assist = ai_assist
    await _annotate_handover_items(db, created)
    return result


@router.post("/handovers/{case_id}/submit", response_model=list[ApprovalTaskOut])
async def submit_handover(case_id: int, body: list[ApprovalTaskCreate], db: DB, current_user: HandoverManagerUser):
    case = await _get_handover(db, case_id)
    namespace_id = await _require_handover_namespace_write(db, current_user, case)
    _assert_transition(
        case,
        {HandoverStatus.DRAFT, HandoverStatus.ANALYZING, HandoverStatus.PENDING_APPROVAL},
    )
    items = (
        await db.execute(
            select(HandoverItem)
            .where(HandoverItem.handover_case_id == case.id)
        )
    ).scalars().all()
    if not items:
        raise HTTPException(status_code=422, detail="No handover items available for approval")
    await _assert_handover_item_namespaces(
        db,
        namespace_id=namespace_id,
        items=items,
    )
    case.status = HandoverStatus.PENDING_APPROVAL
    tasks: list[ApprovalTask] = []
    for approval in body:
        task = ApprovalTask(
            handover_case_id=case.id,
            approval_type=approval.approval_type,
            approver_user_id=approval.approver_user_id,
        )
        db.add(task)
        tasks.append(task)
    await db.flush()
    await audit(
        db,
        user=current_user,
        action="handover.submitted",
        resource_type="handover_case",
        resource_id=case.id,
        details={"approval_count": len(tasks)},
    )
    return tasks


@router.get("/handovers/{case_id}/approvals", response_model=list[ApprovalTaskOut])
async def list_handover_approvals(case_id: int, db: DB, current_user: HandoverReaderUser):
    await _get_handover(db, case_id)
    return (
        await db.execute(
            select(ApprovalTask)
            .where(ApprovalTask.handover_case_id == case_id)
            .order_by(ApprovalTask.created_at.desc(), ApprovalTask.id.desc())
        )
    ).scalars().all()


@router.post("/handovers/{case_id}/approvals/{approval_id}/decide", response_model=ApprovalTaskOut)
async def decide_approval(
    case_id: int,
    approval_id: int,
    body: ApprovalDecision,
    db: DB,
    current_user: CurrentUser,
):
    case = await _get_handover(db, case_id)
    namespace_id = await _active_handover_namespace_id(db, case)
    if case.status != HandoverStatus.PENDING_APPROVAL:
        raise HTTPException(status_code=409, detail="Handover case is not pending approval")
    task = (
        await db.execute(
            select(ApprovalTask).where(
                ApprovalTask.id == approval_id,
                ApprovalTask.handover_case_id == case.id,
            )
        )
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Approval task not found")
    if task.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=409, detail="Approval task has already been decided")
    # US-005: 指派审批任务本身是一个 case-scoped 写授权，保留既有“受派人可决”
    # 语义；其他 handover manager 仍必须同时拥有该 Namespace 的写权限。
    is_assigned_approver = task.approver_user_id == current_user.id
    if current_user.system_role != SystemRole.ADMIN and not is_assigned_approver:
        await ensure_builtin_rbac(db)
        if not await has_permission(db, user=current_user, permission_key="handover.manage"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the assigned approver or handover managers may decide this approval",
            )
    if not is_assigned_approver:
        await require_namespace_writer(current_user, namespace_id, db)
    if body.decision not in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}:
        raise HTTPException(status_code=422, detail="Decision must be approved or rejected")
    task.status = body.decision
    task.comment = body.comment
    task.decided_at = datetime.now(timezone.utc)
    if body.decision == ApprovalStatus.REJECTED:
        case.status = HandoverStatus.REJECTED
    else:
        statuses = (
            await db.execute(select(ApprovalTask.status).where(ApprovalTask.handover_case_id == case.id))
        ).scalars().all()
        if statuses and all(status == ApprovalStatus.APPROVED for status in statuses):
            case.status = HandoverStatus.APPROVED
    await audit(
        db,
        user=current_user,
        action="handover.approval.decided",
        resource_type="approval_task",
        resource_id=task.id,
        details={"handover_case_id": case.id, "decision": body.decision.value},
    )
    return task


@router.post("/handovers/{case_id}/execute", response_model=list[ExecutionActionOut], status_code=status.HTTP_202_ACCEPTED)
async def execute_handover(case_id: int, body: ExecuteHandoverRequest, db: DB, current_user: HandoverManagerUser):
    case = await _get_handover(db, case_id, for_update=True)
    namespace_id = await _require_handover_namespace_write(db, current_user, case)
    existing_actions = await _existing_execution_actions_for_idempotency(
        db,
        case_id=case.id,
        idempotency_key=body.idempotency_key,
    )
    if existing_actions is not None:
        return existing_actions
    if body.execution_mode == ExecutionMode.AUTO:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="auto 执行(探针 lease)尚未实现,见 specs/001 T085;请用 manual",
        )
    if case.status != HandoverStatus.APPROVED:
        raise HTTPException(status_code=409, detail="Handover case must be approved before execution")
    stmt = select(HandoverItem).where(HandoverItem.handover_case_id == case.id).order_by(HandoverItem.id)
    selected_item_ids = set(body.selected_item_ids or [])
    if selected_item_ids:
        owned_item_ids = set(
            (
                await db.execute(
                    select(HandoverItem.id).where(
                        HandoverItem.handover_case_id == case.id,
                        HandoverItem.id.in_(selected_item_ids),
                    )
                )
            ).scalars().all()
        )
        if owned_item_ids != selected_item_ids:
            raise HTTPException(status_code=422, detail="Selected handover items must belong to this case")
        stmt = stmt.where(HandoverItem.id.in_(selected_item_ids))
    items = (await db.execute(stmt)).scalars().all()
    if not items:
        raise HTTPException(status_code=422, detail="No executable handover items")
    await _assert_handover_item_namespaces(
        db,
        namespace_id=namespace_id,
        items=items,
    )
    case.status = HandoverStatus.EXECUTING
    actions: list[ExecutionAction] = []
    for index, item in enumerate(items):
        item.status = HandoverItemStatus.EXECUTING
        action = ExecutionAction(
            handover_case_id=case.id,
            handover_item_id=item.id,
            action_type=item.recommended_action.value,
            provider=RuntimeProvider.CUSTOM,
            status=ExecutionStatus.PENDING,
            request_json={"asset_id": item.asset_id, "receiver_user_id": item.receiver_user_id},
            idempotency_key=body.idempotency_key if index == 0 else None,
            execution_mode=body.execution_mode,
        )
        db.add(action)
        actions.append(action)
    try:
        await db.flush()
    except IntegrityError:
        if not body.idempotency_key:
            raise
        await db.rollback()
        existing_actions = await _existing_execution_actions_for_idempotency(
            db,
            case_id=case_id,
            idempotency_key=body.idempotency_key,
        )
        if existing_actions is None:
            raise
        return existing_actions
    await audit(
        db,
        user=current_user,
        action="handover.execution.requested",
        resource_type="handover_case",
        resource_id=case.id,
        details={"action_count": len(actions), "idempotency_key": body.idempotency_key},
    )
    await _annotate_execution_actions(db, actions)
    return actions


@router.get("/handovers/{case_id}/actions", response_model=list[ExecutionActionOut])
async def list_execution_actions(case_id: int, db: DB, current_user: HandoverReaderUser):
    await _get_handover(db, case_id)
    actions = (
        await db.execute(
            select(ExecutionAction)
            .where(ExecutionAction.handover_case_id == case_id)
            .order_by(ExecutionAction.created_at.desc(), ExecutionAction.id.desc())
        )
    ).scalars().all()
    await _annotate_execution_actions(db, actions)
    return actions


@router.post("/handovers/{case_id}/actions/{action_id}/complete", response_model=ExecutionActionOut)
async def complete_execution_action(
    case_id: int,
    action_id: int,
    body: ExecutionReceipt,
    db: DB,
    current_user: HandoverManagerUser,
):
    """FR-019 manual 回执:人工在厂商平台执行后提交结果+说明 → 审计 + USER_CONFIRM 证据。"""
    case = await _get_handover(db, case_id)
    action = (
        await db.execute(
            select(ExecutionAction).where(
                ExecutionAction.id == action_id,
                ExecutionAction.handover_case_id == case.id,
            )
        )
    ).scalar_one_or_none()
    if action is None:
        raise HTTPException(status_code=404, detail="Execution action not found")
    if body.result not in {ExecutionStatus.SUCCEEDED, ExecutionStatus.FAILED}:
        raise HTTPException(status_code=422, detail="Receipt result must be succeeded or failed")
    evidence_ids = _dedupe_ids(body.evidence_ids)
    if action.status in {ExecutionStatus.SUCCEEDED, ExecutionStatus.FAILED}:
        result_json = action.result_json or {}
        if body.idempotency_key and result_json.get("receipt_idempotency_key") == body.idempotency_key:
            # A missing stored hash (legacy in-flight row recorded before payload hashing
            # existed) cannot be proven identical, so treat it as a conflict rather than
            # silently returning a possibly-different prior receipt.
            expected_hash = result_json.get("receipt_payload_hash")
            if expected_hash != _receipt_payload_hash(
                result=body.result,
                note=body.note,
                evidence_ids=evidence_ids,
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Receipt idempotency key was already used for a different receipt payload",
                )
            await _annotate_execution_actions(db, [action])
            return action
        raise HTTPException(status_code=409, detail="Execution action already completed")
    if case.status != HandoverStatus.EXECUTING:
        raise HTTPException(
            status_code=409, detail="Handover must be in executing state to complete an action"
        )

    item, asset_criticality, trace_sensitivity = await _execution_action_context(db, action)
    requires_evidence = _receipt_requires_evidence(
        asset_criticality=asset_criticality,
        trace_sensitivity=trace_sensitivity,
    )
    if body.result == ExecutionStatus.SUCCEEDED and requires_evidence and not evidence_ids:
        raise HTTPException(
            status_code=422,
            detail="Evidence is required to mark a sensitive or high-criticality handover action done",
        )
    namespace_id = await _require_handover_namespace_write(db, current_user, case)
    await _assert_case_evidence_ids(
        db,
        case_id=case.id,
        namespace_id=namespace_id,
        evidence_ids=evidence_ids,
    )

    action.status = body.result
    action.result_json = {
        **(action.result_json or {}),
        "mode": action.execution_mode.value,
        "note": body.note,
        "completed_by": current_user.id,
        "asset_criticality": asset_criticality.value if asset_criticality else None,
        "trace_sensitivity": trace_sensitivity.value if trace_sensitivity else None,
        "receipt_idempotency_key": body.idempotency_key,
        "receipt_payload_hash": _receipt_payload_hash(
            result=body.result,
            note=body.note,
            evidence_ids=evidence_ids,
        ),
    }
    if item is not None:
        item.status = (
            HandoverItemStatus.DONE
            if body.result == ExecutionStatus.SUCCEEDED
            else HandoverItemStatus.FAILED
        )
    receipt_evidence = build_evidence_item(
        namespace_id=namespace_id,
        source_type=EvidenceSourceType.USER_CONFIRM,
        source_provider=action.provider,
        summary=f"人工执行回执({body.result.value}):{body.note}",
        visibility=EvidenceVisibility.SENSITIVE if requires_evidence else EvidenceVisibility.NORMAL,
        created_by=current_user.id,
    )
    db.add(receipt_evidence)
    await db.flush()
    action.evidence_ids = _dedupe_ids([*evidence_ids, receipt_evidence.id])
    action.result_json["evidence_ids"] = action.evidence_ids
    remaining = (
        await db.execute(
            select(ExecutionAction.id).where(
                ExecutionAction.handover_case_id == case.id,
                ExecutionAction.status.in_(
                    {ExecutionStatus.PENDING, ExecutionStatus.RUNNING, ExecutionStatus.REQUIRES_MANUAL}
                ),
            )
        )
    ).scalars().all()
    if not remaining and case.status == HandoverStatus.EXECUTING:
        case.status = HandoverStatus.VERIFYING
    await db.flush()
    await audit(
        db,
        user=current_user,
        action="handover.execution.receipt",
        resource_type="execution_action",
        resource_id=action.id,
        details={
            "handover_case_id": case.id,
            "result": body.result.value,
            "mode": action.execution_mode.value,
            "note": body.note,
            "evidence_ids": action.evidence_ids,
            "asset_criticality": asset_criticality.value if asset_criticality else None,
            "trace_sensitivity": trace_sensitivity.value if trace_sensitivity else None,
            "receipt_idempotency_key": body.idempotency_key,
        },
    )
    await _annotate_execution_actions(db, [action])
    return action


@router.post("/handovers/{case_id}/verify", response_model=HandoverCaseOut)
async def verify_handover(case_id: int, body: HandoverVerifyRequest, db: DB, current_user: CurrentUser):
    """US-006 验收归档:接收人或 handover.manage 确认验收 → COMPLETED + 证据 + 审计。"""
    case = await _get_handover(db, case_id)
    namespace_id = await _require_handover_namespace_write(db, current_user, case)
    if current_user.system_role != SystemRole.ADMIN and case.receiver_user_id != current_user.id:
        await ensure_builtin_rbac(db)
        if not await has_permission(db, user=current_user, permission_key="handover.manage"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the receiver or handover managers may verify this handover",
            )
    if case.status != HandoverStatus.VERIFYING:
        raise HTTPException(status_code=409, detail="Handover must be in verifying state to be accepted")

    open_item_ids = (
        await db.execute(
            select(HandoverItem.id).where(
                HandoverItem.handover_case_id == case.id,
                HandoverItem.status.in_(
                    {
                        HandoverItemStatus.PROPOSED,
                        HandoverItemStatus.APPROVED,
                        HandoverItemStatus.EXECUTING,
                    }
                ),
            )
        )
    ).scalars().all()
    if open_item_ids:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Handover has items that are not in a terminal state",
                "open_item_ids": list(open_item_ids),
            },
        )

    # HANDOVER-05:存在 FAILED 执行动作/交接项时,不得静默标记 COMPLETED——
    # 必须接收人/管理者显式 acknowledge_failures=True 才能带病归档。
    failed_action_ids = (
        await db.execute(
            select(ExecutionAction.id).where(
                ExecutionAction.handover_case_id == case.id,
                ExecutionAction.status == ExecutionStatus.FAILED,
            )
        )
    ).scalars().all()
    failed_item_ids = (
        await db.execute(
            select(HandoverItem.id).where(
                HandoverItem.handover_case_id == case.id,
                HandoverItem.status == HandoverItemStatus.FAILED,
            )
        )
    ).scalars().all()
    if (failed_action_ids or failed_item_ids) and not body.acknowledge_failures:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Handover has failed execution actions or items; set acknowledge_failures=true to complete anyway",
                "failed_action_ids": list(failed_action_ids),
                "failed_item_ids": list(failed_item_ids),
            },
        )

    case.status = HandoverStatus.COMPLETED
    db.add(
        build_evidence_item(
            namespace_id=namespace_id,
            source_type=EvidenceSourceType.USER_CONFIRM,
            source_provider=RuntimeProvider.CUSTOM,
            summary=f"交接验收确认:{body.note or '已确认'}",
            created_by=current_user.id,
        )
    )
    await db.flush()
    await audit(
        db,
        user=current_user,
        action="handover.verified",
        resource_type="handover_case",
        resource_id=case.id,
        details={"note": body.note, "acknowledge_failures": body.acknowledge_failures},
    )
    return case


# FR-012:对外交接包仅在执行回执完成并进入验收后才允许构建,避免 APPROVED 阶段预览泄露未落地交接。
_PACKAGE_ALLOWED_STATES = {
    HandoverStatus.VERIFYING,
    HandoverStatus.COMPLETED,
}


async def _latest_package_evidence(db: DB, case_id: int) -> EvidenceItem | None:
    """取该 case 最近一次构建的交接包证据(object_uri 落在 handovers/case-{id}/ 前缀下)。"""
    prefix = f"s3://{artifact_service.bucket}/handovers/case-{case_id}/"
    return (
        await db.execute(
            select(EvidenceItem)
            .where(
                EvidenceItem.source_type == EvidenceSourceType.BACKUP_PACKAGE,
                EvidenceItem.object_uri.like(f"{prefix}%"),
            )
            .order_by(EvidenceItem.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


@router.post("/handovers/{case_id}/package", response_model=EvidenceItemOut, status_code=status.HTTP_201_CREATED)
async def create_handover_package(case_id: int, db: DB, current_user: HandoverManagerUser):
    """FR-012:构建精简对外交接包(资产/建议/回执/摘要 + 证据引用)→ 返回可下载的 EvidenceItem。

    仅在 case 已进入 VERIFYING/COMPLETED 时允许;包内不含明文凭证,
    敏感工作历史只放遮蔽摘要(见 handover_package_service)。
    """
    case = await _get_handover(db, case_id)
    await _require_handover_namespace_write(db, current_user, case)
    _assert_transition(case, _PACKAGE_ALLOWED_STATES)
    try:
        evidence = await handover_package_service.build_package(db, case, created_by=current_user.id)
    except ArtifactStorageError as exc:
        raise HTTPException(status_code=502, detail="Handover package storage is temporarily unavailable") from exc
    await audit(
        db,
        user=current_user,
        action="handover.package.built",
        resource_type="handover_case",
        resource_id=case.id,
        details={"evidence_id": evidence.id, "sha256": evidence.sha256},
    )
    return evidence


@router.post("/handovers/{case_id}/package/download-link", response_model=EvidenceDownloadLinkOut)
async def download_handover_package_link(
    case_id: int, body: EvidenceDownloadLinkRequest, db: DB, current_user: EvidenceReaderUser
):
    """FR-012:为最近一次交接包签发**限时**下载 URL(复用证据下载链路)。

    权限 + 原因 + 审计(原则 V):敏感级包在 evidence.read 之上额外要求 evidence.sensitive.read
    (创建者本人 / 系统 admin 例外);拒签文案保持通用,不外泄内部桶名。
    """
    await _get_handover(db, case_id)
    evidence = await _latest_package_evidence(db, case_id)
    if evidence is None or not evidence.object_uri:
        raise HTTPException(status_code=404, detail="No handover package available for this case")

    if (
        evidence.visibility in {EvidenceVisibility.SENSITIVE, EvidenceVisibility.RESTRICTED}
        and current_user.system_role != SystemRole.ADMIN
        and evidence.created_by != current_user.id
    ):
        await ensure_builtin_rbac(db)
        if not await has_permission(db, user=current_user, permission_key="evidence.sensitive.read"):
            # 越权尝试也留痕(原则 V);显式 commit——随后抛 403 会触发 get_db rollback。
            await audit(
                db,
                user=current_user,
                action="evidence.download_link.denied",
                resource_type="evidence_item",
                resource_id=evidence.id,
                details={"reason": body.reason, "visibility": evidence.visibility.value, "handover_case_id": case_id},
            )
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="evidence.sensitive.read required for sensitive/restricted evidence",
            )

    try:
        link = evidence_service.build_download_link(object_uri=evidence.object_uri, expires_in=body.expires_in)
    except EvidenceObjectError as exc:
        # 文案保持通用,不外泄内部存储结构(桶/scheme 细节)。
        raise HTTPException(status_code=422, detail="Handover package cannot be issued a download link") from exc
    except ArtifactStorageError as exc:
        raise HTTPException(status_code=502, detail="Handover package storage is temporarily unavailable") from exc

    await audit(
        db,
        user=current_user,
        action="evidence.download_link.issued",
        resource_type="evidence_item",
        resource_id=evidence.id,
        details={
            "reason": body.reason,
            "visibility": evidence.visibility.value,
            "expires_in": link["expires_in"],
            "handover_case_id": case_id,
        },
    )
    return EvidenceDownloadLinkOut(
        evidence_id=evidence.id,
        visibility=evidence.visibility,
        download_url=link["download_url"],
        expires_in=link["expires_in"],
        expires_at=link["expires_at"],
        sha256=evidence.sha256,
        archive_path=link["archive_path"],
        is_archive_member=link["is_archive_member"],
    )
