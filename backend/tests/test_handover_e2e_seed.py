from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from sqlalchemy import select

from app.api.v1.endpoints.control_plane import complete_execution_action, decide_approval, execute_handover, verify_handover
from app.models.control_plane import ExecutionAction, ExecutionStatus, HandoverCase, HandoverStatus
from app.models.namespace import NamespaceMember, NamespaceRole
from app.models.user import SystemRole, User
from app.schemas.control_plane import ApprovalDecision, ApprovalStatus, ExecuteHandoverRequest, ExecutionReceipt, HandoverVerifyRequest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "backend" / "scripts" / "seed_handover_e2e.py"


def load_seed_module():
    spec = importlib.util.spec_from_file_location("seed_handover_e2e", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def test_handover_e2e_seed_creates_pending_case_that_can_close(async_session):
    seed = load_seed_module()

    result = await seed.seed_handover_closed_loop(
        async_session,
        seed.HandoverE2ESeedConfig(
            admin_username="seed-e2e-admin",
            admin_password="DuckDock@Seed2026!",
            run_id="unit",
        ),
    )

    admin = (await async_session.execute(select(User).where(User.username == "seed-e2e-admin"))).scalar_one()
    case = await async_session.get(HandoverCase, result.case_id)
    assert admin.system_role == SystemRole.ADMIN
    assert case is not None
    assert case.status == HandoverStatus.PENDING_APPROVAL
    admin_membership = (
        await async_session.execute(
            select(NamespaceMember).where(
                NamespaceMember.namespace_id == case.namespace_id,
                NamespaceMember.user_id == admin.id,
            )
        )
    ).scalar_one()
    assert admin_membership.role == NamespaceRole.ADMIN

    for approval_id in result.approval_ids:
        await decide_approval(
            result.case_id,
            approval_id,
            ApprovalDecision(decision=ApprovalStatus.APPROVED, comment="seed test approval"),
            async_session,
            admin,
        )
    await async_session.flush()
    await async_session.refresh(case)
    assert case.status == HandoverStatus.APPROVED

    actions = await execute_handover(
        result.case_id,
        ExecuteHandoverRequest(idempotency_key="seed-e2e-exec"),
        async_session,
        admin,
    )
    assert len(actions) == 1
    assert actions[0].requires_evidence is False

    receipt = await complete_execution_action(
        result.case_id,
        actions[0].id,
        ExecutionReceipt(
            result=ExecutionStatus.SUCCEEDED,
            note="seed test receipt",
            idempotency_key="seed-e2e-receipt",
        ),
        async_session,
        admin,
    )
    assert receipt.status == ExecutionStatus.SUCCEEDED
    assert receipt.evidence_ids

    completed = await verify_handover(
        result.case_id,
        HandoverVerifyRequest(note="seed test verification"),
        async_session,
        admin,
    )
    assert completed.status == HandoverStatus.COMPLETED
    action_rows = (
        await async_session.execute(select(ExecutionAction).where(ExecutionAction.handover_case_id == result.case_id))
    ).scalars().all()
    assert [row.status for row in action_rows] == [ExecutionStatus.SUCCEEDED]
