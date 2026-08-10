"""Read-only contract gate for Foundation tenant ownership."""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.control_plane import AIAsset, EvidenceItem, RuntimeBinding, RuntimeInstance, WorkTrace


@dataclass(frozen=True, slots=True)
class TenantContractReport:
    null_counts: dict[str, int]
    relationship_counts: dict[str, int]

    @property
    def total_blockers(self) -> int:
        return sum(self.null_counts.values()) + sum(self.relationship_counts.values())

    @property
    def safe(self) -> bool:
        return self.total_blockers == 0

    @property
    def exit_code(self) -> int:
        return 0 if self.safe else 2

    @property
    def remediation_command(self) -> str:
        return (
            "python scripts/remediate_foundation_tenants.py "
            "--manifest <approved-manifest.json> --dry-run --json"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "safe": self.safe,
            "total_blockers": self.total_blockers,
            "exit_code": self.exit_code,
            "null_counts": self.null_counts,
            "relationship_counts": self.relationship_counts,
            "remediation_command": self.remediation_command,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def to_human(self) -> str:
        lines = [
            (
                f"tenant-contract safe={str(self.safe).lower()} "
                f"total_blockers={self.total_blockers} exit_code={self.exit_code}"
            )
        ]
        lines.extend(f"null {key}={value}" for key, value in self.null_counts.items())
        lines.extend(
            f"relationship {key}={value}"
            for key, value in self.relationship_counts.items()
        )
        if not self.safe:
            lines.append(f"remediation: {self.remediation_command}")
        return "\n".join(lines)


async def check_tenant_contract(db: AsyncSession) -> TenantContractReport:
    duplicate_binding_keys = (
        select(
            RuntimeBinding.namespace_id,
            RuntimeBinding.runtime_id,
            RuntimeBinding.asset_id,
            RuntimeBinding.environment,
        )
        .where(RuntimeBinding.namespace_id.is_not(None))
        .group_by(
            RuntimeBinding.namespace_id,
            RuntimeBinding.runtime_id,
            RuntimeBinding.asset_id,
            RuntimeBinding.environment,
        )
        .having(func.count(RuntimeBinding.id) > 1)
        .subquery()
    )
    null_counts = {
        "runtime_instances": await _count_null(db, RuntimeInstance),
        "ai_assets": await _count_null(db, AIAsset),
        "runtime_bindings": await _count_null(db, RuntimeBinding),
        "work_traces": await _count_null(db, WorkTrace),
        "evidence_items": await _count_null(db, EvidenceItem),
    }
    relationship_counts = {
        "runtime_binding_runtime_missing_or_mismatch": await _count(
            db,
            select(func.count(RuntimeBinding.id))
            .join(RuntimeInstance, RuntimeInstance.id == RuntimeBinding.runtime_id)
            .where(
                RuntimeBinding.namespace_id.is_not(None),
                or_(
                    RuntimeInstance.namespace_id.is_(None),
                    RuntimeInstance.namespace_id != RuntimeBinding.namespace_id,
                ),
            ),
        ),
        "runtime_binding_asset_missing_or_mismatch": await _count(
            db,
            select(func.count(RuntimeBinding.id))
            .join(AIAsset, AIAsset.id == RuntimeBinding.asset_id)
            .where(
                RuntimeBinding.namespace_id.is_not(None),
                or_(
                    AIAsset.namespace_id.is_(None),
                    AIAsset.namespace_id != RuntimeBinding.namespace_id,
                ),
            ),
        ),
        "work_trace_runtime_missing_or_mismatch": await _count(
            db,
            select(func.count(WorkTrace.id))
            .join(RuntimeInstance, RuntimeInstance.id == WorkTrace.runtime_id)
            .where(
                WorkTrace.namespace_id.is_not(None),
                WorkTrace.runtime_id.is_not(None),
                or_(
                    RuntimeInstance.namespace_id.is_(None),
                    RuntimeInstance.namespace_id != WorkTrace.namespace_id,
                ),
            ),
        ),
        "work_trace_asset_missing_or_mismatch": await _count(
            db,
            select(func.count(WorkTrace.id))
            .join(AIAsset, AIAsset.id == WorkTrace.asset_id)
            .where(
                WorkTrace.namespace_id.is_not(None),
                WorkTrace.asset_id.is_not(None),
                or_(
                    AIAsset.namespace_id.is_(None),
                    AIAsset.namespace_id != WorkTrace.namespace_id,
                ),
            ),
        ),
        "evidence_work_trace_missing_or_mismatch": await _count(
            db,
            select(func.count(EvidenceItem.id))
            .join(WorkTrace, WorkTrace.id == EvidenceItem.work_trace_id)
            .where(
                EvidenceItem.namespace_id.is_not(None),
                EvidenceItem.work_trace_id.is_not(None),
                or_(
                    WorkTrace.namespace_id.is_(None),
                    WorkTrace.namespace_id != EvidenceItem.namespace_id,
                ),
            ),
        ),
        "runtime_binding_tenant_key_duplicates": await _count(
            db,
            select(func.count()).select_from(duplicate_binding_keys),
        ),
    }
    return TenantContractReport(
        null_counts=null_counts,
        relationship_counts=relationship_counts,
    )


async def _count_null(db: AsyncSession, model) -> int:
    value = await db.scalar(
        select(func.count(model.id)).where(model.namespace_id.is_(None))
    )
    return int(value or 0)


async def _count(db: AsyncSession, statement) -> int:
    value = await db.scalar(statement)
    return int(value or 0)
