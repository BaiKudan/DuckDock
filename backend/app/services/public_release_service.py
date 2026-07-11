from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import ensure_utc
from app.models.governance import NamespaceGovernancePolicy
from app.models.public_release import PublicReleaseApprovalStatus, PublicSkillRelease
from app.models.skill import SkillVersion, SkillVersionStatus


async def get_public_release(
    db: AsyncSession,
    *,
    version_id: int,
) -> PublicSkillRelease | None:
    result = await db.execute(
        select(PublicSkillRelease).where(PublicSkillRelease.version_id == version_id)
    )
    return result.scalar_one_or_none()


def _expires_at_from_policy(
    *,
    policy: NamespaceGovernancePolicy | None,
    requested_days: int | None,
) -> datetime | None:
    days = requested_days
    if days is None and policy is not None:
        days = policy.public_share_default_expiry_days
    if days is None or days <= 0:
        return None
    return datetime.now(timezone.utc) + timedelta(days=days)


async def set_public_release(
    db: AsyncSession,
    *,
    version: SkillVersion,
    shared: bool,
    shared_by: int | None,
    policy: NamespaceGovernancePolicy | None = None,
    license_name: str | None = None,
    license_attested: bool = False,
    risk_acknowledged: bool = False,
    public_expires_in_days: int | None = None,
    approval_notes: str | None = None,
    approval_status: PublicReleaseApprovalStatus | None = None,
    acting_user_id: int | None = None,
) -> PublicSkillRelease | None:
    release = await get_public_release(db, version_id=version.id)
    if not shared:
        if release is not None:
            await db.delete(release)
        return None

    now = datetime.now(timezone.utc)
    desired_approval_status = approval_status
    if desired_approval_status is None:
        if policy is not None and policy.public_sharing_requires_approval:
            desired_approval_status = PublicReleaseApprovalStatus.PENDING
        else:
            desired_approval_status = PublicReleaseApprovalStatus.APPROVED

    if release is None:
        release = PublicSkillRelease(
            version_id=version.id,
            shared_by=shared_by,
        )
        db.add(release)

    release.shared_by = shared_by
    release.approval_status = desired_approval_status
    release.approval_notes = approval_notes
    release.expires_at = _expires_at_from_policy(
        policy=policy,
        requested_days=public_expires_in_days,
    )
    release.license_name = license_name.strip() if license_name else None
    release.license_attested = license_attested
    release.risk_acknowledged = risk_acknowledged

    if license_attested:
        release.license_attested_by = acting_user_id
        release.license_attested_at = now
    else:
        release.license_attested_by = None
        release.license_attested_at = None

    if risk_acknowledged:
        release.risk_acknowledged_by = acting_user_id
        release.risk_acknowledged_at = now
    else:
        release.risk_acknowledged_by = None
        release.risk_acknowledged_at = None

    if desired_approval_status == PublicReleaseApprovalStatus.APPROVED:
        release.approved_by = acting_user_id
        release.approved_at = now
    else:
        release.approved_by = None
        release.approved_at = None

    await db.flush()
    await db.refresh(release)
    return release


def is_public_release_expired(release: PublicSkillRelease | None) -> bool:
    return bool(release and release.expires_at and ensure_utc(release.expires_at) <= datetime.now(timezone.utc))


def is_public_release_pending(release: PublicSkillRelease | None) -> bool:
    if release is None:
        return False
    if is_public_release_expired(release):
        return False
    return release.approval_status == PublicReleaseApprovalStatus.PENDING


def is_publicly_available(
    version: SkillVersion,
    release: PublicSkillRelease | None,
) -> bool:
    return (
        release is not None
        and not is_public_release_expired(release)
        and release.approval_status == PublicReleaseApprovalStatus.APPROVED
        and version.status == SkillVersionStatus.PRODUCTION
    )
