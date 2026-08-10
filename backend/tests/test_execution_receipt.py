"""FR-019 manual 回执流程测试(specs/001 T084 · 宪法原则 IV/V)。

覆盖:成功/失败回执 → 动作终态 + item 状态 + USER_CONFIRM 证据 + 审计含说明;
全部动作终态后 case EXECUTING→VERIFYING;重复回执 409;非法结果 422;auto 模式标记。
"""
from __future__ import annotations

from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.datastructures import UploadFile
from sqlalchemy import select

from app.api.v1.endpoints.control_plane import (
    complete_execution_action,
    create_handover,
    create_handover_item,
    decide_approval,
    execute_handover,
    list_execution_actions,
    list_handover_items,
    submit_handover,
    upload_handover_evidence,
    verify_handover,
)
from app.models.audit import AuditLog
from app.models.control_plane import (
    AIAsset,
    ApprovalStatus,
    ApprovalType,
    AssetType,
    Criticality,
    EvidenceItem,
    EvidenceSourceType,
    EvidenceVisibility,
    ExecutionAction,
    ExecutionMode,
    ExecutionStatus,
    HandoverAction,
    HandoverCaseType,
    HandoverItem,
    HandoverItemStatus,
    HandoverStatus,
    RuntimeProvider,
    Sensitivity,
    TraceType,
    WorkTrace,
)
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.user import SystemRole, User
from app.schemas.control_plane import (
    ApprovalDecision,
    ApprovalTaskCreate,
    ExecuteHandoverRequest,
    ExecutionReceipt,
    HandoverCaseCreate,
    HandoverItemCreate,
    HandoverVerifyRequest,
)
from app.services.artifact_service import artifact_service


class _FakeEvidenceStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.metadata: dict[str, dict[str, str]] = {}

    def put_object(self, *, object_key, body, content_type=None, metadata=None):
        self.objects[object_key] = body
        self.metadata[object_key] = dict(metadata or {})
        return {"bucket": artifact_service.bucket, "object_key": object_key}


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


async def _make_namespace(session, owner: User) -> Namespace:
    namespace = Namespace(name=f"receipt-{uuid4().hex[:16]}", owner_id=owner.id)
    session.add(namespace)
    await session.flush()
    return namespace


async def _approved_case_with_action(
    session,
    admin,
    *,
    suffix,
    execution_mode=ExecutionMode.MANUAL,
    receiver_user_id=None,
    criticality=Criticality.MEDIUM,
    trace_sensitivity: Sensitivity | None = None,
    with_item_evidence=False,
):
    """造一条已审批、已 execute 的交接链,返回 (case, action)。"""
    approver = await _make_user(session, username=f"approver-{suffix}")
    namespace = await _make_namespace(session, admin)
    if receiver_user_id is not None:
        session.add(
            NamespaceMember(
                namespace_id=namespace.id,
                user_id=receiver_user_id,
                role=NamespaceRole.DEVELOPER,
            )
        )
        await session.flush()
    case = await create_handover(
        HandoverCaseCreate(
            namespace_id=namespace.id,
            case_type=HandoverCaseType.EMPLOYEE_OFFBOARDING,
            title=f"交接-{suffix}",
            receiver_user_id=receiver_user_id,
        ),
        session,
        admin,
    )
    asset = AIAsset(
        namespace_id=namespace.id,
        asset_type=AssetType.SKILL,
        name=f"asset-{suffix}",
        source_provider=RuntimeProvider.OPENCLAW,
        criticality=criticality,
    )
    session.add(asset)
    await session.flush()
    if trace_sensitivity is not None:
        session.add(
            WorkTrace(
                namespace_id=namespace.id,
                asset_id=asset.id,
                title=f"trace-{suffix}",
                trace_type=TraceType.SESSION,
                sensitivity=trace_sensitivity,
            )
        )
        await session.flush()
    evidence_id = None
    if with_item_evidence:
        evidence = EvidenceItem(
            namespace_id=namespace.id,
            source_type=EvidenceSourceType.API,
            source_provider=RuntimeProvider.OPENCLAW,
            summary=f"case evidence {suffix}",
            created_by=admin.id,
        )
        session.add(evidence)
        await session.flush()
        evidence_id = evidence.id
    await create_handover_item(
        case.id,
        HandoverItemCreate(
            asset_id=asset.id,
            recommended_action=HandoverAction.TRANSFER_OWNER,
            evidence_id=evidence_id,
        ),
        session,
        admin,
    )
    approvals = await submit_handover(
        case.id,
        [ApprovalTaskCreate(approval_type=ApprovalType.MANAGER, approver_user_id=approver.id)],
        session,
        admin,
    )
    await decide_approval(
        case.id, approvals[0].id, ApprovalDecision(decision=ApprovalStatus.APPROVED), session, approver
    )
    actions = await execute_handover(
        case.id, ExecuteHandoverRequest(execution_mode=execution_mode), session, admin
    )
    return case, actions[0]


async def test_succeeded_receipt_full_chain(async_session):
    admin = await _make_user(async_session, username="admin-er1", system_role=SystemRole.ADMIN)
    case, action = await _approved_case_with_action(async_session, admin, suffix="er1")
    assert action.execution_mode == ExecutionMode.MANUAL  # FR-019 默认 manual

    done = await complete_execution_action(
        case.id,
        action.id,
        ExecutionReceipt(result=ExecutionStatus.SUCCEEDED, note="已在 OpenClaw 后台完成归属转移"),
        async_session,
        admin,
    )

    assert done.status == ExecutionStatus.SUCCEEDED
    assert done.result_json["note"] == "已在 OpenClaw 后台完成归属转移"
    assert done.result_json["mode"] == "manual"
    assert done.result_json["completed_by"] == admin.id
    assert done.result_json["asset_criticality"] == "medium"
    assert done.result_json["trace_sensitivity"] is None

    item = (
        await async_session.execute(select(HandoverItem).where(HandoverItem.handover_case_id == case.id))
    ).scalar_one()
    assert item.status == HandoverItemStatus.DONE
    assert case.status == HandoverStatus.VERIFYING  # 全部动作终态 → 验收阶段

    evidence = (
        await async_session.execute(
            select(EvidenceItem).where(EvidenceItem.source_type == EvidenceSourceType.USER_CONFIRM)
        )
    ).scalar_one()
    assert "归属转移" in evidence.summary
    assert evidence.visibility == EvidenceVisibility.NORMAL
    assert done.evidence_ids == [evidence.id]
    assert done.result_json["evidence_ids"] == [evidence.id]

    log = (
        await async_session.execute(
            select(AuditLog).where(AuditLog.action == "handover.execution.receipt")
        )
    ).scalar_one()
    assert log.details["note"] == "已在 OpenClaw 后台完成归属转移"
    assert log.details["result"] == "succeeded"
    assert log.details["evidence_ids"] == [evidence.id]


async def test_failed_receipt_marks_item_failed(async_session):
    admin = await _make_user(async_session, username="admin-er2", system_role=SystemRole.ADMIN)
    case, action = await _approved_case_with_action(async_session, admin, suffix="er2")

    await complete_execution_action(
        case.id,
        action.id,
        ExecutionReceipt(result=ExecutionStatus.FAILED, note="厂商后台无该资产,需人工核查"),
        async_session,
        admin,
    )

    assert action.status == ExecutionStatus.FAILED
    item = (
        await async_session.execute(select(HandoverItem).where(HandoverItem.handover_case_id == case.id))
    ).scalar_one()
    assert item.status == HandoverItemStatus.FAILED
    assert case.status == HandoverStatus.VERIFYING  # 失败也算终态,进入验收复盘


async def test_sensitive_done_receipt_requires_existing_case_evidence(async_session):
    admin = await _make_user(async_session, username="admin-er-sensitive", system_role=SystemRole.ADMIN)
    case, action = await _approved_case_with_action(
        async_session,
        admin,
        suffix="er-sensitive",
        criticality=Criticality.HIGH,
    )

    with pytest.raises(HTTPException) as exc:
        await complete_execution_action(
            case.id,
            action.id,
            ExecutionReceipt(result=ExecutionStatus.SUCCEEDED, note="已执行高敏动作"),
            async_session,
            admin,
        )

    assert exc.value.status_code == 422
    assert action.status == ExecutionStatus.PENDING
    assert action.result_json is None


async def test_upload_execution_evidence_allows_sensitive_done_receipt(async_session, monkeypatch):
    admin = await _make_user(async_session, username="admin-er-upload", system_role=SystemRole.ADMIN)
    store = _FakeEvidenceStore()
    monkeypatch.setattr(artifact_service, "put_object", store.put_object, raising=False)
    case, action = await _approved_case_with_action(
        async_session,
        admin,
        suffix="er-upload",
        criticality=Criticality.HIGH,
    )

    uploaded = await upload_handover_evidence(
        case.id,
        async_session,
        admin,
        file=UploadFile(filename="proof.txt", file=BytesIO(b"owner transfer completed")),
        summary="execution proof screenshot",
        visibility=EvidenceVisibility.SENSITIVE,
    )

    assert uploaded.object_uri is not None
    prefix = f"s3://{artifact_service.bucket}/handovers/case-{case.id}/evidence/"
    assert uploaded.object_uri.startswith(prefix)
    object_key = uploaded.object_uri.removeprefix(f"s3://{artifact_service.bucket}/")
    assert store.objects[object_key] == b"owner transfer completed"
    assert store.metadata[object_key]["handover_case_id"] == str(case.id)

    done = await complete_execution_action(
        case.id,
        action.id,
        ExecutionReceipt(
            result=ExecutionStatus.SUCCEEDED,
            note="高危资产已按截图完成",
            evidence_ids=[uploaded.id],
        ),
        async_session,
        admin,
    )

    assert done.status == ExecutionStatus.SUCCEEDED
    assert uploaded.id in done.evidence_ids


async def test_upload_execution_evidence_requires_executing_case(async_session, monkeypatch):
    admin = await _make_user(async_session, username="admin-er-upload-state", system_role=SystemRole.ADMIN)
    namespace = await _make_namespace(async_session, admin)
    store = _FakeEvidenceStore()
    monkeypatch.setattr(artifact_service, "put_object", store.put_object, raising=False)
    case = await create_handover(
        HandoverCaseCreate(
            namespace_id=namespace.id,
            case_type=HandoverCaseType.EMPLOYEE_OFFBOARDING,
            title="draft evidence upload",
        ),
        async_session,
        admin,
    )

    with pytest.raises(HTTPException) as exc:
        await upload_handover_evidence(
            case.id,
            async_session,
            admin,
            file=UploadFile(filename="proof.txt", file=BytesIO(b"draft proof")),
            summary="should not upload",
        )

    assert exc.value.status_code == 409
    assert store.objects == {}


async def test_low_public_done_receipt_allows_no_prior_evidence(async_session):
    admin = await _make_user(async_session, username="admin-er-low", system_role=SystemRole.ADMIN)
    case, action = await _approved_case_with_action(
        async_session,
        admin,
        suffix="er-low",
        criticality=Criticality.LOW,
        trace_sensitivity=Sensitivity.PUBLIC,
    )

    done = await complete_execution_action(
        case.id,
        action.id,
        ExecutionReceipt(result=ExecutionStatus.SUCCEEDED, note="低敏资产已执行"),
        async_session,
        admin,
    )

    assert done.status == ExecutionStatus.SUCCEEDED
    assert done.result_json["asset_criticality"] == "low"
    assert done.result_json["trace_sensitivity"] == "public"


async def test_items_and_actions_payload_expose_sensitivity_evidence_requirement(async_session):
    admin = await _make_user(async_session, username="admin-er-requires", system_role=SystemRole.ADMIN)
    case, action = await _approved_case_with_action(
        async_session,
        admin,
        suffix="er-requires",
        criticality=Criticality.MEDIUM,
        trace_sensitivity=Sensitivity.INTERNAL,
    )

    items = await list_handover_items(case.id, async_session, admin)
    actions = await list_execution_actions(case.id, async_session, admin)

    assert getattr(items[0], "requires_evidence") is True
    assert actions[0].id == action.id
    assert getattr(actions[0], "requires_evidence") is True


async def test_sensitive_receipt_links_supplied_evidence_and_marks_confirm_sensitive(async_session):
    admin = await _make_user(async_session, username="admin-er-evidence", system_role=SystemRole.ADMIN)
    case, action = await _approved_case_with_action(
        async_session,
        admin,
        suffix="er-evidence",
        criticality=Criticality.MEDIUM,
        trace_sensitivity=Sensitivity.INTERNAL,
        with_item_evidence=True,
    )
    item = (
        await async_session.execute(select(HandoverItem).where(HandoverItem.handover_case_id == case.id))
    ).scalar_one()
    assert item.evidence_id is not None

    done = await complete_execution_action(
        case.id,
        action.id,
        ExecutionReceipt(
            result=ExecutionStatus.SUCCEEDED,
            note="敏感 trace 已执行",
            evidence_ids=[item.evidence_id],
        ),
        async_session,
        admin,
    )

    assert item.evidence_id in done.evidence_ids
    receipt_evidence_id = next(evidence_id for evidence_id in done.evidence_ids if evidence_id != item.evidence_id)
    receipt_evidence = await async_session.get(EvidenceItem, receipt_evidence_id)
    assert receipt_evidence is not None
    assert receipt_evidence.visibility == EvidenceVisibility.SENSITIVE
    assert done.result_json["trace_sensitivity"] == "internal"


async def test_receipt_rejects_evidence_from_another_case(async_session):
    admin = await _make_user(async_session, username="admin-er-cross", system_role=SystemRole.ADMIN)
    case, action = await _approved_case_with_action(
        async_session,
        admin,
        suffix="er-cross",
        criticality=Criticality.HIGH,
    )
    other_case, _ = await _approved_case_with_action(
        async_session,
        admin,
        suffix="er-cross-other",
        with_item_evidence=True,
    )
    other_item = (
        await async_session.execute(select(HandoverItem).where(HandoverItem.handover_case_id == other_case.id))
    ).scalar_one()
    assert other_item.evidence_id is not None

    with pytest.raises(HTTPException) as exc:
        await complete_execution_action(
            case.id,
            action.id,
            ExecutionReceipt(
                result=ExecutionStatus.SUCCEEDED,
                note="借用其他 case 证据",
                evidence_ids=[other_item.evidence_id],
            ),
            async_session,
            admin,
        )

    assert exc.value.status_code == 422


async def test_receipt_idempotency_key_replays_completed_action(async_session):
    admin = await _make_user(async_session, username="admin-er-idem", system_role=SystemRole.ADMIN)
    case, action = await _approved_case_with_action(async_session, admin, suffix="er-idem")
    receipt = ExecutionReceipt(
        result=ExecutionStatus.SUCCEEDED,
        note="幂等回执",
        idempotency_key="receipt-er-idem",
    )

    first = await complete_execution_action(case.id, action.id, receipt, async_session, admin)
    replay = await complete_execution_action(case.id, action.id, receipt, async_session, admin)

    assert replay.id == first.id
    assert replay.result_json["receipt_idempotency_key"] == "receipt-er-idem"

    with pytest.raises(HTTPException) as exc:
        await complete_execution_action(
            case.id,
            action.id,
            ExecutionReceipt(
                result=ExecutionStatus.SUCCEEDED,
                note="不同幂等键",
                idempotency_key="receipt-er-idem-other",
            ),
            async_session,
            admin,
        )
    assert exc.value.status_code == 409


async def test_receipt_idempotency_key_rejects_different_payload(async_session):
    admin = await _make_user(async_session, username="admin-er-idem-diff", system_role=SystemRole.ADMIN)
    case, action = await _approved_case_with_action(async_session, admin, suffix="er-idem-diff")
    await complete_execution_action(
        case.id,
        action.id,
        ExecutionReceipt(
            result=ExecutionStatus.SUCCEEDED,
            note="第一次幂等回执",
            idempotency_key="receipt-er-idem-diff",
        ),
        async_session,
        admin,
    )

    with pytest.raises(HTTPException) as exc:
        await complete_execution_action(
            case.id,
            action.id,
            ExecutionReceipt(
                result=ExecutionStatus.SUCCEEDED,
                note="同 key 但不同说明",
                idempotency_key="receipt-er-idem-diff",
            ),
            async_session,
            admin,
        )

    assert exc.value.status_code == 409
    assert "different receipt payload" in str(exc.value.detail)


async def test_receipt_payload_hash_ignores_evidence_id_order():
    from app.api.v1.endpoints.control_plane import _receipt_payload_hash

    base = _receipt_payload_hash(result=ExecutionStatus.SUCCEEDED, note="n", evidence_ids=[1, 2, 3])
    reordered = _receipt_payload_hash(result=ExecutionStatus.SUCCEEDED, note="n", evidence_ids=[3, 1, 2, 1])
    assert base == reordered  # same evidence set, any order/dupes -> identical idempotency hash
    different = _receipt_payload_hash(result=ExecutionStatus.SUCCEEDED, note="n", evidence_ids=[1, 2])
    assert base != different  # genuinely different evidence set -> different hash


async def test_receipt_idempotency_legacy_row_without_hash_is_rejected(async_session):
    # A terminal action that recorded a receipt_idempotency_key before payload hashing
    # existed (in-flight rows from the 15a0e0b->f11bbd1 window). A same-key replay can no
    # longer be proven identical, so it must conflict (409), not silently return the old row.
    admin = await _make_user(
        async_session, username="admin-er-legacy", system_role=SystemRole.ADMIN
    )
    case, action = await _approved_case_with_action(async_session, admin, suffix="er-legacy")
    action.status = ExecutionStatus.SUCCEEDED
    action.result_json = {"receipt_idempotency_key": "legacy-key", "note": "pre-hash"}
    await async_session.commit()

    with pytest.raises(HTTPException) as exc:
        await complete_execution_action(
            case.id,
            action.id,
            ExecutionReceipt(
                result=ExecutionStatus.SUCCEEDED,
                note="replay without stored hash",
                idempotency_key="legacy-key",
            ),
            async_session,
            admin,
        )
    assert exc.value.status_code == 409


async def test_double_receipt_conflicts(async_session):
    admin = await _make_user(async_session, username="admin-er3", system_role=SystemRole.ADMIN)
    case, action = await _approved_case_with_action(async_session, admin, suffix="er3")
    receipt = ExecutionReceipt(result=ExecutionStatus.SUCCEEDED, note="第一次回执")
    await complete_execution_action(case.id, action.id, receipt, async_session, admin)

    with pytest.raises(HTTPException) as exc:
        await complete_execution_action(case.id, action.id, receipt, async_session, admin)
    assert exc.value.status_code == 409


async def test_invalid_receipt_result_rejected(async_session):
    admin = await _make_user(async_session, username="admin-er4", system_role=SystemRole.ADMIN)
    case, action = await _approved_case_with_action(async_session, admin, suffix="er4")

    with pytest.raises(HTTPException) as exc:
        await complete_execution_action(
            case.id,
            action.id,
            ExecutionReceipt(result=ExecutionStatus.RUNNING, note="不该被接受"),
            async_session,
            admin,
        )
    assert exc.value.status_code == 422
    assert action.status == ExecutionStatus.PENDING  # 未被污染


async def test_execute_request_rejects_unavailable_auto_mode(async_session):
    admin = await _make_user(async_session, username="admin-er5", system_role=SystemRole.ADMIN)
    approver = await _make_user(async_session, username="approver-er5")
    namespace = await _make_namespace(async_session, admin)
    case = await create_handover(
        HandoverCaseCreate(
            namespace_id=namespace.id,
            case_type=HandoverCaseType.EMPLOYEE_OFFBOARDING,
            title="handover-er5",
        ),
        async_session,
        admin,
    )
    asset = AIAsset(
        namespace_id=namespace.id,
        asset_type=AssetType.SKILL,
        name="asset-er5",
        source_provider=RuntimeProvider.OPENCLAW,
    )
    async_session.add(asset)
    await async_session.flush()
    await create_handover_item(
        case.id,
        HandoverItemCreate(asset_id=asset.id, recommended_action=HandoverAction.TRANSFER_OWNER),
        async_session,
        admin,
    )
    approvals = await submit_handover(
        case.id,
        [ApprovalTaskCreate(approval_type=ApprovalType.MANAGER, approver_user_id=approver.id)],
        async_session,
        admin,
    )
    await decide_approval(
        case.id, approvals[0].id, ApprovalDecision(decision=ApprovalStatus.APPROVED), async_session, approver
    )

    with pytest.raises(ValidationError):
        ExecuteHandoverRequest(execution_mode=ExecutionMode.AUTO)
    actions = (
        await async_session.execute(select(ExecutionAction).where(ExecutionAction.handover_case_id == case.id))
    ).scalars().all()

    assert actions == []
    assert case.status == HandoverStatus.APPROVED


# --- T086 验收/归档(VERIFYING → COMPLETED) ---


async def _drive_to_verifying(session, admin, *, suffix, receiver_user_id=None):
    case, action = await _approved_case_with_action(
        session, admin, suffix=suffix, receiver_user_id=receiver_user_id
    )
    await complete_execution_action(
        case.id, action.id, ExecutionReceipt(result=ExecutionStatus.SUCCEEDED, note="已执行"), session, admin
    )
    assert case.status == HandoverStatus.VERIFYING
    return case


async def test_receiver_verifies_and_completes(async_session):
    admin = await _make_user(async_session, username="admin-v1", system_role=SystemRole.ADMIN)
    receiver = await _make_user(async_session, username="receiver-v1")
    case = await _drive_to_verifying(async_session, admin, suffix="v1", receiver_user_id=receiver.id)

    completed = await verify_handover(
        case.id, HandoverVerifyRequest(note="资产已收到,确认无误"), async_session, receiver
    )
    assert completed.status == HandoverStatus.COMPLETED

    # 验收 = 第二条 USER_CONFIRM 证据(回执 1 + 验收 1)
    evidence_count = len(
        (
            await async_session.execute(
                select(EvidenceItem).where(EvidenceItem.source_type == EvidenceSourceType.USER_CONFIRM)
            )
        ).scalars().all()
    )
    assert evidence_count == 2
    log = (
        await async_session.execute(select(AuditLog).where(AuditLog.action == "handover.verified"))
    ).scalar_one()
    assert log.resource_id == case.id


async def test_verify_before_verifying_state_conflicts(async_session):
    admin = await _make_user(async_session, username="admin-v2", system_role=SystemRole.ADMIN)
    # 只 execute、未回执 → case 仍 EXECUTING
    case, _ = await _approved_case_with_action(async_session, admin, suffix="v2")
    assert case.status == HandoverStatus.EXECUTING

    with pytest.raises(HTTPException) as exc:
        await verify_handover(case.id, HandoverVerifyRequest(), async_session, admin)
    assert exc.value.status_code == 409


async def test_outsider_cannot_verify(async_session):
    admin = await _make_user(async_session, username="admin-v3", system_role=SystemRole.ADMIN)
    receiver = await _make_user(async_session, username="receiver-v3")
    outsider = await _make_user(async_session, username="outsider-v3")
    case = await _drive_to_verifying(async_session, admin, suffix="v3", receiver_user_id=receiver.id)

    with pytest.raises(HTTPException) as exc:
        await verify_handover(case.id, HandoverVerifyRequest(), async_session, outsider)
    assert exc.value.status_code == 403
    assert case.status == HandoverStatus.VERIFYING  # 未被越权改动
