"""FND-012/013 regression tests for tenant-safe new writes."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.v1.endpoints import control_plane as control_plane_api
from app.api.v1.endpoints.control_plane import (
    collect_handover,
    create_asset,
    create_handover,
    create_runtime,
    create_runtime_report_token,
    decide_approval,
    execute_handover,
    ingest_report_upload_session_endpoint,
    revoke_runtime_report_token,
    submit_asset_feedback,
    submit_handover,
    update_handover,
)
from app.models.control_plane import (
    AIAsset,
    ApprovalStatus,
    ApprovalTask,
    ApprovalType,
    AssetOwnership,
    AssetType,
    CollectionJob,
    EvidenceItem,
    EvidenceSourceType,
    ExecutionAction,
    HandoverAction,
    HandoverCase,
    HandoverCaseType,
    HandoverItem,
    HandoverStatus,
    OwnerType,
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
    RuntimeReportToken,
    ReportUploadSession,
    Sensitivity,
    TraceType,
    WorkTrace,
)
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.user import SystemRole, User
from app.schemas.control_plane import (
    AIAssetFeedbackCreate,
    AIAssetCreate,
    ApprovalDecision,
    ApprovalTaskCreate,
    ExecuteHandoverRequest,
    HandoverCaseCreate,
    HandoverCaseUpdate,
    RuntimeInstanceCreate,
    RuntimeReportTokenCreate,
)
from app.services.tenant_write_service import (
    MissingNamespaceError,
    TenantMismatchError,
    build_evidence_item,
    build_runtime_binding,
    build_work_trace,
    ensure_resource_namespace,
)


async def _user(db, suffix: str) -> User:
    user = User(
        username=f"tenant-write-{suffix}",
        email=f"tenant-write-{suffix}@example.test",
        hashed_password="unused",
        system_role=SystemRole.USER,
    )
    db.add(user)
    await db.flush()
    return user


async def _namespace(db, suffix: str, owner: User) -> Namespace:
    namespace = Namespace(name=f"tenant-write-{suffix}", owner_id=owner.id)
    db.add(namespace)
    await db.flush()
    db.add(
        NamespaceMember(
            namespace_id=namespace.id,
            user_id=owner.id,
            role=NamespaceRole.ADMIN,
        )
    )
    await db.flush()
    return namespace


async def _runtime(db, suffix: str, namespace_id: int | None) -> RuntimeInstance:
    runtime = RuntimeInstance(
        namespace_id=namespace_id,
        provider=RuntimeProvider.CUSTOM,
        name=f"runtime-{suffix}",
        deploy_type=RuntimeDeployType.PRIVATE,
    )
    db.add(runtime)
    await db.flush()
    return runtime


async def _asset(db, suffix: str, namespace_id: int | None) -> AIAsset:
    asset = AIAsset(
        namespace_id=namespace_id,
        asset_type=AssetType.AGENT,
        name=f"asset-{suffix}",
        source_provider=RuntimeProvider.CUSTOM,
    )
    db.add(asset)
    await db.flush()
    return asset


async def _trace(db, suffix: str, namespace_id: int | None) -> WorkTrace:
    trace = WorkTrace(
        namespace_id=namespace_id,
        title=f"trace-{suffix}",
        trace_type=TraceType.SESSION,
        sensitivity=Sensitivity.INTERNAL,
    )
    db.add(trace)
    await db.flush()
    return trace


def test_management_create_contracts_require_explicit_namespace() -> None:
    with pytest.raises(ValidationError, match="namespace_id"):
        RuntimeInstanceCreate(provider=RuntimeProvider.CUSTOM, name="missing-tenant")
    with pytest.raises(ValidationError, match="namespace_id"):
        AIAssetCreate(
            asset_type=AssetType.AGENT,
            name="missing-tenant",
            source_provider=RuntimeProvider.CUSTOM,
        )


async def test_runtime_create_requires_namespace_write_authorization(async_session) -> None:
    owner = await _user(async_session, "runtime-owner")
    outsider = await _user(async_session, "runtime-outsider")
    namespace = await _namespace(async_session, "runtime", owner)
    body = RuntimeInstanceCreate(
        namespace_id=namespace.id,
        provider=RuntimeProvider.CUSTOM,
        name="authorized-runtime",
    )

    with pytest.raises(HTTPException) as exc_info:
        await create_runtime(body, async_session, outsider)
    assert exc_info.value.status_code == 403
    assert await async_session.scalar(select(func.count(RuntimeInstance.id))) == 0

    runtime = await create_runtime(body, async_session, owner)
    assert runtime.namespace_id == namespace.id


async def test_asset_create_rejects_cross_tenant_runtime(async_session) -> None:
    owner = await _user(async_session, "asset-owner")
    first = await _namespace(async_session, "asset-first", owner)
    second = await _namespace(async_session, "asset-second", owner)
    runtime = await _runtime(async_session, "asset-source", second.id)

    with pytest.raises(HTTPException) as exc_info:
        await create_asset(
            AIAssetCreate(
                namespace_id=first.id,
                asset_type=AssetType.AGENT,
                name="cross-tenant-asset",
                source_provider=RuntimeProvider.CUSTOM,
                source_runtime_id=runtime.id,
            ),
            async_session,
            owner,
        )

    assert exc_info.value.status_code == 409
    assert await async_session.scalar(select(func.count(AIAsset.id))) == 0


@pytest.mark.parametrize("authorization_source", ["ownership", "work_trace"])
async def test_asset_feedback_accepts_same_tenant_typed_authorization(
    async_session,
    authorization_source: str,
) -> None:
    user = await _user(async_session, f"feedback-valid-{authorization_source}")
    namespace = await _namespace(
        async_session,
        f"feedback-valid-{authorization_source}",
        user,
    )
    asset = await _asset(
        async_session,
        f"feedback-valid-{authorization_source}",
        namespace.id,
    )
    if authorization_source == "ownership":
        async_session.add(
            AssetOwnership(
                namespace_id=namespace.id,
                asset_id=asset.id,
                user_id=user.id,
                owner_type=OwnerType.CREATOR,
            )
        )
    else:
        async_session.add(
            WorkTrace(
                namespace_id=namespace.id,
                asset_id=asset.id,
                actor_user_id=user.id,
                title="same-tenant feedback authorization",
                trace_type=TraceType.SESSION,
                sensitivity=Sensitivity.INTERNAL,
            )
        )
    await async_session.flush()

    result = await submit_asset_feedback(
        asset.id,
        AIAssetFeedbackCreate(action="confirm", note="same tenant"),
        async_session,
        user,
    )

    assert result is asset
    assert asset.metadata_json is not None
    feedback = asset.metadata_json["duckdock_user_feedback"][str(user.id)]
    assert feedback["action"] == "confirm"
    assert feedback["note"] == "same tenant"


@pytest.mark.parametrize("authorization_source", ["ownership", "work_trace"])
async def test_asset_feedback_rejects_cross_tenant_typed_authorization(
    async_session,
    authorization_source: str,
) -> None:
    user = await _user(async_session, f"feedback-cross-{authorization_source}")
    asset_namespace = await _namespace(
        async_session,
        f"feedback-cross-asset-{authorization_source}",
        user,
    )
    relation_namespace = await _namespace(
        async_session,
        f"feedback-cross-relation-{authorization_source}",
        user,
    )
    asset = await _asset(
        async_session,
        f"feedback-cross-{authorization_source}",
        asset_namespace.id,
    )
    if authorization_source == "ownership":
        async_session.add(
            AssetOwnership(
                namespace_id=relation_namespace.id,
                asset_id=asset.id,
                user_id=user.id,
                owner_type=OwnerType.CREATOR,
            )
        )
    else:
        async_session.add(
            WorkTrace(
                namespace_id=relation_namespace.id,
                asset_id=asset.id,
                actor_user_id=user.id,
                title="cross-tenant feedback authorization",
                trace_type=TraceType.SESSION,
                sensitivity=Sensitivity.INTERNAL,
            )
        )
    await async_session.flush()

    with pytest.raises(HTTPException) as exc_info:
        await submit_asset_feedback(
            asset.id,
            AIAssetFeedbackCreate(action="confirm"),
            async_session,
            user,
        )

    assert exc_info.value.status_code == 403
    assert asset.metadata_json is None


@pytest.mark.parametrize("tenant_state", ["missing", "inactive"])
async def test_asset_feedback_rejects_asset_without_active_namespace(
    async_session,
    tenant_state: str,
) -> None:
    user = await _user(async_session, f"feedback-{tenant_state}")
    namespace = await _namespace(
        async_session,
        f"feedback-{tenant_state}",
        user,
    )
    asset = await _asset(
        async_session,
        f"feedback-{tenant_state}",
        namespace.id,
    )
    async_session.add(
        AssetOwnership(
            namespace_id=namespace.id,
            asset_id=asset.id,
            user_id=user.id,
            owner_type=OwnerType.CREATOR,
        )
    )
    await async_session.flush()
    if tenant_state == "missing":
        asset.namespace_id = None
    else:
        namespace.deleted_at = datetime.now(timezone.utc)

    with pytest.raises(HTTPException) as exc_info:
        await submit_asset_feedback(
            asset.id,
            AIAssetFeedbackCreate(action="confirm"),
            async_session,
            user,
        )

    assert exc_info.value.status_code == 422
    assert asset.metadata_json is None


async def test_relationship_builders_reject_missing_namespace(async_session) -> None:
    runtime = await _runtime(async_session, "missing", None)
    asset = await _asset(async_session, "missing", None)
    trace = await _trace(async_session, "missing", None)

    with pytest.raises(MissingNamespaceError):
        build_runtime_binding(namespace_id=None, runtime=runtime, asset=asset)
    with pytest.raises(MissingNamespaceError):
        build_work_trace(
            namespace_id=None,
            runtime=runtime,
            asset=asset,
            title="missing tenant",
            trace_type=TraceType.SESSION,
        )
    with pytest.raises(MissingNamespaceError):
        build_evidence_item(
            namespace_id=None,
            work_trace=trace,
            source_type=EvidenceSourceType.API,
            source_provider=RuntimeProvider.CUSTOM,
            summary="missing tenant",
        )


def test_existing_resource_without_namespace_is_not_silently_claimed() -> None:
    legacy_asset = AIAsset(
        namespace_id=None,
        asset_type=AssetType.AGENT,
        name="legacy-unassigned-asset",
        source_provider=RuntimeProvider.CUSTOM,
    )

    with pytest.raises(MissingNamespaceError, match="asset.*has no namespace_id"):
        ensure_resource_namespace(
            legacy_asset,
            42,
            relationship="asset",
        )

    assert legacy_asset.namespace_id is None


async def test_relationship_builders_reject_cross_tenant_links(async_session) -> None:
    owner = await _user(async_session, "relations-owner")
    first = await _namespace(async_session, "relations-first", owner)
    second = await _namespace(async_session, "relations-second", owner)
    runtime = await _runtime(async_session, "relations-first", first.id)
    asset = await _asset(async_session, "relations-second", second.id)
    trace = await _trace(async_session, "relations-second", second.id)

    with pytest.raises(TenantMismatchError, match="asset"):
        build_runtime_binding(namespace_id=first.id, runtime=runtime, asset=asset)
    with pytest.raises(TenantMismatchError, match="asset"):
        build_work_trace(
            namespace_id=first.id,
            runtime=runtime,
            asset=asset,
            title="cross tenant",
            trace_type=TraceType.SESSION,
        )
    with pytest.raises(TenantMismatchError, match="work_trace"):
        build_evidence_item(
            namespace_id=first.id,
            work_trace=trace,
            source_type=EvidenceSourceType.API,
            source_provider=RuntimeProvider.CUSTOM,
            summary="cross tenant",
            created_at=datetime.now(timezone.utc),
        )

    assert await async_session.scalar(select(func.count(EvidenceItem.id))) == 0


async def test_relationship_builders_persist_same_tenant_direct_and_typed_links(async_session) -> None:
    owner = await _user(async_session, "relations-same-owner")
    namespace = await _namespace(async_session, "relations-same", owner)
    runtime = await _runtime(async_session, "relations-same", namespace.id)
    asset = await _asset(async_session, "relations-same", namespace.id)

    binding = build_runtime_binding(
        namespace_id=namespace.id,
        runtime=runtime,
        asset=asset,
    )
    trace = build_work_trace(
        namespace_id=namespace.id,
        runtime=runtime,
        asset=asset,
        title="same tenant",
        trace_type=TraceType.SESSION,
    )
    async_session.add_all([binding, trace])
    await async_session.flush()
    evidence = build_evidence_item(
        namespace_id=namespace.id,
        work_trace=trace,
        source_type=EvidenceSourceType.API,
        source_provider=RuntimeProvider.CUSTOM,
        summary="same tenant typed evidence",
    )
    async_session.add(evidence)
    await async_session.flush()

    assert binding.namespace_id == namespace.id
    assert trace.namespace_id == namespace.id
    assert evidence.namespace_id == namespace.id
    assert evidence.work_trace_id == trace.id


async def test_handover_management_writes_require_case_namespace_authorization(
    async_session,
) -> None:
    owner = await _user(async_session, "handover-owner")
    outsider = await _user(async_session, "handover-outsider")
    namespace = await _namespace(async_session, "handover-authz", owner)
    case = HandoverCase(
        namespace_id=namespace.id,
        case_type=HandoverCaseType.PROJECT_HANDOVER,
        title="namespace protected handover",
        status=HandoverStatus.DRAFT,
        created_by=owner.id,
        summary_json={"runtime_ids": []},
    )
    async_session.add(case)
    await async_session.flush()

    calls = (
        update_handover(
            case.id,
            HandoverCaseUpdate(title="unauthorized update"),
            async_session,
            outsider,
        ),
        collect_handover(case.id, async_session, outsider),
        submit_handover(
            case.id,
            [
                ApprovalTaskCreate(
                    approval_type=ApprovalType.MANAGER,
                    approver_user_id=outsider.id,
                )
            ],
            async_session,
            outsider,
        ),
        execute_handover(
            case.id,
            ExecuteHandoverRequest(),
            async_session,
            outsider,
        ),
    )
    for call in calls:
        with pytest.raises(HTTPException) as exc_info:
            await call
        assert exc_info.value.status_code == 403

    assert case.title == "namespace protected handover"
    assert case.status == HandoverStatus.DRAFT
    assert await async_session.scalar(select(func.count(CollectionJob.id))) == 0
    assert await async_session.scalar(select(func.count(ApprovalTask.id))) == 0
    assert await async_session.scalar(select(func.count(ExecutionAction.id))) == 0


async def test_handover_submit_rejects_cross_namespace_legacy_item(async_session) -> None:
    owner = await _user(async_session, "handover-cross-owner")
    first = await _namespace(async_session, "handover-cross-first", owner)
    second = await _namespace(async_session, "handover-cross-second", owner)
    asset = await _asset(async_session, "handover-cross", second.id)
    case = HandoverCase(
        namespace_id=first.id,
        case_type=HandoverCaseType.PROJECT_HANDOVER,
        title="cross namespace handover",
        status=HandoverStatus.DRAFT,
        created_by=owner.id,
    )
    async_session.add(case)
    await async_session.flush()
    async_session.add(
        HandoverItem(
            handover_case_id=case.id,
            asset_id=asset.id,
            recommended_action=HandoverAction.MANUAL_REVIEW,
        )
    )
    await async_session.flush()

    with pytest.raises(HTTPException) as exc_info:
        await submit_handover(
            case.id,
            [
                ApprovalTaskCreate(
                    approval_type=ApprovalType.MANAGER,
                    approver_user_id=owner.id,
                )
            ],
            async_session,
            owner,
        )

    assert exc_info.value.status_code == 409
    assert await async_session.scalar(select(func.count(ApprovalTask.id))) == 0


async def test_handover_collect_rejects_cross_namespace_runtime_scope(async_session) -> None:
    owner = await _user(async_session, "handover-runtime-owner")
    first = await _namespace(async_session, "handover-runtime-first", owner)
    second = await _namespace(async_session, "handover-runtime-second", owner)
    runtime = await _runtime(async_session, "handover-runtime-cross", second.id)
    case = HandoverCase(
        namespace_id=first.id,
        case_type=HandoverCaseType.PROJECT_HANDOVER,
        title="cross namespace runtime scope",
        status=HandoverStatus.DRAFT,
        created_by=owner.id,
        summary_json={"runtime_ids": [runtime.id]},
    )
    async_session.add(case)
    await async_session.flush()

    with pytest.raises(HTTPException) as exc_info:
        await collect_handover(case.id, async_session, owner)

    assert exc_info.value.status_code == 409
    assert case.status == HandoverStatus.DRAFT
    assert await async_session.scalar(select(func.count(CollectionJob.id))) == 0


async def test_handover_metadata_cannot_override_validated_runtime_scope(async_session) -> None:
    owner = await _user(async_session, "handover-metadata-owner")
    first = await _namespace(async_session, "handover-metadata-first", owner)
    second = await _namespace(async_session, "handover-metadata-second", owner)
    first_runtime = await _runtime(async_session, "handover-metadata-valid", first.id)
    second_runtime = await _runtime(async_session, "handover-metadata-cross", second.id)

    case = await create_handover(
        HandoverCaseCreate(
            namespace_id=first.id,
            case_type=HandoverCaseType.PROJECT_HANDOVER,
            title="reserved runtime scope",
            runtime_ids=[first_runtime.id],
            metadata_json={"runtime_ids": [second_runtime.id]},
        ),
        async_session,
        owner,
    )

    assert case.summary_json is not None
    assert case.summary_json["runtime_ids"] == [first_runtime.id]


async def test_assigned_approver_keeps_case_scoped_write_compatibility(async_session) -> None:
    owner = await _user(async_session, "approval-owner")
    assigned = await _user(async_session, "approval-assigned")
    namespace = await _namespace(async_session, "approval-compat", owner)
    case = HandoverCase(
        namespace_id=namespace.id,
        case_type=HandoverCaseType.PROJECT_HANDOVER,
        title="assigned approval compatibility",
        status=HandoverStatus.PENDING_APPROVAL,
        created_by=owner.id,
    )
    async_session.add(case)
    await async_session.flush()
    task = ApprovalTask(
        handover_case_id=case.id,
        approver_user_id=assigned.id,
        approval_type=ApprovalType.MANAGER,
    )
    async_session.add(task)
    await async_session.flush()

    decided = await decide_approval(
        case.id,
        task.id,
        ApprovalDecision(decision=ApprovalStatus.APPROVED),
        async_session,
        assigned,
    )

    assert decided.status == ApprovalStatus.APPROVED
    assert case.status == HandoverStatus.APPROVED


async def test_runtime_report_token_writes_require_runtime_namespace_authorization(
    async_session,
) -> None:
    owner = await _user(async_session, "token-owner")
    outsider = await _user(async_session, "token-outsider")
    namespace = await _namespace(async_session, "token-authz", owner)
    runtime = await _runtime(async_session, "token-runtime", namespace.id)
    body = RuntimeReportTokenCreate(name="namespace protected token")

    with pytest.raises(HTTPException) as exc_info:
        await create_runtime_report_token(runtime.id, body, async_session, outsider)
    assert exc_info.value.status_code == 403
    assert await async_session.scalar(select(func.count(RuntimeReportToken.id))) == 0

    created = await create_runtime_report_token(runtime.id, body, async_session, owner)
    token = await async_session.get(RuntimeReportToken, created.id)
    assert token is not None
    assert token.is_active is True

    with pytest.raises(HTTPException) as exc_info:
        await revoke_runtime_report_token(token.id, async_session, outsider)
    assert exc_info.value.status_code == 403
    assert token.is_active is True

    assert await revoke_runtime_report_token(token.id, async_session, owner) is None
    assert token.is_active is False


async def test_admin_report_ingest_requires_target_runtime_namespace_writer(
    async_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await _user(async_session, "ingest-owner")
    outsider = await _user(async_session, "ingest-outsider")
    namespace = await _namespace(async_session, "ingest-authz", owner)
    runtime = await _runtime(async_session, "ingest-runtime", namespace.id)
    upload = ReportUploadSession(
        report_id="rpt_tenant_write_authz",
        runtime_id=runtime.id,
        bucket="test-bucket",
        object_key="reports/tenant-write-authz.zip",
        upload_expires_at=datetime.now(timezone.utc),
    )
    async_session.add(upload)
    await async_session.flush()

    ingested: list[str] = []

    async def fake_ingest(db, *, report_id: str):
        assert db is async_session
        ingested.append(report_id)
        return upload

    monkeypatch.setattr(
        control_plane_api,
        "ingest_report_upload_session",
        fake_ingest,
    )

    with pytest.raises(HTTPException) as exc_info:
        await ingest_report_upload_session_endpoint(
            upload.report_id,
            async_session,
            outsider,
        )
    assert exc_info.value.status_code == 403
    assert ingested == []

    result = await ingest_report_upload_session_endpoint(
        upload.report_id,
        async_session,
        owner,
    )
    assert result.report_id == upload.report_id
    assert result.runtime_id == runtime.id
    assert ingested == [upload.report_id]
