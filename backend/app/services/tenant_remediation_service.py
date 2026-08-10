"""Explicit, atomic remediation for unresolved Foundation tenant ownership."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeAlias

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog
from app.models.control_plane import AIAsset, EvidenceItem, RuntimeBinding, RuntimeInstance, WorkTrace
from app.models.namespace import Namespace
from app.models.user import SystemRole, User
from app.schemas.tenant_remediation import TenantRemediationManifest
from app.services.tenant_resolution_service import TenantEntityType


class TenantRemediationError(ValueError):
    """The explicit decision is unauthorized, incomplete, or inconsistent."""


class TenantRemediationFindingStatus(str, Enum):
    WOULD_ASSIGN = "would_assign"
    ASSIGNED = "assigned"
    ALREADY_ASSIGNED = "already_assigned"


_TargetModel: TypeAlias = type[AIAsset] | type[RuntimeInstance] | type[RuntimeBinding] | type[WorkTrace] | type[EvidenceItem]
_TARGET_MODELS: dict[TenantEntityType, _TargetModel] = {
    TenantEntityType.AI_ASSET: AIAsset,
    TenantEntityType.RUNTIME_INSTANCE: RuntimeInstance,
    TenantEntityType.RUNTIME_BINDING: RuntimeBinding,
    TenantEntityType.WORK_TRACE: WorkTrace,
    TenantEntityType.EVIDENCE_ITEM: EvidenceItem,
}


@dataclass(frozen=True, slots=True)
class TenantRemediationFinding:
    target_type: TenantEntityType
    target_id: int
    namespace_id: int
    status: TenantRemediationFindingStatus

    def to_dict(self) -> dict[str, object]:
        return {
            "target_type": self.target_type.value,
            "target_id": self.target_id,
            "namespace_id": self.namespace_id,
            "status": self.status.value,
        }


@dataclass(frozen=True, slots=True)
class TenantRemediationReport:
    manifest_id: str
    manifest_sha256: str
    change_ticket: str
    dry_run: bool
    findings: tuple[TenantRemediationFinding, ...]

    @property
    def assignment_count(self) -> int:
        return len(self.findings)

    @property
    def would_assign_count(self) -> int:
        return sum(item.status is TenantRemediationFindingStatus.WOULD_ASSIGN for item in self.findings)

    @property
    def changed_count(self) -> int:
        return sum(item.status is TenantRemediationFindingStatus.ASSIGNED for item in self.findings)

    @property
    def replayed_count(self) -> int:
        return sum(item.status is TenantRemediationFindingStatus.ALREADY_ASSIGNED for item in self.findings)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "manifest_id": self.manifest_id,
            "manifest_sha256": self.manifest_sha256,
            "change_ticket": self.change_ticket,
            "dry_run": self.dry_run,
            "assignment_count": self.assignment_count,
            "would_assign_count": self.would_assign_count,
            "changed_count": self.changed_count,
            "replayed_count": self.replayed_count,
            "findings": [item.to_dict() for item in self.findings],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def to_human(self) -> str:
        lines = [
            (
                f"tenant-remediation manifest={self.manifest_id} dry_run={str(self.dry_run).lower()} "
                f"assignments={self.assignment_count} would_assign={self.would_assign_count} "
                f"changed={self.changed_count} replayed={self.replayed_count}"
            )
        ]
        lines.extend(
            (
                f"{item.status.value} target={item.target_type.value}:{item.target_id} "
                f"namespace_id={item.namespace_id}"
            )
            for item in self.findings
        )
        return "\n".join(lines)


async def apply_tenant_remediation(
    db: AsyncSession,
    *,
    manifest: TenantRemediationManifest,
    dry_run: bool,
) -> TenantRemediationReport:
    """Validate the complete planned graph, then atomically assign direct tenants."""

    approver = await _require_approver(db, manifest.approved_by_user_id)
    manifest_sha256 = _manifest_sha256(manifest)
    planned = {
        (item.target_type, item.target_id): item.namespace_id
        for item in manifest.assignments
    }
    cache: dict[tuple[TenantEntityType, int], Any] = {}

    namespace_ids = sorted({item.namespace_id for item in manifest.assignments})
    existing_namespace_ids = set(
        (
            await db.scalars(
                select(Namespace.id).where(Namespace.id.in_(namespace_ids))
            )
        ).all()
    )
    missing_namespace_ids = sorted(set(namespace_ids) - existing_namespace_ids)
    if missing_namespace_ids:
        raise TenantRemediationError(
            f"manifest references missing Namespace ids: {missing_namespace_ids}"
        )

    for assignment in manifest.assignments:
        target = await _target(
            db,
            target_type=assignment.target_type,
            target_id=assignment.target_id,
            cache=cache,
            for_update=not dry_run,
        )
        current = target.namespace_id
        if current is not None and int(current) != assignment.namespace_id:
            raise TenantRemediationError(
                f"{assignment.target_type.value} id={assignment.target_id} already belongs "
                f"to namespace_id={current}, refusing namespace_id={assignment.namespace_id}"
            )

    for assignment in manifest.assignments:
        await _validate_relationships(
            db,
            target_type=assignment.target_type,
            target_id=assignment.target_id,
            namespace_id=assignment.namespace_id,
            planned=planned,
            cache=cache,
        )

    findings: list[TenantRemediationFinding] = []
    for assignment in manifest.assignments:
        target = cache[(assignment.target_type, assignment.target_id)]
        if target.namespace_id is not None:
            status = TenantRemediationFindingStatus.ALREADY_ASSIGNED
        elif dry_run:
            status = TenantRemediationFindingStatus.WOULD_ASSIGN
        else:
            target.namespace_id = assignment.namespace_id
            status = TenantRemediationFindingStatus.ASSIGNED
            db.add(
                AuditLog(
                    user_id=approver.id,
                    username=approver.username,
                    action="foundation.tenant.remediated",
                    resource_type=assignment.target_type.value,
                    resource_id=assignment.target_id,
                    namespace_id=assignment.namespace_id,
                    details={
                        "manifest_id": manifest.manifest_id,
                        "manifest_sha256": manifest_sha256,
                        "change_ticket": manifest.change_ticket,
                        "approved_by_user_id": approver.id,
                        "reason": assignment.reason or manifest.reason,
                    },
                )
            )
        findings.append(
            TenantRemediationFinding(
                target_type=assignment.target_type,
                target_id=assignment.target_id,
                namespace_id=assignment.namespace_id,
                status=status,
            )
        )
    if not dry_run:
        await db.flush()
    return TenantRemediationReport(
        manifest_id=manifest.manifest_id,
        manifest_sha256=manifest_sha256,
        change_ticket=manifest.change_ticket,
        dry_run=dry_run,
        findings=tuple(findings),
    )


async def _require_approver(db: AsyncSession, user_id: int) -> User:
    user = await db.get(User, user_id)
    if (
        user is None
        or not user.is_active
        or user.system_role is not SystemRole.ADMIN
    ):
        raise TenantRemediationError(
            f"approved_by_user_id={user_id} must identify an active system admin"
        )
    return user


async def _target(
    db: AsyncSession,
    *,
    target_type: TenantEntityType,
    target_id: int,
    cache: dict[tuple[TenantEntityType, int], Any],
    for_update: bool = False,
) -> Any:
    key = (target_type, target_id)
    cached = cache.get(key)
    if cached is not None:
        return cached
    model = _TARGET_MODELS[target_type]
    statement = select(model).where(model.id == target_id)
    if for_update:
        statement = statement.with_for_update()
    row = (await db.execute(statement)).scalar_one_or_none()
    if row is None:
        raise TenantRemediationError(
            f"{target_type.value} id={target_id} does not exist"
        )
    cache[key] = row
    return row


async def _final_namespace(
    db: AsyncSession,
    *,
    target_type: TenantEntityType,
    target_id: int,
    planned: dict[tuple[TenantEntityType, int], int],
    cache: dict[tuple[TenantEntityType, int], Any],
) -> int | None:
    planned_value = planned.get((target_type, target_id))
    if planned_value is not None:
        return planned_value
    target = await _target(
        db,
        target_type=target_type,
        target_id=target_id,
        cache=cache,
    )
    return int(target.namespace_id) if target.namespace_id is not None else None


async def _validate_relationships(
    db: AsyncSession,
    *,
    target_type: TenantEntityType,
    target_id: int,
    namespace_id: int,
    planned: dict[tuple[TenantEntityType, int], int],
    cache: dict[tuple[TenantEntityType, int], Any],
) -> None:
    target = cache[(target_type, target_id)]
    if target_type is TenantEntityType.AI_ASSET:
        binding_ids = (
            await db.scalars(
                select(RuntimeBinding.id)
                .where(RuntimeBinding.asset_id == target_id)
                .order_by(RuntimeBinding.id)
            )
        ).all()
        for binding_id in binding_ids:
            await _require_related_namespace(
                db,
                related_type=TenantEntityType.RUNTIME_BINDING,
                related_id=binding_id,
                namespace_id=namespace_id,
                label="runtime_binding",
                planned=planned,
                cache=cache,
            )
        trace_ids = (
            await db.scalars(
                select(WorkTrace.id)
                .where(WorkTrace.asset_id == target_id)
                .order_by(WorkTrace.id)
            )
        ).all()
        for trace_id in trace_ids:
            await _require_related_namespace(
                db,
                related_type=TenantEntityType.WORK_TRACE,
                related_id=trace_id,
                namespace_id=namespace_id,
                label="work_trace",
                planned=planned,
                cache=cache,
            )
        return
    if target_type is TenantEntityType.RUNTIME_INSTANCE:
        binding_ids = (
            await db.scalars(
                select(RuntimeBinding.id)
                .where(RuntimeBinding.runtime_id == target_id)
                .order_by(RuntimeBinding.id)
            )
        ).all()
        for binding_id in binding_ids:
            await _require_related_namespace(
                db,
                related_type=TenantEntityType.RUNTIME_BINDING,
                related_id=binding_id,
                namespace_id=namespace_id,
                label="runtime_binding",
                planned=planned,
                cache=cache,
            )
        asset_ids = (
            await db.scalars(
                select(RuntimeBinding.asset_id)
                .where(RuntimeBinding.runtime_id == target_id)
                .distinct()
                .order_by(RuntimeBinding.asset_id)
            )
        ).all()
        for asset_id in asset_ids:
            asset_namespace = await _final_namespace(
                db,
                target_type=TenantEntityType.AI_ASSET,
                target_id=asset_id,
                planned=planned,
                cache=cache,
            )
            if asset_namespace != namespace_id:
                raise TenantRemediationError(
                    f"runtime_instance id={target_id} bound asset id={asset_id} "
                    f"must resolve to namespace_id={namespace_id}"
                )
        trace_ids = (
            await db.scalars(
                select(WorkTrace.id)
                .where(WorkTrace.runtime_id == target_id)
                .order_by(WorkTrace.id)
            )
        ).all()
        for trace_id in trace_ids:
            await _require_related_namespace(
                db,
                related_type=TenantEntityType.WORK_TRACE,
                related_id=trace_id,
                namespace_id=namespace_id,
                label="work_trace",
                planned=planned,
                cache=cache,
            )
        return
    if target_type is TenantEntityType.RUNTIME_BINDING:
        await _require_related_namespace(
            db,
            related_type=TenantEntityType.RUNTIME_INSTANCE,
            related_id=target.runtime_id,
            namespace_id=namespace_id,
            label="runtime",
            planned=planned,
            cache=cache,
        )
        await _require_related_namespace(
            db,
            related_type=TenantEntityType.AI_ASSET,
            related_id=target.asset_id,
            namespace_id=namespace_id,
            label="asset",
            planned=planned,
            cache=cache,
        )
        return
    if target_type is TenantEntityType.WORK_TRACE:
        if target.runtime_id is not None:
            await _require_related_namespace(
                db,
                related_type=TenantEntityType.RUNTIME_INSTANCE,
                related_id=target.runtime_id,
                namespace_id=namespace_id,
                label="runtime",
                planned=planned,
                cache=cache,
            )
        if target.asset_id is not None:
            await _require_related_namespace(
                db,
                related_type=TenantEntityType.AI_ASSET,
                related_id=target.asset_id,
                namespace_id=namespace_id,
                label="asset",
                planned=planned,
                cache=cache,
            )
        evidence_ids = (
            await db.scalars(
                select(EvidenceItem.id)
                .where(EvidenceItem.work_trace_id == target_id)
                .order_by(EvidenceItem.id)
            )
        ).all()
        for evidence_id in evidence_ids:
            await _require_related_namespace(
                db,
                related_type=TenantEntityType.EVIDENCE_ITEM,
                related_id=evidence_id,
                namespace_id=namespace_id,
                label="evidence_item",
                planned=planned,
                cache=cache,
            )
        return
    if target.work_trace_id is not None:
        await _require_related_namespace(
            db,
            related_type=TenantEntityType.WORK_TRACE,
            related_id=target.work_trace_id,
            namespace_id=namespace_id,
            label="work_trace",
            planned=planned,
            cache=cache,
        )


async def _require_related_namespace(
    db: AsyncSession,
    *,
    related_type: TenantEntityType,
    related_id: int,
    namespace_id: int,
    label: str,
    planned: dict[tuple[TenantEntityType, int], int],
    cache: dict[tuple[TenantEntityType, int], Any],
) -> None:
    related_namespace = await _final_namespace(
        db,
        target_type=related_type,
        target_id=related_id,
        planned=planned,
        cache=cache,
    )
    if related_namespace != namespace_id:
        raise TenantRemediationError(
            f"{label} id={related_id} must resolve to namespace_id={namespace_id}"
        )


def _manifest_sha256(manifest: TenantRemediationManifest) -> str:
    payload = json.dumps(
        manifest.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
