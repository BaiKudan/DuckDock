from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.api.v2.router import api_router
from app.core.api_token_security import hash_reporter_token_secret
from app.models.audit import AuditLog
from app.models.control_plane import (
    AIAsset,
    AssetType,
    ReporterCredential,
    RuntimeDeployType,
    RuntimeInstance,
    RuntimeProvider,
)
from app.models.deployment import AgentDeployment, AgentDeploymentStatus
from app.models.execution import (
    AgentRun,
    AgentRunStatus,
    ContentCaptureMode,
    TrustLevel,
    TrustSource,
)
from app.models.namespace import Namespace, NamespaceMember, NamespaceRole
from app.models.outbox import OutboxEvent
from app.models.package_registry import (
    AgentPackage,
    AgentPackageStatus,
    AgentPackageVersion,
    AgentPackageVersionStatus,
    PackageComponent,
    PackageComponentType,
    PackageSbom,
    PackageSbomFormat,
    PackageSignatureAlgorithm,
    PackageSigningKey,
    PackageSigningKeyStatus,
)
from app.models.release_control import (
    ReleaseApprovalDecision,
    ReleaseApprovalRole,
    ReleaseCanaryOutcome,
    ReleaseEnvironmentRelease,
    ReleaseEnvironmentReleaseStatus,
    ReleaseExceptionReviewDecision,
    ReleasePolicyEnforcementOutcome,
    ReleasePolicyMode,
    ReleasePolicyRawOutcome,
    ReleasePolicyRuleType,
    ReleasePolicyRuleVerdict,
    ReleasePromotionStatus,
    ReleasePromotionStrategy,
    ReleaseReceiptKind,
    ReleaseReceiptStatus,
    ReleaseRollback,
    ReleaseRollbackStatus,
)
from app.models.user import SystemRole, User
from app.schemas.package_registry import AgentPackageVersionVerificationOut
from app.schemas.release_control import (
    ReleaseCandidateApprovalCreate,
    ReleaseCandidateCreate,
    ReleaseCanaryConfig,
    ReleaseCanaryEvaluationCreate,
    ReleaseDeploymentReceiptCreate,
    ReleaseEnvironmentCreate,
    ReleasePolicyCreate,
    ReleasePolicyEvaluationCreate,
    ReleasePolicyExceptionCreate,
    ReleasePolicyExceptionReviewCreate,
    ReleasePolicyRuleIn,
    ReleasePolicyVersionCreate,
    ReleasePromotionCreate,
    RuntimeReceiptReportedStatus,
    VulnerabilitySeverity,
)
from app.services.deployment_service import DeploymentImmutableError, update_registered_deployment
from app.services.release_control_service import (
    ReleaseControlConflictError,
    ReleaseControlStateError,
    create_release_candidate,
    create_release_environment,
    create_release_policy,
    create_release_policy_version,
    evaluate_release_policy,
    get_release_candidate,
)
from app.services.release_promotion_service import (
    create_release_candidate_approval,
    create_release_policy_exception,
    create_release_promotion,
    evaluate_release_canary,
    list_release_environment_releases,
    record_release_deployment_receipt,
    review_release_policy_exception,
)
from app.services.reporter_identity_service import (
    ReporterExecutionIdentity,
    ReporterScopeError,
    authenticate_reporter_release_credential,
)
from app.services.report_upload_service import generate_runtime_report_token


def _canonical_bytes(value: dict) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


class FakeReleaseStorage:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    async def read_object_bytes_async(self, object_key: str, *, max_bytes: int | None = None) -> bytes:
        payload = self.objects[object_key]
        assert max_bytes is None or len(payload) <= max_bytes
        return payload


async def _seed_release_subject(db, suffix: str = "main"):
    actor = User(
        username=f"release-control-{suffix}",
        email=f"release-control-{suffix}@example.test",
        hashed_password="unused",
        system_role=SystemRole.USER,
    )
    db.add(actor)
    await db.flush()
    namespace = Namespace(name=f"release-control-{suffix}", owner_id=actor.id)
    db.add(namespace)
    await db.flush()
    db.add(
        NamespaceMember(
            namespace_id=namespace.id,
            user_id=actor.id,
            role=NamespaceRole.ADMIN,
        )
    )
    asset = AIAsset(
        namespace_id=namespace.id,
        asset_type=AssetType.AGENT,
        name=f"release-agent-{suffix}",
        source_provider=RuntimeProvider.CUSTOM,
        external_id=f"release-agent-{suffix}",
    )
    runtime = RuntimeInstance(
        namespace_id=namespace.id,
        provider=RuntimeProvider.CUSTOM,
        name=f"release-runtime-{suffix}",
        deploy_type=RuntimeDeployType.PRIVATE,
    )
    db.add_all([asset, runtime])
    await db.flush()
    package = AgentPackage(
        public_id=f"pkg_release_{suffix}",
        namespace_id=namespace.id,
        namespace_ref=namespace.name,
        name=f"release-package-{suffix}",
        agent_asset_id=asset.id,
        agent_asset_ref=asset.external_id,
        status=AgentPackageStatus.ACTIVE,
        created_by_user_id=actor.id,
    )
    key = PackageSigningKey(
        public_id=f"pkey_release_{suffix}",
        namespace_id=namespace.id,
        key_id=f"release-key-{suffix}",
        algorithm=PackageSignatureAlgorithm.ED25519,
        public_key_pem="-----BEGIN PUBLIC KEY-----\nplaceholder\n-----END PUBLIC KEY-----\n",
        public_key_fingerprint="1" * 64,
        status=PackageSigningKeyStatus.ACTIVE,
        created_by_user_id=actor.id,
    )
    db.add_all([package, key])
    await db.flush()
    sbom_document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "components": [
            {
                "type": "application",
                "name": "runtime",
                "version": "1.0.0",
                "hashes": [{"alg": "SHA-256", "content": "a" * 64}],
            }
        ],
        "vulnerabilities": [],
    }
    sbom_bytes = _canonical_bytes(sbom_document)
    sbom_digest = hashlib.sha256(sbom_bytes).hexdigest()
    manifest = {
        "schema_version": "2.0",
        "package_id": package.public_id,
        "package_version": "1.0.0",
        "namespace_id": namespace.name,
        "agent": {"asset_id": asset.external_id, "name": asset.name},
        "runtime": {
            "harness": "hermes",
            "entrypoint": "agents/release.yaml",
            "minimum_harness_version": "1.0.0",
            "capabilities": ["model_call", "tool_call"],
        },
        "artifacts": [
            {
                "type": "skill",
                "asset_id": "release-skill",
                "version_id": "release-skill-v1",
                "uri": "s3://release/skill.zip",
                "sha256": "a" * 64,
                "media_type": "application/zip",
            }
        ],
        "provenance": {
            "source_repository": "https://github.com/example/release-agent",
            "source_revision": "2" * 40,
            "built_at": "2026-08-04T01:00:00Z",
            "builder_id": "release-builder",
            "build_id": "release-build-1",
        },
        "telemetry": {
            "schema_version": "1.0",
            "content_policy": "metadata_only",
            "required_attributes": [
                "duckdock.namespace_id",
                "duckdock.deployment_revision",
                "duckdock.component_digests",
            ],
        },
        "evaluation_policy_id": "release-eval-v1",
        "component_graph": {"edges": []},
        "sbom": {
            "format": "cyclonedx-json",
            "spec_version": "1.6",
            "document_sha256": sbom_digest,
            "media_type": "application/vnd.cyclonedx+json",
        },
        "annotations": {"owner": "release-team", "risk_tier": "medium"},
    }
    version = AgentPackageVersion(
        public_id=f"pkgv_release_{suffix}",
        namespace_id=namespace.id,
        package_id=package.id,
        version="1.0.0",
        status=AgentPackageVersionStatus.VERIFIED,
        manifest_schema_version="2.0",
        manifest_json=manifest,
        manifest_digest="3" * 64,
        graph_digest="4" * 64,
        evaluation_policy_id="release-eval-v1",
        source_repository="https://github.com/example/release-agent",
        source_revision="2" * 40,
        built_at=datetime(2026, 8, 4, 1, tzinfo=timezone.utc),
        builder_id="release-builder",
        build_id="release-build-1",
        provenance_digest="5" * 64,
        signing_key_id=key.id,
        signature_algorithm=PackageSignatureAlgorithm.ED25519,
        signature_value="A" * 88,
        signature_digest="6" * 64,
        signature_verified_at=datetime(2026, 8, 4, 1, 1, tzinfo=timezone.utc),
        idempotency_key=f"release-package-{suffix}",
        created_by_user_id=actor.id,
    )
    db.add(version)
    await db.flush()
    component = PackageComponent(
        package_version_id=version.id,
        position=0,
        component_ref="skill:release-skill:release-skill-v1",
        component_type=PackageComponentType.SKILL,
        asset_ref="release-skill",
        version_ref="release-skill-v1",
        uri="s3://release/skill.zip",
        sha256="a" * 64,
        media_type="application/zip",
    )
    sbom = PackageSbom(
        public_id=f"psbom_release_{suffix}",
        package_version_id=version.id,
        format=PackageSbomFormat.CYCLONEDX_JSON,
        spec_version="1.6",
        media_type="application/vnd.cyclonedx+json",
        document_sha256=sbom_digest,
        object_key=f"release/{suffix}/sbom.json",
        size_bytes=len(sbom_bytes),
        component_count=1,
    )
    db.add_all([component, sbom])
    await db.flush()
    deployment = AgentDeployment(
        public_id=f"dep_release_control_{suffix}",
        namespace_id=namespace.id,
        runtime_id=runtime.id,
        agent_asset_id=asset.id,
        package_version_id=version.id,
        external_deployment_id=f"release-control-{suffix}",
        environment="staging",
        revision=f"release-{suffix}-1",
        configuration_digest="7" * 64,
        status=AgentDeploymentStatus.REGISTERED,
        created_by_user_id=actor.id,
    )
    db.add(deployment)
    await db.flush()
    return (
        actor,
        namespace,
        key,
        version,
        sbom,
        deployment,
        FakeReleaseStorage({sbom.object_key: sbom_bytes}),
    )


def _rules(*, include_eval: bool) -> list[ReleasePolicyRuleIn]:
    rules = [
        ReleasePolicyRuleIn(
            rule_id="package-evidence",
            rule_type=ReleasePolicyRuleType.PACKAGE_EVIDENCE_VERIFIED,
        ),
        ReleasePolicyRuleIn(
            rule_id="signing-key",
            rule_type=ReleasePolicyRuleType.SIGNING_KEY_ACTIVE,
        ),
        ReleasePolicyRuleIn(
            rule_id="risk-tier",
            rule_type=ReleasePolicyRuleType.RISK_TIER_ALLOWED,
            allowed_risk_tiers=["low", "medium"],
        ),
        ReleasePolicyRuleIn(
            rule_id="tool-diff",
            rule_type=ReleasePolicyRuleType.MAX_TOOL_ADDITIONS,
            maximum_additions=0,
        ),
        ReleasePolicyRuleIn(
            rule_id="capability-diff",
            rule_type=ReleasePolicyRuleType.FORBID_CAPABILITY_EXPANSION,
            allowed_capabilities=["model_call", "tool_call"],
        ),
        ReleasePolicyRuleIn(
            rule_id="vulnerability-threshold",
            rule_type=ReleasePolicyRuleType.MAX_VULNERABILITY_SEVERITY,
            maximum_severity=VulnerabilitySeverity.NONE,
        ),
        ReleasePolicyRuleIn(
            rule_id="rollback-target",
            rule_type=ReleasePolicyRuleType.ROLLBACK_TARGET_REQUIRED,
            required=False,
        ),
    ]
    if include_eval:
        rules.insert(
            2,
            ReleasePolicyRuleIn(
                rule_id="evaluation-gate",
                rule_type=ReleasePolicyRuleType.EVALUATION_GATE_PASS,
            ),
        )
    return rules


async def test_release_policy_modes_are_explainable_idempotent_and_content_free(
    async_session,
    monkeypatch,
):
    actor, namespace, key, version, sbom, deployment, storage = await _seed_release_subject(
        async_session
    )

    async def fake_verify(*_args, **_kwargs):
        return AgentPackageVersionVerificationOut(
            package_version_public_id=version.public_id,
            manifest_digest=version.manifest_digest,
            graph_digest=version.graph_digest,
            provenance_digest=version.provenance_digest,
            signature_valid=True,
            signature_verified_at=version.signature_verified_at,
            signing_key_status=key.status,
            signing_key_fingerprint=key.public_key_fingerprint,
            sbom_digest_valid=True,
            sbom_component_coverage_valid=True,
            sbom_document_sha256=sbom.document_sha256,
            verified=True,
        )

    monkeypatch.setattr(
        "app.services.release_control_service.verify_package_version",
        fake_verify,
    )
    environment = await create_release_environment(
        async_session,
        request=ReleaseEnvironmentCreate(
            namespace_id=namespace.id,
            name="staging",
            kind="STAGING",
            promotion_order=20,
        ),
        actor=actor,
    )
    candidate_request = ReleaseCandidateCreate(
        namespace_id=namespace.id,
        package_version_public_id=version.public_id,
        deployment_public_id=deployment.public_id,
        target_environment_public_id=environment.public_id,
        idempotency_key="release-candidate-main",
    )
    candidate = await create_release_candidate(async_session, request=candidate_request, actor=actor)
    replay = await create_release_candidate(async_session, request=candidate_request, actor=actor)
    assert replay.public_id == candidate.public_id
    with pytest.raises(DeploymentImmutableError, match="ReleaseCandidate"):
        await update_registered_deployment(
            async_session,
            deployment.id,
            actor=actor,
            revision="mutated-after-candidate",
        )

    policy = await create_release_policy(
        async_session,
        request=ReleasePolicyCreate(
            namespace_id=namespace.id,
            name="staging-release",
            description="Release policy description must not enter Outbox.",
        ),
        actor=actor,
    )
    decisions = {}
    for mode, expected in (
        (ReleasePolicyMode.SHADOW, ReleasePolicyEnforcementOutcome.ALLOW),
        (ReleasePolicyMode.WARN, ReleasePolicyEnforcementOutcome.WARN),
        (ReleasePolicyMode.ENFORCE, ReleasePolicyEnforcementOutcome.BLOCK),
    ):
        policy_version = await create_release_policy_version(
            async_session,
            policy_public_id=policy.public_id,
            request=ReleasePolicyVersionCreate(
                namespace_id=namespace.id,
                target_environment_public_id=environment.public_id,
                mode=mode,
                rules=_rules(include_eval=True),
            ),
            actor=actor,
        )
        decision_request = ReleasePolicyEvaluationCreate(
            namespace_id=namespace.id,
            policy_version_public_id=policy_version.public_id,
            idempotency_key=f"release-policy-{mode.value.casefold()}",
        )
        decision = await evaluate_release_policy(
            async_session,
            candidate_public_id=candidate.public_id,
            request=decision_request,
            actor=actor,
            storage=storage,
        )
        exact_replay = await evaluate_release_policy(
            async_session,
            candidate_public_id=candidate.public_id,
            request=decision_request,
            actor=actor,
            storage=storage,
        )
        assert exact_replay.public_id == decision.public_id
        assert decision.raw_outcome == ReleasePolicyRawOutcome.FAIL
        assert decision.enforcement_outcome == expected
        assert decision.would_block is True
        eval_result = next(
            result
            for result in decision.rule_results
            if result.rule_type == ReleasePolicyRuleType.EVALUATION_GATE_PASS
        )
        assert eval_result.verdict == ReleasePolicyRuleVerdict.FAIL
        assert eval_result.reason_code == "runtime_evaluation_missing"
        decisions[mode] = decision

    assert decisions[ReleasePolicyMode.SHADOW].enforcement_outcome.value == "ALLOW"
    assert decisions[ReleasePolicyMode.WARN].enforcement_outcome.value == "WARN"
    assert decisions[ReleasePolicyMode.ENFORCE].enforcement_outcome.value == "BLOCK"

    pass_version = await create_release_policy_version(
        async_session,
        policy_public_id=policy.public_id,
        request=ReleasePolicyVersionCreate(
            namespace_id=namespace.id,
            target_environment_public_id=environment.public_id,
            mode=ReleasePolicyMode.ENFORCE,
            rules=_rules(include_eval=False),
        ),
        actor=actor,
    )
    passed = await evaluate_release_policy(
        async_session,
        candidate_public_id=candidate.public_id,
        request=ReleasePolicyEvaluationCreate(
            namespace_id=namespace.id,
            policy_version_public_id=pass_version.public_id,
            idempotency_key="release-policy-pass",
        ),
        actor=actor,
        storage=storage,
    )
    assert passed.raw_outcome == ReleasePolicyRawOutcome.PASS
    assert passed.enforcement_outcome == ReleasePolicyEnforcementOutcome.ALLOW
    assert passed.would_block is False
    rollback_result = next(
        result
        for result in passed.rule_results
        if result.rule_type == ReleasePolicyRuleType.ROLLBACK_TARGET_REQUIRED
    )
    assert rollback_result.verdict == ReleasePolicyRuleVerdict.NOT_APPLICABLE

    key.status = PackageSigningKeyStatus.REVOKED
    key.revoked_by_user_id = actor.id
    key.revoked_at = datetime.now(timezone.utc)
    with pytest.raises(ReleaseControlConflictError, match="idempotency key"):
        await evaluate_release_policy(
            async_session,
            candidate_public_id=candidate.public_id,
            request=ReleasePolicyEvaluationCreate(
                namespace_id=namespace.id,
                policy_version_public_id=pass_version.public_id,
                idempotency_key="release-policy-pass",
            ),
            actor=actor,
            storage=storage,
        )

    events = list((await async_session.scalars(select(OutboxEvent).order_by(OutboxEvent.id))).all())
    assert [event.event_type for event in events].count("ReleaseCandidateCreated") == 1
    assert [event.event_type for event in events].count("ReleasePolicyVersionCreated") == 4
    assert [event.event_type for event in events].count("ReleasePolicyDecisionRecorded") == 4
    serialized_events = str([event.payload_json for event in events])
    serialized_audits = str(
        [row.details for row in (await async_session.scalars(select(AuditLog))).all()]
    )
    for forbidden in (
        "Release policy description must not enter Outbox.",
        "sbom_document",
        "BEGIN PUBLIC KEY",
        "tool_arguments",
        "tool_result",
    ):
        assert forbidden not in serialized_events
        assert forbidden not in serialized_audits
    assert await get_release_candidate(async_session, candidate.public_id)


async def test_release_approval_exception_runtime_receipt_canary_and_rollback(
    async_session,
    monkeypatch,
):
    actor, namespace, key, version, sbom, deployment, storage = await _seed_release_subject(
        async_session,
        "promotion",
    )
    reviewer = User(
        username="release-control-reviewer",
        email="release-control-reviewer@example.test",
        hashed_password="unused",
        system_role=SystemRole.USER,
    )
    async_session.add(reviewer)
    await async_session.flush()
    async_session.add(
        NamespaceMember(
            namespace_id=namespace.id,
            user_id=reviewer.id,
            role=NamespaceRole.ADMIN,
        )
    )

    async def fake_verify(*_args, **_kwargs):
        return AgentPackageVersionVerificationOut(
            package_version_public_id=version.public_id,
            manifest_digest=version.manifest_digest,
            graph_digest=version.graph_digest,
            provenance_digest=version.provenance_digest,
            signature_valid=True,
            signature_verified_at=version.signature_verified_at,
            signing_key_status=key.status,
            signing_key_fingerprint=key.public_key_fingerprint,
            sbom_digest_valid=True,
            sbom_component_coverage_valid=True,
            sbom_document_sha256=sbom.document_sha256,
            verified=True,
        )

    monkeypatch.setattr(
        "app.services.release_control_service.verify_package_version",
        fake_verify,
    )
    environment = await create_release_environment(
        async_session,
        request=ReleaseEnvironmentCreate(
            namespace_id=namespace.id,
            name="staging",
            kind="STAGING",
            promotion_order=20,
            protected=True,
            minimum_approvals=1,
        ),
        actor=actor,
    )
    candidate = await create_release_candidate(
        async_session,
        request=ReleaseCandidateCreate(
            namespace_id=namespace.id,
            package_version_public_id=version.public_id,
            deployment_public_id=deployment.public_id,
            target_environment_public_id=environment.public_id,
            idempotency_key="promotion-candidate-one",
        ),
        actor=actor,
    )
    policy = await create_release_policy(
        async_session,
        request=ReleasePolicyCreate(
            namespace_id=namespace.id,
            name="protected-staging",
        ),
        actor=actor,
    )
    blocking_version = await create_release_policy_version(
        async_session,
        policy_public_id=policy.public_id,
        request=ReleasePolicyVersionCreate(
            namespace_id=namespace.id,
            target_environment_public_id=environment.public_id,
            mode=ReleasePolicyMode.ENFORCE,
            rules=_rules(include_eval=True),
        ),
        actor=actor,
    )
    blocked = await evaluate_release_policy(
        async_session,
        candidate_public_id=candidate.public_id,
        request=ReleasePolicyEvaluationCreate(
            namespace_id=namespace.id,
            policy_version_public_id=blocking_version.public_id,
            idempotency_key="promotion-policy-blocked",
        ),
        actor=actor,
        storage=storage,
    )
    assert blocked.enforcement_outcome == ReleasePolicyEnforcementOutcome.BLOCK

    approval_request = ReleaseCandidateApprovalCreate(
        namespace_id=namespace.id,
        policy_decision_public_id=blocked.public_id,
        decision=ReleaseApprovalDecision.APPROVED,
        role=ReleaseApprovalRole.RELEASE_MANAGER,
        comment="sensitive approval note must remain out of evidence events",
        idempotency_key="promotion-approval-one",
    )
    with pytest.raises(ReleaseControlStateError, match="four-eyes"):
        await create_release_candidate_approval(
            async_session,
            candidate_public_id=candidate.public_id,
            request=approval_request,
            actor=actor,
        )
    approval = await create_release_candidate_approval(
        async_session,
        candidate_public_id=candidate.public_id,
        request=approval_request,
        actor=reviewer,
    )
    approval_replay = await create_release_candidate_approval(
        async_session,
        candidate_public_id=candidate.public_id,
        request=approval_request,
        actor=reviewer,
    )
    assert approval_replay.public_id == approval.public_id

    failed_rule_ids = [
        result.rule_id
        for result in blocked.rule_results
        if result.verdict == ReleasePolicyRuleVerdict.FAIL
    ]
    exception_reason = "Sensitive incident context that must remain outside audit and Outbox payloads."
    exception = await create_release_policy_exception(
        async_session,
        request=ReleasePolicyExceptionCreate(
            namespace_id=namespace.id,
            policy_decision_public_id=blocked.public_id,
            waived_rule_ids=failed_rule_ids,
            reason=exception_reason,
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            idempotency_key="promotion-exception-one",
        ),
        actor=actor,
    )
    review_request = ReleasePolicyExceptionReviewCreate(
        namespace_id=namespace.id,
        decision=ReleaseExceptionReviewDecision.APPROVED,
        comment="independent bounded exception review",
        idempotency_key="promotion-exception-review-one",
    )
    with pytest.raises(ReleaseControlStateError, match="different operator"):
        await review_release_policy_exception(
            async_session,
            exception_public_id=exception.public_id,
            request=review_request,
            actor=actor,
        )
    review = await review_release_policy_exception(
        async_session,
        exception_public_id=exception.public_id,
        request=review_request,
        actor=reviewer,
    )
    assert review.decision == ReleaseExceptionReviewDecision.APPROVED

    first_promotion_request = ReleasePromotionCreate(
        namespace_id=namespace.id,
        candidate_public_id=candidate.public_id,
        policy_decision_public_id=blocked.public_id,
        strategy=ReleasePromotionStrategy.ALL_AT_ONCE,
        idempotency_key="promotion-dispatch-one",
    )
    first_promotion = await create_release_promotion(
        async_session,
        request=first_promotion_request,
        actor=actor,
    )
    first_promotion_replay = await create_release_promotion(
        async_session,
        request=first_promotion_request,
        actor=actor,
    )
    assert first_promotion_replay.public_id == first_promotion.public_id
    assert first_promotion.exception_digest is not None

    credential = ReporterCredential(
        runtime_id=deployment.runtime_id,
        user_id=actor.id,
        device_id="release-control-test-device",
        name="release-control-test-reporter",
        token_prefix="rcpt-test",
        token_hash="unused",
        scopes=["release.receipt"],
        is_active=True,
    )
    async_session.add(credential)
    await async_session.flush()
    identity = ReporterExecutionIdentity(
        namespace_id=namespace.id,
        runtime_id=deployment.runtime_id,
        credential_id=credential.id,
        actor_user_id=actor.id,
        trust_level=TrustLevel.CHANNEL_AUTHENTICATED,
        trust_source=TrustSource.REPORTER,
    )
    first_occurred_at = datetime.now(timezone.utc) - timedelta(seconds=20)
    first_receipt_request = ReleaseDeploymentReceiptCreate(
        dispatch_public_id=first_promotion.dispatch_public_id,
        kind=ReleaseReceiptKind.PROMOTION,
        external_receipt_id="runtime-promotion-one",
        status=RuntimeReceiptReportedStatus.APPLIED,
        observed_package_version_public_id=version.public_id,
        observed_deployment_revision=deployment.revision,
        observed_configuration_digest=deployment.configuration_digest,
        runtime_release_ref="hermes-release-one",
        occurred_at=first_occurred_at,
        idempotency_key="runtime-receipt-one",
    )
    first_receipt = await record_release_deployment_receipt(
        async_session,
        request=first_receipt_request,
        identity=identity,
    )
    first_receipt_replay = await record_release_deployment_receipt(
        async_session,
        request=first_receipt_request,
        identity=identity,
    )
    assert first_receipt_replay.public_id == first_receipt.public_id
    assert first_receipt.status == ReleaseReceiptStatus.APPLIED
    assert first_promotion.status == ReleasePromotionStatus.SUCCEEDED

    second_deployment = AgentDeployment(
        public_id="dep_release_control_promotion_two",
        namespace_id=namespace.id,
        runtime_id=deployment.runtime_id,
        agent_asset_id=deployment.agent_asset_id,
        package_version_id=version.id,
        external_deployment_id="release-control-promotion-two",
        environment="staging",
        revision="release-promotion-2",
        configuration_digest="8" * 64,
        status=AgentDeploymentStatus.REGISTERED,
        created_by_user_id=actor.id,
    )
    async_session.add(second_deployment)
    await async_session.flush()
    second_candidate = await create_release_candidate(
        async_session,
        request=ReleaseCandidateCreate(
            namespace_id=namespace.id,
            package_version_public_id=version.public_id,
            deployment_public_id=second_deployment.public_id,
            target_environment_public_id=environment.public_id,
            idempotency_key="promotion-candidate-two",
        ),
        actor=actor,
    )
    passing_version = await create_release_policy_version(
        async_session,
        policy_public_id=policy.public_id,
        request=ReleasePolicyVersionCreate(
            namespace_id=namespace.id,
            target_environment_public_id=environment.public_id,
            mode=ReleasePolicyMode.ENFORCE,
            rules=_rules(include_eval=False),
        ),
        actor=actor,
    )
    passed = await evaluate_release_policy(
        async_session,
        candidate_public_id=second_candidate.public_id,
        request=ReleasePolicyEvaluationCreate(
            namespace_id=namespace.id,
            policy_version_public_id=passing_version.public_id,
            idempotency_key="promotion-policy-passed",
        ),
        actor=actor,
        storage=storage,
    )
    await create_release_candidate_approval(
        async_session,
        candidate_public_id=second_candidate.public_id,
        request=ReleaseCandidateApprovalCreate(
            namespace_id=namespace.id,
            policy_decision_public_id=passed.public_id,
            decision=ReleaseApprovalDecision.APPROVED,
            role=ReleaseApprovalRole.RELEASE_MANAGER,
            idempotency_key="promotion-approval-two",
        ),
        actor=reviewer,
    )
    canary_promotion = await create_release_promotion(
        async_session,
        request=ReleasePromotionCreate(
            namespace_id=namespace.id,
            candidate_public_id=second_candidate.public_id,
            policy_decision_public_id=passed.public_id,
            strategy=ReleasePromotionStrategy.CANARY,
            canary=ReleaseCanaryConfig(
                minimum_completed_runs=1,
                maximum_failure_rate=0,
                maximum_untrusted_rate=0,
                observation_window_seconds=30,
            ),
            idempotency_key="promotion-canary-two",
        ),
        actor=actor,
    )
    canary_occurred_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    canary_receipt = await record_release_deployment_receipt(
        async_session,
        request=ReleaseDeploymentReceiptCreate(
            dispatch_public_id=canary_promotion.dispatch_public_id,
            kind=ReleaseReceiptKind.PROMOTION,
            external_receipt_id="runtime-canary-two",
            status=RuntimeReceiptReportedStatus.APPLIED,
            observed_package_version_public_id=version.public_id,
            observed_deployment_revision=second_deployment.revision,
            observed_configuration_digest=second_deployment.configuration_digest,
            runtime_release_ref="hermes-release-two",
            occurred_at=canary_occurred_at,
            idempotency_key="runtime-canary-receipt-two",
        ),
        identity=identity,
    )
    assert canary_receipt.status == ReleaseReceiptStatus.APPLIED
    assert canary_promotion.status == ReleasePromotionStatus.OBSERVING

    run_started_at = canary_occurred_at + timedelta(seconds=1)
    async_session.add(
        AgentRun(
            public_id="run-release-canary-failed",
            namespace_id=namespace.id,
            runtime_id=deployment.runtime_id,
            deployment_id=second_deployment.id,
            external_run_id="release-canary-failed",
            attempt=1,
            status=AgentRunStatus.FAILED,
            trust_level=TrustLevel.UNVERIFIED,
            trust_source=TrustSource.REPORTER,
            source_schema="duckdock.execution",
            source_schema_version="1.0",
            normalizer_version="test",
            content_capture_mode=ContentCaptureMode.METADATA_ONLY,
            started_at=run_started_at,
            ended_at=run_started_at + timedelta(seconds=1),
            duration_ms=1000,
            start_idempotency_key="release-canary-run-start",
            start_envelope_sha256="9" * 64,
            completion_idempotency_key="release-canary-run-complete",
            completion_envelope_sha256="a" * 64,
        )
    )
    await async_session.flush()
    evaluation = await evaluate_release_canary(
        async_session,
        promotion_public_id=canary_promotion.public_id,
        request=ReleaseCanaryEvaluationCreate(
            namespace_id=namespace.id,
            idempotency_key="canary-evaluation-failed",
        ),
        actor=actor,
    )
    assert evaluation.outcome == ReleaseCanaryOutcome.FAIL
    rollback = await async_session.scalar(
        select(ReleaseRollback).where(ReleaseRollback.promotion_id == canary_promotion.id)
    )
    assert rollback is not None
    assert rollback.status == ReleaseRollbackStatus.DISPATCHED
    assert canary_promotion.status == ReleasePromotionStatus.ROLLBACK_REQUESTED

    rollback_receipt = await record_release_deployment_receipt(
        async_session,
        request=ReleaseDeploymentReceiptCreate(
            dispatch_public_id=rollback.dispatch_public_id,
            kind=ReleaseReceiptKind.ROLLBACK,
            external_receipt_id="runtime-rollback-two",
            status=RuntimeReceiptReportedStatus.APPLIED,
            observed_package_version_public_id=version.public_id,
            observed_deployment_revision=deployment.revision,
            observed_configuration_digest=deployment.configuration_digest,
            runtime_release_ref="hermes-release-one-restored",
            occurred_at=datetime.now(timezone.utc),
            idempotency_key="runtime-rollback-receipt-two",
        ),
        identity=identity,
    )
    assert rollback_receipt.status == ReleaseReceiptStatus.APPLIED
    assert rollback.status == ReleaseRollbackStatus.SUCCEEDED
    assert canary_promotion.status == ReleasePromotionStatus.ROLLED_BACK
    assert second_deployment.status == AgentDeploymentStatus.RETIRED

    mismatch_deployment = AgentDeployment(
        public_id="dep_release_control_promotion_mismatch",
        namespace_id=namespace.id,
        runtime_id=deployment.runtime_id,
        agent_asset_id=deployment.agent_asset_id,
        package_version_id=version.id,
        external_deployment_id="release-control-promotion-mismatch",
        environment="staging",
        revision="release-promotion-mismatch",
        configuration_digest="b" * 64,
        status=AgentDeploymentStatus.REGISTERED,
        created_by_user_id=actor.id,
    )
    async_session.add(mismatch_deployment)
    await async_session.flush()
    mismatch_candidate = await create_release_candidate(
        async_session,
        request=ReleaseCandidateCreate(
            namespace_id=namespace.id,
            package_version_public_id=version.public_id,
            deployment_public_id=mismatch_deployment.public_id,
            target_environment_public_id=environment.public_id,
            idempotency_key="promotion-candidate-mismatch",
        ),
        actor=actor,
    )
    mismatch_decision = await evaluate_release_policy(
        async_session,
        candidate_public_id=mismatch_candidate.public_id,
        request=ReleasePolicyEvaluationCreate(
            namespace_id=namespace.id,
            policy_version_public_id=passing_version.public_id,
            idempotency_key="promotion-policy-mismatch",
        ),
        actor=actor,
        storage=storage,
    )
    await create_release_candidate_approval(
        async_session,
        candidate_public_id=mismatch_candidate.public_id,
        request=ReleaseCandidateApprovalCreate(
            namespace_id=namespace.id,
            policy_decision_public_id=mismatch_decision.public_id,
            decision=ReleaseApprovalDecision.APPROVED,
            role=ReleaseApprovalRole.RELEASE_MANAGER,
            idempotency_key="promotion-approval-mismatch",
        ),
        actor=reviewer,
    )
    mismatch_promotion = await create_release_promotion(
        async_session,
        request=ReleasePromotionCreate(
            namespace_id=namespace.id,
            candidate_public_id=mismatch_candidate.public_id,
            policy_decision_public_id=mismatch_decision.public_id,
            strategy=ReleasePromotionStrategy.ALL_AT_ONCE,
            idempotency_key="promotion-dispatch-mismatch",
        ),
        actor=actor,
    )
    mismatch_receipt = await record_release_deployment_receipt(
        async_session,
        request=ReleaseDeploymentReceiptCreate(
            dispatch_public_id=mismatch_promotion.dispatch_public_id,
            kind=ReleaseReceiptKind.PROMOTION,
            external_receipt_id="runtime-promotion-mismatch",
            status=RuntimeReceiptReportedStatus.APPLIED,
            observed_package_version_public_id=version.public_id,
            observed_deployment_revision="unexpected-runtime-revision",
            observed_configuration_digest=mismatch_deployment.configuration_digest,
            occurred_at=datetime.now(timezone.utc),
            idempotency_key="runtime-receipt-mismatch",
        ),
        identity=identity,
    )
    assert mismatch_receipt.status == ReleaseReceiptStatus.MISMATCH
    assert mismatch_receipt.error_code == "runtime_release_evidence_mismatch"
    assert mismatch_promotion.status == ReleasePromotionStatus.FAILED
    assert mismatch_deployment.status == AgentDeploymentStatus.FAILED

    releases = await list_release_environment_releases(
        async_session,
        namespace_id=namespace.id,
        environment_public_id=environment.public_id,
    )
    assert len(releases) == 2
    assert releases[0].candidate_id == candidate.id
    assert releases[0].status == ReleaseEnvironmentReleaseStatus.ACTIVE
    assert releases[1].status == ReleaseEnvironmentReleaseStatus.ROLLED_BACK
    assert len(
        list(
            (
                await async_session.scalars(
                    select(ReleaseEnvironmentRelease).where(
                        ReleaseEnvironmentRelease.status
                        == ReleaseEnvironmentReleaseStatus.ACTIVE
                    )
                )
            ).all()
        )
    ) == 1

    events = list((await async_session.scalars(select(OutboxEvent))).all())
    audits = list((await async_session.scalars(select(AuditLog))).all())
    serialized_evidence = str([event.payload_json for event in events]) + str(
        [row.details for row in audits]
    )
    assert exception_reason not in serialized_evidence
    assert approval_request.comment not in serialized_evidence
    for expected_event in (
        "ReleaseCandidateApprovalRecorded",
        "ReleasePolicyExceptionRequested",
        "ReleasePolicyExceptionReviewed",
        "ReleasePromotionDispatched",
        "ReleaseReceiptRecorded",
        "ReleaseCanaryEvaluated",
        "ReleaseRollbackDispatched",
        "ReleaseEnvironmentActivated",
    ):
        assert expected_event in {event.event_type for event in events}


async def test_release_receipt_scope_is_isolated_from_execution_ingestion(async_session):
    actor, namespace, _key, _version, _sbom, deployment, _storage = await _seed_release_subject(
        async_session,
        "receipt-scope",
    )
    prefix, secret, token = generate_runtime_report_token()
    credential = ReporterCredential(
        runtime_id=deployment.runtime_id,
        user_id=actor.id,
        device_id="release-scope-device",
        name="release-scope-test",
        token_prefix=prefix,
        token_hash=hash_reporter_token_secret(secret),
        scopes=["execution.write"],
        is_active=True,
    )
    async_session.add(credential)
    await async_session.flush()

    with pytest.raises(ReporterScopeError, match="release.receipt"):
        await authenticate_reporter_release_credential(async_session, token)

    credential.scopes = ["release.receipt"]
    await async_session.flush()
    identity = await authenticate_reporter_release_credential(async_session, token)
    assert identity.namespace_id == namespace.id
    assert identity.runtime_id == deployment.runtime_id
    assert identity.credential_id == credential.id


def test_release_control_openapi_exposes_typed_policy_surface() -> None:
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v2")
    spec = app.openapi()
    for path in (
        "/api/v2/release-environments",
        "/api/v2/release-policies",
        "/api/v2/release-policies/{public_id}/versions",
        "/api/v2/release-candidates",
        "/api/v2/release-candidates/{public_id}/policy-evaluations",
        "/api/v2/release-candidates/{public_id}/approvals",
        "/api/v2/release-policy-decisions",
        "/api/v2/release-policy-exceptions",
        "/api/v2/release-policy-exceptions/{public_id}/review",
        "/api/v2/release-promotions",
        "/api/v2/release-promotions/{public_id}/canary-evaluations",
        "/api/v2/release-promotions/{public_id}/rollback",
        "/api/v2/release-rollbacks",
        "/api/v2/release-rollbacks/{public_id}",
        "/api/v2/release-canary-evaluations",
        "/api/v2/release-environment-releases",
        "/api/v2/release-deployment-receipts",
        "/api/v2/release-receipt-credentials",
        "/api/v2/release-receipt-credentials/{credential_id}/revoke",
        "/api/v2/reporter/promotion-receipts",
    ):
        assert path in spec["paths"]
    rule_schema = spec["components"]["schemas"]["ReleasePolicyRuleIn"]
    assert rule_schema["additionalProperties"] is False
    enum_values = set(
        spec["components"]["schemas"]["ReleasePolicyRuleType"]["enum"]
    )
    assert {
        "PACKAGE_EVIDENCE_VERIFIED",
        "EVALUATION_GATE_PASS",
        "MAX_VULNERABILITY_SEVERITY",
        "ROLLBACK_TARGET_REQUIRED",
    }.issubset(enum_values)
    receipt_schema = spec["components"]["schemas"]["ReleaseDeploymentReceiptCreate"]
    assert receipt_schema["additionalProperties"] is False
    assert "namespace_id" not in receipt_schema["properties"]
    assert "runtime_id" not in receipt_schema["properties"]
    assert "reporter_credential_id" not in receipt_schema["properties"]
    receipt_operation = spec["paths"]["/api/v2/reporter/promotion-receipts"]["post"]
    assert receipt_operation["security"]
