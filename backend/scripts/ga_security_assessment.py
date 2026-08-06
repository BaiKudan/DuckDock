"""Strict machine-readable contract for independent DuckDock security assessments."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ASSESSMENT_REPORT_SCHEMA_VERSION = "duckdock-ga-security-assessment-report-v1"
ASSESSMENT_EVIDENCE_SCHEMA_VERSION = "duckdock-ga-independent-security-evidence-v2"
ASSESSMENT_SIGNATURE_NAMESPACE = "duckdock-security-assessment"
REQUIRED_ASSESSMENT_SCOPE = (
    "application-and-api",
    "identity-and-access",
    "kubernetes-infrastructure",
    "supply-chain",
    "agent-security",
)
REQUIRED_ASSESSMENT_METHODOLOGIES = ("penetration-test", "manual-code-review")
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
        "scope",
        "methodologies",
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
        and scope is not None
        and set(scope) == set(REQUIRED_ASSESSMENT_SCOPE)
        and methodologies is not None
        and set(REQUIRED_ASSESSMENT_METHODOLOGIES).issubset(methodologies)
        and started_at is not None
        and completed_at is not None
        and started_at < completed_at <= current
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
    artifact_path = Path(str(raw_artifact_path)).expanduser()
    if not artifact_path.is_absolute():
        artifact_path = (assessment_path.parent / artifact_path).resolve()
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
