"""Durable, content-free security audit records for rejected v2 writes."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.audit_service import audit
from app.services.reporter_identity_service import ReporterExecutionIdentity


FORGED_GOVERNANCE_FIELDS = frozenset(
    {
        "namespace_id",
        "runtime_id",
        "runtime_instance_id",
        "trust_level",
        "trust_source",
    }
)


async def persist_execution_rejection_audit(
    db: AsyncSession,
    *,
    identity: ReporterExecutionIdentity,
    action: str,
    operation: str,
    resource_public_id: str | None = None,
    rejected_fields: Iterable[str] = (),
    reason_code: str,
    requested_trust_level: str | None = None,
) -> None:
    """Rollback the rejected write, then commit only its sanitized audit row.

    FastAPI's normal database dependency rolls a request transaction back when
    an endpoint raises. Persisting the denial after an explicit rollback keeps
    the security evidence durable without committing partial lifecycle writes.
    """

    fields = sorted(set(rejected_fields))
    details: dict[str, object] = {
        "credential_id": identity.credential_id,
        "operation": operation,
        "reason_code": reason_code,
        "runtime_id": identity.runtime_id,
    }
    if fields:
        details["rejected_fields"] = fields
    if resource_public_id is not None:
        details["resource_public_id"] = resource_public_id
    if requested_trust_level is not None:
        details["requested_trust_level"] = requested_trust_level
        details["effective_trust_level"] = identity.trust_level.value

    await db.rollback()
    await audit(
        db,
        user_id=identity.actor_user_id,
        username=None
        if identity.actor_user_id is not None
        else "system:reporter",
        action=action,
        resource_type="reporter_credential",
        resource_id=identity.credential_id,
        namespace_id=identity.namespace_id,
        details=details,
    )
    await db.commit()
