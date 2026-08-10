from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import ensure_utc
from app.models.clinic import ClinicEvaluation, EvalStatus
from app.models.governance import NamespaceGovernancePolicy
from app.models.namespace import Namespace
from app.models.sandbox_validation import SandboxValidationRun, SandboxValidationStatus
from app.models.scan import ScanResult, ScanStatus
from app.models.skill import Skill, SkillVersion, SkillVersionReviewStatus, SkillVersionStatus
from app.services.release_evidence_ports import (
    CandidateEvidenceReference,
    NullReleaseEvidenceLookupPort,
    ReleaseEvidenceLookupPort,
    ReleaseEvidenceSelector,
)


@dataclass
class ReleaseGateDecision:
    status: SkillVersionStatus
    review_status: SkillVersionReviewStatus
    gate_result: dict


@dataclass(frozen=True, slots=True)
class CandidateReleaseGateDecision:
    selector: ReleaseEvidenceSelector
    outcome: str
    reason_codes: tuple[str, ...]
    evidence: tuple[CandidateEvidenceReference, ...]
    decision_digest: str


class ReleaseGateService:
    def __init__(
        self,
        *,
        evidence_lookup: ReleaseEvidenceLookupPort | None = None,
    ) -> None:
        # Candidate-pinned evidence stays separate from the legacy Skill/Clinic
        # evaluation path so neither path can silently fall back to the other.
        self._evidence_lookup = evidence_lookup or NullReleaseEvidenceLookupPort()

    async def lookup_candidate_evidence(
        self,
        selector: ReleaseEvidenceSelector,
    ) -> tuple[CandidateEvidenceReference, ...]:
        return await self._evidence_lookup.lookup(selector)

    async def evaluate_candidate(
        self,
        selector: ReleaseEvidenceSelector,
    ) -> CandidateReleaseGateDecision:
        """Enforce candidate-pinned runtime evaluation evidence.

        This is deliberately separate from the legacy Skill/Clinic evaluate()
        path. It has no Namespace-latest fallback: an exact selector with no
        bound evidence is blocked.
        """

        evidence = await self.lookup_candidate_evidence(selector)
        if not evidence:
            outcome = "BLOCKED"
            reason_codes = ("runtime_evaluation_missing",)
        else:
            reasons: list[str] = []
            comparison_verdicts = {
                item.verdict
                for item in evidence
                if item.evidence_kind == "evaluation_comparison"
            }
            manual_review_verdicts = {
                item.verdict
                for item in evidence
                if item.evidence_kind == "manual_review"
            }
            if "fail" in comparison_verdicts:
                reasons.append("runtime_evaluation_regression")
            if "inconclusive" in comparison_verdicts:
                reasons.append("runtime_evaluation_inconclusive")
            if "fail" in manual_review_verdicts:
                reasons.append("manual_review_rejected")
            if reasons:
                outcome = "BLOCKED"
                reason_codes = tuple(reasons)
            else:
                outcome = "PASS"
                reason_codes = ("runtime_evaluation_passed",)
        canonical = {
            "schema_name": "duckdock-candidate-release-gate",
            "schema_version": "1.0",
            "selector": {
                "namespace_id": selector.namespace_id,
                "release_candidate_ref": (
                    selector.release_candidate_ref
                ),
                "deployment_public_id": selector.deployment_public_id,
                "deployment_revision": selector.deployment_revision,
            },
            "outcome": outcome,
            "reason_codes": list(reason_codes),
            "evidence": [
                {
                    "evidence_id": item.evidence_id,
                    "evidence_kind": item.evidence_kind,
                    "verdict": item.verdict,
                    "artifact_sha256": item.artifact_sha256,
                    "observed_at": ensure_utc(item.observed_at)
                    .isoformat()
                    .replace("+00:00", "Z"),
                }
                for item in evidence
            ],
        }
        decision_digest = hashlib.sha256(
            json.dumps(
                canonical,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return CandidateReleaseGateDecision(
            selector=selector,
            outcome=outcome,
            reason_codes=reason_codes,
            evidence=evidence,
            decision_digest=decision_digest,
        )

    async def evaluate(
        self,
        db: AsyncSession,
        *,
        namespace: Namespace,
        skill: Skill,
        version: SkillVersion,
        files: dict[str, str],
        policy: NamespaceGovernancePolicy,
        scan: ScanResult | None = None,
        sandbox_run: SandboxValidationRun | None = None,
    ) -> ReleaseGateDecision:
        issues: list[dict] = []
        latest_eval = await self._latest_clinic_evaluation(db, namespace_id=namespace.id)

        if policy.require_examples and not any(path.startswith("examples/") for path in files):
            issues.append(
                {
                    "code": "package_examples_missing",
                    "severity": "high",
                    "message": "Package must include at least one example file under examples/.",
                }
            )

        if policy.require_validation_spec and ".duckdock/validation.yaml" not in files:
            issues.append(
                {
                    "code": "validation_spec_missing",
                    "severity": "high",
                    "message": "Package must include .duckdock/validation.yaml for runtime validation.",
                }
            )

        if policy.require_sandbox_success and sandbox_run is not None and sandbox_run.status != SandboxValidationStatus.PASSED:
            issues.append(
                {
                    "code": "sandbox_not_passed",
                    "severity": "critical",
                    "message": "Sandbox validation must pass before a version can enter production.",
                }
            )

        clinic_payload = self._clinic_gate_payload(policy, latest_eval)
        clinic_issue = clinic_payload.get("blocking_issue")
        if clinic_issue:
            issues.append(clinic_issue)

        review_status = version.review_status
        if policy.manual_review_required:
            if review_status == SkillVersionReviewStatus.NOT_REQUIRED:
                review_status = SkillVersionReviewStatus.PENDING
            if review_status != SkillVersionReviewStatus.APPROVED:
                issues.append(
                    {
                        "code": "manual_review_pending",
                        "severity": "high",
                        "message": "Namespace policy requires manual review before production promotion.",
                    }
                )

        if review_status == SkillVersionReviewStatus.REJECTED:
            issues.append(
                {
                    "code": "manual_review_rejected",
                    "severity": "critical",
                    "message": "Version review was explicitly rejected.",
                }
            )

        if scan is not None and scan.status == ScanStatus.FAILED:
            return ReleaseGateDecision(
                status=SkillVersionStatus.REJECTED,
                review_status=review_status,
                gate_result=self._build_gate_result(
                    policy=policy,
                    issues=issues,
                    clinic_payload=clinic_payload,
                    scan_status=scan.status.value,
                    sandbox_status=sandbox_run.status.value if sandbox_run else None,
                    final_status=SkillVersionStatus.REJECTED,
                ),
            )

        if any(item["code"] in {"sandbox_not_passed", "manual_review_rejected"} for item in issues):
            final_status = SkillVersionStatus.REJECTED
        elif issues:
            final_status = SkillVersionStatus.REVIEW
        else:
            final_status = SkillVersionStatus.PRODUCTION
            if review_status == SkillVersionReviewStatus.PENDING and not policy.manual_review_required:
                review_status = SkillVersionReviewStatus.NOT_REQUIRED

        return ReleaseGateDecision(
            status=final_status,
            review_status=review_status,
            gate_result=self._build_gate_result(
                policy=policy,
                issues=issues,
                clinic_payload=clinic_payload,
                scan_status=scan.status.value if scan else None,
                sandbox_status=sandbox_run.status.value if sandbox_run else None,
                final_status=final_status,
            ),
        )

    async def _latest_clinic_evaluation(
        self,
        db: AsyncSession,
        *,
        namespace_id: int,
    ) -> ClinicEvaluation | None:
        return (
            await db.execute(
                select(ClinicEvaluation)
                .where(
                    ClinicEvaluation.namespace_id == namespace_id,
                    ClinicEvaluation.status == EvalStatus.COMPLETED,
                )
                .order_by(ClinicEvaluation.completed_at.desc(), ClinicEvaluation.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    def _clinic_gate_payload(
        self,
        policy: NamespaceGovernancePolicy,
        evaluation: ClinicEvaluation | None,
    ) -> dict:
        payload = {
            "enabled": policy.clinic_gate_enabled,
            "evaluation_id": evaluation.id if evaluation else None,
            "score": evaluation.overall_score if evaluation else None,
            "grade": evaluation.grade if evaluation else None,
            "completed_at": evaluation.completed_at.isoformat() if evaluation and evaluation.completed_at else None,
            "ai_assist": evaluation.ai_assist if evaluation else None,
            "blocking_issue": None,
        }
        if not policy.clinic_gate_enabled:
            return payload

        if evaluation is None or evaluation.overall_score is None or evaluation.completed_at is None:
            payload["blocking_issue"] = {
                "code": "clinic_missing",
                "severity": "high",
                "message": "Clinic gate is enabled but there is no completed evaluation for this namespace.",
            }
            return payload

        age_hours = max(0, int((datetime.now(timezone.utc) - ensure_utc(evaluation.completed_at)).total_seconds() // 3600))
        payload["age_hours"] = age_hours
        if age_hours > policy.clinic_max_age_hours:
            payload["blocking_issue"] = {
                "code": "clinic_stale",
                "severity": "high",
                "message": f"Latest Clinic evaluation is stale ({age_hours}h old).",
            }
            return payload

        if policy.min_clinic_score is not None and float(evaluation.overall_score) < float(policy.min_clinic_score):
            payload["blocking_issue"] = {
                "code": "clinic_below_threshold",
                "severity": "high",
                "message": (
                    f"Latest Clinic score {float(evaluation.overall_score):.1f} is below the policy threshold "
                    f"{float(policy.min_clinic_score):.1f}."
                ),
            }
        return payload

    def _build_gate_result(
        self,
        *,
        policy: NamespaceGovernancePolicy,
        issues: list[dict],
        clinic_payload: dict,
        scan_status: str | None,
        sandbox_status: str | None,
        final_status: SkillVersionStatus,
    ) -> dict:
        return {
            "final_status": final_status.value,
            "issue_count": len(issues),
            "issues": issues,
            "clinic": clinic_payload,
            "policy": {
                "manual_review_required": policy.manual_review_required,
                "require_examples": policy.require_examples,
                "require_validation_spec": policy.require_validation_spec,
                "require_sandbox_success": policy.require_sandbox_success,
                "clinic_gate_enabled": policy.clinic_gate_enabled,
                "min_clinic_score": policy.min_clinic_score,
                "clinic_max_age_hours": policy.clinic_max_age_hours,
            },
            "technical_checks": {
                "scan_status": scan_status,
                "sandbox_status": sandbox_status,
            },
        }


release_gate_service = ReleaseGateService()
