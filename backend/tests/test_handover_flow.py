"""交接状态机与审批闸门测试(specs/001 T045/T070 · FR-010/FR-011 · 宪法原则 II/IV)。

直接调用端点函数(绕过 FastAPI DI,鉴权闸门由 test_control_plane_authz 单独覆盖),
聚焦状态机转移、审批闸门、执行动作生成与审批人身份校验。
"""
from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.v1.endpoints.control_plane import (
    analyze_handover,
    collect_handover,
    complete_execution_action,
    create_handover,
    create_handover_item,
    decide_approval,
    execute_handover,
    list_execution_actions,
    list_handover_approvals,
    submit_handover,
    update_handover,
    verify_handover,
)
from app.models.control_plane import (
    AIAsset,
    ApprovalStatus,
    AssetOwnership,
    AssetType,
    EvidenceItem,
    ExecutionAction,
    ExecutionStatus,
    HandoverAction,
    HandoverCaseType,
    HandoverItem,
    HandoverItemStatus,
    HandoverStatus,
    OwnerType,
    RuntimeProvider,
)
from app.models.user import SystemRole, User
from app.schemas.control_plane import (
    ApprovalDecision,
    ApprovalTaskCreate,
    ExecuteHandoverRequest,
    ExecutionReceipt,
    HandoverCaseCreate,
    HandoverCaseUpdate,
    HandoverItemCreate,
    HandoverVerifyRequest,
)
from app.models.control_plane import ApprovalType
from app.models.namespace import Namespace


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


async def _seed_asset(session, *, name="Reporter Skill"):
    asset = AIAsset(
        namespace_id=session.info["handover_namespace_id"],
        asset_type=AssetType.SKILL,
        name=name,
        source_provider=RuntimeProvider.OPENCLAW,
    )
    session.add(asset)
    await session.flush()
    return asset


async def _make_case(session, admin, *, subject_user_id=None, receiver_user_id=None):
    namespace = Namespace(
        name=f"handover-{uuid4().hex[:16]}",
        owner_id=admin.id,
    )
    session.add(namespace)
    await session.flush()
    session.info["handover_namespace_id"] = namespace.id
    return await create_handover(
        HandoverCaseCreate(
            namespace_id=namespace.id,
            case_type=HandoverCaseType.EMPLOYEE_OFFBOARDING,
            title="张三离职交接",
            subject_user_id=subject_user_id,
            receiver_user_id=receiver_user_id,
        ),
        session,
        admin,
    )


async def test_new_case_starts_draft_and_execute_is_blocked(async_session):
    admin = await _make_user(async_session, username="admin-h1", system_role=SystemRole.ADMIN)
    case = await _make_case(async_session, admin)
    assert case.status == HandoverStatus.DRAFT

    with pytest.raises(HTTPException) as exc:
        await execute_handover(case.id, ExecuteHandoverRequest(), async_session, admin)
    assert exc.value.status_code == 409  # FR-011 审批闸门


async def test_full_flow_requires_all_approvals_then_executes(async_session):
    admin = await _make_user(async_session, username="admin-h2", system_role=SystemRole.ADMIN)
    manager = await _make_user(async_session, username="manager-h2")
    security = await _make_user(async_session, username="security-h2")
    receiver = await _make_user(async_session, username="receiver-h2")
    case = await _make_case(async_session, admin, receiver_user_id=receiver.id)
    asset = await _seed_asset(async_session)
    await create_handover_item(
        case.id,
        HandoverItemCreate(asset_id=asset.id, recommended_action=HandoverAction.TRANSFER_OWNER),
        async_session,
        admin,
    )

    approvals = await submit_handover(
        case.id,
        [
            ApprovalTaskCreate(approval_type=ApprovalType.MANAGER, approver_user_id=manager.id),
            ApprovalTaskCreate(approval_type=ApprovalType.SECURITY, approver_user_id=security.id),
        ],
        async_session,
        admin,
    )
    assert case.status == HandoverStatus.PENDING_APPROVAL

    # 指派审批人本人(非 admin、无 handover.manage)可决自己的任务 —— US-005
    await decide_approval(
        case.id, approvals[0].id, ApprovalDecision(decision=ApprovalStatus.APPROVED), async_session, manager
    )
    assert case.status == HandoverStatus.PENDING_APPROVAL  # 还差一票

    await decide_approval(
        case.id, approvals[1].id, ApprovalDecision(decision=ApprovalStatus.APPROVED), async_session, security
    )
    assert case.status == HandoverStatus.APPROVED  # 全票通过

    actions = await execute_handover(
        case.id, ExecuteHandoverRequest(idempotency_key="exec-h2"), async_session, admin
    )
    assert case.status == HandoverStatus.EXECUTING
    assert len(actions) == 1
    assert actions[0].status == ExecutionStatus.PENDING
    assert actions[0].idempotency_key == "exec-h2"
    approval_rows = await list_handover_approvals(case.id, async_session, admin)
    action_rows = await list_execution_actions(case.id, async_session, admin)
    assert [row.id for row in approval_rows] == [approvals[1].id, approvals[0].id]
    assert [row.id for row in action_rows] == [actions[0].id]
    item = (
        await async_session.execute(select(HandoverItem).where(HandoverItem.handover_case_id == case.id))
    ).scalar_one()
    assert item.status == HandoverItemStatus.EXECUTING


async def test_update_handover_receiver_before_terminal_state(async_session):
    admin = await _make_user(async_session, username="admin-h-update", system_role=SystemRole.ADMIN)
    receiver = await _make_user(async_session, username="receiver-h-update")
    case = await _make_case(async_session, admin)

    updated = await update_handover(
        case.id,
        HandoverCaseUpdate(receiver_user_id=receiver.id, title="新的交接标题"),
        async_session,
        admin,
    )

    assert updated.receiver_user_id == receiver.id
    assert updated.title == "新的交接标题"


def test_update_handover_rejects_blank_title():
    with pytest.raises(ValidationError):
        HandoverCaseUpdate(title="   ")


async def test_execute_handover_reuses_existing_actions_for_idempotency_key(async_session):
    admin = await _make_user(async_session, username="admin-h-idem", system_role=SystemRole.ADMIN)
    approver = await _make_user(async_session, username="approver-h-idem")
    receiver = await _make_user(async_session, username="receiver-h-idem")
    case = await _make_case(async_session, admin, receiver_user_id=receiver.id)
    asset = await _seed_asset(async_session, name="asset-h-idem")
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

    first = await execute_handover(
        case.id, ExecuteHandoverRequest(idempotency_key="exec-idem"), async_session, admin
    )
    second = await execute_handover(
        case.id, ExecuteHandoverRequest(idempotency_key="exec-idem"), async_session, admin
    )
    rows = (
        await async_session.execute(
            select(ExecutionAction).where(ExecutionAction.handover_case_id == case.id)
        )
    ).scalars().all()

    assert [action.id for action in second] == [action.id for action in first]
    assert len(rows) == 1
    assert rows[0].idempotency_key == "exec-idem"
    assert case.status == HandoverStatus.EXECUTING


async def test_execute_handover_idempotency_key_reuses_full_action_set(async_session):
    admin = await _make_user(async_session, username="admin-h-idem-many", system_role=SystemRole.ADMIN)
    approver = await _make_user(async_session, username="approver-h-idem-many")
    receiver = await _make_user(async_session, username="receiver-h-idem-many")
    case = await _make_case(async_session, admin, receiver_user_id=receiver.id)
    for index in range(2):
        asset = await _seed_asset(async_session, name=f"asset-h-idem-many-{index}")
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

    first = await execute_handover(
        case.id, ExecuteHandoverRequest(idempotency_key="exec-idem-many"), async_session, admin
    )
    second = await execute_handover(
        case.id, ExecuteHandoverRequest(idempotency_key="exec-idem-many"), async_session, admin
    )
    rows = (
        await async_session.execute(
            select(ExecutionAction).where(ExecutionAction.handover_case_id == case.id).order_by(ExecutionAction.id)
        )
    ).scalars().all()

    assert [action.id for action in second] == [action.id for action in first]
    assert len(rows) == 2
    assert rows[0].idempotency_key == "exec-idem-many"
    assert rows[1].idempotency_key is None


@pytest.mark.mysql
async def test_execute_handover_concurrent_same_key_is_idempotent_on_mysql(async_session_mysql):
    admin = await _make_user(async_session_mysql, username="admin-h-idem-race", system_role=SystemRole.ADMIN)
    approver = await _make_user(async_session_mysql, username="approver-h-idem-race")
    receiver = await _make_user(async_session_mysql, username="receiver-h-idem-race")
    case = await _make_case(async_session_mysql, admin, receiver_user_id=receiver.id)
    asset = await _seed_asset(async_session_mysql, name="asset-h-idem-race")
    await create_handover_item(
        case.id,
        HandoverItemCreate(asset_id=asset.id, recommended_action=HandoverAction.TRANSFER_OWNER),
        async_session_mysql,
        admin,
    )
    approvals = await submit_handover(
        case.id,
        [ApprovalTaskCreate(approval_type=ApprovalType.MANAGER, approver_user_id=approver.id)],
        async_session_mysql,
        admin,
    )
    await decide_approval(
        case.id,
        approvals[0].id,
        ApprovalDecision(decision=ApprovalStatus.APPROVED),
        async_session_mysql,
        approver,
    )
    await async_session_mysql.commit()

    bind = async_session_mysql.bind
    assert bind is not None
    session_factory = async_sessionmaker(bind, expire_on_commit=False)

    async def execute_once():
        async with session_factory() as session:
            actor = await session.get(User, admin.id)
            assert actor is not None
            actions = await execute_handover(
                case.id,
                ExecuteHandoverRequest(idempotency_key="exec-idem-race"),
                session,
                actor,
            )
            await session.commit()
            return [action.id for action in actions]

    first, second = await asyncio.gather(execute_once(), execute_once())
    rows = (
        await async_session_mysql.execute(
            select(ExecutionAction).where(ExecutionAction.handover_case_id == case.id).order_by(ExecutionAction.id)
        )
    ).scalars().all()

    assert first == second
    assert len(rows) == 1
    assert rows[0].idempotency_key == "exec-idem-race"


async def test_execute_rejects_approved_case_with_no_items(async_session):
    admin = await _make_user(async_session, username="admin-h0", system_role=SystemRole.ADMIN)
    case = await _make_case(async_session, admin)
    case.status = HandoverStatus.APPROVED
    await async_session.flush()
    assert case.status == HandoverStatus.APPROVED

    with pytest.raises(HTTPException) as exc:
        await execute_handover(case.id, ExecuteHandoverRequest(), async_session, admin)

    assert exc.value.status_code == 422
    assert case.status == HandoverStatus.APPROVED


async def test_submit_rejects_case_with_no_items(async_session):
    admin = await _make_user(async_session, username="admin-h-empty-submit", system_role=SystemRole.ADMIN)
    approver = await _make_user(async_session, username="approver-h-empty-submit")
    case = await _make_case(async_session, admin)

    with pytest.raises(HTTPException) as exc:
        await submit_handover(
            case.id,
            [ApprovalTaskCreate(approval_type=ApprovalType.MANAGER, approver_user_id=approver.id)],
            async_session,
            admin,
        )

    assert exc.value.status_code == 422
    assert case.status == HandoverStatus.DRAFT


async def test_execute_rejects_selected_item_ids_from_other_case(async_session):
    admin = await _make_user(async_session, username="admin-h-other", system_role=SystemRole.ADMIN)
    approver = await _make_user(async_session, username="approver-h-other")

    case = await _make_case(async_session, admin)
    own_asset = await _seed_asset(async_session, name="owned-by-target-case")
    await create_handover_item(
        case.id,
        HandoverItemCreate(asset_id=own_asset.id, recommended_action=HandoverAction.TRANSFER_OWNER),
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

    other_case = await _make_case(async_session, admin)
    asset = await _seed_asset(async_session, name="owned-by-other-case")
    other_item = await create_handover_item(
        other_case.id,
        HandoverItemCreate(asset_id=asset.id, recommended_action=HandoverAction.TRANSFER_OWNER),
        async_session,
        admin,
    )

    with pytest.raises(HTTPException) as exc:
        await execute_handover(
            case.id,
            ExecuteHandoverRequest(selected_item_ids=[other_item.id]),
            async_session,
            admin,
        )

    assert exc.value.status_code == 422
    assert case.status == HandoverStatus.APPROVED


async def test_rejection_moves_case_to_rejected_and_blocks_execution(async_session):
    admin = await _make_user(async_session, username="admin-h3", system_role=SystemRole.ADMIN)
    approver = await _make_user(async_session, username="approver-h3")
    case = await _make_case(async_session, admin)
    asset = await _seed_asset(async_session, name="asset-h3")
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
        case.id,
        approvals[0].id,
        ApprovalDecision(decision=ApprovalStatus.REJECTED, comment="资产范围不完整"),
        async_session,
        approver,
    )
    assert case.status == HandoverStatus.REJECTED

    with pytest.raises(HTTPException) as exc:
        await execute_handover(case.id, ExecuteHandoverRequest(), async_session, admin)
    assert exc.value.status_code == 409


async def test_rejected_case_cannot_be_decided_again(async_session):
    admin = await _make_user(async_session, username="admin-h-redecide", system_role=SystemRole.ADMIN)
    approver = await _make_user(async_session, username="approver-h-redecide")
    case = await _make_case(async_session, admin)
    asset = await _seed_asset(async_session, name="asset-h-redecide")
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
        case.id,
        approvals[0].id,
        ApprovalDecision(decision=ApprovalStatus.REJECTED, comment="范围不完整"),
        async_session,
        approver,
    )

    with pytest.raises(HTTPException) as exc:
        await decide_approval(
            case.id,
            approvals[0].id,
            ApprovalDecision(decision=ApprovalStatus.APPROVED),
            async_session,
            admin,
        )

    assert exc.value.status_code == 409
    assert case.status == HandoverStatus.REJECTED
    assert approvals[0].status == ApprovalStatus.REJECTED


async def test_decided_approval_task_cannot_be_decided_again(async_session):
    admin = await _make_user(async_session, username="admin-h-redo-task", system_role=SystemRole.ADMIN)
    approver = await _make_user(async_session, username="approver-h-redo-task")
    security = await _make_user(async_session, username="security-h-redo-task")
    case = await _make_case(async_session, admin)
    asset = await _seed_asset(async_session, name="asset-h-redo-task")
    await create_handover_item(
        case.id,
        HandoverItemCreate(asset_id=asset.id, recommended_action=HandoverAction.TRANSFER_OWNER),
        async_session,
        admin,
    )
    approvals = await submit_handover(
        case.id,
        [
            ApprovalTaskCreate(approval_type=ApprovalType.MANAGER, approver_user_id=approver.id),
            ApprovalTaskCreate(approval_type=ApprovalType.SECURITY, approver_user_id=security.id),
        ],
        async_session,
        admin,
    )
    await decide_approval(
        case.id, approvals[0].id, ApprovalDecision(decision=ApprovalStatus.APPROVED), async_session, approver
    )

    with pytest.raises(HTTPException) as exc:
        await decide_approval(
            case.id,
            approvals[0].id,
            ApprovalDecision(decision=ApprovalStatus.REJECTED),
            async_session,
            admin,
        )

    assert exc.value.status_code == 409
    assert case.status == HandoverStatus.PENDING_APPROVAL
    assert approvals[0].status == ApprovalStatus.APPROVED


async def test_outsider_cannot_decide_approval(async_session):
    admin = await _make_user(async_session, username="admin-h4", system_role=SystemRole.ADMIN)
    approver = await _make_user(async_session, username="approver-h4")
    outsider = await _make_user(async_session, username="outsider-h4")
    case = await _make_case(async_session, admin)
    asset = await _seed_asset(async_session, name="asset-h4")
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

    with pytest.raises(HTTPException) as exc:
        await decide_approval(
            case.id, approvals[0].id, ApprovalDecision(decision=ApprovalStatus.APPROVED), async_session, outsider
        )
    assert exc.value.status_code == 403
    assert case.status == HandoverStatus.PENDING_APPROVAL  # 未被越权改动


async def test_invalid_decision_value_is_rejected(async_session):
    admin = await _make_user(async_session, username="admin-h5", system_role=SystemRole.ADMIN)
    approver = await _make_user(async_session, username="approver-h5")
    case = await _make_case(async_session, admin)
    asset = await _seed_asset(async_session, name="asset-h5")
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

    with pytest.raises(HTTPException) as exc:
        await decide_approval(
            case.id, approvals[0].id, ApprovalDecision(decision=ApprovalStatus.PENDING), async_session, approver
        )
    assert exc.value.status_code == 422


async def test_analyze_generates_items_for_subject_assets(async_session):
    admin = await _make_user(async_session, username="admin-h6", system_role=SystemRole.ADMIN)
    subject = await _make_user(async_session, username="subject-h6")
    case = await _make_case(async_session, admin, subject_user_id=subject.id)
    asset = await _seed_asset(async_session, name="Subject-owned Workflow")
    async_session.add(
        AssetOwnership(asset_id=asset.id, owner_type=OwnerType.CREATOR, user_id=subject.id, is_primary=True)
    )
    await async_session.flush()
    items = await analyze_handover(case.id, async_session, admin)

    assert case.status == HandoverStatus.PENDING_APPROVAL
    assert len(items) == 1
    assert items[0].asset_id == asset.id
    assert items[0].recommended_action == HandoverAction.MANUAL_REVIEW
    assert items[0].status == HandoverItemStatus.PROPOSED
    assert items[0].confidence == 0.0  # 规则版顾问:无模型判断 -> 置信度 0


async def _executing_case_with_action(session, admin, *, suffix):
    """造一条已 execute(EXECUTING)的交接链,返回 (case, action)。"""
    approver = await _make_user(session, username=f"approver-{suffix}")
    case = await _make_case(session, admin)
    asset = await _seed_asset(session, name=f"asset-{suffix}")
    await create_handover_item(
        case.id,
        HandoverItemCreate(asset_id=asset.id, recommended_action=HandoverAction.TRANSFER_OWNER),
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
    actions = await execute_handover(case.id, ExecuteHandoverRequest(), session, admin)
    return case, actions[0]


async def test_complete_execution_action_rejects_non_executing_case(async_session):
    """HANDOVER-06 纵深防御:case 不在 EXECUTING 时,动作回执必须 409。"""
    admin = await _make_user(async_session, username="admin-h7", system_role=SystemRole.ADMIN)
    case, action = await _executing_case_with_action(async_session, admin, suffix="h7")

    # 人为把 case 退回 APPROVED(非 EXECUTING),动作仍存在且未终态
    case.status = HandoverStatus.APPROVED
    await async_session.flush()

    with pytest.raises(HTTPException) as exc:
        await complete_execution_action(
            case.id,
            action.id,
            ExecutionReceipt(result=ExecutionStatus.SUCCEEDED, note="不该被接受"),
            async_session,
            admin,
        )
    assert exc.value.status_code == 409
    assert action.status == ExecutionStatus.PENDING  # 未被改动


async def test_complete_execution_action_succeeds_while_executing(async_session):
    """HANDOVER-06 正常路径:case 处于 EXECUTING 时回执成功落终态。"""
    admin = await _make_user(async_session, username="admin-h8", system_role=SystemRole.ADMIN)
    case, action = await _executing_case_with_action(async_session, admin, suffix="h8")
    assert case.status == HandoverStatus.EXECUTING

    done = await complete_execution_action(
        case.id,
        action.id,
        ExecutionReceipt(result=ExecutionStatus.SUCCEEDED, note="已完成"),
        async_session,
        admin,
    )
    assert done.status == ExecutionStatus.SUCCEEDED


async def test_verify_rejects_open_handover_items(async_session):
    """P3-05:VERIFYING 前置校验不能让未闭合 item 被静默归档。"""
    admin = await _make_user(async_session, username="admin-h-open-verify", system_role=SystemRole.ADMIN)
    case, _ = await _executing_case_with_action(async_session, admin, suffix="h-open-verify")
    item = (
        await async_session.execute(select(HandoverItem).where(HandoverItem.handover_case_id == case.id))
    ).scalar_one()
    assert item.status == HandoverItemStatus.EXECUTING
    case.status = HandoverStatus.VERIFYING
    await async_session.flush()

    with pytest.raises(HTTPException) as exc:
        await verify_handover(case.id, HandoverVerifyRequest(note="不应完成"), async_session, admin)

    assert exc.value.status_code == 409
    assert exc.value.detail["open_item_ids"] == [item.id]
    assert case.status == HandoverStatus.VERIFYING


# --- HANDOVER-04 前台状态机来源态闸门 ---


async def test_collect_rejects_illegal_source_status(async_session):
    """HANDOVER-04:case 已 APPROVED 时再 collect → 409,状态不被拉回 COLLECTING。"""
    admin = await _make_user(async_session, username="admin-fsm-collect", system_role=SystemRole.ADMIN)
    case = await _make_case(async_session, admin)
    case.status = HandoverStatus.APPROVED
    await async_session.flush()

    with pytest.raises(HTTPException) as exc:
        await collect_handover(case.id, async_session, admin)
    assert exc.value.status_code == 409
    assert case.status == HandoverStatus.APPROVED


async def test_collect_allows_draft_source_status(async_session):
    """HANDOVER-04 正常路径:DRAFT → collect 仍然落 COLLECTING。"""
    admin = await _make_user(async_session, username="admin-fsm-collect-ok", system_role=SystemRole.ADMIN)
    case = await _make_case(async_session, admin)
    assert case.status == HandoverStatus.DRAFT

    job = await collect_handover(case.id, async_session, admin)
    assert case.status == HandoverStatus.COLLECTING
    assert job.scope_json["handover_case_id"] == case.id


async def test_analyze_rejects_illegal_source_status(async_session):
    """HANDOVER-04:case 已 APPROVED 时再 analyze → 409,不被拉回 ANALYZING/PENDING_APPROVAL。"""
    admin = await _make_user(async_session, username="admin-fsm-analyze", system_role=SystemRole.ADMIN)
    subject = await _make_user(async_session, username="subject-fsm-analyze")
    case = await _make_case(async_session, admin, subject_user_id=subject.id)
    asset = await _seed_asset(async_session, name="fsm-analyze-asset")
    async_session.add(
        AssetOwnership(asset_id=asset.id, owner_type=OwnerType.CREATOR, user_id=subject.id, is_primary=True)
    )
    await async_session.flush()
    case.status = HandoverStatus.APPROVED
    await async_session.flush()

    with pytest.raises(HTTPException) as exc:
        await analyze_handover(case.id, async_session, admin)
    assert exc.value.status_code == 409
    assert case.status == HandoverStatus.APPROVED


async def test_analyze_allows_draft_source_status(async_session):
    """HANDOVER-04 正常路径:DRAFT → analyze 仍生成建议并进入 PENDING_APPROVAL。"""
    admin = await _make_user(async_session, username="admin-fsm-analyze-ok", system_role=SystemRole.ADMIN)
    subject = await _make_user(async_session, username="subject-fsm-analyze-ok")
    case = await _make_case(async_session, admin, subject_user_id=subject.id)
    asset = await _seed_asset(async_session, name="fsm-analyze-ok-asset")
    async_session.add(
        AssetOwnership(asset_id=asset.id, owner_type=OwnerType.CREATOR, user_id=subject.id, is_primary=True)
    )
    await async_session.flush()
    items = await analyze_handover(case.id, async_session, admin)
    assert len(items) == 1
    assert case.status == HandoverStatus.PENDING_APPROVAL


async def test_submit_rejects_illegal_source_status(async_session):
    """HANDOVER-04:case 已 APPROVED 时再 submit → 409,不被重新拉回 PENDING_APPROVAL。"""
    admin = await _make_user(async_session, username="admin-fsm-submit", system_role=SystemRole.ADMIN)
    approver = await _make_user(async_session, username="approver-fsm-submit")
    case = await _make_case(async_session, admin)
    asset = await _seed_asset(async_session, name="fsm-submit-asset")
    await create_handover_item(
        case.id,
        HandoverItemCreate(asset_id=asset.id, recommended_action=HandoverAction.TRANSFER_OWNER),
        async_session,
        admin,
    )
    case.status = HandoverStatus.APPROVED
    await async_session.flush()

    with pytest.raises(HTTPException) as exc:
        await submit_handover(
            case.id,
            [ApprovalTaskCreate(approval_type=ApprovalType.MANAGER, approver_user_id=approver.id)],
            async_session,
            admin,
        )
    assert exc.value.status_code == 409
    assert case.status == HandoverStatus.APPROVED


async def test_submit_allows_draft_source_status(async_session):
    """HANDOVER-04 正常路径:DRAFT + 有 item → submit 仍落 PENDING_APPROVAL。"""
    admin = await _make_user(async_session, username="admin-fsm-submit-ok", system_role=SystemRole.ADMIN)
    approver = await _make_user(async_session, username="approver-fsm-submit-ok")
    case = await _make_case(async_session, admin)
    asset = await _seed_asset(async_session, name="fsm-submit-ok-asset")
    await create_handover_item(
        case.id,
        HandoverItemCreate(asset_id=asset.id, recommended_action=HandoverAction.TRANSFER_OWNER),
        async_session,
        admin,
    )

    tasks = await submit_handover(
        case.id,
        [ApprovalTaskCreate(approval_type=ApprovalType.MANAGER, approver_user_id=approver.id)],
        async_session,
        admin,
    )
    assert len(tasks) == 1
    assert case.status == HandoverStatus.PENDING_APPROVAL


# --- HANDOVER-05 验收前必须确认失败动作/交接项 ---


async def _executing_case_with_failed_action(session, admin, *, suffix, receiver_user_id=None):
    """造一条 EXECUTING → 回执 FAILED → VERIFYING 的交接链,返回 (case, action)。"""
    approver = await _make_user(session, username=f"approver-{suffix}")
    case = await _make_case(session, admin, receiver_user_id=receiver_user_id)
    asset = await _seed_asset(session, name=f"asset-{suffix}")
    await create_handover_item(
        case.id,
        HandoverItemCreate(asset_id=asset.id, recommended_action=HandoverAction.TRANSFER_OWNER),
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
    actions = await execute_handover(case.id, ExecuteHandoverRequest(), session, admin)
    action = actions[0]
    await complete_execution_action(
        case.id,
        action.id,
        ExecutionReceipt(result=ExecutionStatus.FAILED, note="厂商侧执行失败"),
        session,
        admin,
    )
    assert case.status == HandoverStatus.VERIFYING
    assert action.status == ExecutionStatus.FAILED
    return case, action


async def test_verify_rejects_failed_actions_without_acknowledge(async_session):
    """HANDOVER-05:存在 FAILED 动作/项且未确认 → 验收被拒,detail 列出失败 id,case 仍 VERIFYING。"""
    admin = await _make_user(async_session, username="admin-h05-reject", system_role=SystemRole.ADMIN)
    case, action = await _executing_case_with_failed_action(async_session, admin, suffix="h05-reject")

    with pytest.raises(HTTPException) as exc:
        await verify_handover(case.id, HandoverVerifyRequest(note="收到"), async_session, admin)

    assert exc.value.status_code == 409
    assert action.id in exc.value.detail["failed_action_ids"]
    item = (
        await async_session.execute(select(HandoverItem).where(HandoverItem.handover_case_id == case.id))
    ).scalar_one()
    assert item.id in exc.value.detail["failed_item_ids"]
    assert case.status == HandoverStatus.VERIFYING  # 未带病归档


async def test_verify_completes_failed_case_with_acknowledge(async_session):
    """HANDOVER-05:显式 acknowledge_failures=True → 带病归档为 COMPLETED。"""
    admin = await _make_user(async_session, username="admin-h05-ack", system_role=SystemRole.ADMIN)
    case, _ = await _executing_case_with_failed_action(async_session, admin, suffix="h05-ack")

    completed = await verify_handover(
        case.id,
        HandoverVerifyRequest(note="已知失败,人工补救后归档", acknowledge_failures=True),
        async_session,
        admin,
    )
    assert completed.status == HandoverStatus.COMPLETED


async def test_verify_all_success_path_unaffected(async_session):
    """HANDOVER-05 回归:全部成功时 acknowledge_failures 默认 False 仍能 COMPLETED。"""
    admin = await _make_user(async_session, username="admin-h05-ok", system_role=SystemRole.ADMIN)
    case, action = await _executing_case_with_action(async_session, admin, suffix="h05-ok")
    await complete_execution_action(
        case.id,
        action.id,
        ExecutionReceipt(result=ExecutionStatus.SUCCEEDED, note="已完成"),
        async_session,
        admin,
    )
    assert case.status == HandoverStatus.VERIFYING

    completed = await verify_handover(case.id, HandoverVerifyRequest(note="确认无误"), async_session, admin)
    assert completed.status == HandoverStatus.COMPLETED


# --- FR-009-EVIDENCE-LINK 分析生成的交接项链接证据 ---


async def test_analyze_links_generated_items_to_evidence(async_session):
    """FR-009-EVIDENCE-LINK:analyze 生成的每条 HandoverItem 必须有非空 evidence_id,
    且指向一条真实存在的 EvidenceItem(分析阶段溯源证据)。"""
    admin = await _make_user(async_session, username="admin-fr009", system_role=SystemRole.ADMIN)
    subject = await _make_user(async_session, username="subject-fr009")
    case = await _make_case(async_session, admin, subject_user_id=subject.id)
    asset = await _seed_asset(async_session, name="FR009 Workflow")
    async_session.add(
        AssetOwnership(asset_id=asset.id, owner_type=OwnerType.CREATOR, user_id=subject.id, is_primary=True)
    )
    await async_session.flush()
    items = await analyze_handover(case.id, async_session, admin)

    assert len(items) == 1
    item = items[0]
    assert item.evidence_id is not None
    evidence = await async_session.get(EvidenceItem, item.evidence_id)
    assert evidence is not None
    assert evidence.id == item.evidence_id
