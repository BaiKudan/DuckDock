"""Strict machine-readable contract for independent DuckDock security assessments."""

from __future__ import annotations

import hashlib
import ipaddress
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.ga_path_resolution import ga_file_resolution_override
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_path_resolution import ga_file_resolution_override


ASSESSMENT_ENGAGEMENT_SCHEMA_VERSION = "duckdock-ga-security-assessment-engagement-v1"
ASSESSMENT_REPORT_SCHEMA_VERSION = "duckdock-ga-security-assessment-report-v2"
ASSESSMENT_EVIDENCE_SCHEMA_VERSION = "duckdock-ga-independent-security-evidence-v3"
ASSESSMENT_ENGAGEMENT_SIGNATURE_NAMESPACE = "duckdock-security-assessment-engagement"
ASSESSMENT_SIGNATURE_NAMESPACE = "duckdock-security-assessment"
REQUIRED_ASSESSMENT_SCOPE = (
    "application-and-api",
    "identity-and-access",
    "kubernetes-infrastructure",
    "supply-chain",
    "agent-security",
)
REQUIRED_ASSESSMENT_METHODOLOGIES = ("penetration-test", "manual-code-review")
REQUIRED_PROHIBITED_ACTIONS = (
    "denial-of-service",
    "destructive-data-modification",
    "physical-security-testing",
    "production-data-exfiltration",
    "persistence-or-backdoors",
    "social-engineering",
    "third-party-systems-outside-target",
)
REQUIRED_STOP_AUTHORITIES = ("Operations", "Security")
MAXIMUM_ASSESSMENT_WINDOW = timedelta(days=30)
MAXIMUM_EVIDENCE_RETENTION = timedelta(days=90)
FINDING_SEVERITIES = ("critical", "high", "medium", "low", "informational")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
PLACEHOLDER_MARKERS = ("__CHANGE_ME", "example.invalid", "<", ">")


def meaningful(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not any(marker in value for marker in PLACEHOLDER_MARKERS)
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def contains_secret_material_key(value: Any) -> bool:
    forbidden = {
        "access_key",
        "api_key",
        "client_secret",
        "credential",
        "credential_value",
        "decryption_key",
        "password",
        "password_value",
        "plaintext",
        "plaintext_value",
        "private_key",
        "raw_secret",
        "secret_value",
        "secret_values",
        "session_token",
        "token",
        "token_value",
    }
    if isinstance(value, dict):
        return any(
            str(key).lower() in forbidden or contains_secret_material_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(contains_secret_material_key(item) for item in value)
    return False


def _exact_meaningful_list(value: Any, *, allow_empty: bool = False) -> list[str] | None:
    if not (
        isinstance(value, list)
        and (allow_empty or bool(value))
        and all(meaningful(item) for item in value)
        and len(value) == len(set(value))
    ):
        return None
    return [str(item) for item in value]


def _severity_counts(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict) or set(value) != set(FINDING_SEVERITIES):
        return None
    if not all(
        isinstance(value[severity], int)
        and not isinstance(value[severity], bool)
        and value[severity] >= 0
        for severity in FINDING_SEVERITIES
    ):
        return None
    return {severity: value[severity] for severity in FINDING_SEVERITIES}


def _canonical_cidrs(value: Any) -> list[str] | None:
    values = _exact_meaningful_list(value)
    if values is None or len(values) > 64:
        return None
    canonical: list[str] = []
    for raw in values:
        try:
            network = ipaddress.ip_network(raw, strict=True)
        except ValueError:
            return None
        if network.prefixlen == 0 or str(network) != raw:
            return None
        canonical.append(str(network))
    return canonical


def validate_assessment_engagement(
    engagement: Any,
    *,
    expected_provider: str,
    expected_assessor_identity: str,
    expected_security_authorizer_identity: str,
    target_environment: str,
    source_commit: str,
    backend_image: str,
    frontend_image: str,
    contract_digest: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate the pre-test, Security-signed rules of engagement."""

    if not isinstance(engagement, dict):
        raise ValueError("signed security assessment engagement must be a JSON object")
    expected_keys = {
        "schema_version",
        "engagement_id",
        "security_authorizer_identity",
        "provider",
        "assessor_identity",
        "target_environment",
        "source_commit",
        "images",
        "contract_digest",
        "scope",
        "methodologies",
        "authorization_window",
        "permitted_sources",
        "test_account_ids",
        "safety",
        "data_handling",
        "deliverables",
        "authorized_at",
    }
    images = engagement.get("images")
    scope = _exact_meaningful_list(engagement.get("scope"))
    methodologies = _exact_meaningful_list(engagement.get("methodologies"))
    window = engagement.get("authorization_window")
    window_starts_at = (
        parse_time(window.get("starts_at")) if isinstance(window, dict) else None
    )
    window_expires_at = (
        parse_time(window.get("expires_at")) if isinstance(window, dict) else None
    )
    authorized_at = parse_time(engagement.get("authorized_at"))
    current = now or datetime.now(timezone.utc)
    permitted_sources = engagement.get("permitted_sources")
    source_cidrs = (
        _canonical_cidrs(permitted_sources.get("cidrs"))
        if isinstance(permitted_sources, dict)
        else None
    )
    source_system_ids = (
        _exact_meaningful_list(permitted_sources.get("system_ids"))
        if isinstance(permitted_sources, dict)
        else None
    )
    test_account_ids = _exact_meaningful_list(engagement.get("test_account_ids"))
    safety = engagement.get("safety")
    prohibited_actions = (
        _exact_meaningful_list(safety.get("prohibited_actions"))
        if isinstance(safety, dict)
        else None
    )
    stop_authorities = (
        _exact_meaningful_list(safety.get("stop_authorities"))
        if isinstance(safety, dict)
        else None
    )
    emergency_contacts = (
        _exact_meaningful_list(safety.get("emergency_contacts"))
        if isinstance(safety, dict)
        else None
    )
    data_handling = engagement.get("data_handling")
    delete_by = (
        parse_time(data_handling.get("delete_by"))
        if isinstance(data_handling, dict)
        else None
    )
    deliverables = engagement.get("deliverables")
    timeline_valid = (
        authorized_at is not None
        and window_starts_at is not None
        and window_expires_at is not None
        and authorized_at <= current
        and authorized_at <= window_starts_at < window_expires_at
        and window_expires_at - window_starts_at <= MAXIMUM_ASSESSMENT_WINDOW
        and delete_by is not None
        and window_expires_at < delete_by
        and delete_by - window_expires_at <= MAXIMUM_EVIDENCE_RETENTION
    )
    if not (
        set(engagement) == expected_keys
        and engagement.get("schema_version") == ASSESSMENT_ENGAGEMENT_SCHEMA_VERSION
        and meaningful(engagement.get("engagement_id"))
        and engagement.get("security_authorizer_identity")
        == expected_security_authorizer_identity
        and engagement.get("provider") == expected_provider
        and engagement.get("assessor_identity") == expected_assessor_identity
        and engagement.get("target_environment") == target_environment
        and engagement.get("source_commit") == source_commit
        and isinstance(images, dict)
        and set(images) == {"backend", "frontend"}
        and all(
            isinstance(images.get(component), dict)
            and set(images[component]) == {"name"}
            for component in ("backend", "frontend")
        )
        and images["backend"].get("name") == backend_image
        and images["frontend"].get("name") == frontend_image
        and engagement.get("contract_digest") == contract_digest
        and bool(DIGEST_RE.fullmatch(str(contract_digest)))
        and scope is not None
        and set(scope) == set(REQUIRED_ASSESSMENT_SCOPE)
        and methodologies is not None
        and set(REQUIRED_ASSESSMENT_METHODOLOGIES).issubset(methodologies)
        and isinstance(window, dict)
        and set(window) == {"starts_at", "expires_at"}
        and isinstance(permitted_sources, dict)
        and set(permitted_sources) == {"cidrs", "system_ids"}
        and source_cidrs is not None
        and source_system_ids is not None
        and len(source_system_ids) <= 64
        and test_account_ids is not None
        and len(test_account_ids) <= 64
        and isinstance(safety, dict)
        and set(safety)
        == {
            "prohibited_actions",
            "stop_authorities",
            "emergency_contacts",
            "stop_acknowledgement_sla_minutes",
        }
        and prohibited_actions is not None
        and set(REQUIRED_PROHIBITED_ACTIONS).issubset(prohibited_actions)
        and stop_authorities is not None
        and set(stop_authorities) == set(REQUIRED_STOP_AUTHORITIES)
        and emergency_contacts is not None
        and len(emergency_contacts) >= 2
        and isinstance(safety.get("stop_acknowledgement_sla_minutes"), int)
        and not isinstance(safety.get("stop_acknowledgement_sla_minutes"), bool)
        and 1 <= safety["stop_acknowledgement_sla_minutes"] <= 15
        and isinstance(data_handling, dict)
        and set(data_handling)
        == {
            "artifacts_encrypted_at_rest",
            "secret_material_recording_allowed",
            "production_data_retention_allowed",
            "deletion_attestation_required",
            "delete_by",
        }
        and data_handling.get("artifacts_encrypted_at_rest") is True
        and data_handling.get("secret_material_recording_allowed") is False
        and data_handling.get("production_data_retention_allowed") is False
        and data_handling.get("deletion_attestation_required") is True
        and isinstance(deliverables, dict)
        and set(deliverables)
        == {
            "signed_machine_readable_report",
            "final_pdf",
            "finding_level_evidence",
            "critical_high_retest_required",
            "deletion_attestation",
        }
        and all(value is True for value in deliverables.values())
        and timeline_valid
        and not contains_secret_material_key(engagement)
    ):
        raise ValueError(
            "signed security assessment engagement is unsafe, out of scope, or not release-bound"
        )
    return {
        "engagement_id": engagement["engagement_id"],
        "assessor_identity": engagement["assessor_identity"],
        "security_authorizer_identity": engagement["security_authorizer_identity"],
        "window_starts_at": window_starts_at,
        "window_expires_at": window_expires_at,
        "delete_by": delete_by,
        "scope": scope,
        "methodologies": methodologies,
        "source_cidrs": source_cidrs,
        "source_system_ids": source_system_ids,
        "test_account_ids": test_account_ids,
    }


def validate_assessment_report(
    report: Any,
    *,
    assessment_path: Path,
    expected_provider: str,
    expected_assessor_identity: str,
    target_environment: str,
    source_commit: str,
    backend_image: str,
    frontend_image: str,
    contract_digest: str,
    expected_engagement_id: str,
    expected_engagement_sha256: str,
    engagement_window_starts_at: datetime,
    engagement_window_expires_at: datetime,
    engagement_delete_by: datetime,
    expected_scope: list[str],
    expected_methodologies: list[str],
    expected_source_cidrs: list[str],
    expected_source_system_ids: list[str],
    expected_test_account_ids: list[str],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate signed raw assessor JSON and return values derived from raw findings."""

    if not isinstance(report, dict):
        raise ValueError("signed security assessment report must be a JSON object")
    expected_keys = {
        "schema_version",
        "assessment_id",
        "provider",
        "assessor_identity",
        "independence",
        "target_environment",
        "source_commit",
        "images",
        "contract_digest",
        "engagement",
        "scope",
        "methodologies",
        "execution_identity",
        "data_handling_attestation",
        "started_at",
        "completed_at",
        "findings",
        "summary",
        "report_artifact",
        "completed",
    }
    independence = report.get("independence")
    images = report.get("images")
    scope = _exact_meaningful_list(report.get("scope"))
    methodologies = _exact_meaningful_list(report.get("methodologies"))
    started_at = parse_time(report.get("started_at"))
    completed_at = parse_time(report.get("completed_at"))
    engagement = report.get("engagement")
    execution_identity = report.get("execution_identity")
    actual_source_cidrs = (
        _canonical_cidrs(execution_identity.get("source_cidrs"))
        if isinstance(execution_identity, dict)
        else None
    )
    actual_source_system_ids = (
        _exact_meaningful_list(execution_identity.get("system_ids"))
        if isinstance(execution_identity, dict)
        else None
    )
    actual_test_account_ids = (
        _exact_meaningful_list(execution_identity.get("test_account_ids"))
        if isinstance(execution_identity, dict)
        else None
    )
    data_handling_attestation = report.get("data_handling_attestation")
    deleted_at = (
        parse_time(data_handling_attestation.get("deleted_at"))
        if isinstance(data_handling_attestation, dict)
        else None
    )
    current = now or datetime.now(timezone.utc)
    if not (
        set(report) == expected_keys
        and report.get("schema_version") == ASSESSMENT_REPORT_SCHEMA_VERSION
        and meaningful(report.get("assessment_id"))
        and report.get("provider") == expected_provider
        and report.get("assessor_identity") == expected_assessor_identity
        and isinstance(independence, dict)
        and set(independence)
        == {
            "independent_of_implementation",
            "conflict_check_completed",
            "implementation_contributors",
        }
        and independence.get("independent_of_implementation") is True
        and independence.get("conflict_check_completed") is True
        and independence.get("implementation_contributors") == 0
        and report.get("target_environment") == target_environment
        and report.get("source_commit") == source_commit
        and isinstance(images, dict)
        and set(images) == {"backend", "frontend"}
        and all(
            isinstance(images.get(component), dict)
            and set(images[component]) == {"name"}
            for component in ("backend", "frontend")
        )
        and images["backend"].get("name") == backend_image
        and images["frontend"].get("name") == frontend_image
        and report.get("contract_digest") == contract_digest
        and bool(DIGEST_RE.fullmatch(str(contract_digest)))
        and isinstance(engagement, dict)
        and set(engagement) == {"engagement_id", "sha256"}
        and engagement.get("engagement_id") == expected_engagement_id
        and engagement.get("sha256") == expected_engagement_sha256
        and bool(DIGEST_RE.fullmatch(str(expected_engagement_sha256)))
        and scope is not None
        and set(scope) == set(REQUIRED_ASSESSMENT_SCOPE)
        and set(scope) == set(expected_scope)
        and methodologies is not None
        and set(REQUIRED_ASSESSMENT_METHODOLOGIES).issubset(methodologies)
        and set(methodologies) == set(expected_methodologies)
        and isinstance(execution_identity, dict)
        and set(execution_identity)
        == {"source_cidrs", "system_ids", "test_account_ids"}
        and actual_source_cidrs == expected_source_cidrs
        and actual_source_system_ids == expected_source_system_ids
        and actual_test_account_ids == expected_test_account_ids
        and started_at is not None
        and completed_at is not None
        and engagement_window_starts_at <= started_at < completed_at
        and completed_at <= engagement_window_expires_at
        and completed_at <= current
        and isinstance(data_handling_attestation, dict)
        and set(data_handling_attestation)
        == {
            "working_evidence_encrypted_at_rest",
            "secret_material_recorded",
            "production_data_retained",
            "working_evidence_deleted",
            "deleted_at",
        }
        and data_handling_attestation.get("working_evidence_encrypted_at_rest") is True
        and data_handling_attestation.get("secret_material_recorded") is False
        and data_handling_attestation.get("production_data_retained") is False
        and data_handling_attestation.get("working_evidence_deleted") is True
        and deleted_at is not None
        and started_at <= deleted_at <= completed_at
        and deleted_at <= engagement_delete_by
        and report.get("completed") is True
        and not contains_secret_material_key(report)
    ):
        raise ValueError("signed security assessment is not release-bound, independent, or complete")

    findings = report.get("findings")
    if not isinstance(findings, list):
        raise ValueError("signed security assessment findings must be an array")
    expected_finding_keys = {
        "finding_id",
        "severity",
        "status",
        "affected_components",
        "discovered_at",
        "retested_at",
    }
    finding_ids: list[str] = []
    computed_open = {severity: 0 for severity in FINDING_SEVERITIES}
    computed_closed = {severity: 0 for severity in FINDING_SEVERITIES}
    for finding in findings:
        if not isinstance(finding, dict) or set(finding) != expected_finding_keys:
            raise ValueError("signed security assessment contains a malformed finding")
        finding_id = finding.get("finding_id")
        severity = finding.get("severity")
        status = finding.get("status")
        components = _exact_meaningful_list(finding.get("affected_components"))
        discovered_at = parse_time(finding.get("discovered_at"))
        retested_at = parse_time(finding.get("retested_at"))
        timeline_valid = (
            discovered_at is not None
            and started_at <= discovered_at <= completed_at
            and (
                (status == "open" and finding.get("retested_at") is None)
                or (
                    status == "closed"
                    and retested_at is not None
                    and discovered_at <= retested_at <= completed_at
                )
            )
        )
        if not (
            meaningful(finding_id)
            and severity in FINDING_SEVERITIES
            and status in {"open", "closed"}
            and components is not None
            and timeline_valid
        ):
            raise ValueError("signed security assessment contains an invalid finding")
        finding_ids.append(str(finding_id))
        (computed_open if status == "open" else computed_closed)[str(severity)] += 1
    if len(finding_ids) != len(set(finding_ids)):
        raise ValueError("signed security assessment reuses a finding ID")

    summary = report.get("summary")
    open_counts = _severity_counts(summary.get("open_by_severity")) if isinstance(summary, dict) else None
    closed_counts = (
        _severity_counts(summary.get("closed_by_severity")) if isinstance(summary, dict) else None
    )
    no_open_critical_high = computed_open["critical"] == 0 and computed_open["high"] == 0
    if not (
        isinstance(summary, dict)
        and set(summary)
        == {
            "total_findings",
            "open_by_severity",
            "closed_by_severity",
            "critical_high_retest_completed",
        }
        and summary.get("total_findings") == len(findings)
        and open_counts == computed_open
        and closed_counts == computed_closed
        and summary.get("critical_high_retest_completed") is no_open_critical_high
    ):
        raise ValueError("security assessment summary does not match raw findings")

    artifact = report.get("report_artifact")
    if not isinstance(artifact, dict) or set(artifact) != {"path", "sha256", "format"}:
        raise ValueError("signed security assessment has no exact report artifact reference")
    raw_artifact_path = artifact.get("path")
    if not meaningful(raw_artifact_path) or artifact.get("format") != "pdf":
        raise ValueError("signed security assessment report artifact must be a PDF")
    handled, overridden = ga_file_resolution_override(raw_artifact_path)
    if handled:
        artifact_path = overridden
    else:
        artifact_path = Path(str(raw_artifact_path)).expanduser()
        if not artifact_path.is_absolute():
            artifact_path = (assessment_path.parent / artifact_path).resolve()
    if artifact_path is None:
        raise ValueError("signed security assessment report artifact is missing")
    artifact_digest = sha256(artifact_path) if artifact_path.is_file() else "missing"
    if (
        artifact_path.resolve() == assessment_path.resolve()
        or not DIGEST_RE.fullmatch(str(artifact.get("sha256", "")))
        or artifact_digest != artifact.get("sha256")
    ):
        raise ValueError("signed security assessment report artifact is missing or digest-mismatched")

    return {
        "assessment_id": report["assessment_id"],
        "started_at": started_at,
        "completed_at": completed_at,
        "scope": scope,
        "methodologies": methodologies,
        "total_findings": len(findings),
        "open_by_severity": computed_open,
        "closed_by_severity": computed_closed,
        "open_critical": computed_open["critical"],
        "open_high": computed_open["high"],
        "critical_high_retest_completed": no_open_critical_high,
        "report_artifact_path": artifact_path,
        "report_artifact_sha256": artifact_digest,
    }
