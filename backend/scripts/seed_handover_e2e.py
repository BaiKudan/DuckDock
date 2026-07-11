#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.control_plane import (
    create_handover,
    create_handover_item,
    decide_approval,
    submit_handover,
)
from app.core.config import settings
from app.core.database import AsyncSessionLocal, engine
from app.core.security import hash_password
from app.models.control_plane import (
    AIAsset,
    AssetStatus,
    AssetType,
    Criticality,
    HandoverAction,
    HandoverCaseType,
    HandoverStatus,
    RuntimeProvider,
)
from app.models.user import AuthSource, SystemRole, User
from app.schemas.control_plane import (
    ApprovalDecision,
    ApprovalStatus,
    ApprovalTaskCreate,
    ApprovalType,
    HandoverCaseCreate,
    HandoverItemCreate,
)

DEFAULT_ADMIN_USERNAME = "e2e-p3-admin"
DEFAULT_ADMIN_PASSWORD = "DuckDock@E2E2026!"


@dataclass(frozen=True)
class HandoverE2ESeedConfig:
    admin_username: str = DEFAULT_ADMIN_USERNAME
    admin_password: str = DEFAULT_ADMIN_PASSWORD
    reset_admin_password: bool = False
    preapprove: bool = False
    run_id: str = ""


@dataclass(frozen=True)
class HandoverE2ESeedResult:
    case_id: int
    case_status: str
    admin_username: str
    subject_username: str
    receiver_username: str
    approver_username: str
    asset_id: int
    item_id: int
    approval_ids: list[int]
    run_id: str


async def seed_handover_closed_loop(
    db: AsyncSession,
    config: HandoverE2ESeedConfig,
) -> HandoverE2ESeedResult:
    """Seed one low-risk handover case for the browser closed-loop E2E."""

    run_id = config.run_id or uuid4().hex[:10]
    admin = await _ensure_user(
        db,
        username=config.admin_username,
        password=config.admin_password,
        role=SystemRole.ADMIN,
        reset_password=config.reset_admin_password or config.admin_username.startswith("e2e-"),
    )
    subject = await _ensure_user(
        db,
        username=f"e2e-p3-subject-{run_id}",
        password="DuckDock@Subject2026!",
        role=SystemRole.USER,
        reset_password=True,
    )
    receiver = await _ensure_user(
        db,
        username=f"e2e-p3-receiver-{run_id}",
        password="DuckDock@Receiver2026!",
        role=SystemRole.USER,
        reset_password=True,
    )
    approver = await _ensure_user(
        db,
        username=f"e2e-p3-approver-{run_id}",
        password="DuckDock@Approver2026!",
        role=SystemRole.USER,
        reset_password=True,
    )

    asset = AIAsset(
        asset_type=AssetType.SKILL,
        name=f"E2E P3-11 Low Risk Skill {run_id}",
        description="Low criticality seed asset for the handover closed-loop browser test.",
        source_provider=RuntimeProvider.CUSTOM,
        external_id=f"e2e-p3-11-{run_id}",
        status=AssetStatus.ACTIVE,
        criticality=Criticality.LOW,
        metadata_json={"e2e": True, "scenario": "p3-11-closed-loop", "run_id": run_id},
    )
    db.add(asset)
    await db.flush()

    case = await create_handover(
        HandoverCaseCreate(
            case_type=HandoverCaseType.EMPLOYEE_OFFBOARDING,
            title=f"E2E P3-11 closed loop {run_id}",
            subject_user_id=subject.id,
            receiver_user_id=receiver.id,
            metadata_json={"e2e": True, "scenario": "p3-11-closed-loop", "run_id": run_id},
        ),
        db,
        admin,
    )
    item = await create_handover_item(
        case.id,
        HandoverItemCreate(
            asset_id=asset.id,
            recommended_action=HandoverAction.TRANSFER_OWNER,
            receiver_user_id=receiver.id,
            risk_reason="E2E low-risk ownership transfer; no sensitive trace evidence required.",
        ),
        db,
        admin,
    )
    approvals = await submit_handover(
        case.id,
        [ApprovalTaskCreate(approval_type=ApprovalType.MANAGER, approver_user_id=approver.id)],
        db,
        admin,
    )
    if config.preapprove:
        for approval in approvals:
            await decide_approval(
                case.id,
                approval.id,
                ApprovalDecision(decision=ApprovalStatus.APPROVED, comment="E2E seed approval"),
                db,
                admin,
            )
    await db.flush()
    await db.refresh(case)
    expected_status = HandoverStatus.APPROVED if config.preapprove else HandoverStatus.PENDING_APPROVAL
    if case.status != expected_status:
        raise RuntimeError(f"Expected {expected_status.value} handover seed, got {case.status.value}")

    return HandoverE2ESeedResult(
        case_id=case.id,
        case_status=case.status.value,
        admin_username=admin.username,
        subject_username=subject.username,
        receiver_username=receiver.username,
        approver_username=approver.username,
        asset_id=asset.id,
        item_id=item.id,
        approval_ids=[approval.id for approval in approvals],
        run_id=run_id,
    )


async def _ensure_user(
    db: AsyncSession,
    *,
    username: str,
    password: str,
    role: SystemRole,
    reset_password: bool,
) -> User:
    user = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
    if user is None:
        user = User(
            username=username,
            email=f"{username}@e2e.duckdock.local",
            full_name=username.replace("-", " ").title(),
            hashed_password=hash_password(password),
            system_role=role,
            auth_source=AuthSource.LOCAL,
            is_active=True,
        )
        db.add(user)
    else:
        user.is_active = True
        user.system_role = role
        if reset_password:
            user.hashed_password = hash_password(password)
    await db.flush()
    return user


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed a pending-approval handover case for P3-11 Playwright E2E.")
    parser.add_argument("--json", action="store_true", help="Print JSON output (default).")
    parser.add_argument("--shell", action="store_true", help="Print shell exports for E2E_* variables.")
    parser.add_argument("--allow-non-debug", action="store_true", help="Allow seeding when DEBUG is false.")
    parser.add_argument("--admin-username", default=os.getenv("E2E_ADMIN_USER", DEFAULT_ADMIN_USERNAME))
    parser.add_argument("--admin-password", default=os.getenv("E2E_ADMIN_PASS", DEFAULT_ADMIN_PASSWORD))
    parser.add_argument("--reset-admin-password", action="store_true", default=os.getenv("E2E_RESET_ADMIN_PASSWORD") == "1")
    parser.add_argument(
        "--preapprove",
        action="store_true",
        default=os.getenv("E2E_PREAPPROVE") == "1",
        help="Advance the seed to approved instead of leaving approval for the browser.",
    )
    parser.add_argument("--run-id", default=os.getenv("E2E_RUN_ID", ""))
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    engine.echo = False
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    if not settings.DEBUG and not args.allow_non_debug and os.getenv("E2E_ALLOW_SEED") != "1":
        raise SystemExit(
            "Refusing to seed handover E2E data while DEBUG=false. "
            "Use DEBUG=true for dev/test, or pass --allow-non-debug intentionally."
        )

    config = HandoverE2ESeedConfig(
        admin_username=args.admin_username,
        admin_password=args.admin_password,
        reset_admin_password=args.reset_admin_password,
        preapprove=args.preapprove,
        run_id=args.run_id,
    )
    async with AsyncSessionLocal() as db:
        result = await seed_handover_closed_loop(db, config)
        await db.commit()

    if args.shell:
        print("export E2E_SEEDED=1")
        print(f"export E2E_CASE_ID={result.case_id}")
        print(f"export E2E_ADMIN_USER={json.dumps(result.admin_username)}")
    else:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))
