"""Deterministic tenant resolution for the DuckDock 2.0 expand phase.

The resolver is intentionally read-only and fail-closed when inferring a
missing Namespace.  It accepts only typed database relationships from the
Foundation data-model contract; weak signals such as memberships, names, URLs
and JSON metadata are never read.  Persistence, batching and checkpointing
belong to the backfill service.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeAlias, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.control_plane import (
    AIAsset,
    AssetOwnership,
    EvidenceItem,
    RuntimeBinding,
    RuntimeInstance,
    WorkTrace,
)
from app.models.namespace import Namespace


class TenantEntityType(str, Enum):
    """Legacy control-plane entities covered by the tenant expand phase."""

    RUNTIME_INSTANCE = "runtime_instance"
    AI_ASSET = "ai_asset"
    RUNTIME_BINDING = "runtime_binding"
    WORK_TRACE = "work_trace"
    EVIDENCE_ITEM = "evidence_item"


class TenantResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    CONFLICT = "conflict"


class TenantResolutionRule(str, Enum):
    """Stable audit identifiers for the approved resolution evidence classes."""

    DIRECT_NAMESPACE = "direct_namespace"
    VERIFIED_TYPED_SOURCE = "verified_typed_source"
    ASSET_OWNERSHIP = "asset_ownership"
    RUNTIME_BOUND_ASSETS = "runtime_bound_assets"
    BINDING_RELATIONS = "binding_relations"
    WORK_TRACE_ASSET = "work_trace_asset"
    WORK_TRACE_RUNTIME = "work_trace_runtime"
    WORK_TRACE_RELATIONS = "work_trace_relations"
    EVIDENCE_WORK_TRACE = "evidence_work_trace"


@dataclass(frozen=True, slots=True)
class TenantResolutionResult:
    """One deterministic, serializable resolution decision."""

    target_type: TenantEntityType
    target_id: int
    status: TenantResolutionStatus
    namespace_id: int | None
    rule: TenantResolutionRule | None
    candidate_namespace_ids: tuple[int, ...]
    reason: str

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe representation used by the audit command."""

        return {
            "target_type": self.target_type.value,
            "target_id": self.target_id,
            "status": self.status.value,
            "namespace_id": self.namespace_id,
            "rule": self.rule.value if self.rule is not None else None,
            "candidate_namespace_ids": list(self.candidate_namespace_ids),
            "reason": self.reason,
        }


_TargetModel: TypeAlias = type[RuntimeInstance] | type[AIAsset] | type[RuntimeBinding] | type[WorkTrace] | type[EvidenceItem]


class TenantResolutionService:
    """Resolve one entity without mutating it or the surrounding transaction."""

    async def resolve(
        self,
        db: AsyncSession,
        *,
        target_type: TenantEntityType,
        target_id: int,
    ) -> TenantResolutionResult:
        entity_type = TenantEntityType(target_type)
        cache: dict[tuple[TenantEntityType, int], TenantResolutionResult] = {}
        return await self._resolve(db, target_type=entity_type, target_id=target_id, cache=cache)

    async def _resolve(
        self,
        db: AsyncSession,
        *,
        target_type: TenantEntityType,
        target_id: int,
        cache: dict[tuple[TenantEntityType, int], TenantResolutionResult],
    ) -> TenantResolutionResult:
        key = (target_type, target_id)
        cached = cache.get(key)
        if cached is not None:
            return cached

        model = self._model_for(target_type)
        target = cast(Any, await db.get(model, target_id))
        if target is None:
            result = self._unresolved(target_type, target_id, "target_not_found")
        else:
            direct = await self._resolve_direct(db, target_type, target_id, target.namespace_id)
            if direct is not None:
                result = direct
            elif target.namespace_id is not None:
                result = self._unresolved(target_type, target_id, "direct_namespace_is_missing")
            elif target_type is TenantEntityType.AI_ASSET:
                result = await self._resolve_asset(db, target, cache)
            elif target_type is TenantEntityType.RUNTIME_INSTANCE:
                result = await self._resolve_runtime(db, target, cache)
            elif target_type is TenantEntityType.RUNTIME_BINDING:
                result = await self._resolve_binding(db, target, cache)
            elif target_type is TenantEntityType.WORK_TRACE:
                result = await self._resolve_work_trace(db, target, cache)
            else:
                result = await self._resolve_evidence(db, target, cache)

        cache[key] = result
        return result

    async def _resolve_direct(
        self,
        db: AsyncSession,
        target_type: TenantEntityType,
        target_id: int,
        namespace_id: int | None,
    ) -> TenantResolutionResult | None:
        if namespace_id is None:
            return None
        if not await self._namespace_exists(db, namespace_id):
            return None
        return self._resolved(
            target_type,
            target_id,
            namespace_id,
            TenantResolutionRule.DIRECT_NAMESPACE,
            "valid_direct_namespace",
        )

    async def _resolve_asset(
        self,
        db: AsyncSession,
        asset: AIAsset,
        cache: dict[tuple[TenantEntityType, int], TenantResolutionResult],
    ) -> TenantResolutionResult:
        del cache  # AssetOwnership is terminal evidence and cannot recurse.
        namespace_ids = tuple(
            namespace_id
            for namespace_id in (
                await db.scalars(
                    select(AssetOwnership.namespace_id)
                    .where(
                        AssetOwnership.asset_id == asset.id,
                        AssetOwnership.namespace_id.is_not(None),
                    )
                    .distinct()
                    .order_by(AssetOwnership.namespace_id)
                )
            ).all()
            if namespace_id is not None
        )
        if not namespace_ids:
            return self._unresolved(TenantEntityType.AI_ASSET, asset.id, "no_supported_tenant_evidence")
        if not await self._all_namespaces_exist(db, namespace_ids):
            return self._unresolved(
                TenantEntityType.AI_ASSET,
                asset.id,
                "asset_ownership_references_missing_namespace",
            )
        if len(namespace_ids) == 1:
            return self._resolved(
                TenantEntityType.AI_ASSET,
                asset.id,
                namespace_ids[0],
                TenantResolutionRule.ASSET_OWNERSHIP,
                "exactly_one_asset_ownership_namespace",
            )
        return self._conflict(
            TenantEntityType.AI_ASSET,
            asset.id,
            TenantResolutionRule.ASSET_OWNERSHIP,
            namespace_ids,
            "asset_ownership_namespaces_disagree",
        )

    async def _resolve_runtime(
        self,
        db: AsyncSession,
        runtime: RuntimeInstance,
        cache: dict[tuple[TenantEntityType, int], TenantResolutionResult],
    ) -> TenantResolutionResult:
        asset_ids = tuple(
            (
                await db.scalars(
                    select(RuntimeBinding.asset_id)
                    .where(RuntimeBinding.runtime_id == runtime.id)
                    .distinct()
                    .order_by(RuntimeBinding.asset_id)
                )
            ).all()
        )
        if not asset_ids:
            return self._unresolved(TenantEntityType.RUNTIME_INSTANCE, runtime.id, "runtime_has_no_bound_assets")

        resolutions = [
            await self._resolve(
                db,
                target_type=TenantEntityType.AI_ASSET,
                target_id=asset_id,
                cache=cache,
            )
            for asset_id in asset_ids
        ]
        conflicts = [result for result in resolutions if result.status is TenantResolutionStatus.CONFLICT]
        if conflicts:
            return self._conflict(
                TenantEntityType.RUNTIME_INSTANCE,
                runtime.id,
                TenantResolutionRule.RUNTIME_BOUND_ASSETS,
                self._candidate_union(resolutions),
                "one_or_more_bound_assets_have_conflicting_namespaces",
            )
        if any(result.status is TenantResolutionStatus.UNRESOLVED for result in resolutions):
            return self._unresolved(
                TenantEntityType.RUNTIME_INSTANCE,
                runtime.id,
                "one_or_more_bound_assets_are_unresolved",
            )

        namespace_ids = self._resolved_namespace_ids(resolutions)
        if len(namespace_ids) == 1:
            return self._resolved(
                TenantEntityType.RUNTIME_INSTANCE,
                runtime.id,
                namespace_ids[0],
                TenantResolutionRule.RUNTIME_BOUND_ASSETS,
                "all_bound_assets_agree",
            )
        return self._conflict(
            TenantEntityType.RUNTIME_INSTANCE,
            runtime.id,
            TenantResolutionRule.RUNTIME_BOUND_ASSETS,
            namespace_ids,
            "bound_asset_namespaces_disagree",
        )

    async def _resolve_binding(
        self,
        db: AsyncSession,
        binding: RuntimeBinding,
        cache: dict[tuple[TenantEntityType, int], TenantResolutionResult],
    ) -> TenantResolutionResult:
        runtime_result = await self._resolve(
            db,
            target_type=TenantEntityType.RUNTIME_INSTANCE,
            target_id=binding.runtime_id,
            cache=cache,
        )
        asset_result = await self._resolve(
            db,
            target_type=TenantEntityType.AI_ASSET,
            target_id=binding.asset_id,
            cache=cache,
        )
        related = (runtime_result, asset_result)
        conflicts = [result for result in related if result.status is TenantResolutionStatus.CONFLICT]
        if conflicts:
            candidates = self._candidate_union((*conflicts, *related))
            if len(candidates) >= 2:
                return self._conflict(
                    TenantEntityType.RUNTIME_BINDING,
                    binding.id,
                    TenantResolutionRule.BINDING_RELATIONS,
                    candidates,
                    "runtime_or_asset_has_conflicting_namespaces",
                )
        if any(result.status is not TenantResolutionStatus.RESOLVED for result in related):
            return self._unresolved(
                TenantEntityType.RUNTIME_BINDING,
                binding.id,
                "runtime_and_asset_must_both_resolve",
            )

        namespace_ids = self._resolved_namespace_ids(related)
        if len(namespace_ids) == 1:
            return self._resolved(
                TenantEntityType.RUNTIME_BINDING,
                binding.id,
                namespace_ids[0],
                TenantResolutionRule.BINDING_RELATIONS,
                "runtime_and_asset_agree",
            )
        return self._conflict(
            TenantEntityType.RUNTIME_BINDING,
            binding.id,
            TenantResolutionRule.BINDING_RELATIONS,
            namespace_ids,
            "runtime_and_asset_namespaces_disagree",
        )

    async def _resolve_work_trace(
        self,
        db: AsyncSession,
        trace: WorkTrace,
        cache: dict[tuple[TenantEntityType, int], TenantResolutionResult],
    ) -> TenantResolutionResult:
        asset_result = None
        runtime_result = None
        if trace.asset_id is not None:
            asset_result = await self._resolve(
                db,
                target_type=TenantEntityType.AI_ASSET,
                target_id=trace.asset_id,
                cache=cache,
            )
        if trace.runtime_id is not None:
            runtime_result = await self._resolve(
                db,
                target_type=TenantEntityType.RUNTIME_INSTANCE,
                target_id=trace.runtime_id,
                cache=cache,
            )

        related = tuple(result for result in (asset_result, runtime_result) if result is not None)
        conflicts = [result for result in related if result.status is TenantResolutionStatus.CONFLICT]
        if conflicts:
            candidates = self._candidate_union((*conflicts, *related))
            if len(candidates) >= 2:
                return self._conflict(
                    TenantEntityType.WORK_TRACE,
                    trace.id,
                    TenantResolutionRule.WORK_TRACE_RELATIONS,
                    candidates,
                    "asset_or_runtime_has_conflicting_namespaces",
                )

        if asset_result is not None and asset_result.status is TenantResolutionStatus.RESOLVED:
            if runtime_result is not None and runtime_result.status is TenantResolutionStatus.RESOLVED:
                if asset_result.namespace_id != runtime_result.namespace_id:
                    return self._conflict(
                        TenantEntityType.WORK_TRACE,
                        trace.id,
                        TenantResolutionRule.WORK_TRACE_RELATIONS,
                        (asset_result.namespace_id, runtime_result.namespace_id),
                        "asset_and_runtime_namespaces_disagree",
                    )
            return self._resolved(
                TenantEntityType.WORK_TRACE,
                trace.id,
                asset_result.namespace_id,
                TenantResolutionRule.WORK_TRACE_ASSET,
                "resolved_asset_namespace",
            )

        if (
            trace.asset_id is None
            and runtime_result is not None
            and runtime_result.status is TenantResolutionStatus.RESOLVED
        ):
            return self._resolved(
                TenantEntityType.WORK_TRACE,
                trace.id,
                runtime_result.namespace_id,
                TenantResolutionRule.WORK_TRACE_RUNTIME,
                "resolved_runtime_without_conflicting_relation",
            )
        return self._unresolved(TenantEntityType.WORK_TRACE, trace.id, "no_resolved_asset_or_runtime")

    async def _resolve_evidence(
        self,
        db: AsyncSession,
        evidence: EvidenceItem,
        cache: dict[tuple[TenantEntityType, int], TenantResolutionResult],
    ) -> TenantResolutionResult:
        if evidence.work_trace_id is None:
            return self._unresolved(
                TenantEntityType.EVIDENCE_ITEM,
                evidence.id,
                "no_typed_work_trace_link",
            )
        trace_result = await self._resolve(
            db,
            target_type=TenantEntityType.WORK_TRACE,
            target_id=evidence.work_trace_id,
            cache=cache,
        )
        if trace_result.status is TenantResolutionStatus.RESOLVED:
            return self._resolved(
                TenantEntityType.EVIDENCE_ITEM,
                evidence.id,
                trace_result.namespace_id,
                TenantResolutionRule.EVIDENCE_WORK_TRACE,
                "typed_work_trace_resolved",
            )
        if trace_result.status is TenantResolutionStatus.CONFLICT:
            return self._conflict(
                TenantEntityType.EVIDENCE_ITEM,
                evidence.id,
                TenantResolutionRule.EVIDENCE_WORK_TRACE,
                trace_result.candidate_namespace_ids,
                "typed_work_trace_has_conflicting_namespaces",
            )
        return self._unresolved(
            TenantEntityType.EVIDENCE_ITEM,
            evidence.id,
            "typed_work_trace_is_unresolved",
        )

    async def _namespace_exists(self, db: AsyncSession, namespace_id: int) -> bool:
        namespace = await db.get(Namespace, namespace_id)
        # Namespace uses soft deletion.  The row remains the authoritative
        # historical tenant identity even when it is no longer active for new
        # writes; FND-018 owns active-Namespace authorization for new writes.
        return namespace is not None

    async def _all_namespaces_exist(self, db: AsyncSession, namespace_ids: tuple[int, ...]) -> bool:
        for namespace_id in namespace_ids:
            if not await self._namespace_exists(db, namespace_id):
                return False
        return True

    @staticmethod
    def _model_for(target_type: TenantEntityType) -> _TargetModel:
        model = {
            TenantEntityType.RUNTIME_INSTANCE: RuntimeInstance,
            TenantEntityType.AI_ASSET: AIAsset,
            TenantEntityType.RUNTIME_BINDING: RuntimeBinding,
            TenantEntityType.WORK_TRACE: WorkTrace,
            TenantEntityType.EVIDENCE_ITEM: EvidenceItem,
        }[target_type]
        return cast(_TargetModel, model)

    @staticmethod
    def _resolved_namespace_ids(results: tuple[TenantResolutionResult, ...] | list[TenantResolutionResult]) -> tuple[int, ...]:
        return tuple(sorted({result.namespace_id for result in results if result.namespace_id is not None}))

    @staticmethod
    def _candidate_union(results: tuple[TenantResolutionResult, ...] | list[TenantResolutionResult]) -> tuple[int, ...]:
        candidates: set[int] = set()
        for result in results:
            candidates.update(result.candidate_namespace_ids)
            if result.namespace_id is not None:
                candidates.add(result.namespace_id)
        return tuple(sorted(candidates))

    @staticmethod
    def _resolved(
        target_type: TenantEntityType,
        target_id: int,
        namespace_id: int | None,
        rule: TenantResolutionRule,
        reason: str,
    ) -> TenantResolutionResult:
        if namespace_id is None:
            raise ValueError("resolved tenant result requires a namespace")
        return TenantResolutionResult(
            target_type=target_type,
            target_id=target_id,
            status=TenantResolutionStatus.RESOLVED,
            namespace_id=namespace_id,
            rule=rule,
            candidate_namespace_ids=(namespace_id,),
            reason=reason,
        )

    @staticmethod
    def _unresolved(
        target_type: TenantEntityType,
        target_id: int,
        reason: str,
    ) -> TenantResolutionResult:
        return TenantResolutionResult(
            target_type=target_type,
            target_id=target_id,
            status=TenantResolutionStatus.UNRESOLVED,
            namespace_id=None,
            rule=None,
            candidate_namespace_ids=(),
            reason=reason,
        )

    @staticmethod
    def _conflict(
        target_type: TenantEntityType,
        target_id: int,
        rule: TenantResolutionRule,
        candidates: tuple[int | None, ...],
        reason: str,
    ) -> TenantResolutionResult:
        normalized = tuple(sorted({candidate for candidate in candidates if candidate is not None}))
        if len(normalized) < 2:
            raise ValueError("conflict tenant result requires at least two namespace candidates")
        return TenantResolutionResult(
            target_type=target_type,
            target_id=target_id,
            status=TenantResolutionStatus.CONFLICT,
            namespace_id=None,
            rule=rule,
            candidate_namespace_ids=normalized,
            reason=reason,
        )
