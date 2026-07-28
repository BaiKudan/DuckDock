"""Application-layer tenant invariants for Foundation expand-phase writes.

Database columns intentionally remain nullable until FND-019. Every current
write path must nevertheless supply an active Namespace and prove that typed
relationships belong to that same Namespace.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.models.control_plane import (
    AIAsset,
    EvidenceItem,
    RuntimeBinding,
    RuntimeInstance,
    WorkTrace,
)
from app.models.namespace import Namespace


class TenantWriteError(ValueError):
    """Base error for a rejected tenant-scoped write."""


class MissingNamespaceError(TenantWriteError):
    """The write or one of its typed relationships has no direct Namespace."""


class InactiveNamespaceError(TenantWriteError):
    """The requested Namespace does not exist or is soft-deleted."""


class TenantMismatchError(TenantWriteError):
    """A typed relationship crosses a Namespace boundary."""


def require_namespace_id(namespace_id: int | None, *, resource: str = "write") -> int:
    if namespace_id is None:
        raise MissingNamespaceError(f"{resource} requires an explicit namespace_id")
    return int(namespace_id)


async def require_active_namespace(db, namespace_id: int | None) -> Namespace:
    resolved_id = require_namespace_id(namespace_id)
    namespace = (
        await db.execute(
            select(Namespace).where(
                Namespace.id == resolved_id,
                Namespace.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if namespace is None:
        raise InactiveNamespaceError(
            f"namespace_id={resolved_id} does not identify an active Namespace"
        )
    return namespace


def ensure_resource_namespace(
    resource: Any,
    namespace_id: int | None,
    *,
    relationship: str,
) -> int:
    expected = require_namespace_id(namespace_id)
    actual = getattr(resource, "namespace_id", None)
    if actual is None:
        raise MissingNamespaceError(
            f"{relationship} id={getattr(resource, 'id', None)} has no namespace_id"
        )
    if int(actual) != expected:
        raise TenantMismatchError(
            f"{relationship} id={getattr(resource, 'id', None)} belongs to "
            f"namespace_id={actual}, not namespace_id={expected}"
        )
    return expected


def build_runtime_binding(
    *,
    namespace_id: int | None,
    runtime: RuntimeInstance,
    asset: AIAsset,
    **values: Any,
) -> RuntimeBinding:
    resolved_id = require_namespace_id(namespace_id, resource="RuntimeBinding")
    ensure_resource_namespace(runtime, resolved_id, relationship="runtime")
    ensure_resource_namespace(asset, resolved_id, relationship="asset")
    return RuntimeBinding(
        namespace_id=resolved_id,
        runtime_id=runtime.id,
        asset_id=asset.id,
        **values,
    )


def build_work_trace(
    *,
    namespace_id: int | None,
    runtime: RuntimeInstance | None = None,
    asset: AIAsset | None = None,
    **values: Any,
) -> WorkTrace:
    resolved_id = require_namespace_id(namespace_id, resource="WorkTrace")
    if runtime is not None:
        ensure_resource_namespace(runtime, resolved_id, relationship="runtime")
    if asset is not None:
        ensure_resource_namespace(asset, resolved_id, relationship="asset")
    return WorkTrace(
        namespace_id=resolved_id,
        runtime_id=runtime.id if runtime is not None else None,
        asset_id=asset.id if asset is not None else None,
        **values,
    )


def build_evidence_item(
    *,
    namespace_id: int | None,
    work_trace: WorkTrace | None = None,
    **values: Any,
) -> EvidenceItem:
    resolved_id = require_namespace_id(namespace_id, resource="EvidenceItem")
    if work_trace is not None:
        ensure_resource_namespace(work_trace, resolved_id, relationship="work_trace")
    return EvidenceItem(
        namespace_id=resolved_id,
        work_trace_id=work_trace.id if work_trace is not None else None,
        **values,
    )
