"""Idempotent tenant backfill and audit reporting for Foundation S1-B."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol, TypeAlias, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.control_plane import AIAsset, EvidenceItem, RuntimeBinding, RuntimeInstance, WorkTrace
from app.services.tenant_resolution_service import (
    TenantEntityType,
    TenantResolutionResult,
    TenantResolutionRule,
    TenantResolutionService,
    TenantResolutionStatus,
)


_TargetModel: TypeAlias = type[AIAsset] | type[RuntimeInstance] | type[RuntimeBinding] | type[WorkTrace] | type[EvidenceItem]
REPORT_SCHEMA_VERSION = 1


class TenantResolver(Protocol):
    async def resolve(
        self,
        db: AsyncSession,
        *,
        target_type: TenantEntityType,
        target_id: int,
    ) -> TenantResolutionResult: ...


@dataclass(frozen=True, slots=True)
class BackfillCheckpoint:
    """Exclusive cursor for resuming a bounded backfill run."""

    target_type: TenantEntityType
    last_id: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "target_type", TenantEntityType(self.target_type))
        if self.last_id < 0:
            raise ValueError("checkpoint last_id must be zero or greater")

    def to_dict(self) -> dict[str, object]:
        return {"target_type": self.target_type.value, "last_id": self.last_id}

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> BackfillCheckpoint:
        try:
            target_type = TenantEntityType(str(payload["target_type"]))
            last_id = int(str(payload["last_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid tenant backfill checkpoint") from exc
        return cls(target_type=target_type, last_id=last_id)


@dataclass(frozen=True, slots=True)
class TenantBackfillFinding:
    target_type: TenantEntityType
    table: str
    target_id: int
    status: TenantResolutionStatus
    namespace_id: int | None
    resolution_rule: TenantResolutionRule | None
    candidates: tuple[int, ...]
    reason: str
    updated: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "target": self.target_type.value,
            "table": self.table,
            "id": self.target_id,
            "status": self.status.value,
            "namespace_id": self.namespace_id,
            "resolution_rule": self.resolution_rule.value if self.resolution_rule is not None else None,
            "candidates": list(self.candidates),
            "reason": self.reason,
            "updated": self.updated,
        }


@dataclass(frozen=True, slots=True)
class TenantBackfillReport:
    dry_run: bool
    findings: tuple[TenantBackfillFinding, ...]
    checkpoint: BackfillCheckpoint | None

    @property
    def scanned_count(self) -> int:
        return len(self.findings)

    @property
    def updated_count(self) -> int:
        return sum(finding.updated for finding in self.findings)

    @property
    def resolved_count(self) -> int:
        return sum(finding.status is TenantResolutionStatus.RESOLVED for finding in self.findings)

    @property
    def unresolved_count(self) -> int:
        return sum(finding.status is TenantResolutionStatus.UNRESOLVED for finding in self.findings)

    @property
    def conflict_count(self) -> int:
        return sum(finding.status is TenantResolutionStatus.CONFLICT for finding in self.findings)

    @property
    def exit_code(self) -> int:
        return 2 if self.unresolved_count or self.conflict_count else 0

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "dry_run": self.dry_run,
            "scanned_count": self.scanned_count,
            "updated_count": self.updated_count,
            "resolved_count": self.resolved_count,
            "unresolved_count": self.unresolved_count,
            "conflict_count": self.conflict_count,
            "exit_code": self.exit_code,
            "checkpoint": self.checkpoint.to_dict() if self.checkpoint is not None else None,
            "findings": [finding.to_dict() for finding in self.findings],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def to_human(self) -> str:
        checkpoint = "none"
        if self.checkpoint is not None:
            checkpoint = f"{self.checkpoint.target_type.value}:{self.checkpoint.last_id}"
        lines = [
            (
                "tenant-backfill "
                f"dry_run={str(self.dry_run).lower()} "
                f"scanned={self.scanned_count} updated={self.updated_count} "
                f"resolved={self.resolved_count} unresolved={self.unresolved_count} "
                f"conflict={self.conflict_count} exit_code={self.exit_code} "
                f"checkpoint={checkpoint}"
            )
        ]
        for finding in self.findings:
            candidates = ",".join(str(value) for value in finding.candidates) or "-"
            rule = finding.resolution_rule.value if finding.resolution_rule is not None else "-"
            namespace_id = str(finding.namespace_id) if finding.namespace_id is not None else "-"
            lines.append(
                f"{finding.status.value} {finding.table} id={finding.target_id} "
                f"namespace_id={namespace_id} rule={rule} candidates={candidates} "
                f"updated={str(finding.updated).lower()} reason={finding.reason}"
            )
        return "\n".join(lines)


_TARGETS: tuple[tuple[TenantEntityType, _TargetModel, str], ...] = (
    (TenantEntityType.AI_ASSET, AIAsset, "ai_assets"),
    (TenantEntityType.RUNTIME_INSTANCE, RuntimeInstance, "runtime_instances"),
    (TenantEntityType.RUNTIME_BINDING, RuntimeBinding, "runtime_bindings"),
    (TenantEntityType.WORK_TRACE, WorkTrace, "work_traces"),
    (TenantEntityType.EVIDENCE_ITEM, EvidenceItem, "evidence_items"),
)


async def run_tenant_backfill(
    db: AsyncSession,
    *,
    resolver: TenantResolver | None = None,
    batch_size: int,
    dry_run: bool = False,
    checkpoint: BackfillCheckpoint | None = None,
) -> TenantBackfillReport:
    """Scan at most ``batch_size`` null tenant rows and safely fill resolved rows.

    The caller owns transaction commit/rollback.  This lets the CLI commit a
    partial, safe batch even when unresolved findings produce exit code 2,
    while application callers can compose the operation into their own unit of
    work.
    """

    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")
    if checkpoint is not None and not isinstance(checkpoint, BackfillCheckpoint):
        raise ValueError("checkpoint must be a BackfillCheckpoint")

    active_resolver = resolver or TenantResolutionService()
    remaining = batch_size
    findings: list[TenantBackfillFinding] = []
    last_checkpoint = checkpoint
    start_index = 0
    if checkpoint is not None:
        start_index = next(
            index for index, (target_type, _model, _table) in enumerate(_TARGETS) if target_type is checkpoint.target_type
        )

    for index, (target_type, model, table) in enumerate(_TARGETS):
        if index < start_index or remaining <= 0:
            continue
        last_id = checkpoint.last_id if checkpoint is not None and target_type is checkpoint.target_type else 0
        target_ids = tuple(
            (
                await db.scalars(
                    select(model.id)
                    .where(model.namespace_id.is_(None), model.id > last_id)
                    .order_by(model.id)
                    .limit(remaining)
                )
            ).all()
        )
        for target_id in target_ids:
            result = await active_resolver.resolve(
                db,
                target_type=target_type,
                target_id=target_id,
            )
            _validate_resolution_result(result, target_type=target_type, target_id=target_id)
            finding = await _apply_resolution(
                db,
                model=model,
                table=table,
                result=result,
                dry_run=dry_run,
            )
            findings.append(finding)
            last_checkpoint = BackfillCheckpoint(target_type=target_type, last_id=target_id)
            remaining -= 1
            if remaining == 0:
                break

    return TenantBackfillReport(
        dry_run=dry_run,
        findings=tuple(findings),
        checkpoint=last_checkpoint,
    )


def _validate_resolution_result(
    result: TenantResolutionResult,
    *,
    target_type: TenantEntityType,
    target_id: int,
) -> None:
    if result.target_type is not target_type or result.target_id != target_id:
        raise ValueError("tenant resolver returned a result for the wrong target")
    if result.status is TenantResolutionStatus.RESOLVED:
        if result.namespace_id is None or result.rule is None:
            raise ValueError("resolved tenant result requires namespace_id and rule")
    elif result.namespace_id is not None:
        raise ValueError("unresolved/conflict tenant result cannot contain namespace_id")


async def _apply_resolution(
    db: AsyncSession,
    *,
    model: _TargetModel,
    table: str,
    result: TenantResolutionResult,
    dry_run: bool,
) -> TenantBackfillFinding:
    status = result.status
    namespace_id = result.namespace_id
    rule = result.rule
    candidates = tuple(sorted(set(result.candidate_namespace_ids)))
    reason = result.reason or ""
    updated = False

    if status is TenantResolutionStatus.RESOLVED and not dry_run:
        if namespace_id is None:
            raise ValueError("resolved tenant result requires namespace_id")
        write_result = cast(
            CursorResult[Any],
            await db.execute(
                update(model)
                .where(model.id == result.target_id, model.namespace_id.is_(None))
                .values(namespace_id=namespace_id)
            ),
        )
        updated = write_result.rowcount == 1
        if not updated:
            # MySQL's default REPEATABLE READ can otherwise return the stale
            # snapshot that was established by the batch scan.  A locking
            # current read is required to audit the value that defeated CAS.
            current_namespace_id = await db.scalar(
                select(model.namespace_id)
                .where(model.id == result.target_id)
                .with_for_update()
            )
            if current_namespace_id is None:
                status = TenantResolutionStatus.UNRESOLVED
                namespace_id = None
                rule = None
                candidates = ()
                reason = "target_disappeared_or_remained_unassigned_during_conditional_update"
            elif current_namespace_id != namespace_id:
                status = TenantResolutionStatus.CONFLICT
                candidates = tuple(sorted({current_namespace_id, namespace_id}))
                namespace_id = None
                reason = "concurrent_namespace_assignment_conflicts_with_resolution"

    return TenantBackfillFinding(
        target_type=result.target_type,
        table=table,
        target_id=result.target_id,
        status=status,
        namespace_id=namespace_id,
        resolution_rule=rule,
        candidates=candidates,
        reason=reason,
        updated=updated,
    )
