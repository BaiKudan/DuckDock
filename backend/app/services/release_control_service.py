from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.time import ensure_utc
from app.models.deployment import AgentDeployment, AgentDeploymentStatus
from app.models.package_registry import (
    AgentPackageVersion,
    AgentPackageVersionStatus,
    PackageComponentType,
    PackageSigningKeyStatus,
)
from app.models.release_control import (
    ReleaseCandidate,
    ReleaseEnvironment,
    ReleaseEnvironmentStatus,
    ReleasePolicy,
    ReleasePolicyDecision,
    ReleasePolicyEnforcementOutcome,
    ReleasePolicyMode,
    ReleasePolicyRawOutcome,
    ReleasePolicyRuleResult,
    ReleasePolicyRuleType,
    ReleasePolicyRuleVerdict,
    ReleasePolicyStatus,
    ReleasePolicyVersion,
)
from app.models.user import User
from app.schemas.package_registry import AgentPackageManifestV2
from app.schemas.release_control import (
    ReleaseCandidateCreate,
    ReleaseCandidateOut,
    ReleaseEnvironmentCreate,
    ReleasePolicyCreate,
    ReleasePolicyDecisionOut,
    ReleasePolicyEvaluationCreate,
    ReleasePolicyOut,
    ReleasePolicyRuleIn,
    ReleasePolicyRuleResultOut,
    ReleasePolicyVersionCreate,
    ReleasePolicyVersionOut,
    VulnerabilitySeverity,
)
from app.services.artifact_service import ArtifactStorageService, artifact_service
from app.services.audit_service import audit
from app.services.outbox_event_service import (
    build_release_candidate_created,
    build_release_policy_decision_recorded,
    build_release_policy_version_created,
    enqueue_domain_event,
)
from app.services.package_registry_service import MAX_SBOM_BYTES, verify_package_version
from app.services.release_candidate_evaluation_service import DatabaseReleaseEvidenceLookupPort
from app.services.release_evidence_ports import ReleaseEvidenceSelector
from app.services.release_gate_service import ReleaseGateService
from app.services.tenant_write_service import require_active_namespace


POLICY_SCHEMA_NAME = "duckdock-release-policy"
POLICY_SCHEMA_VERSION = "1.0"
CANDIDATE_SCHEMA_NAME = "duckdock-release-candidate"
CANDIDATE_SCHEMA_VERSION = "1.0"
DECISION_SCHEMA_NAME = "duckdock-release-policy-decision"
DECISION_SCHEMA_VERSION = "1.0"
MAX_VULNERABILITIES = 1000


class ReleaseControlError(ValueError):
    pass


class ReleaseControlNotFoundError(ReleaseControlError):
    pass


class ReleaseControlConflictError(ReleaseControlError):
    pass


class ReleaseControlReferenceError(ReleaseControlError):
    pass


class ReleaseControlTenantMismatchError(ReleaseControlReferenceError):
    pass


class ReleaseControlStateError(ReleaseControlError):
    pass


def _public_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _is_unique_violation(exc: IntegrityError) -> bool:
    args = getattr(exc.orig, "args", ())
    return bool(args and args[0] == 1062) or "unique constraint failed" in str(exc.orig).lower()


def _candidate_options():
    return (
        selectinload(ReleaseCandidate.package_version).selectinload(AgentPackageVersion.package),
        selectinload(ReleaseCandidate.package_version).selectinload(AgentPackageVersion.signing_key),
        selectinload(ReleaseCandidate.package_version).selectinload(AgentPackageVersion.components),
        selectinload(ReleaseCandidate.package_version).selectinload(AgentPackageVersion.sbom),
        selectinload(ReleaseCandidate.deployment),
        selectinload(ReleaseCandidate.target_environment),
        selectinload(ReleaseCandidate.baseline_candidate)
        .selectinload(ReleaseCandidate.package_version)
        .selectinload(AgentPackageVersion.package),
        selectinload(ReleaseCandidate.baseline_candidate)
        .selectinload(ReleaseCandidate.package_version)
        .selectinload(AgentPackageVersion.components),
    )


def _decision_options():
    return (
        selectinload(ReleasePolicyDecision.rule_results),
        selectinload(ReleasePolicyDecision.candidate),
        selectinload(ReleasePolicyDecision.policy_version)
        .selectinload(ReleasePolicyVersion.policy),
        selectinload(ReleasePolicyDecision.policy_version)
        .selectinload(ReleasePolicyVersion.target_environment),
    )


async def create_release_environment(
    db: AsyncSession,
    *,
    request: ReleaseEnvironmentCreate,
    actor: User,
) -> ReleaseEnvironment:
    await require_active_namespace(db, request.namespace_id)
    row = ReleaseEnvironment(
        public_id=_public_id("renv"),
        namespace_id=request.namespace_id,
        name=request.name,
        kind=request.kind,
        promotion_order=request.promotion_order,
        protected=request.protected,
        minimum_approvals=request.minimum_approvals,
        requires_canary=request.requires_canary,
        status=ReleaseEnvironmentStatus.ACTIVE,
        created_by_user_id=actor.id,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        if _is_unique_violation(exc):
            raise ReleaseControlConflictError("Environment name or promotion order already exists") from exc
        raise
    await audit(
        db,
        user=actor,
        action="release.environment.created",
        resource_type="release_environment",
        resource_id=row.id,
        namespace_id=row.namespace_id,
        details={
            "public_id": row.public_id,
            "name": row.name,
            "kind": row.kind.value,
            "promotion_order": row.promotion_order,
            "protected": row.protected,
            "minimum_approvals": row.minimum_approvals,
            "requires_canary": row.requires_canary,
        },
    )
    return row


async def list_release_environments(
    db: AsyncSession,
    *,
    namespace_id: int,
    include_retired: bool = False,
) -> list[ReleaseEnvironment]:
    statement = select(ReleaseEnvironment).where(ReleaseEnvironment.namespace_id == namespace_id)
    if not include_retired:
        statement = statement.where(ReleaseEnvironment.status == ReleaseEnvironmentStatus.ACTIVE)
    statement = statement.order_by(ReleaseEnvironment.promotion_order, ReleaseEnvironment.id)
    return list((await db.scalars(statement)).all())


async def get_release_environment(db: AsyncSession, public_id: str) -> ReleaseEnvironment:
    row = await db.scalar(select(ReleaseEnvironment).where(ReleaseEnvironment.public_id == public_id))
    if row is None:
        raise ReleaseControlNotFoundError("Release Environment was not found")
    return row


async def create_release_policy(
    db: AsyncSession,
    *,
    request: ReleasePolicyCreate,
    actor: User,
) -> ReleasePolicy:
    await require_active_namespace(db, request.namespace_id)
    row = ReleasePolicy(
        public_id=_public_id("rpol"),
        namespace_id=request.namespace_id,
        name=request.name,
        description=request.description,
        status=ReleasePolicyStatus.ACTIVE,
        created_by_user_id=actor.id,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        if _is_unique_violation(exc):
            raise ReleaseControlConflictError("Release Policy name already exists") from exc
        raise
    await audit(
        db,
        user=actor,
        action="release.policy.created",
        resource_type="release_policy",
        resource_id=row.id,
        namespace_id=row.namespace_id,
        details={"public_id": row.public_id, "name": row.name},
    )
    return row


async def get_release_policy(db: AsyncSession, public_id: str) -> ReleasePolicy:
    row = await db.scalar(
        select(ReleasePolicy)
        .options(
            selectinload(ReleasePolicy.versions).selectinload(ReleasePolicyVersion.target_environment)
        )
        .where(ReleasePolicy.public_id == public_id)
    )
    if row is None:
        raise ReleaseControlNotFoundError("Release Policy was not found")
    return row


async def list_release_policies(
    db: AsyncSession,
    *,
    namespace_id: int,
    limit: int = 100,
) -> list[ReleasePolicy]:
    return list(
        (
            await db.scalars(
                select(ReleasePolicy)
                .options(
                    selectinload(ReleasePolicy.versions).selectinload(
                        ReleasePolicyVersion.target_environment
                    )
                )
                .where(ReleasePolicy.namespace_id == namespace_id)
                .order_by(ReleasePolicy.created_at.desc(), ReleasePolicy.id.desc())
                .limit(limit)
            )
        ).all()
    )


def _policy_rules_document(rules: list[ReleasePolicyRuleIn]) -> list[dict[str, Any]]:
    return [rule.model_dump(mode="json", exclude_none=True) for rule in rules]


async def create_release_policy_version(
    db: AsyncSession,
    *,
    policy_public_id: str,
    request: ReleasePolicyVersionCreate,
    actor: User,
) -> ReleasePolicyVersion:
    policy = await db.scalar(
        select(ReleasePolicy).where(ReleasePolicy.public_id == policy_public_id).with_for_update()
    )
    if policy is None:
        raise ReleaseControlNotFoundError("Release Policy was not found")
    if policy.namespace_id != request.namespace_id:
        raise ReleaseControlTenantMismatchError("Release Policy belongs to another Namespace")
    if policy.status != ReleasePolicyStatus.ACTIVE:
        raise ReleaseControlStateError("Release Policy is not active")
    environment = await get_release_environment(db, request.target_environment_public_id)
    if environment.namespace_id != request.namespace_id:
        raise ReleaseControlTenantMismatchError("target Environment belongs to another Namespace")
    if environment.status != ReleaseEnvironmentStatus.ACTIVE:
        raise ReleaseControlStateError("target Environment is not active")
    version = int(
        await db.scalar(
            select(func.coalesce(func.max(ReleasePolicyVersion.version), 0)).where(
                ReleasePolicyVersion.policy_id == policy.id
            )
        )
        or 0
    ) + 1
    rules_document = _policy_rules_document(request.rules)
    rules_digest = _digest(rules_document)
    content = {
        "schema_name": POLICY_SCHEMA_NAME,
        "schema_version": POLICY_SCHEMA_VERSION,
        "policy_public_id": policy.public_id,
        "version": version,
        "target_environment_public_id": environment.public_id,
        "mode": request.mode.value,
        "rules": rules_document,
    }
    row = ReleasePolicyVersion(
        public_id=_public_id("rpolv"),
        namespace_id=policy.namespace_id,
        policy_id=policy.id,
        version=version,
        target_environment_id=environment.id,
        mode=request.mode,
        rules_json=rules_document,
        rule_count=len(rules_document),
        rules_digest=rules_digest,
        content_digest=_digest(content),
        schema_name=POLICY_SCHEMA_NAME,
        schema_version=POLICY_SCHEMA_VERSION,
        created_by_user_id=actor.id,
    )
    row.policy = policy
    row.target_environment = environment
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        if _is_unique_violation(exc):
            raise ReleaseControlConflictError("identical Release Policy version already exists") from exc
        raise
    await audit(
        db,
        user=actor,
        action="release.policy_version.created",
        resource_type="release_policy_version",
        resource_id=row.id,
        namespace_id=row.namespace_id,
        details={
            "public_id": row.public_id,
            "policy_public_id": policy.public_id,
            "version": row.version,
            "target_environment_public_id": environment.public_id,
            "mode": row.mode.value,
            "rule_count": row.rule_count,
            "rules_digest": row.rules_digest,
            "content_digest": row.content_digest,
        },
    )
    await enqueue_domain_event(db, build_release_policy_version_created(row), guaranteed_new=True)
    return row


async def get_release_policy_version(db: AsyncSession, public_id: str) -> ReleasePolicyVersion:
    row = await db.scalar(
        select(ReleasePolicyVersion)
        .options(
            selectinload(ReleasePolicyVersion.policy),
            selectinload(ReleasePolicyVersion.target_environment),
        )
        .where(ReleasePolicyVersion.public_id == public_id)
    )
    if row is None:
        raise ReleaseControlNotFoundError("Release PolicyVersion was not found")
    return row


def release_policy_version_out(row: ReleasePolicyVersion) -> ReleasePolicyVersionOut:
    return ReleasePolicyVersionOut(
        public_id=row.public_id,
        namespace_id=row.namespace_id,
        policy_public_id=row.policy.public_id,
        version=row.version,
        target_environment_public_id=row.target_environment.public_id,
        target_environment_name=row.target_environment.name,
        mode=row.mode,
        rules=[ReleasePolicyRuleIn.model_validate(rule) for rule in row.rules_json],
        rule_count=row.rule_count,
        rules_digest=row.rules_digest,
        content_digest=row.content_digest,
        schema_name=row.schema_name,
        schema_version=row.schema_version,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
    )


def release_policy_out(row: ReleasePolicy) -> ReleasePolicyOut:
    return ReleasePolicyOut(
        public_id=row.public_id,
        namespace_id=row.namespace_id,
        name=row.name,
        description=row.description,
        status=row.status,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        versions=[release_policy_version_out(version) for version in row.versions],
    )


async def _load_package_version(db: AsyncSession, public_id: str) -> AgentPackageVersion:
    row = await db.scalar(
        select(AgentPackageVersion)
        .options(
            selectinload(AgentPackageVersion.package),
            selectinload(AgentPackageVersion.signing_key),
            selectinload(AgentPackageVersion.components),
            selectinload(AgentPackageVersion.sbom),
        )
        .where(AgentPackageVersion.public_id == public_id)
    )
    if row is None:
        raise ReleaseControlReferenceError("PackageVersion was not found")
    return row


async def _load_deployment(db: AsyncSession, public_id: str) -> AgentDeployment:
    row = await db.scalar(select(AgentDeployment).where(AgentDeployment.public_id == public_id))
    if row is None:
        raise ReleaseControlReferenceError("Deployment was not found")
    return row


async def get_release_candidate(db: AsyncSession, public_id: str) -> ReleaseCandidate:
    row = await db.scalar(
        select(ReleaseCandidate).options(*_candidate_options()).where(ReleaseCandidate.public_id == public_id)
    )
    if row is None:
        raise ReleaseControlNotFoundError("ReleaseCandidate was not found")
    return row


async def list_release_candidates(
    db: AsyncSession,
    *,
    namespace_id: int,
    environment_public_id: str | None = None,
    limit: int = 100,
) -> list[ReleaseCandidate]:
    statement = (
        select(ReleaseCandidate)
        .options(*_candidate_options())
        .where(ReleaseCandidate.namespace_id == namespace_id)
        .order_by(ReleaseCandidate.created_at.desc(), ReleaseCandidate.id.desc())
        .limit(limit)
    )
    if environment_public_id is not None:
        statement = statement.join(
            ReleaseEnvironment,
            ReleaseEnvironment.id == ReleaseCandidate.target_environment_id,
        ).where(ReleaseEnvironment.public_id == environment_public_id)
    return list((await db.scalars(statement)).all())


async def create_release_candidate(
    db: AsyncSession,
    *,
    request: ReleaseCandidateCreate,
    actor: User,
) -> ReleaseCandidate:
    await require_active_namespace(db, request.namespace_id)
    package_version = await _load_package_version(db, request.package_version_public_id)
    deployment = await _load_deployment(db, request.deployment_public_id)
    environment = await get_release_environment(db, request.target_environment_public_id)
    for resource_name, resource_namespace_id in (
        ("PackageVersion", package_version.namespace_id),
        ("Deployment", deployment.namespace_id),
        ("Environment", environment.namespace_id),
    ):
        if resource_namespace_id != request.namespace_id:
            raise ReleaseControlTenantMismatchError(f"{resource_name} belongs to another Namespace")
    if package_version.status != AgentPackageVersionStatus.VERIFIED:
        raise ReleaseControlReferenceError("PackageVersion must be VERIFIED")
    if environment.status != ReleaseEnvironmentStatus.ACTIVE:
        raise ReleaseControlStateError("target Environment is not active")
    if deployment.status != AgentDeploymentStatus.REGISTERED:
        raise ReleaseControlStateError("ReleaseCandidate requires a REGISTERED Deployment")
    if deployment.package_version_id != package_version.id:
        raise ReleaseControlReferenceError("Deployment must pin the exact PackageVersion")
    if deployment.agent_asset_id != package_version.package.agent_asset_id:
        raise ReleaseControlReferenceError("Deployment and PackageVersion must refer to the same Agent")
    if deployment.environment.casefold() != environment.name.casefold():
        raise ReleaseControlReferenceError("Deployment environment must match target Environment name")

    baseline: ReleaseCandidate | None = None
    if request.baseline_candidate_public_id is not None:
        baseline = await get_release_candidate(db, request.baseline_candidate_public_id)
        if baseline.namespace_id != request.namespace_id:
            raise ReleaseControlTenantMismatchError("baseline candidate belongs to another Namespace")
        if baseline.package_version.package_id != package_version.package_id:
            raise ReleaseControlReferenceError("baseline candidate must use the same Agent Package")
        if baseline.target_environment.promotion_order >= environment.promotion_order:
            raise ReleaseControlReferenceError("baseline candidate must belong to an earlier Environment")

    subject = {
        "schema_name": CANDIDATE_SCHEMA_NAME,
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "namespace_id": request.namespace_id,
        "package_version_public_id": package_version.public_id,
        "package_manifest_digest": package_version.manifest_digest,
        "deployment_public_id": deployment.public_id,
        "deployment_revision": deployment.revision,
        "deployment_configuration_digest": deployment.configuration_digest,
        "target_environment_public_id": environment.public_id,
        "baseline_candidate_public_id": baseline.public_id if baseline is not None else None,
    }
    candidate_digest = _digest(subject)
    replay = await db.scalar(
        select(ReleaseCandidate)
        .options(*_candidate_options())
        .where(
            ReleaseCandidate.namespace_id == request.namespace_id,
            ReleaseCandidate.idempotency_key == request.idempotency_key,
        )
    )
    if replay is not None:
        if replay.candidate_digest == candidate_digest:
            return replay
        raise ReleaseControlConflictError("idempotency key is bound to another ReleaseCandidate")
    exact = await db.scalar(
        select(ReleaseCandidate)
        .options(*_candidate_options())
        .where(
            ReleaseCandidate.namespace_id == request.namespace_id,
            ReleaseCandidate.candidate_digest == candidate_digest,
        )
    )
    if exact is not None:
        return exact
    row = ReleaseCandidate(
        public_id=_public_id("rcand"),
        namespace_id=request.namespace_id,
        package_version_id=package_version.id,
        deployment_id=deployment.id,
        target_environment_id=environment.id,
        baseline_candidate_id=baseline.id if baseline is not None else None,
        idempotency_key=request.idempotency_key,
        candidate_digest=candidate_digest,
        schema_name=CANDIDATE_SCHEMA_NAME,
        schema_version=CANDIDATE_SCHEMA_VERSION,
        created_by_user_id=actor.id,
    )
    row.package_version = package_version
    row.deployment = deployment
    row.target_environment = environment
    row.baseline_candidate = baseline
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        if _is_unique_violation(exc):
            raise ReleaseControlConflictError("ReleaseCandidate subject or idempotency key already exists") from exc
        raise
    await audit(
        db,
        user=actor,
        action="release.candidate.created",
        resource_type="release_candidate",
        resource_id=row.id,
        namespace_id=row.namespace_id,
        details={
            "public_id": row.public_id,
            "package_version_public_id": package_version.public_id,
            "package_manifest_digest": package_version.manifest_digest,
            "deployment_public_id": deployment.public_id,
            "deployment_revision": deployment.revision,
            "deployment_configuration_digest": deployment.configuration_digest,
            "target_environment_public_id": environment.public_id,
            "baseline_candidate_public_id": baseline.public_id if baseline is not None else None,
            "candidate_digest": candidate_digest,
        },
    )
    await enqueue_domain_event(db, build_release_candidate_created(row), guaranteed_new=True)
    return await get_release_candidate(db, row.public_id)


def release_candidate_out(row: ReleaseCandidate) -> ReleaseCandidateOut:
    return ReleaseCandidateOut(
        public_id=row.public_id,
        namespace_id=row.namespace_id,
        package_public_id=row.package_version.package.public_id,
        package_name=row.package_version.package.name,
        package_version_public_id=row.package_version.public_id,
        package_version=row.package_version.version,
        package_manifest_digest=row.package_version.manifest_digest,
        deployment_public_id=row.deployment.public_id,
        deployment_revision=row.deployment.revision,
        deployment_configuration_digest=row.deployment.configuration_digest,
        runtime_id=row.deployment.runtime_id,
        target_environment_public_id=row.target_environment.public_id,
        target_environment_name=row.target_environment.name,
        target_environment_kind=row.target_environment.kind,
        baseline_candidate_public_id=(
            row.baseline_candidate.public_id if row.baseline_candidate is not None else None
        ),
        idempotency_key=row.idempotency_key,
        candidate_digest=row.candidate_digest,
        schema_name=row.schema_name,
        schema_version=row.schema_version,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
    )


@dataclass(frozen=True, slots=True)
class _RuleEvidence:
    verdict: ReleasePolicyRuleVerdict
    reason_code: str
    evidence_kind: str
    evidence_ref: str
    evidence_digest: str
    metrics: dict[str, Any]
    observed_at: datetime


def _evidence_digest(*parts: Any) -> str:
    return _digest(list(parts))


def _optional_missing(rule: ReleasePolicyRuleIn, *, reason_code: str, kind: str, ref: str) -> _RuleEvidence:
    verdict = ReleasePolicyRuleVerdict.FAIL if rule.required else ReleasePolicyRuleVerdict.NOT_APPLICABLE
    return _RuleEvidence(
        verdict=verdict,
        reason_code=reason_code,
        evidence_kind=kind,
        evidence_ref=ref,
        evidence_digest=_evidence_digest(kind, ref, reason_code),
        metrics={"evidence_present": False},
        observed_at=datetime.now(timezone.utc),
    )


def _baseline_manifest(candidate: ReleaseCandidate) -> AgentPackageManifestV2 | None:
    if candidate.baseline_candidate is None:
        return None
    return AgentPackageManifestV2.model_validate(candidate.baseline_candidate.package_version.manifest_json)


def _severity_rank(value: VulnerabilitySeverity) -> int:
    return {
        VulnerabilitySeverity.NONE: 0,
        VulnerabilitySeverity.LOW: 1,
        VulnerabilitySeverity.MEDIUM: 2,
        VulnerabilitySeverity.HIGH: 3,
        VulnerabilitySeverity.CRITICAL: 4,
        VulnerabilitySeverity.UNKNOWN: 5,
    }[value]


def _normalize_severity(value: Any) -> VulnerabilitySeverity:
    normalized = str(value or "").strip().casefold()
    mapping = {
        "none": VulnerabilitySeverity.NONE,
        "negligible": VulnerabilitySeverity.LOW,
        "low": VulnerabilitySeverity.LOW,
        "moderate": VulnerabilitySeverity.MEDIUM,
        "medium": VulnerabilitySeverity.MEDIUM,
        "high": VulnerabilitySeverity.HIGH,
        "critical": VulnerabilitySeverity.CRITICAL,
    }
    return mapping.get(normalized, VulnerabilitySeverity.UNKNOWN)


async def _vulnerability_summary(
    candidate: ReleaseCandidate,
    *,
    storage: ArtifactStorageService,
) -> tuple[dict[str, Any], str]:
    package_version = candidate.package_version
    sbom_bytes = await storage.read_object_bytes_async(
        package_version.sbom.object_key,
        max_bytes=MAX_SBOM_BYTES,
    )
    if hashlib.sha256(sbom_bytes).hexdigest() != package_version.sbom.document_sha256:
        raise ReleaseControlStateError("SBOM digest verification failed during Policy evaluation")
    document = json.loads(sbom_bytes.decode("utf-8"))
    severities: list[VulnerabilitySeverity] = []
    if package_version.sbom.format.value == "cyclonedx-json":
        vulnerabilities = document.get("vulnerabilities", [])
        if not isinstance(vulnerabilities, list) or len(vulnerabilities) > MAX_VULNERABILITIES:
            raise ReleaseControlStateError("CycloneDX vulnerabilities are malformed or exceed the bound")
        for vulnerability in vulnerabilities:
            if not isinstance(vulnerability, dict):
                severities.append(VulnerabilitySeverity.UNKNOWN)
                continue
            ratings = vulnerability.get("ratings", [])
            if not isinstance(ratings, list) or not ratings:
                severities.append(VulnerabilitySeverity.UNKNOWN)
                continue
            normalized_ratings = [
                _normalize_severity(rating.get("severity"))
                for rating in ratings
                if isinstance(rating, dict)
            ]
            severities.append(
                max(normalized_ratings, key=_severity_rank)
                if normalized_ratings
                else VulnerabilitySeverity.UNKNOWN
            )
    else:
        # SPDX JSON has no portable first-class vulnerability collection. An
        # empty package is known clean only when no vulnerability-like external
        # reference is declared; declarations without a normalized severity are
        # conservatively UNKNOWN.
        packages = document.get("packages", [])
        if not isinstance(packages, list):
            raise ReleaseControlStateError("SPDX packages are malformed")
        for package in packages:
            if not isinstance(package, dict):
                continue
            references = package.get("externalRefs", [])
            if not isinstance(references, list):
                severities.append(VulnerabilitySeverity.UNKNOWN)
                continue
            for reference in references:
                if not isinstance(reference, dict):
                    continue
                ref_type = str(reference.get("referenceType", "")).casefold()
                if "vulnerab" in ref_type or "security" in ref_type:
                    severities.append(VulnerabilitySeverity.UNKNOWN)
    counts = {severity.value: severities.count(severity) for severity in VulnerabilitySeverity}
    maximum = max(severities, key=_severity_rank) if severities else VulnerabilitySeverity.NONE
    return (
        {
            "vulnerability_count": len(severities),
            "maximum_severity": maximum.value,
            "severity_counts": counts,
        },
        package_version.sbom.document_sha256,
    )


async def _evaluate_rule(
    db: AsyncSession,
    *,
    candidate: ReleaseCandidate,
    rule: ReleasePolicyRuleIn,
    storage: ArtifactStorageService,
) -> _RuleEvidence:
    package_version = candidate.package_version
    manifest = AgentPackageManifestV2.model_validate(package_version.manifest_json)
    baseline_manifest = _baseline_manifest(candidate)
    now = datetime.now(timezone.utc)

    if rule.rule_type == ReleasePolicyRuleType.PACKAGE_EVIDENCE_VERIFIED:
        verification = await verify_package_version(db, public_id=package_version.public_id, storage=storage)
        metrics = {
            "signature_valid": verification.signature_valid,
            "sbom_digest_valid": verification.sbom_digest_valid,
            "sbom_component_coverage_valid": verification.sbom_component_coverage_valid,
            "manifest_digest": verification.manifest_digest,
            "graph_digest": verification.graph_digest,
            "provenance_digest": verification.provenance_digest,
        }
        return _RuleEvidence(
            verdict=(
                ReleasePolicyRuleVerdict.PASS if verification.verified else ReleasePolicyRuleVerdict.FAIL
            ),
            reason_code=("package_evidence_verified" if verification.verified else "package_evidence_invalid"),
            evidence_kind="package_verification",
            evidence_ref=package_version.public_id,
            evidence_digest=_digest(metrics),
            metrics=metrics,
            observed_at=now,
        )

    if rule.rule_type == ReleasePolicyRuleType.SIGNING_KEY_ACTIVE:
        active = package_version.signing_key.status == PackageSigningKeyStatus.ACTIVE
        metrics = {
            "status": package_version.signing_key.status.value,
            "fingerprint": package_version.signing_key.public_key_fingerprint,
        }
        return _RuleEvidence(
            verdict=ReleasePolicyRuleVerdict.PASS if active else ReleasePolicyRuleVerdict.FAIL,
            reason_code="signing_key_active" if active else "signing_key_revoked",
            evidence_kind="package_signing_key",
            evidence_ref=package_version.signing_key.public_id,
            evidence_digest=_digest(metrics),
            metrics=metrics,
            observed_at=ensure_utc(package_version.signature_verified_at),
        )

    if rule.rule_type == ReleasePolicyRuleType.EVALUATION_GATE_PASS:
        selector = ReleaseEvidenceSelector(
            namespace_id=candidate.namespace_id,
            release_candidate_ref=candidate.public_id,
            deployment_public_id=candidate.deployment.public_id,
            deployment_revision=candidate.deployment.revision,
        )
        decision = await ReleaseGateService(
            evidence_lookup=DatabaseReleaseEvidenceLookupPort(db)
        ).evaluate_candidate(selector)
        metrics = {
            "outcome": decision.outcome,
            "reason_codes": list(decision.reason_codes),
            "evidence_count": len(decision.evidence),
        }
        return _RuleEvidence(
            verdict=(
                ReleasePolicyRuleVerdict.PASS
                if decision.outcome == "PASS"
                else ReleasePolicyRuleVerdict.FAIL
            ),
            reason_code=(
                "evaluation_gate_passed" if decision.outcome == "PASS" else decision.reason_codes[0]
            ),
            evidence_kind="candidate_evaluation_gate",
            evidence_ref=candidate.public_id,
            evidence_digest=decision.decision_digest,
            metrics=metrics,
            observed_at=max(
                (ensure_utc(item.observed_at) for item in decision.evidence),
                default=now,
            ),
        )

    if rule.rule_type == ReleasePolicyRuleType.RISK_TIER_ALLOWED:
        risk_tier = manifest.annotations.risk_tier if manifest.annotations is not None else None
        if risk_tier is None:
            return _optional_missing(
                rule,
                reason_code="risk_tier_missing",
                kind="package_manifest",
                ref=package_version.public_id,
            )
        allowed = risk_tier.casefold() in set(rule.allowed_risk_tiers or [])
        metrics = {"risk_tier": risk_tier.casefold(), "allowed_risk_tiers": rule.allowed_risk_tiers}
        return _RuleEvidence(
            verdict=ReleasePolicyRuleVerdict.PASS if allowed else ReleasePolicyRuleVerdict.FAIL,
            reason_code="risk_tier_allowed" if allowed else "risk_tier_forbidden",
            evidence_kind="package_manifest",
            evidence_ref=package_version.public_id,
            evidence_digest=package_version.manifest_digest,
            metrics=metrics,
            observed_at=ensure_utc(package_version.created_at),
        )

    if rule.rule_type == ReleasePolicyRuleType.MAX_TOOL_ADDITIONS:
        candidate_tools = {
            component.component_ref
            for component in package_version.components
            if component.component_type == PackageComponentType.TOOL
        }
        baseline_tools = (
            {
                component.component_ref
                for component in candidate.baseline_candidate.package_version.components
                if component.component_type == PackageComponentType.TOOL
            }
            if candidate.baseline_candidate is not None
            else set()
        )
        additions = sorted(candidate_tools - baseline_tools)
        maximum = int(rule.maximum_additions or 0)
        passed = len(additions) <= maximum
        metrics = {
            "candidate_tool_count": len(candidate_tools),
            "baseline_tool_count": len(baseline_tools),
            "addition_count": len(additions),
            "maximum_additions": maximum,
            "added_tool_refs": additions,
        }
        return _RuleEvidence(
            verdict=ReleasePolicyRuleVerdict.PASS if passed else ReleasePolicyRuleVerdict.FAIL,
            reason_code="tool_additions_within_limit" if passed else "tool_additions_exceeded",
            evidence_kind="package_component_diff",
            evidence_ref=package_version.public_id,
            evidence_digest=_digest(metrics),
            metrics=metrics,
            observed_at=ensure_utc(package_version.created_at),
        )

    if rule.rule_type == ReleasePolicyRuleType.FORBID_CAPABILITY_EXPANSION:
        candidate_capabilities = set(manifest.runtime.capabilities)
        baseline_capabilities = (
            set(baseline_manifest.runtime.capabilities) if baseline_manifest is not None else set()
        )
        capability_additions = candidate_capabilities - baseline_capabilities
        allowed_additions = set(rule.allowed_capabilities or [])
        forbidden = sorted(capability_additions - allowed_additions)
        capability_metrics = {
            "candidate_capability_count": len(candidate_capabilities),
            "baseline_capability_count": len(baseline_capabilities),
            "added_capabilities": sorted(capability_additions),
            "allowed_additions": sorted(allowed_additions),
            "forbidden_additions": forbidden,
        }
        return _RuleEvidence(
            verdict=ReleasePolicyRuleVerdict.PASS if not forbidden else ReleasePolicyRuleVerdict.FAIL,
            reason_code=("capability_expansion_allowed" if not forbidden else "capability_expansion_forbidden"),
            evidence_kind="package_runtime_diff",
            evidence_ref=package_version.public_id,
            evidence_digest=_digest(capability_metrics),
            metrics=capability_metrics,
            observed_at=ensure_utc(package_version.created_at),
        )

    if rule.rule_type == ReleasePolicyRuleType.MAX_VULNERABILITY_SEVERITY:
        try:
            vulnerability_metrics, evidence_digest = await _vulnerability_summary(
                candidate, storage=storage
            )
        except Exception:
            return _optional_missing(
                rule,
                reason_code="vulnerability_evidence_invalid",
                kind="package_sbom",
                ref=package_version.sbom.public_id,
            )
        actual = VulnerabilitySeverity(vulnerability_metrics["maximum_severity"])
        vulnerability_maximum = rule.maximum_severity or VulnerabilitySeverity.NONE
        passed = _severity_rank(actual) <= _severity_rank(vulnerability_maximum)
        vulnerability_metrics = {
            **vulnerability_metrics,
            "allowed_maximum_severity": vulnerability_maximum.value,
        }
        return _RuleEvidence(
            verdict=ReleasePolicyRuleVerdict.PASS if passed else ReleasePolicyRuleVerdict.FAIL,
            reason_code="vulnerability_threshold_passed" if passed else "vulnerability_threshold_exceeded",
            evidence_kind="package_sbom_vulnerability_summary",
            evidence_ref=package_version.sbom.public_id,
            evidence_digest=evidence_digest,
            metrics=vulnerability_metrics,
            observed_at=ensure_utc(package_version.sbom.stored_at),
        )

    if rule.rule_type == ReleasePolicyRuleType.ROLLBACK_TARGET_REQUIRED:
        if candidate.baseline_candidate is None:
            return _optional_missing(
                rule,
                reason_code="rollback_target_missing",
                kind="release_candidate",
                ref=candidate.public_id,
            )
        metrics = {
            "baseline_candidate_public_id": candidate.baseline_candidate.public_id,
            "baseline_candidate_digest": candidate.baseline_candidate.candidate_digest,
        }
        return _RuleEvidence(
            verdict=ReleasePolicyRuleVerdict.PASS,
            reason_code="rollback_target_pinned",
            evidence_kind="release_candidate",
            evidence_ref=candidate.baseline_candidate.public_id,
            evidence_digest=candidate.baseline_candidate.candidate_digest,
            metrics=metrics,
            observed_at=ensure_utc(candidate.baseline_candidate.created_at),
        )

    raise ReleaseControlStateError(f"unsupported Release Policy rule: {rule.rule_type.value}")


async def get_release_policy_decision(db: AsyncSession, public_id: str) -> ReleasePolicyDecision:
    row = await db.scalar(
        select(ReleasePolicyDecision)
        .options(*_decision_options())
        .where(ReleasePolicyDecision.public_id == public_id)
    )
    if row is None:
        raise ReleaseControlNotFoundError("Release PolicyDecision was not found")
    return row


async def list_release_policy_decisions(
    db: AsyncSession,
    *,
    namespace_id: int,
    candidate_public_id: str | None = None,
    limit: int = 100,
) -> list[ReleasePolicyDecision]:
    statement = (
        select(ReleasePolicyDecision)
        .options(*_decision_options())
        .where(ReleasePolicyDecision.namespace_id == namespace_id)
        .order_by(ReleasePolicyDecision.created_at.desc(), ReleasePolicyDecision.id.desc())
        .limit(limit)
    )
    if candidate_public_id is not None:
        statement = statement.join(
            ReleaseCandidate,
            ReleaseCandidate.id == ReleasePolicyDecision.candidate_id,
        ).where(ReleaseCandidate.public_id == candidate_public_id)
    return list((await db.scalars(statement)).all())


async def evaluate_release_policy(
    db: AsyncSession,
    *,
    candidate_public_id: str,
    request: ReleasePolicyEvaluationCreate,
    actor: User,
    storage: ArtifactStorageService = artifact_service,
) -> ReleasePolicyDecision:
    started_ns = time.perf_counter_ns()
    candidate = await get_release_candidate(db, candidate_public_id)
    policy_version = await get_release_policy_version(db, request.policy_version_public_id)
    if candidate.namespace_id != request.namespace_id:
        raise ReleaseControlTenantMismatchError("ReleaseCandidate belongs to another Namespace")
    if policy_version.namespace_id != request.namespace_id:
        raise ReleaseControlTenantMismatchError("PolicyVersion belongs to another Namespace")
    if policy_version.target_environment_id != candidate.target_environment_id:
        raise ReleaseControlReferenceError("PolicyVersion target does not match candidate Environment")
    rules = [ReleasePolicyRuleIn.model_validate(rule) for rule in policy_version.rules_json]
    evaluated = [
        await _evaluate_rule(db, candidate=candidate, rule=rule, storage=storage)
        for rule in rules
    ]
    evidence_document = [
        {
            "position": position,
            "rule_id": rule.rule_id,
            "rule_type": rule.rule_type.value,
            "verdict": result.verdict.value,
            "reason_code": result.reason_code,
            "evidence_kind": result.evidence_kind,
            "evidence_ref": result.evidence_ref,
            "evidence_digest": result.evidence_digest,
            "metrics": result.metrics,
            "observed_at": ensure_utc(result.observed_at).isoformat().replace("+00:00", "Z"),
        }
        for position, (rule, result) in enumerate(zip(rules, evaluated, strict=True))
    ]
    # Observation timestamps are useful display metadata but are not evidence
    # identity. In particular, a fail-closed "missing" observation is made at
    # evaluation time. Excluding that timestamp keeps exact retries stable while
    # every substantive ref/digest/verdict/metric change still yields a new
    # snapshot.
    evidence_snapshot_digest = _digest(
        [
            {key: value for key, value in item.items() if key != "observed_at"}
            for item in evidence_document
        ]
    )
    failures = [item for item in evidence_document if item["verdict"] == ReleasePolicyRuleVerdict.FAIL.value]
    raw_outcome = ReleasePolicyRawOutcome.FAIL if failures else ReleasePolicyRawOutcome.PASS
    would_block = raw_outcome == ReleasePolicyRawOutcome.FAIL
    enforcement_outcome = (
        ReleasePolicyEnforcementOutcome.ALLOW
        if not would_block or policy_version.mode == ReleasePolicyMode.SHADOW
        else (
            ReleasePolicyEnforcementOutcome.WARN
            if policy_version.mode == ReleasePolicyMode.WARN
            else ReleasePolicyEnforcementOutcome.BLOCK
        )
    )
    reason_codes = (
        [str(item["reason_code"]) for item in failures]
        if failures
        else ["all_release_policy_rules_passed"]
    )
    decision_document = {
        "schema_name": DECISION_SCHEMA_NAME,
        "schema_version": DECISION_SCHEMA_VERSION,
        "candidate_public_id": candidate.public_id,
        "candidate_digest": candidate.candidate_digest,
        "policy_version_public_id": policy_version.public_id,
        "policy_content_digest": policy_version.content_digest,
        "policy_mode": policy_version.mode.value,
        "raw_outcome": raw_outcome.value,
        "enforcement_outcome": enforcement_outcome.value,
        "would_block": would_block,
        "reason_codes": reason_codes,
        "evidence_snapshot_digest": evidence_snapshot_digest,
    }
    decision_digest = _digest(decision_document)

    replay = await db.scalar(
        select(ReleasePolicyDecision)
        .options(*_decision_options())
        .where(
            ReleasePolicyDecision.namespace_id == request.namespace_id,
            ReleasePolicyDecision.idempotency_key == request.idempotency_key,
        )
    )
    if replay is not None:
        if replay.decision_digest == decision_digest:
            return replay
        raise ReleaseControlConflictError("idempotency key is bound to another Policy evidence snapshot")
    exact = await db.scalar(
        select(ReleasePolicyDecision)
        .options(*_decision_options())
        .where(
            ReleasePolicyDecision.candidate_id == candidate.id,
            ReleasePolicyDecision.policy_version_id == policy_version.id,
            ReleasePolicyDecision.evidence_snapshot_digest == evidence_snapshot_digest,
        )
    )
    if exact is not None:
        return exact

    elapsed_ms = max(0, (time.perf_counter_ns() - started_ns) // 1_000_000)
    row = ReleasePolicyDecision(
        public_id=_public_id("rpdec"),
        namespace_id=request.namespace_id,
        candidate_id=candidate.id,
        policy_version_id=policy_version.id,
        idempotency_key=request.idempotency_key,
        raw_outcome=raw_outcome,
        enforcement_outcome=enforcement_outcome,
        would_block=would_block,
        reason_codes_json=reason_codes,
        evidence_snapshot_digest=evidence_snapshot_digest,
        decision_digest=decision_digest,
        evaluation_duration_ms=elapsed_ms,
        schema_name=DECISION_SCHEMA_NAME,
        schema_version=DECISION_SCHEMA_VERSION,
        evaluated_by_user_id=actor.id,
        rule_results=[
            ReleasePolicyRuleResult(
                position=position,
                rule_id=rule.rule_id,
                rule_type=rule.rule_type,
                verdict=result.verdict,
                reason_code=result.reason_code,
                evidence_kind=result.evidence_kind,
                evidence_ref=result.evidence_ref,
                evidence_digest=result.evidence_digest,
                metrics_json=result.metrics,
                observed_at=ensure_utc(result.observed_at),
            )
            for position, (rule, result) in enumerate(zip(rules, evaluated, strict=True))
        ],
    )
    row.candidate = candidate
    row.policy_version = policy_version
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        if _is_unique_violation(exc):
            raise ReleaseControlConflictError("Policy decision evidence or idempotency key already exists") from exc
        raise
    await audit(
        db,
        user=actor,
        action="release.policy_evaluated",
        resource_type="release_policy_decision",
        resource_id=row.id,
        namespace_id=row.namespace_id,
        details={
            "public_id": row.public_id,
            "candidate_public_id": candidate.public_id,
            "policy_version_public_id": policy_version.public_id,
            "policy_mode": policy_version.mode.value,
            "raw_outcome": row.raw_outcome.value,
            "enforcement_outcome": row.enforcement_outcome.value,
            "would_block": row.would_block,
            "reason_codes": row.reason_codes_json,
            "rule_count": len(row.rule_results),
            "evidence_snapshot_digest": row.evidence_snapshot_digest,
            "decision_digest": row.decision_digest,
            "evaluation_duration_ms": row.evaluation_duration_ms,
        },
    )
    await enqueue_domain_event(db, build_release_policy_decision_recorded(row), guaranteed_new=True)
    return await get_release_policy_decision(db, row.public_id)


def release_policy_decision_out(row: ReleasePolicyDecision) -> ReleasePolicyDecisionOut:
    return ReleasePolicyDecisionOut(
        public_id=row.public_id,
        namespace_id=row.namespace_id,
        candidate_public_id=row.candidate.public_id,
        policy_public_id=row.policy_version.policy.public_id,
        policy_version_public_id=row.policy_version.public_id,
        policy_version=row.policy_version.version,
        policy_mode=row.policy_version.mode,
        raw_outcome=row.raw_outcome,
        enforcement_outcome=row.enforcement_outcome,
        would_block=row.would_block,
        reason_codes=list(row.reason_codes_json),
        evidence_snapshot_digest=row.evidence_snapshot_digest,
        decision_digest=row.decision_digest,
        evaluation_duration_ms=row.evaluation_duration_ms,
        schema_name=row.schema_name,
        schema_version=row.schema_version,
        evaluated_by_user_id=row.evaluated_by_user_id,
        created_at=row.created_at,
        rule_results=[
            ReleasePolicyRuleResultOut(
                position=result.position,
                rule_id=result.rule_id,
                rule_type=result.rule_type,
                verdict=result.verdict,
                reason_code=result.reason_code,
                evidence_kind=result.evidence_kind,
                evidence_ref=result.evidence_ref,
                evidence_digest=result.evidence_digest,
                metrics=result.metrics_json,
                observed_at=result.observed_at,
            )
            for result in row.rule_results
        ],
    )
