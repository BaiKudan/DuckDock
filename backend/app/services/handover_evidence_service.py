from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import uuid
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.control_plane import (
    AIAsset,
    AssetOwnership,
    AssetType,
    Criticality,
    EvidenceItem,
    EvidenceSourceType,
    EvidenceVisibility,
    ExecutionAction,
    ExecutionStatus,
    HandoverCase,
    HandoverItem,
    HandoverStatus,
    RuntimeProvider,
    Sensitivity,
    WorkTrace,
)
from app.models.deployment import AgentDeployment, AgentDeploymentStatus, DeploymentComponent
from app.models.evaluation import EvaluationComparisonOutcome
from app.models.execution import AgentRun
from app.models.handover import (
    HandoverAcceptance,
    HandoverAcceptanceDecision,
    HandoverEvidenceSnapshot,
    HandoverObligation,
    HandoverObligationReceipt,
    HandoverObligationReceiptDecision,
    HandoverObligationSeverity,
    HandoverObligationType,
    HandoverReadinessOutcome,
    HandoverSignedPackage,
)
from app.models.package_registry import (
    AgentPackageVersion,
    PackageSignatureAlgorithm,
    PackageSigningKey,
    PackageSigningKeyStatus,
)
from app.models.release import ReleaseCandidateEvaluationBinding
from app.models.release_control import (
    ReleaseCandidate,
    ReleaseEnvironment,
    ReleaseEnvironmentKind,
    ReleaseEnvironmentRelease,
    ReleaseEnvironmentReleaseStatus,
)
from app.models.user import SystemRole, User
from app.schemas.handover import (
    HandoverAcceptanceCreate,
    HandoverAcceptanceOut,
    HandoverEvidenceSnapshotCreate,
    HandoverEvidenceSnapshotOut,
    HandoverObligationOut,
    HandoverObligationReceiptCreate,
    HandoverObligationReceiptOut,
    HandoverSignedPackageCreate,
    HandoverSignedPackageOut,
    HandoverSigningPayloadOut,
)
from app.services.artifact_service import artifact_service
from app.services.audit_service import audit
from app.services.handover_package_service import _put_object, _sanitize_freetext
from app.services.iam_service import effective_permissions
from app.services.package_registry_service import PackageSignatureError, _verify_signature
from app.services.outbox_event_service import (
    build_handover_acceptance_recorded,
    build_handover_obligation_receipt_recorded,
    build_handover_signed_package_created,
    build_handover_snapshot_created,
    enqueue_domain_event,
)
from app.services.tenant_write_service import build_evidence_item


HANDOVER_MANIFEST_SCHEMA = "duckdock-handover-pack-v2"
HANDOVER_MANIFEST_VERSION = "2.0"
_HIGH_RISK = {Criticality.HIGH, Criticality.CRITICAL}
_SENSITIVE = {Sensitivity.CONFIDENTIAL, Sensitivity.RESTRICTED}
_RUNBOOK_KEYS = ("runbook_url", "runbook_ref", "runbook_version")


class HandoverEvidenceError(ValueError):
    pass


class HandoverEvidenceNotFoundError(HandoverEvidenceError):
    pass


class HandoverEvidenceConflictError(HandoverEvidenceError):
    pass


class HandoverEvidenceReferenceError(HandoverEvidenceError):
    pass


class HandoverEvidenceStateError(HandoverEvidenceError):
    pass


class HandoverEvidenceTenantMismatchError(HandoverEvidenceError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _public_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _runbook(metadata: dict | None) -> dict[str, str]:
    if not isinstance(metadata, dict):
        return {}
    result: dict[str, str] = {}
    for key in _RUNBOOK_KEYS:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = (_sanitize_freetext(value.strip()) or "")[:500]
    return result


def _node_key(kind: str, ref: str | int) -> str:
    return f"{kind}:{ref}"


def _add_node(nodes: dict[str, dict], *, kind: str, ref: str | int, **fields: Any) -> str:
    key = _node_key(kind, ref)
    nodes[key] = {"key": key, "kind": kind, "ref": str(ref), **fields}
    return key


def _add_edge(edges: dict[str, dict], *, source: str, target: str, relationship: str) -> None:
    key = f"{source}|{relationship}|{target}"
    edges[key] = {
        "key": key,
        "source": source,
        "target": target,
        "relationship": relationship,
    }


def _check(
    *,
    key: str,
    passed: bool,
    reason_code: str,
    evidence_refs: list[str] | None = None,
    obligation_type: HandoverObligationType | None = None,
    severity: HandoverObligationSeverity = HandoverObligationSeverity.BLOCKING,
    title: str | None = None,
    requires_evidence: bool = False,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "key": key,
        "outcome": "PASS" if passed else "BLOCK",
        "reason_code": reason_code,
        "evidence_refs": sorted(evidence_refs or []),
    }
    if not passed and obligation_type is not None:
        row["obligation"] = {
            "type": obligation_type.value,
            "severity": severity.value,
            "title": title or reason_code,
            "requires_evidence": requires_evidence,
        }
    return row


async def _get_case(db: AsyncSession, case_id: int) -> HandoverCase:
    row = await db.get(HandoverCase, case_id)
    if row is None or row.namespace_id is None:
        raise HandoverEvidenceNotFoundError("handover case was not found")
    return row


async def get_handover_snapshot(db: AsyncSession, public_id: str) -> HandoverEvidenceSnapshot:
    row = (
        await db.execute(
            select(HandoverEvidenceSnapshot)
            .where(HandoverEvidenceSnapshot.public_id == public_id)
            .execution_options(populate_existing=True)
            .options(
                selectinload(HandoverEvidenceSnapshot.obligations).selectinload(
                    HandoverObligation.receipts
                )
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HandoverEvidenceNotFoundError("handover snapshot was not found")
    return row


async def list_handover_snapshots(
    db: AsyncSession,
    *,
    namespace_id: int | None = None,
    handover_case_id: int | None = None,
    limit: int = 100,
) -> list[HandoverEvidenceSnapshot]:
    stmt = (
        select(HandoverEvidenceSnapshot)
        .options(
            selectinload(HandoverEvidenceSnapshot.obligations).selectinload(
                HandoverObligation.receipts
            )
        )
        .order_by(HandoverEvidenceSnapshot.created_at.desc(), HandoverEvidenceSnapshot.id.desc())
        .limit(limit)
    )
    if namespace_id is not None:
        stmt = stmt.where(HandoverEvidenceSnapshot.namespace_id == namespace_id)
    if handover_case_id is not None:
        stmt = stmt.where(HandoverEvidenceSnapshot.handover_case_id == handover_case_id)
    return list((await db.execute(stmt)).scalars().all())


async def _snapshot_source(
    db: AsyncSession,
    *,
    case: HandoverCase,
) -> tuple[dict, list[dict], list[dict], dict, list[dict]]:
    namespace_id = int(case.namespace_id or 0)
    items = list(
        (
            await db.execute(
                select(HandoverItem)
                .where(HandoverItem.handover_case_id == case.id)
                .order_by(HandoverItem.id)
            )
        ).scalars().all()
    )
    asset_ids = sorted({item.asset_id for item in items})
    assets = list(
        (
            await db.execute(select(AIAsset).where(AIAsset.id.in_(asset_ids)).order_by(AIAsset.id))
        ).scalars().all()
    ) if asset_ids else []
    if len(assets) != len(asset_ids) or any(asset.namespace_id != namespace_id for asset in assets):
        raise HandoverEvidenceTenantMismatchError("handover assets must belong to the case Namespace")

    user_ids = {
        value
        for value in (case.subject_user_id, case.receiver_user_id, case.fallback_owner_user_id)
        if value is not None
    }
    users = {
        user.id: user
        for user in (
            await db.execute(select(User).where(User.id.in_(user_ids)))
        ).scalars().all()
    } if user_ids else {}
    receiver = users.get(case.receiver_user_id or 0)
    receiver_permissions = sorted(
        await effective_permissions(db, user=receiver, namespace_id=namespace_id)
    ) if receiver is not None and receiver.is_active else []

    ownerships = list(
        (
            await db.execute(
                select(AssetOwnership)
                .where(AssetOwnership.asset_id.in_(asset_ids))
                .order_by(AssetOwnership.asset_id, AssetOwnership.id)
            )
        ).scalars().all()
    ) if asset_ids else []
    if any(row.namespace_id not in {None, namespace_id} for row in ownerships):
        raise HandoverEvidenceTenantMismatchError("asset ownership must belong to the case Namespace")

    active_release_rows = list(
        (
            await db.execute(
                select(ReleaseEnvironmentRelease)
                .join(ReleaseEnvironment, ReleaseEnvironment.id == ReleaseEnvironmentRelease.environment_id)
                .where(
                    ReleaseEnvironmentRelease.namespace_id == namespace_id,
                    ReleaseEnvironmentRelease.status == ReleaseEnvironmentReleaseStatus.ACTIVE,
                    ReleaseEnvironment.kind == ReleaseEnvironmentKind.PRODUCTION,
                )
                .options(
                    selectinload(ReleaseEnvironmentRelease.candidate).selectinload(
                        ReleaseCandidate.deployment
                    ),
                    selectinload(ReleaseEnvironmentRelease.candidate).selectinload(
                        ReleaseCandidate.package_version
                    ),
                )
            )
        ).scalars().all()
    )
    production_deployments: dict[int, AgentDeployment] = {}
    release_by_deployment: dict[int, ReleaseEnvironmentRelease] = {}
    for release in active_release_rows:
        deployment = release.candidate.deployment
        if deployment.agent_asset_id in asset_ids:
            production_deployments[deployment.id] = deployment
            release_by_deployment[deployment.id] = release

    component_deployment_ids = set(
        (
            await db.execute(
                select(DeploymentComponent.deployment_id).where(
                    DeploymentComponent.ai_asset_id.in_(asset_ids)
                )
            )
        ).scalars().all()
    ) if asset_ids else set()
    fallback_deployments = list(
        (
            await db.execute(
                select(AgentDeployment)
                .where(
                    AgentDeployment.namespace_id == namespace_id,
                    AgentDeployment.status == AgentDeploymentStatus.ACTIVE,
                    func.lower(AgentDeployment.environment).in_({"prod", "production"}),
                    or_(
                        AgentDeployment.agent_asset_id.in_(asset_ids),
                        AgentDeployment.id.in_(component_deployment_ids),
                    ),
                )
                .options(
                    selectinload(AgentDeployment.components),
                    selectinload(AgentDeployment.package_version).selectinload(
                        AgentPackageVersion.components
                    ),
                    selectinload(AgentDeployment.package_version).selectinload(
                        AgentPackageVersion.dependencies
                    ),
                )
            )
        ).scalars().all()
    ) if asset_ids else []
    for deployment in fallback_deployments:
        production_deployments.setdefault(deployment.id, deployment)

    deployment_ids = sorted(production_deployments)
    bindings = list(
        (
            await db.execute(
                select(ReleaseCandidateEvaluationBinding)
                .where(
                    ReleaseCandidateEvaluationBinding.namespace_id == namespace_id,
                    ReleaseCandidateEvaluationBinding.deployment_id.in_(deployment_ids),
                )
                .options(selectinload(ReleaseCandidateEvaluationBinding.evaluation_comparison))
                .order_by(ReleaseCandidateEvaluationBinding.created_at.desc())
            )
        ).scalars().all()
    ) if deployment_ids else []
    binding_by_deployment: dict[int, ReleaseCandidateEvaluationBinding] = {}
    for binding in bindings:
        binding_by_deployment.setdefault(binding.deployment_id, binding)

    runs = list(
        (
            await db.execute(
                select(AgentRun)
                .where(
                    AgentRun.namespace_id == namespace_id,
                    AgentRun.deployment_id.in_(deployment_ids),
                )
                .order_by(AgentRun.started_at.desc(), AgentRun.id.desc())
                .limit(50)
            )
        ).scalars().all()
    ) if deployment_ids else []
    traces = list(
        (
            await db.execute(
                select(WorkTrace)
                .where(WorkTrace.asset_id.in_(asset_ids))
                .order_by(WorkTrace.asset_id, WorkTrace.started_at.desc(), WorkTrace.id.desc())
            )
        ).scalars().all()
    ) if asset_ids else []
    if any(trace.namespace_id != namespace_id for trace in traces):
        raise HandoverEvidenceTenantMismatchError("work traces must belong to the case Namespace")

    actions = list(
        (
            await db.execute(
                select(ExecutionAction)
                .where(ExecutionAction.handover_case_id == case.id)
                .order_by(ExecutionAction.id)
            )
        ).scalars().all()
    )
    case_evidence_ids = {item.evidence_id for item in items if item.evidence_id is not None}
    for action in actions:
        case_evidence_ids.update(int(value) for value in (action.evidence_ids or []) if isinstance(value, int))
    evidence_rows = list(
        (
            await db.execute(
                select(EvidenceItem).where(
                    EvidenceItem.id.in_(case_evidence_ids),
                    EvidenceItem.namespace_id == namespace_id,
                )
            )
        ).scalars().all()
    ) if case_evidence_ids else []
    evidence_by_id = {row.id: row for row in evidence_rows}

    nodes: dict[str, dict] = {}
    edges: dict[str, dict] = {}
    case_node = _add_node(
        nodes,
        kind="handover_case",
        ref=case.id,
        case_type=case.case_type.value,
        status=case.status.value,
        risk_level=case.risk_level.value,
    )
    for role, user_id in (
        ("subject", case.subject_user_id),
        ("receiver", case.receiver_user_id),
        ("fallback_owner", case.fallback_owner_user_id),
    ):
        if user_id is None:
            continue
        user = users.get(user_id)
        user_node = _add_node(
            nodes,
            kind="user",
            ref=user_id,
            username=user.username if user else None,
            active=user.is_active if user else False,
            auth_source=user.auth_source.value if user else None,
        )
        _add_edge(edges, source=case_node, target=user_node, relationship=role)

    owner_by_asset: dict[int, list[AssetOwnership]] = {}
    for ownership in ownerships:
        owner_by_asset.setdefault(ownership.asset_id, []).append(ownership)
    traces_by_asset: dict[int, list[WorkTrace]] = {}
    for trace in traces:
        traces_by_asset.setdefault(trace.asset_id or 0, []).append(trace)
    deployments_by_asset: dict[int, list[AgentDeployment]] = {}
    for deployment in production_deployments.values():
        if deployment.agent_asset_id is not None:
            deployments_by_asset.setdefault(deployment.agent_asset_id, []).append(deployment)
        for component in deployment.components:
            if component.ai_asset_id is not None:
                deployments_by_asset.setdefault(component.ai_asset_id, []).append(deployment)

    checks: list[dict] = []
    checks.append(
        _check(
            key="receiver:assigned",
            passed=receiver is not None,
            reason_code="receiver_assigned" if receiver else "receiver_missing",
            obligation_type=HandoverObligationType.RECEIVER_ACCESS,
            title="Assign a receiver",
        )
    )
    checks.append(
        _check(
            key="receiver:active",
            passed=bool(receiver and receiver.is_active),
            reason_code="receiver_active" if receiver and receiver.is_active else "receiver_inactive",
            obligation_type=HandoverObligationType.RECEIVER_ACCESS,
            title="Activate or replace the receiver",
        )
    )
    receiver_has_access = bool(
        receiver
        and receiver.is_active
        and {"namespace.read", "namespace.write", "namespace.admin"}.intersection(receiver_permissions)
    )
    checks.append(
        _check(
            key="receiver:namespace_access",
            passed=receiver_has_access,
            reason_code="receiver_access_confirmed" if receiver_has_access else "receiver_access_missing",
            evidence_refs=receiver_permissions,
            obligation_type=HandoverObligationType.RECEIVER_ACCESS,
            title="Grant receiver Namespace access",
            requires_evidence=True,
        )
    )

    for asset in assets:
        asset_node = _add_node(
            nodes,
            kind="asset",
            ref=asset.id,
            asset_type=asset.asset_type.value,
            name=(_sanitize_freetext(asset.name) or "")[:255],
            status=asset.status.value,
            criticality=asset.criticality.value,
            content_hash=asset.content_hash,
            runbook=_runbook(asset.metadata_json),
        )
        _add_edge(edges, source=case_node, target=asset_node, relationship="includes")
        asset_owners = owner_by_asset.get(asset.id, [])
        valid_owner = any(
            owner.user_id is not None or owner.org_unit_id is not None or owner.namespace_id is not None
            for owner in asset_owners
        )
        takeover_available = bool(case.receiver_user_id or case.fallback_owner_user_id)
        checks.append(
            _check(
                key=f"asset:{asset.id}:owner_or_fallback",
                passed=valid_owner and takeover_available,
                reason_code="owner_and_takeover_confirmed" if valid_owner and takeover_available else "owner_or_fallback_missing",
                evidence_refs=[f"ownership:{owner.id}" for owner in asset_owners],
                obligation_type=HandoverObligationType.OWNER_OR_FALLBACK,
                title=f"Assign owner/fallback for asset {asset.id}",
                requires_evidence=True,
            )
        )
        for owner in asset_owners:
            owner_ref = owner.user_id or owner.org_unit_id or owner.namespace_id or owner.id
            owner_kind = "user" if owner.user_id else "org_unit" if owner.org_unit_id else "namespace"
            owner_node = _add_node(
                nodes,
                kind=owner_kind,
                ref=owner_ref,
                ownership_type=owner.owner_type.value,
                primary=owner.is_primary,
                confidence=owner.confidence,
            )
            _add_edge(edges, source=owner_node, target=asset_node, relationship="owns")

        asset_deployments = {row.id: row for row in deployments_by_asset.get(asset.id, [])}
        if asset.asset_type == AssetType.AGENT:
            checks.append(
                _check(
                    key=f"asset:{asset.id}:production_version",
                    passed=bool(asset_deployments),
                    reason_code="production_version_pinned" if asset_deployments else "production_version_missing",
                    evidence_refs=[row.public_id for row in asset_deployments.values()],
                    obligation_type=HandoverObligationType.PRODUCTION_VERSION,
                    title=f"Pin production version for Agent asset {asset.id}",
                    requires_evidence=True,
                )
            )
            runbook = _runbook(asset.metadata_json)
            checks.append(
                _check(
                    key=f"asset:{asset.id}:runbook",
                    passed=bool(runbook),
                    reason_code="runbook_present" if runbook else "runbook_missing",
                    evidence_refs=sorted(runbook.values()),
                    obligation_type=HandoverObligationType.RUNBOOK,
                    title=f"Attach a runbook for Agent asset {asset.id}",
                    requires_evidence=True,
                )
            )
        sensitive = any(trace.sensitivity in _SENSITIVE for trace in traces_by_asset.get(asset.id, []))
        item_evidence = [
            item.evidence_id
            for item in items
            if item.asset_id == asset.id and item.evidence_id in evidence_by_id
        ]
        if asset.criticality in _HIGH_RISK or sensitive:
            checks.append(
                _check(
                    key=f"asset:{asset.id}:risk_evidence",
                    passed=bool(item_evidence),
                    reason_code="risk_evidence_present" if item_evidence else "risk_evidence_missing",
                    evidence_refs=[f"evidence:{value}" for value in item_evidence],
                    obligation_type=HandoverObligationType.RISK_EVIDENCE,
                    title=f"Provide evidence for high-risk/sensitive asset {asset.id}",
                    requires_evidence=True,
                )
            )

    runs_by_deployment: dict[int, list[AgentRun]] = {}
    for run in runs:
        if run.deployment_id is not None:
            runs_by_deployment.setdefault(run.deployment_id, []).append(run)
    for deployment_id in sorted(production_deployments):
        deployment = production_deployments[deployment_id]
        deployment_node = _add_node(
            nodes,
            kind="deployment",
            ref=deployment.public_id,
            revision=deployment.revision,
            configuration_digest=deployment.configuration_digest,
            environment=deployment.environment,
            status=deployment.status.value,
            runtime_id=deployment.runtime_id,
            package_version_public_id=(
                deployment.package_version.public_id if deployment.package_version is not None else None
            ),
        )
        if deployment.agent_asset_id is not None:
            _add_edge(
                edges,
                source=_node_key("asset", deployment.agent_asset_id),
                target=deployment_node,
                relationship="deployed_as",
            )
        environment_release = release_by_deployment.get(deployment.id)
        if environment_release is not None:
            release_node = _add_node(
                nodes,
                kind="environment_release",
                ref=environment_release.public_id,
                candidate_public_id=environment_release.candidate.public_id,
                activation_digest=environment_release.activation_digest,
                activated_at=_iso(environment_release.activated_at),
                status=environment_release.status.value,
            )
            _add_edge(edges, source=deployment_node, target=release_node, relationship="active_release")
        package_version = deployment.package_version
        if package_version is not None:
            package_node = _add_node(
                nodes,
                kind="package_version",
                ref=package_version.public_id,
                version=package_version.version,
                manifest_digest=package_version.manifest_digest,
                graph_digest=package_version.graph_digest,
                provenance_digest=package_version.provenance_digest,
            )
            _add_edge(edges, source=package_node, target=deployment_node, relationship="released_by")
            component_node_by_id: dict[int, str] = {}
            for package_component in package_version.components:
                component_node = _add_node(
                    nodes,
                    kind="package_component",
                    ref=f"{package_version.public_id}:{package_component.component_ref}",
                    component_ref=package_component.component_ref,
                    component_type=package_component.component_type.value,
                    version_ref=package_component.version_ref,
                    sha256=package_component.sha256,
                )
                component_node_by_id[package_component.id] = component_node
                _add_edge(edges, source=package_node, target=component_node, relationship="contains")
            for dependency in package_version.dependencies:
                source = component_node_by_id.get(dependency.from_component_id)
                target = component_node_by_id.get(dependency.to_component_id)
                if source and target:
                    _add_edge(
                        edges,
                        source=source,
                        target=target,
                        relationship=dependency.relationship_kind.value,
                    )
        evaluation_binding = binding_by_deployment.get(deployment.id)
        checks.append(
            _check(
                key=f"deployment:{deployment.public_id}:evaluation_baseline",
                passed=bool(
                    evaluation_binding
                    and evaluation_binding.evaluation_comparison.outcome
                    == EvaluationComparisonOutcome.PASS
                ),
                reason_code=(
                    "evaluation_baseline_pass"
                    if evaluation_binding
                    and evaluation_binding.evaluation_comparison.outcome
                    == EvaluationComparisonOutcome.PASS
                    else "evaluation_baseline_missing_or_not_pass"
                ),
                evidence_refs=[evaluation_binding.public_id] if evaluation_binding else [],
                obligation_type=HandoverObligationType.EVALUATION_BASELINE,
                title=f"Provide a passing Eval baseline for deployment {deployment.public_id}",
                requires_evidence=True,
            )
        )
        if evaluation_binding is not None:
            comparison = evaluation_binding.evaluation_comparison
            eval_node = _add_node(
                nodes,
                kind="evaluation_comparison",
                ref=comparison.public_id,
                outcome=comparison.outcome.value,
                reason_code=comparison.reason_code,
                reproducibility_digest=comparison.reproducibility_digest,
                binding_public_id=binding.public_id,
                binding_digest=binding.binding_digest,
            )
            _add_edge(edges, source=eval_node, target=deployment_node, relationship="evaluates")
        deployment_runs = runs_by_deployment.get(deployment.id, [])[:10]
        checks.append(
            _check(
                key=f"deployment:{deployment.public_id}:recent_runs",
                passed=bool(deployment_runs),
                reason_code="recent_runs_present" if deployment_runs else "recent_runs_absent",
                evidence_refs=[run.public_id for run in deployment_runs],
                obligation_type=HandoverObligationType.PRODUCTION_VERSION,
                severity=HandoverObligationSeverity.ADVISORY,
                title=f"Confirm recent runs for deployment {deployment.public_id}",
            )
        )
        for run in deployment_runs:
            run_node = _add_node(
                nodes,
                kind="agent_run",
                ref=run.public_id,
                status=run.status.value,
                trust_level=run.trust_level.value,
                started_at=_iso(run.started_at),
                ended_at=_iso(run.ended_at),
                duration_ms=run.duration_ms,
            )
            _add_edge(edges, source=deployment_node, target=run_node, relationship="executed")

    failed_actions = [action for action in actions if action.status == ExecutionStatus.FAILED]
    if failed_actions:
        checks.append(
            _check(
                key="case:failed_actions",
                passed=False,
                reason_code="failed_actions_require_acknowledgement",
                evidence_refs=[f"execution_action:{action.id}" for action in failed_actions],
                obligation_type=HandoverObligationType.FAILED_ACTION_ACKNOWLEDGEMENT,
                title="Acknowledge failed handover actions",
                requires_evidence=True,
            )
        )

    subject = {
        "handover_case_id": case.id,
        "case_type": case.case_type.value,
        "case_status": case.status.value,
        "subject_user_id": case.subject_user_id,
        "receiver_user_id": case.receiver_user_id,
        "fallback_owner_user_id": case.fallback_owner_user_id,
        "due_at": _iso(case.due_at),
        "receiver_permissions": receiver_permissions,
    }
    evidence_summary = {
        "asset_count": len(assets),
        "production_deployment_count": len(production_deployments),
        "recent_run_count": len(runs),
        "evaluation_binding_count": len(bindings),
        "case_evidence_ids": sorted(evidence_by_id),
        "failed_action_ids": [action.id for action in failed_actions],
        "sensitive_trace_count": sum(trace.sensitivity in _SENSITIVE for trace in traces),
    }
    return (
        subject,
        [nodes[key] for key in sorted(nodes)],
        [edges[key] for key in sorted(edges)],
        evidence_summary,
        sorted(checks, key=lambda row: row["key"]),
    )


async def create_handover_snapshot(
    db: AsyncSession,
    *,
    request: HandoverEvidenceSnapshotCreate,
    actor: User,
) -> HandoverEvidenceSnapshot:
    case = await _get_case(db, request.handover_case_id)
    namespace_id = int(case.namespace_id or 0)
    replay = (
        await db.execute(
            select(HandoverEvidenceSnapshot).where(
                HandoverEvidenceSnapshot.namespace_id == namespace_id,
                HandoverEvidenceSnapshot.idempotency_key == request.idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if replay is not None:
        if replay.handover_case_id != case.id:
            raise HandoverEvidenceConflictError("snapshot idempotency key is already in use")
        return await get_handover_snapshot(db, replay.public_id)

    subject, nodes, edges, evidence_summary, readiness = await _snapshot_source(db, case=case)
    document = {
        "schema_name": "duckdock-handover-evidence-snapshot",
        "schema_version": "2.0",
        "namespace_id": namespace_id,
        "handover_case_id": case.id,
        "subject": subject,
        "nodes": nodes,
        "edges": edges,
        "evidence_summary": evidence_summary,
        "readiness": readiness,
    }
    digest = _sha256(canonical_json_bytes(document))
    sequence = int(
        (
            await db.execute(
                select(func.coalesce(func.max(HandoverEvidenceSnapshot.sequence), 0)).where(
                    HandoverEvidenceSnapshot.handover_case_id == case.id
                )
            )
        ).scalar_one()
    ) + 1
    outcome = (
        HandoverReadinessOutcome.BLOCKED
        if any(row["outcome"] == "BLOCK" and row.get("obligation", {}).get("severity") == "BLOCKING" for row in readiness)
        else HandoverReadinessOutcome.READY
    )
    snapshot = HandoverEvidenceSnapshot(
        public_id=_public_id("hsnap"),
        namespace_id=namespace_id,
        handover_case_id=case.id,
        sequence=sequence,
        subject_json=subject,
        nodes_json=nodes,
        edges_json=edges,
        evidence_summary_json=evidence_summary,
        readiness_json=readiness,
        node_count=len(nodes),
        edge_count=len(edges),
        check_count=len(readiness),
        readiness_outcome=outcome,
        snapshot_digest=digest,
        idempotency_key=request.idempotency_key,
        created_by_user_id=actor.id,
    )
    db.add(snapshot)
    await db.flush()
    for row in readiness:
        obligation = row.get("obligation")
        if row["outcome"] != "BLOCK" or not isinstance(obligation, dict):
            continue
        requirement = {
            "readiness_key": row["key"],
            "reason_code": row["reason_code"],
            "evidence_refs": row["evidence_refs"],
        }
        obligation_row = HandoverObligation(
            public_id=_public_id("hobl"),
            namespace_id=namespace_id,
            handover_case_id=case.id,
            snapshot_id=snapshot.id,
            obligation_key=row["key"],
            obligation_type=HandoverObligationType(obligation["type"]),
            severity=HandoverObligationSeverity(obligation["severity"]),
            title=obligation["title"],
            requirement_json=requirement,
            requires_evidence=bool(obligation["requires_evidence"]),
            obligation_digest=_sha256(canonical_json_bytes({"snapshot_digest": digest, **requirement, **obligation})),
        )
        db.add(obligation_row)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="handover.snapshot.created",
        resource_type="handover_evidence_snapshot",
        resource_id=snapshot.id,
        namespace_id=namespace_id,
        details={
            "public_id": snapshot.public_id,
            "handover_case_id": case.id,
            "sequence": sequence,
            "snapshot_digest": digest,
            "readiness_outcome": outcome.value,
            "node_count": len(nodes),
            "edge_count": len(edges),
        },
    )
    await enqueue_domain_event(db, build_handover_snapshot_created(snapshot), guaranteed_new=True)
    return await get_handover_snapshot(db, snapshot.public_id)


def handover_obligation_receipt_out(row: HandoverObligationReceipt) -> HandoverObligationReceiptOut:
    return HandoverObligationReceiptOut(
        public_id=row.public_id,
        namespace_id=row.namespace_id,
        decision=row.decision,
        note=row.note,
        evidence_ids=list(row.evidence_ids_json or []),
        receipt_digest=row.receipt_digest,
        decided_by_user_id=row.decided_by_user_id,
        created_at=row.created_at,
    )


def handover_obligation_out(row: HandoverObligation) -> HandoverObligationOut:
    receipt = row.receipts[-1] if row.receipts else None
    return HandoverObligationOut(
        public_id=row.public_id,
        namespace_id=row.namespace_id,
        handover_case_id=row.handover_case_id,
        snapshot_public_id=row.snapshot.public_id,
        obligation_key=row.obligation_key,
        obligation_type=row.obligation_type,
        severity=row.severity,
        title=row.title,
        requirement=row.requirement_json,
        requires_evidence=row.requires_evidence,
        obligation_digest=row.obligation_digest,
        receipt=handover_obligation_receipt_out(receipt) if receipt else None,
        created_at=row.created_at,
    )


def handover_snapshot_out(row: HandoverEvidenceSnapshot) -> HandoverEvidenceSnapshotOut:
    obligations = [handover_obligation_out(item) for item in row.obligations]
    blocking = [item for item in row.obligations if item.severity == HandoverObligationSeverity.BLOCKING]
    open_blocking = [
        item
        for item in blocking
        if not item.receipts or item.receipts[-1].decision != HandoverObligationReceiptDecision.FULFILLED
    ]
    return HandoverEvidenceSnapshotOut(
        public_id=row.public_id,
        namespace_id=row.namespace_id,
        handover_case_id=row.handover_case_id,
        sequence=row.sequence,
        subject=row.subject_json,
        nodes=list(row.nodes_json or []),
        edges=list(row.edges_json or []),
        evidence_summary=row.evidence_summary_json,
        readiness=list(row.readiness_json or []),
        node_count=row.node_count,
        edge_count=row.edge_count,
        check_count=row.check_count,
        readiness_outcome=row.readiness_outcome,
        current_readiness_outcome=(
            HandoverReadinessOutcome.BLOCKED if open_blocking else HandoverReadinessOutcome.READY
        ),
        blocking_obligation_count=len(blocking),
        open_blocking_obligation_count=len(open_blocking),
        snapshot_digest=row.snapshot_digest,
        obligations=obligations,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
    )


async def get_handover_obligation(db: AsyncSession, public_id: str) -> HandoverObligation:
    row = (
        await db.execute(
            select(HandoverObligation)
            .where(HandoverObligation.public_id == public_id)
            .execution_options(populate_existing=True)
            .options(
                selectinload(HandoverObligation.snapshot),
                selectinload(HandoverObligation.receipts),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HandoverEvidenceNotFoundError("handover obligation was not found")
    return row


async def record_handover_obligation_receipt(
    db: AsyncSession,
    *,
    obligation_public_id: str,
    request: HandoverObligationReceiptCreate,
    actor: User,
) -> HandoverObligationReceipt:
    obligation = await get_handover_obligation(db, obligation_public_id)
    replay = (
        await db.execute(
            select(HandoverObligationReceipt).where(
                HandoverObligationReceipt.namespace_id == obligation.namespace_id,
                HandoverObligationReceipt.idempotency_key == request.idempotency_key,
            )
        )
    ).scalar_one_or_none()
    payload = {
        "obligation_digest": obligation.obligation_digest,
        "decision": request.decision.value,
        "note": request.note,
        "evidence_ids": request.evidence_ids,
        "decided_by_user_id": actor.id,
    }
    digest = _sha256(canonical_json_bytes(payload))
    if replay is not None:
        if replay.obligation_id != obligation.id or replay.receipt_digest != digest:
            raise HandoverEvidenceConflictError("obligation receipt idempotency key is already in use")
        return replay
    if obligation.receipts:
        raise HandoverEvidenceConflictError("handover obligation already has a terminal receipt")
    if (
        obligation.severity == HandoverObligationSeverity.BLOCKING
        and request.decision == HandoverObligationReceiptDecision.WAIVED
    ):
        raise HandoverEvidenceStateError("blocking obligations cannot be waived")
    if obligation.requires_evidence and request.decision == HandoverObligationReceiptDecision.FULFILLED:
        if not request.evidence_ids:
            raise HandoverEvidenceReferenceError("evidence is required to fulfil this obligation")
    if request.evidence_ids:
        evidence = list(
            (
                await db.execute(
                    select(EvidenceItem).where(
                        EvidenceItem.id.in_(request.evidence_ids),
                        EvidenceItem.namespace_id == obligation.namespace_id,
                    )
                )
            ).scalars().all()
        )
        if len(evidence) != len(request.evidence_ids):
            raise HandoverEvidenceReferenceError("obligation evidence must belong to the Namespace")
    row = HandoverObligationReceipt(
        public_id=_public_id("horcp"),
        namespace_id=obligation.namespace_id,
        obligation_id=obligation.id,
        decision=request.decision,
        note=request.note,
        evidence_ids_json=request.evidence_ids,
        receipt_digest=digest,
        idempotency_key=request.idempotency_key,
        decided_by_user_id=actor.id,
    )
    db.add(row)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="handover.obligation.receipt.recorded",
        resource_type="handover_obligation",
        resource_id=obligation.id,
        namespace_id=obligation.namespace_id,
        details={
            "obligation_public_id": obligation.public_id,
            "decision": row.decision.value,
            "receipt_digest": row.receipt_digest,
            "evidence_ids": request.evidence_ids,
        },
    )
    row.obligation = obligation
    await enqueue_domain_event(
        db,
        build_handover_obligation_receipt_recorded(row),
        guaranteed_new=True,
    )
    return row


async def get_handover_acceptance(db: AsyncSession, public_id: str) -> HandoverAcceptance:
    row = (
        await db.execute(
            select(HandoverAcceptance)
            .where(HandoverAcceptance.public_id == public_id)
            .options(selectinload(HandoverAcceptance.snapshot))
        )
    ).scalar_one_or_none()
    if row is None:
        raise HandoverEvidenceNotFoundError("handover acceptance was not found")
    return row


def handover_acceptance_out(row: HandoverAcceptance) -> HandoverAcceptanceOut:
    return HandoverAcceptanceOut(
        public_id=row.public_id,
        namespace_id=row.namespace_id,
        handover_case_id=row.handover_case_id,
        snapshot_public_id=row.snapshot.public_id,
        decision=row.decision,
        acknowledges_failures=row.acknowledges_failures,
        comment=row.comment,
        obligation_receipt_digests=list(row.obligation_receipt_digests_json or []),
        acceptance_digest=row.acceptance_digest,
        accepted_by_user_id=row.accepted_by_user_id,
        created_at=row.created_at,
    )


async def create_handover_acceptance(
    db: AsyncSession,
    *,
    request: HandoverAcceptanceCreate,
    actor: User,
) -> HandoverAcceptance:
    snapshot = await get_handover_snapshot(db, request.snapshot_public_id)
    case = snapshot.handover_case
    replay = (
        await db.execute(
            select(HandoverAcceptance)
            .where(
                HandoverAcceptance.namespace_id == snapshot.namespace_id,
                HandoverAcceptance.idempotency_key == request.idempotency_key,
            )
            .options(selectinload(HandoverAcceptance.snapshot))
        )
    ).scalar_one_or_none()
    terminal_receipts = [item.receipts[-1] for item in snapshot.obligations if item.receipts]
    receipt_digests = sorted(item.receipt_digest for item in terminal_receipts)
    payload = {
        "snapshot_digest": snapshot.snapshot_digest,
        "decision": request.decision.value,
        "acknowledges_failures": request.acknowledges_failures,
        "comment": request.comment,
        "obligation_receipt_digests": receipt_digests,
        "accepted_by_user_id": actor.id,
    }
    digest = _sha256(canonical_json_bytes(payload))
    if replay is not None:
        if replay.snapshot_id != snapshot.id or replay.acceptance_digest != digest:
            raise HandoverEvidenceConflictError("acceptance idempotency key is already in use")
        return replay
    if request.decision == HandoverAcceptanceDecision.ACCEPTED:
        if case.status not in {HandoverStatus.VERIFYING, HandoverStatus.COMPLETED}:
            raise HandoverEvidenceStateError("handover must reach verifying before receiver acceptance")
        if actor.system_role != SystemRole.ADMIN and actor.id != case.receiver_user_id:
            raise HandoverEvidenceStateError("only the assigned receiver or an administrator may accept")
        receiver = await db.get(User, case.receiver_user_id) if case.receiver_user_id else None
        if receiver is None or not receiver.is_active:
            raise HandoverEvidenceStateError("the assigned receiver must be active")
        open_blocking = [
            item
            for item in snapshot.obligations
            if item.severity == HandoverObligationSeverity.BLOCKING
            and (not item.receipts or item.receipts[-1].decision != HandoverObligationReceiptDecision.FULFILLED)
        ]
        if open_blocking:
            raise HandoverEvidenceStateError(
                f"handover has {len(open_blocking)} unresolved blocking obligations"
            )
        failed_actions = int(
            (
                await db.execute(
                    select(func.count(ExecutionAction.id)).where(
                        ExecutionAction.handover_case_id == case.id,
                        ExecutionAction.status == ExecutionStatus.FAILED,
                    )
                )
            ).scalar_one()
        )
        failed_obligations = any(
            receipt.decision == HandoverObligationReceiptDecision.FAILED
            for receipt in terminal_receipts
        )
        if (failed_actions or failed_obligations) and not request.acknowledges_failures:
            raise HandoverEvidenceStateError("failed handover evidence requires explicit acknowledgement")
        prior_accepted = (
            await db.execute(
                select(HandoverAcceptance.id).where(
                    HandoverAcceptance.snapshot_id == snapshot.id,
                    HandoverAcceptance.decision == HandoverAcceptanceDecision.ACCEPTED,
                )
            )
        ).scalar_one_or_none()
        if prior_accepted is not None:
            raise HandoverEvidenceConflictError("snapshot was already accepted")
    row = HandoverAcceptance(
        public_id=_public_id("hacc"),
        namespace_id=snapshot.namespace_id,
        handover_case_id=snapshot.handover_case_id,
        snapshot_id=snapshot.id,
        decision=request.decision,
        acknowledges_failures=request.acknowledges_failures,
        comment=request.comment,
        obligation_receipt_digests_json=receipt_digests,
        acceptance_digest=digest,
        idempotency_key=request.idempotency_key,
        accepted_by_user_id=actor.id,
    )
    db.add(row)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="handover.acceptance.recorded",
        resource_type="handover_acceptance",
        resource_id=row.id,
        namespace_id=row.namespace_id,
        details={
            "public_id": row.public_id,
            "snapshot_public_id": snapshot.public_id,
            "decision": row.decision.value,
            "acknowledges_failures": row.acknowledges_failures,
            "acceptance_digest": row.acceptance_digest,
        },
    )
    row.snapshot = snapshot
    await enqueue_domain_event(db, build_handover_acceptance_recorded(row), guaranteed_new=True)
    return await get_handover_acceptance(db, row.public_id)


async def list_handover_acceptances(
    db: AsyncSession,
    *,
    namespace_id: int | None = None,
    handover_case_id: int | None = None,
    limit: int = 100,
) -> list[HandoverAcceptance]:
    stmt = (
        select(HandoverAcceptance)
        .options(selectinload(HandoverAcceptance.snapshot))
        .order_by(HandoverAcceptance.created_at.desc(), HandoverAcceptance.id.desc())
        .limit(limit)
    )
    if namespace_id is not None:
        stmt = stmt.where(HandoverAcceptance.namespace_id == namespace_id)
    if handover_case_id is not None:
        stmt = stmt.where(HandoverAcceptance.handover_case_id == handover_case_id)
    return list((await db.execute(stmt)).scalars().all())


def _manifest(snapshot: HandoverEvidenceSnapshot, acceptance: HandoverAcceptance) -> dict[str, Any]:
    obligations: list[dict[str, Any]] = []
    for obligation in snapshot.obligations:
        receipt = obligation.receipts[-1] if obligation.receipts else None
        obligations.append(
            {
                "public_id": obligation.public_id,
                "obligation_key": obligation.obligation_key,
                "type": obligation.obligation_type.value,
                "severity": obligation.severity.value,
                "requires_evidence": obligation.requires_evidence,
                "obligation_digest": obligation.obligation_digest,
                "receipt": (
                    {
                        "public_id": receipt.public_id,
                        "decision": receipt.decision.value,
                        "note": _sanitize_freetext(receipt.note),
                        "evidence_ids": list(receipt.evidence_ids_json or []),
                        "receipt_digest": receipt.receipt_digest,
                        "decided_by_user_id": receipt.decided_by_user_id,
                        "created_at": _iso(receipt.created_at),
                    }
                    if receipt
                    else None
                ),
            }
        )
    return {
        "schema_name": HANDOVER_MANIFEST_SCHEMA,
        "schema_version": HANDOVER_MANIFEST_VERSION,
        "namespace_id": snapshot.namespace_id,
        "handover_case_id": snapshot.handover_case_id,
        "snapshot": {
            "public_id": snapshot.public_id,
            "sequence": snapshot.sequence,
            "snapshot_digest": snapshot.snapshot_digest,
            "subject": snapshot.subject_json,
            "nodes": snapshot.nodes_json,
            "edges": snapshot.edges_json,
            "evidence_summary": snapshot.evidence_summary_json,
            "readiness": snapshot.readiness_json,
            "created_at": _iso(snapshot.created_at),
        },
        "obligations": obligations,
        "acceptance": {
            "public_id": acceptance.public_id,
            "decision": acceptance.decision.value,
            "acknowledges_failures": acceptance.acknowledges_failures,
            "comment": _sanitize_freetext(acceptance.comment),
            "obligation_receipt_digests": acceptance.obligation_receipt_digests_json,
            "acceptance_digest": acceptance.acceptance_digest,
            "accepted_by_user_id": acceptance.accepted_by_user_id,
            "created_at": _iso(acceptance.created_at),
        },
    }


async def handover_signing_payload(
    db: AsyncSession,
    *,
    acceptance_public_id: str,
) -> HandoverSigningPayloadOut:
    acceptance = await get_handover_acceptance(db, acceptance_public_id)
    if acceptance.decision != HandoverAcceptanceDecision.ACCEPTED:
        raise HandoverEvidenceStateError("only an accepted handover can be signed")
    snapshot = await get_handover_snapshot(db, acceptance.snapshot.public_id)
    acceptance.snapshot = snapshot
    payload = canonical_json_bytes(_manifest(snapshot, acceptance))
    return HandoverSigningPayloadOut(
        schema_name=HANDOVER_MANIFEST_SCHEMA,
        schema_version=HANDOVER_MANIFEST_VERSION,
        acceptance_public_id=acceptance.public_id,
        snapshot_public_id=snapshot.public_id,
        manifest_digest=_sha256(payload),
        payload_base64=base64.b64encode(payload).decode("ascii"),
    )


def _deterministic_zip(manifest_bytes: bytes, signature_document: dict[str, Any]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, body in (
            ("manifest.json", manifest_bytes),
            ("signature.json", canonical_json_bytes(signature_document)),
        ):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, body)
    return buffer.getvalue()


async def get_handover_signed_package(db: AsyncSession, public_id: str) -> HandoverSignedPackage:
    row = (
        await db.execute(
            select(HandoverSignedPackage)
            .where(HandoverSignedPackage.public_id == public_id)
            .options(
                selectinload(HandoverSignedPackage.acceptance).selectinload(
                    HandoverAcceptance.snapshot
                ),
                selectinload(HandoverSignedPackage.evidence_item),
                selectinload(HandoverSignedPackage.signing_key),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HandoverEvidenceNotFoundError("signed handover package was not found")
    return row


def handover_signed_package_out(row: HandoverSignedPackage) -> HandoverSignedPackageOut:
    return HandoverSignedPackageOut(
        public_id=row.public_id,
        namespace_id=row.namespace_id,
        handover_case_id=row.handover_case_id,
        snapshot_public_id=row.acceptance.snapshot.public_id,
        acceptance_public_id=row.acceptance.public_id,
        evidence_item_id=row.evidence_item_id,
        evidence_object_uri=row.evidence_item.object_uri,
        signing_key_public_id=row.signing_key.public_id,
        signing_key_fingerprint=row.signing_key.public_key_fingerprint,
        manifest_digest=row.manifest_digest,
        archive_digest=row.archive_digest,
        signature_algorithm=row.signature_algorithm,
        signature=row.signature_value,
        signature_digest=row.signature_digest,
        attestation_digest=row.attestation_digest,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
    )


async def create_handover_signed_package(
    db: AsyncSession,
    *,
    request: HandoverSignedPackageCreate,
    actor: User,
) -> HandoverSignedPackage:
    acceptance = await get_handover_acceptance(db, request.acceptance_public_id)
    if acceptance.decision != HandoverAcceptanceDecision.ACCEPTED:
        raise HandoverEvidenceStateError("only an accepted handover can be packaged")
    replay = (
        await db.execute(
            select(HandoverSignedPackage).where(
                HandoverSignedPackage.namespace_id == acceptance.namespace_id,
                HandoverSignedPackage.idempotency_key == request.idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if replay is not None:
        if replay.acceptance_id != acceptance.id or replay.signature_value != request.signature:
            raise HandoverEvidenceConflictError("signed package idempotency key is already in use")
        return await get_handover_signed_package(db, replay.public_id)
    existing = (
        await db.execute(
            select(HandoverSignedPackage.public_id).where(
                HandoverSignedPackage.acceptance_id == acceptance.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HandoverEvidenceConflictError("acceptance already has a signed package")
    key = (
        await db.execute(
            select(PackageSigningKey).where(
                PackageSigningKey.public_id == request.signing_key_public_id
            )
        )
    ).scalar_one_or_none()
    if key is None or key.namespace_id != acceptance.namespace_id:
        raise HandoverEvidenceReferenceError("signing key was not found")
    if key.status != PackageSigningKeyStatus.ACTIVE:
        raise HandoverEvidenceStateError("signing key is revoked")
    snapshot = await get_handover_snapshot(db, acceptance.snapshot.public_id)
    acceptance.snapshot = snapshot
    manifest = _manifest(snapshot, acceptance)
    manifest_bytes = canonical_json_bytes(manifest)
    try:
        signature_bytes = _verify_signature(
            key=key,
            signature_value=request.signature,
            payload=manifest_bytes,
        )
    except PackageSignatureError as exc:
        raise HandoverEvidenceReferenceError(str(exc)) from exc
    manifest_digest = _sha256(manifest_bytes)
    signature_digest = _sha256(signature_bytes)
    signature_document = {
        "algorithm": PackageSignatureAlgorithm.ED25519.value,
        "signing_key_public_id": key.public_id,
        "signing_key_fingerprint": key.public_key_fingerprint,
        "manifest_digest": manifest_digest,
        "signature": request.signature,
        "signature_digest": signature_digest,
    }
    archive = await asyncio.to_thread(_deterministic_zip, manifest_bytes, signature_document)
    archive_digest = _sha256(archive)
    object_key = (
        f"handovers/case-{acceptance.handover_case_id}/v2/"
        f"{acceptance.public_id}-{archive_digest[:16]}.zip"
    )
    await asyncio.to_thread(
        _put_object,
        object_key=object_key,
        body=archive,
        content_type="application/zip",
        metadata={
            "handover_case_id": str(acceptance.handover_case_id),
            "snapshot_digest": snapshot.snapshot_digest,
            "manifest_digest": manifest_digest,
            "archive_digest": archive_digest,
        },
    )
    evidence = build_evidence_item(
        namespace_id=acceptance.namespace_id,
        source_type=EvidenceSourceType.BACKUP_PACKAGE,
        source_provider=RuntimeProvider.CUSTOM,
        object_uri=f"s3://{artifact_service.bucket}/{object_key}",
        sha256=archive_digest,
        summary=(
            f"Signed Handover 2.0 package(case {acceptance.handover_case_id}, "
            f"snapshot {snapshot.public_id}, acceptance {acceptance.public_id})."
        ),
        visibility=EvidenceVisibility.SENSITIVE,
        created_by=actor.id,
    )
    db.add(evidence)
    await db.flush()
    attestation = {
        "snapshot_digest": snapshot.snapshot_digest,
        "acceptance_digest": acceptance.acceptance_digest,
        "manifest_digest": manifest_digest,
        "archive_digest": archive_digest,
        "signing_key_fingerprint": key.public_key_fingerprint,
        "signature_digest": signature_digest,
    }
    row = HandoverSignedPackage(
        public_id=_public_id("hpkg"),
        namespace_id=acceptance.namespace_id,
        handover_case_id=acceptance.handover_case_id,
        snapshot_id=snapshot.id,
        acceptance_id=acceptance.id,
        evidence_item_id=evidence.id,
        signing_key_id=key.id,
        manifest_digest=manifest_digest,
        archive_digest=archive_digest,
        signature_algorithm=PackageSignatureAlgorithm.ED25519.value,
        signature_value=request.signature,
        signature_digest=signature_digest,
        attestation_digest=_sha256(canonical_json_bytes(attestation)),
        idempotency_key=request.idempotency_key,
        created_by_user_id=actor.id,
    )
    db.add(row)
    await db.flush()
    await audit(
        db,
        user=actor,
        action="handover.signed_package.created",
        resource_type="handover_signed_package",
        resource_id=row.id,
        namespace_id=row.namespace_id,
        details={
            "public_id": row.public_id,
            "handover_case_id": row.handover_case_id,
            "snapshot_public_id": snapshot.public_id,
            "acceptance_public_id": acceptance.public_id,
            "evidence_item_id": evidence.id,
            "manifest_digest": manifest_digest,
            "archive_digest": archive_digest,
            "signing_key_public_id": key.public_id,
            "attestation_digest": row.attestation_digest,
        },
    )
    row.acceptance = acceptance
    row.signing_key = key
    await enqueue_domain_event(db, build_handover_signed_package_created(row), guaranteed_new=True)
    return await get_handover_signed_package(db, row.public_id)


async def list_handover_signed_packages(
    db: AsyncSession,
    *,
    namespace_id: int | None = None,
    handover_case_id: int | None = None,
    limit: int = 100,
) -> list[HandoverSignedPackage]:
    stmt = (
        select(HandoverSignedPackage)
        .options(
            selectinload(HandoverSignedPackage.acceptance).selectinload(HandoverAcceptance.snapshot),
            selectinload(HandoverSignedPackage.evidence_item),
            selectinload(HandoverSignedPackage.signing_key),
        )
        .order_by(HandoverSignedPackage.created_at.desc(), HandoverSignedPackage.id.desc())
        .limit(limit)
    )
    if namespace_id is not None:
        stmt = stmt.where(HandoverSignedPackage.namespace_id == namespace_id)
    if handover_case_id is not None:
        stmt = stmt.where(HandoverSignedPackage.handover_case_id == handover_case_id)
    return list((await db.execute(stmt)).scalars().all())
