"""
AuditService — 轻量级操作审计

用法：
    from app.services.audit_service import audit
    await audit(db, user=current_user, action="skill.published",
                resource_type="skill_version", resource_id=version.id,
                namespace_id=ns.id, details={"tag": body.tag})
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog
from app.models.user import User


logger = logging.getLogger(__name__)


async def audit(
    db: AsyncSession,
    *,
    action: str,
    user: User | None = None,
    user_id: int | None = None,
    username: str | None = None,
    resource_type: str | None = None,
    resource_id: int | None = None,
    namespace_id: int | None = None,
    details: dict | None = None,
    ip_address: str | None = None,
) -> None:
    """Write a single audit log entry. Fire-and-forget — never raises."""
    try:
        uid = user_id
        uname = username
        if user is not None:
            robot = getattr(user, "_robot", None)
            if robot is not None or (getattr(user, "id", None) is not None and user.id <= 0):
                uid = None
                uname = user.username
            else:
                uid = user.id
                uname = user.username
        log = AuditLog(
            user_id=uid,
            username=uname,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            namespace_id=namespace_id,
            details=details,
            ip_address=ip_address,
        )
        db.add(log)
        # We intentionally do NOT commit here — the caller's transaction commits it
    except Exception:
        # Audit remains best-effort for callers, but a write-path failure must
        # still be visible to operators. Avoid logging details because they may
        # contain user-provided or security-sensitive fields.
        logger.exception("Failed to stage audit log entry")
