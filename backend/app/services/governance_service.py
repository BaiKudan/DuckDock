from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.governance import NamespaceGovernancePolicy


DEFAULT_ALLOWED_PUBLIC_LICENSES = ["MIT", "MIT-0", "Apache-2.0", "BSD-3-Clause", "Proprietary-Internal"]


def default_policy_payload(namespace_id: int) -> dict:
    return {
        "namespace_id": namespace_id,
        "manual_review_required": False,
        "require_examples": True,
        "require_validation_spec": True,
        "require_sandbox_success": True,
        "clinic_gate_enabled": False,
        "min_clinic_score": 75.0,
        "clinic_max_age_hours": 168,
        "public_sharing_requires_approval": True,
        "public_share_default_expiry_days": 30,
        "require_license_attestation": True,
        "allowed_public_licenses": list(DEFAULT_ALLOWED_PUBLIC_LICENSES),
        "sandbox_network_mode": "registry_only",
        "sandbox_workspace_mode": "ephemeral",
        "sandbox_agent_smoke_enabled": False,
        "sandbox_agent_smoke_timeout_seconds": 45,
    }


async def get_or_create_namespace_governance(
    db: AsyncSession,
    *,
    namespace_id: int,
) -> NamespaceGovernancePolicy:
    row = await _select_namespace_governance(db, namespace_id=namespace_id)
    if row is not None:
        return row

    # 并发竞态：另一个调用可能在我们 SELECT 之后、INSERT 之前抢先写入同一 namespace_id。
    # 用 savepoint 隔离这次 INSERT，冲突时只回滚保存点（不波及外层事务里其它待提交的对象，
    # 如刚创建的 namespace）后重新 SELECT 返回已存在行；savepoint 回滚会把冲突对象退回 transient，
    # 不会污染后续 autoflush（CORR-06）。
    row = NamespaceGovernancePolicy(**default_policy_payload(namespace_id))
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except IntegrityError:
        existing = await _select_namespace_governance(db, namespace_id=namespace_id)
        if existing is None:
            raise
        return existing
    await db.refresh(row)
    return row


async def _select_namespace_governance(
    db: AsyncSession,
    *,
    namespace_id: int,
) -> NamespaceGovernancePolicy | None:
    return (
        await db.execute(
            select(NamespaceGovernancePolicy).where(NamespaceGovernancePolicy.namespace_id == namespace_id)
        )
    ).scalar_one_or_none()


def is_public_license_allowed(policy: NamespaceGovernancePolicy, license_name: str | None) -> bool:
    if not license_name:
        return False
    allowed = [item.lower() for item in (policy.allowed_public_licenses or [])]
    if not allowed:
        return True
    return license_name.strip().lower() in allowed
