#!/usr/bin/env python3
"""Prepare an immutable, non-authorizing execution campaign for DuckDock 2.0 GA."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import math
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

try:
    from scripts.assemble_ga_preapproval_authorization import (
        REQUEST_SCHEMA_VERSION as PREAPPROVAL_REQUEST_SCHEMA_VERSION,
    )
    from scripts.verify_ga_trust_topology import (
        RECEIPT_SCHEMA_VERSION as TRUST_TOPOLOGY_RECEIPT_SCHEMA_VERSION,
        verify as verify_trust_topology,
    )
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from assemble_ga_preapproval_authorization import (
        REQUEST_SCHEMA_VERSION as PREAPPROVAL_REQUEST_SCHEMA_VERSION,
    )
    from verify_ga_trust_topology import (
        RECEIPT_SCHEMA_VERSION as TRUST_TOPOLOGY_RECEIPT_SCHEMA_VERSION,
        verify as verify_trust_topology,
    )


REQUEST_SCHEMA_VERSION = "duckdock-ga-execution-campaign-request-v5"
PLAN_SCHEMA_VERSION = "duckdock-ga-execution-campaign-v5"
CAMPAIGN_ID_RE = re.compile(r"^gaexec_[0-9a-f]{64}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
PLACEHOLDER_MARKERS = ("__CHANGE_ME", "example.invalid", "<", ">")
REQUEST_KEYS = {"schema_version", "release", "target", "execution"}
RELEASE_KEYS = {
    "version",
    "git_commit",
    "backend_image",
    "frontend_image",
    "contract_digest",
}
TARGET_KEYS = {
    "target_id",
    "environment",
    "deployment_mode",
    "public_base_url",
    "object_store_url",
    "fault_domains",
    "maximum_rpo_seconds",
    "maximum_rto_seconds",
    "minimum_sustained_rps",
}
EXECUTION_KEYS = {
    "evidence_root",
    "backup_allowed_signers",
    "kubernetes_context",
    "kubernetes_cluster_uid",
    "kubernetes_principal",
    "kubernetes_scope",
    "change_request_id",
    "namespace",
    "recovery_target_environment",
    "recovery_target_class",
    "window_starts_at",
    "window_expires_at",
}
KUBERNETES_SCOPE_KEYS = {
    "approved_egress_cidrs",
    "secret_name",
    "tls_secret_name",
    "rwx_claim_name",
    "rwx_storage_class",
    "rwx_storage_size",
    "cni_daemonset_namespace",
    "cni_daemonset_name",
    "trusted_probe_namespace",
    "trusted_probe_pod",
    "monitoring_probe_namespace",
    "monitoring_probe_pod",
    "untrusted_probe_namespace",
    "untrusted_probe_pod",
    "ha_drain_zone",
}
KUBERNETES_NON_NAME_SCOPE_KEYS = {
    "approved_egress_cidrs",
    "ha_drain_zone",
    "rwx_storage_size",
}
KUBERNETES_NAME_RE = re.compile(
    r"^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$"
)
KUBERNETES_LABEL_VALUE_RE = re.compile(
    r"^(?:[A-Za-z0-9](?:[-A-Za-z0-9_.]{0,61}[A-Za-z0-9])?)?$"
)
CHANGE_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
STORAGE_SIZE_RE = re.compile(r"^(?P<count>[1-9][0-9]*)(?P<unit>Gi|Ti)$")
RECOVERY_TARGET_CLASSES = {"recovery", "staging"}
MAXIMUM_CAMPAIGN_WINDOW = timedelta(days=14)


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} root must be an object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_payload(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _meaningful(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not any(marker in value for marker in PLACEHOLDER_MARKERS)


def _https_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def _parse_time(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an RFC3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _positive_number(value: Any, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value <= 0:
        raise ValueError(f"{label} must be a positive number")


def _resolve_reference(raw: Any, *, base: Path, label: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{label} path must be a non-empty string")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = base.parent / path
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"{label} does not exist: {path}")
    return path


def _validate_release(release: Any) -> dict[str, Any]:
    if not isinstance(release, dict) or set(release) != RELEASE_KEYS:
        raise ValueError("release must contain the exact final release identity")
    if release.get("version") != "2.0.0":
        raise ValueError("execution campaigns require final version 2.0.0")
    if COMMIT_RE.fullmatch(str(release.get("git_commit", ""))) is None:
        raise ValueError("release git_commit must be a full 40-character commit")
    for key in ("backend_image", "frontend_image"):
        if IMAGE_RE.fullmatch(str(release.get(key, ""))) is None:
            raise ValueError(f"release {key} must be an immutable image digest")
    if DIGEST_RE.fullmatch(str(release.get("contract_digest", ""))) is None:
        raise ValueError("release contract_digest must be SHA-256")
    return dict(release)


def _validate_target(target: Any) -> dict[str, Any]:
    if not isinstance(target, dict) or set(target) != TARGET_KEYS:
        raise ValueError("target must contain the exact production authorization target")
    fault_domains = target.get("fault_domains")
    if not (
        target.get("environment") == "production"
        and target.get("deployment_mode") == "kubernetes-ha"
        and _meaningful(target.get("target_id"))
        and _https_url(target.get("public_base_url"))
        and _https_url(target.get("object_store_url"))
        and isinstance(fault_domains, list)
        and all(_meaningful(domain) for domain in fault_domains)
        and len(fault_domains) == len(set(fault_domains))
        and len(fault_domains) >= 3
    ):
        raise ValueError("target must be a named HTTPS production kubernetes-ha target in 3+ fault domains")
    for key in ("maximum_rpo_seconds", "maximum_rto_seconds", "minimum_sustained_rps"):
        _positive_number(target.get(key), f"target {key}")
    return dict(target)


def _validate_execution(
    execution: Any,
    *,
    target_id: str,
    current: datetime,
    require_fresh_evidence_root: bool,
) -> tuple[dict[str, Any], Path, datetime, datetime]:
    if not isinstance(execution, dict) or set(execution) != EXECUTION_KEYS:
        raise ValueError("execution must contain the exact campaign execution controls")
    for key in (
        "kubernetes_context",
        "kubernetes_cluster_uid",
        "kubernetes_principal",
        "namespace",
        "recovery_target_environment",
    ):
        if not _meaningful(execution.get(key)):
            raise ValueError(f"execution {key} must be a non-placeholder string")
    cluster_uid = str(execution["kubernetes_cluster_uid"])
    if re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        cluster_uid,
    ) is None:
        raise ValueError("execution kubernetes_cluster_uid must be a lowercase RFC 4122 UUID")
    principal = str(execution["kubernetes_principal"])
    if not 3 <= len(principal) <= 256 or not principal.isprintable() or any(
        character.isspace() or character in "*?!<>" for character in principal
    ):
        raise ValueError("execution kubernetes_principal must be one exact safe principal")
    change_request_id = execution.get("change_request_id")
    if (
        not isinstance(change_request_id, str)
        or CHANGE_REQUEST_ID_RE.fullmatch(change_request_id) is None
    ):
        raise ValueError("execution change_request_id must be one exact safe reference")
    scope = execution.get("kubernetes_scope")
    if not isinstance(scope, dict) or set(scope) != KUBERNETES_SCOPE_KEYS:
        raise ValueError("execution kubernetes_scope must contain the exact operational scope")
    name_fields = KUBERNETES_SCOPE_KEYS - KUBERNETES_NON_NAME_SCOPE_KEYS
    if any(
        not isinstance(scope.get(key), str)
        or KUBERNETES_NAME_RE.fullmatch(str(scope[key])) is None
        for key in name_fields
    ):
        raise ValueError("execution kubernetes_scope names must be exact DNS-1123 names")
    drain_zone = scope.get("ha_drain_zone")
    if (
        not isinstance(drain_zone, str)
        or not drain_zone
        or KUBERNETES_LABEL_VALUE_RE.fullmatch(drain_zone) is None
    ):
        raise ValueError("execution kubernetes_scope ha_drain_zone must be one label value")
    storage_size = scope.get("rwx_storage_size")
    storage_match = (
        STORAGE_SIZE_RE.fullmatch(storage_size)
        if isinstance(storage_size, str)
        else None
    )
    storage_gib = (
        int(storage_match.group("count"))
        * (1024 if storage_match.group("unit") == "Ti" else 1)
        if storage_match is not None
        else 0
    )
    if storage_gib < 10:
        raise ValueError("execution kubernetes_scope rwx_storage_size must be at least 10Gi")
    raw_egress = scope.get("approved_egress_cidrs")
    if not isinstance(raw_egress, list) or not raw_egress or len(raw_egress) > 32:
        raise ValueError("execution kubernetes_scope requires 1-32 approved egress CIDRs")
    blocked_networks = (
        ipaddress.ip_network("0.0.0.0/8"),
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("169.254.0.0/16"),
        ipaddress.ip_network("224.0.0.0/4"),
        ipaddress.ip_network("::/128"),
        ipaddress.ip_network("::1/128"),
        ipaddress.ip_network("fe80::/10"),
        ipaddress.ip_network("ff00::/8"),
    )
    egress_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for raw_network in raw_egress:
        try:
            network = ipaddress.ip_network(raw_network, strict=True)
        except ValueError as exc:
            raise ValueError(
                "execution kubernetes_scope approved_egress_cidrs must be canonical"
            ) from exc
        minimum_prefix = 8 if network.version == 4 else 16
        if network.prefixlen < minimum_prefix or any(
            network.version == blocked.version and network.overlaps(blocked)
            for blocked in blocked_networks
        ):
            raise ValueError(
                "execution kubernetes_scope approved_egress_cidrs contains a forbidden or broad network"
            )
        egress_networks.append(network)
    canonical_egress = [
        str(network)
        for network in sorted(
            set(egress_networks),
            key=lambda item: (item.version, int(item.network_address), item.prefixlen),
        )
    ]
    if len(canonical_egress) != len(raw_egress):
        raise ValueError("execution kubernetes_scope approved_egress_cidrs must be unique")
    probe_namespaces = {
        str(scope["trusted_probe_namespace"]),
        str(scope["monitoring_probe_namespace"]),
        str(scope["untrusted_probe_namespace"]),
    }
    if len(probe_namespaces) != 3 or str(execution["namespace"]) in probe_namespaces:
        raise ValueError(
            "execution kubernetes_scope probe namespaces must be distinct from each other and target"
        )
    recovery_target = str(execution["recovery_target_environment"])
    if recovery_target == target_id:
        raise ValueError("destructive recovery target must differ from the production target")
    if execution.get("recovery_target_class") not in RECOVERY_TARGET_CLASSES:
        raise ValueError("recovery_target_class must be recovery or staging")
    raw_root = execution.get("evidence_root")
    if not isinstance(raw_root, str) or not raw_root.strip():
        raise ValueError("execution evidence_root must be a non-empty absolute path")
    evidence_root = Path(raw_root).expanduser()
    if not evidence_root.is_absolute():
        raise ValueError("execution evidence_root must be absolute")
    if evidence_root.is_symlink():
        raise ValueError("execution evidence_root must not be a symbolic link")
    evidence_root = evidence_root.resolve()
    if evidence_root in {Path("/"), Path.home().resolve()} or len(evidence_root.parts) < 4:
        raise ValueError("execution evidence_root is too broad")
    if require_fresh_evidence_root:
        if evidence_root.exists():
            raise ValueError("execution evidence_root already exists; campaigns require a fresh directory")
    elif not evidence_root.is_dir():
        raise ValueError("execution evidence_root must be the existing non-symlink campaign directory")
    backup_reference = execution.get("backup_allowed_signers")
    if not isinstance(backup_reference, dict) or set(backup_reference) != {"path", "sha256"}:
        raise ValueError("execution backup_allowed_signers must contain exactly path and sha256")
    raw_backup_path = backup_reference.get("path")
    if not isinstance(raw_backup_path, str) or not raw_backup_path.strip():
        raise ValueError("execution backup_allowed_signers.path must be a non-empty absolute path")
    backup_path = Path(raw_backup_path).expanduser()
    if not backup_path.is_absolute():
        raise ValueError("execution backup_allowed_signers.path must be absolute")
    if backup_path.is_symlink():
        raise ValueError("execution backup_allowed_signers must not be a symbolic link")
    backup_path = backup_path.resolve()
    if not backup_path.is_file():
        raise ValueError(f"execution backup_allowed_signers does not exist: {backup_path}")
    backup_digest = _sha256(backup_path)
    if backup_reference.get("sha256") != backup_digest:
        raise ValueError("execution backup_allowed_signers digest mismatch")
    starts_at = _parse_time(execution.get("window_starts_at"), "window_starts_at")
    expires_at = _parse_time(execution.get("window_expires_at"), "window_expires_at")
    if starts_at < current - timedelta(minutes=5):
        raise ValueError("execution window starts before campaign preparation")
    if expires_at <= starts_at or expires_at - starts_at > MAXIMUM_CAMPAIGN_WINDOW:
        raise ValueError("execution window must be positive and no longer than 14 days")
    if expires_at <= current:
        raise ValueError("execution window is already expired")
    normalized = dict(execution)
    normalized["kubernetes_scope"] = {
        **scope,
        "approved_egress_cidrs": canonical_egress,
    }
    normalized["evidence_root"] = str(evidence_root)
    normalized["backup_allowed_signers"] = {
        "path": str(backup_path),
        "sha256": backup_digest,
    }
    normalized["window_starts_at"] = starts_at.isoformat()
    normalized["window_expires_at"] = expires_at.isoformat()
    return normalized, evidence_root, starts_at, expires_at


def _verified_topology_receipt(path: Path) -> tuple[dict[str, Any], Path]:
    receipt = _load_object(path, "trust topology receipt")
    if receipt.get("schema_version") != TRUST_TOPOLOGY_RECEIPT_SCHEMA_VERSION or receipt.get("status") != "PASS":
        raise ValueError("trust topology receipt is not a PASS verification")
    manifest_reference = receipt.get("manifest")
    if not isinstance(manifest_reference, dict) or set(manifest_reference) != {"path", "sha256"}:
        raise ValueError("trust topology receipt has no exact manifest reference")
    manifest_path = _resolve_reference(manifest_reference.get("path"), base=path, label="trust topology manifest")
    if _sha256(manifest_path) != manifest_reference.get("sha256"):
        raise ValueError("trust topology manifest digest mismatch")
    verified_at = _parse_time(receipt.get("verified_at"), "trust topology verified_at")
    if verify_trust_topology(manifest_path, now=verified_at) != receipt:
        raise ValueError("trust topology receipt does not independently re-verify")
    return receipt, manifest_path


def _artifact_paths(root: Path) -> dict[str, str]:
    names = {
        "release_tag_object": "v2.0.0-tag-object.txt",
        "release_tag_verification": "v2.0.0-tag-verification.json",
        "release_source_archive": "duckdock-2.0.0-source.tar.gz",
        "release_build_report": "release-build-report.json",
        "release_build_signature": "release-build-report.json.sig",
        "backend_slsa_statement": "backend-slsa.json",
        "backend_registry_slsa_predicate": "backend-buildkit-slsa-predicate.json",
        "backend_sbom_statement": "backend-sbom.spdx.json",
        "backend_registry_sbom": "backend-buildkit-sbom.spdx.json",
        "backend_scout_sarif": "backend-scout.sarif.json",
        "backend_vulnerability_scan": "backend-vulnerability-scan.json",
        "frontend_slsa_statement": "frontend-slsa.json",
        "frontend_registry_slsa_predicate": "frontend-buildkit-slsa-predicate.json",
        "frontend_sbom_statement": "frontend-sbom.spdx.json",
        "frontend_registry_sbom": "frontend-buildkit-sbom.spdx.json",
        "frontend_scout_sarif": "frontend-scout.sarif.json",
        "frontend_vulnerability_scan": "frontend-vulnerability-scan.json",
        "release_provenance": "release-provenance.json",
        "execution_authorization_manifest": "execution-authorization-manifest.json",
        "execution_authorization_security_statement": "execution-authorization-security.json",
        "execution_authorization_security_signature": "execution-authorization-security.json.sig",
        "execution_authorization_operations_statement": "execution-authorization-operations.json",
        "execution_authorization_operations_signature": "execution-authorization-operations.json.sig",
        "target_cluster_identity_report": "target-cluster-identity.json",
        "target_cluster_identity_signature": "target-cluster-identity.json.sig",
        "target_cluster_access_report": "target-cluster-access.json",
        "target_cluster_access_signature": "target-cluster-access.json.sig",
        "target_deployment_bundle_bootstrap": "target-bundle/bootstrap.yaml",
        "target_deployment_bundle_migration": "target-bundle/migration.yaml",
        "target_deployment_bundle_applications": "target-bundle/applications.yaml",
        "target_deployment_bundle_receipt": "target-bundle/receipt.json",
        "target_deployment_bundle_receipt_sha256": "target-bundle/receipt.sha256",
        "phase_start_target_deployment_statement": "phase-start-target-deployment.json",
        "phase_start_target_deployment_signature": "phase-start-target-deployment.json.sig",
        "target_deployment_preflight": "target-deployment-preflight.json",
        "target_deployment_preflight_sha256": "target-deployment-preflight.json.sha256",
        "target_deployment_receipt": "target-deployment.json",
        "target_deployment_receipt_sha256": "target-deployment.json.sha256",
        "phase_start_tls_statement": "phase-start-tls.json",
        "phase_start_tls_signature": "phase-start-tls.json.sig",
        "phase_start_network_statement": "phase-start-network.json",
        "phase_start_network_signature": "phase-start-network.json.sig",
        "phase_start_secrets_statement": "phase-start-secrets.json",
        "phase_start_secrets_signature": "phase-start-secrets.json.sig",
        "phase_start_capacity_statement": "phase-start-capacity.json",
        "phase_start_capacity_signature": "phase-start-capacity.json.sig",
        "phase_start_alerting_statement": "phase-start-alerting.json",
        "phase_start_alerting_signature": "phase-start-alerting.json.sig",
        "phase_start_recovery_statement": "phase-start-recovery.json",
        "phase_start_recovery_signature": "phase-start-recovery.json.sig",
        "phase_start_state_services_statement": "phase-start-state-services.json",
        "phase_start_state_services_signature": "phase-start-state-services.json.sig",
        "phase_start_high_availability_statement": "phase-start-high-availability.json",
        "phase_start_high_availability_signature": "phase-start-high-availability.json.sig",
        "application_readiness": "target-readiness.json",
        "tls_probe_report": "target-tls-raw.json",
        "tls_probe_signature": "target-tls-raw.json.sig",
        "tls": "target-tls.json",
        "network_probe_report": "target-network-raw.json",
        "network_probe_signature": "target-network-raw.json.sig",
        "network": "target-network.json",
        "secret_rotation_receipt": "secret-rotation-receipt.json",
        "secret_rotation_signature": "secret-rotation-receipt.json.sig",
        "secret_verification_receipt": "secret-verification-receipt.json",
        "secret_verification_signature": "secret-verification-receipt.json.sig",
        "secrets": "target-secrets.json",
        "capacity_load_report": "target-capacity-load.json",
        "capacity_load_signature": "target-capacity-load.json.sig",
        "capacity_growth_receipt": "capacity-growth-receipt.json",
        "capacity_growth_signature": "capacity-growth-receipt.json.sig",
        "capacity_cleanup_receipt": "capacity-cleanup-receipt.json",
        "capacity_cleanup_signature": "capacity-cleanup-receipt.json.sig",
        "capacity": "target-capacity.json",
        "alert_firing_receipt": "alert-firing-receipt.json",
        "alert_firing_signature": "alert-firing-receipt.json.sig",
        "oncall_acknowledgement": "oncall-acknowledgement.json",
        "oncall_acknowledgement_signature": "oncall-acknowledgement.json.sig",
        "alert_resolved_receipt": "alert-resolved-receipt.json",
        "alert_resolved_signature": "alert-resolved-receipt.json.sig",
        "alerting": "target-alerting.json",
        "backup_manifest": "backup-manifest.json",
        "backup_manifest_signature": "backup-manifest.json.sig",
        "backup_media_receipt": "backup-media-receipt.json",
        "backup_media_signature": "backup-media-receipt.json.sig",
        "restore_execution_receipt": "restore-execution-receipt.json",
        "restore_execution_signature": "restore-execution-receipt.json.sig",
        "recovery_verification_receipt": "recovery-verification-receipt.json",
        "recovery_verification_signature": "recovery-verification-receipt.json.sig",
        "recovery": "target-recovery.json",
        "state_provider_receipt": "state-services-provider-receipt.json",
        "state_provider_signature": "state-services-provider-receipt.json.sig",
        "state_verification_receipt": "state-services-verification-receipt.json",
        "state_verification_signature": "state-services-verification-receipt.json.sig",
        "state_services": "state-services-evidence.json",
        "state_services_operations_signature": "state-services-evidence.json.sig",
        "high_availability": "target-high-availability.json",
        "security_assessment_engagement": "security-assessment-engagement.json",
        "security_assessment_engagement_signature": "security-assessment-engagement.json.sig",
        "security_assessment_report": "security-assessment-report.json",
        "security_assessment_signature": "security-assessment-report.json.sig",
        "security_assessment_pdf": "security-assessment.pdf",
        "security_assessment": "independent-security-evidence.json",
        "preapproval_authorization": "preapproval-authorization.json",
        "preapproval_assembly_receipt": "preapproval-assembly-receipt.json",
        "execution_closure": "execution-campaign-closure.json",
    }
    return {key: str(root / name) for key, name in names.items()}


def _phase(
    phase_id: str,
    *,
    risk_class: str,
    depends_on: Sequence[str],
    tools: Sequence[str],
    outputs: Sequence[str],
    policy_roles: Sequence[str] = (),
    acknowledgement: str | None = None,
) -> dict[str, Any]:
    return {
        "phase_id": phase_id,
        "status": "PENDING_EXTERNAL_EVIDENCE",
        "risk_class": risk_class,
        "depends_on": list(depends_on),
        "tools": list(tools),
        "outputs": list(outputs),
        "authorized_policy_roles": list(policy_roles),
        "required_acknowledgement": acknowledgement,
    }


def _phases(
    target: dict[str, Any],
    execution: dict[str, Any],
    *,
    assessor_roles: Sequence[str],
) -> list[dict[str, Any]]:
    target_id = str(target["target_id"])
    recovery_target = str(execution["recovery_target_environment"])
    evidence_phases = [
        "application_readiness",
        "tls",
        "secrets",
        "network",
        "alerting",
        "recovery",
        "capacity",
        "high_availability",
        "security_assessment",
    ]
    return [
        _phase(
            "execution_authorization",
            risk_class="ORGANIZATIONAL_AUTHORIZATION",
            depends_on=[],
            tools=[
                "prepare_ga_execution_authorization.py",
                "sign_ga_execution_authorization.py",
                "verify_ga_execution_authorization.py",
            ],
            outputs=[
                "execution_authorization_manifest",
                "execution_authorization_security_statement",
                "execution_authorization_security_signature",
                "execution_authorization_operations_statement",
                "execution_authorization_operations_signature",
            ],
            policy_roles=[
                "approval:approver/Security",
                "approval:approver/Operations",
            ],
        ),
        _phase(
            "release_provenance",
            risk_class="SIGNED_RELEASE_INPUT",
            depends_on=[],
            tools=[
                "git verify-tag/cat-file/archive",
                "docker buildx imagetools inspect",
                "Docker Scout SARIF",
                "ssh-keygen -Y sign",
                "collect_ga_release_provenance.py",
            ],
            outputs=[
                "release_tag_object",
                "release_tag_verification",
                "release_source_archive",
                "release_build_report",
                "release_build_signature",
                "backend_slsa_statement",
                "backend_registry_slsa_predicate",
                "backend_sbom_statement",
                "backend_registry_sbom",
                "backend_scout_sarif",
                "backend_vulnerability_scan",
                "frontend_slsa_statement",
                "frontend_registry_slsa_predicate",
                "frontend_sbom_statement",
                "frontend_registry_sbom",
                "frontend_scout_sarif",
                "frontend_vulnerability_scan",
                "release_provenance",
            ],
            policy_roles=["release-provenance:builder"],
        ),
        _phase(
            "target_cluster_identity",
            risk_class="READ_ONLY_TARGET_IDENTITY",
            depends_on=["execution_authorization", "release_provenance"],
            tools=["collect_ga_target_cluster_identity.py"],
            outputs=[
                "target_cluster_identity_report",
                "target_cluster_identity_signature",
            ],
            policy_roles=["approval:approver/Operations"],
        ),
        _phase(
            "target_cluster_access",
            risk_class="READ_ONLY_TARGET_AUTHORIZATION",
            depends_on=["target_cluster_identity", "release_provenance"],
            tools=["collect_ga_target_cluster_access.py"],
            outputs=[
                "target_cluster_access_report",
                "target_cluster_access_signature",
            ],
            policy_roles=["approval:approver/Operations"],
        ),
        _phase(
            "target_deployment",
            risk_class="MUTATING_TARGET",
            depends_on=[
                "execution_authorization",
                "release_provenance",
                "target_cluster_identity",
                "target_cluster_access",
            ],
            tools=[
                "start_ga_execution_phase.py",
                "prepare-kubernetes-ha-target.py",
                "deploy-kubernetes-ha-target.py",
            ],
            outputs=[
                "target_deployment_bundle_bootstrap",
                "target_deployment_bundle_migration",
                "target_deployment_bundle_applications",
                "target_deployment_bundle_receipt",
                "target_deployment_bundle_receipt_sha256",
                "phase_start_target_deployment_statement",
                "phase_start_target_deployment_signature",
                "target_deployment_preflight",
                "target_deployment_preflight_sha256",
                "target_deployment_receipt",
                "target_deployment_receipt_sha256",
            ],
            acknowledgement=target_id,
        ),
        _phase(
            "application_readiness",
            risk_class="READ_ONLY_TARGET",
            depends_on=["release_provenance", "target_deployment"],
            tools=["collect_ga_target_readiness.py"],
            outputs=["application_readiness"],
        ),
        _phase(
            "tls",
            risk_class="READ_ONLY_EXTERNAL_VANTAGE",
            depends_on=["execution_authorization", "release_provenance"],
            tools=[
                "start_ga_execution_phase.py",
                "probe_ga_target_tls.py",
                "ssh-keygen -Y sign",
                "collect_ga_target_tls.py",
            ],
            outputs=[
                "phase_start_tls_statement",
                "phase_start_tls_signature",
                "tls_probe_report",
                "tls_probe_signature",
                "tls",
            ],
            policy_roles=["tls:probe-operator"],
            acknowledgement=target_id,
        ),
        _phase(
            "network",
            risk_class="READ_ONLY_AND_POLICY_PROBES",
            depends_on=[
                "execution_authorization",
                "release_provenance",
                "target_cluster_identity",
                "target_cluster_access",
            ],
            tools=[
                "start_ga_execution_phase.py",
                "collect_ga_target_network.py",
                "ssh-keygen -Y sign",
                "collect_ga_target_network_evidence.py",
            ],
            outputs=[
                "phase_start_network_statement",
                "phase_start_network_signature",
                "network_probe_report",
                "network_probe_signature",
                "network",
            ],
            policy_roles=["network:probe-operator"],
            acknowledgement=target_id,
        ),
        _phase(
            "secrets",
            risk_class="MUTATING_TARGET",
            depends_on=["execution_authorization", "application_readiness", "network"],
            tools=[
                "start_ga_execution_phase.py",
                "external provider/verifier receipts",
                "collect_ga_target_secrets.py",
            ],
            outputs=[
                "phase_start_secrets_statement",
                "phase_start_secrets_signature",
                "secret_rotation_receipt",
                "secret_rotation_signature",
                "secret_verification_receipt",
                "secret_verification_signature",
                "secrets",
            ],
            policy_roles=["secrets:provider", "secrets:verifier"],
            acknowledgement=target_id,
        ),
        _phase(
            "capacity",
            risk_class="MUTATING_TARGET",
            depends_on=[
                "execution_authorization",
                "application_readiness",
                "network",
                "secrets",
            ],
            tools=[
                "start_ga_execution_phase.py",
                "g2_target_capacity_gate.py",
                "external storage/cleanup receipts",
                "collect_ga_target_capacity.py",
            ],
            outputs=[
                "phase_start_capacity_statement",
                "phase_start_capacity_signature",
                "capacity_load_report",
                "capacity_load_signature",
                "capacity_growth_receipt",
                "capacity_growth_signature",
                "capacity_cleanup_receipt",
                "capacity_cleanup_signature",
                "capacity",
            ],
            policy_roles=[
                "capacity:load-executor",
                "capacity:storage-observer",
                "capacity:cleanup-verifier",
            ],
            acknowledgement=target_id,
        ),
        _phase(
            "alerting",
            risk_class="MUTATING_TARGET",
            depends_on=["execution_authorization", "application_readiness", "network"],
            tools=[
                "start_ga_execution_phase.py",
                "external delivery/on-call receipts",
                "collect_ga_target_alerting.py",
            ],
            outputs=[
                "phase_start_alerting_statement",
                "phase_start_alerting_signature",
                "alert_firing_receipt",
                "alert_firing_signature",
                "oncall_acknowledgement",
                "oncall_acknowledgement_signature",
                "alert_resolved_receipt",
                "alert_resolved_signature",
                "alerting",
            ],
            policy_roles=["alerting:delivery", "alerting:oncall"],
            acknowledgement=target_id,
        ),
        _phase(
            "recovery",
            risk_class="DESTRUCTIVE_NON_PRODUCTION",
            depends_on=["execution_authorization", "release_provenance"],
            tools=[
                "start_ga_execution_phase.py",
                "external backup/restore/verifier receipts",
                "collect_ga_target_recovery.py",
            ],
            outputs=[
                "phase_start_recovery_statement",
                "phase_start_recovery_signature",
                "backup_manifest",
                "backup_manifest_signature",
                "backup_media_receipt",
                "backup_media_signature",
                "restore_execution_receipt",
                "restore_execution_signature",
                "recovery_verification_receipt",
                "recovery_verification_signature",
                "recovery",
            ],
            policy_roles=[
                "recovery:storage",
                "recovery:restore-executor",
                "recovery:verifier",
            ],
            acknowledgement=recovery_target,
        ),
        _phase(
            "state_services",
            risk_class="PROVIDER_FAILOVER_OBSERVATION",
            depends_on=["execution_authorization", "release_provenance"],
            tools=[
                "start_ga_execution_phase.py",
                "external provider/verifier receipts",
                "collect_ga_state_services_ha.py",
            ],
            outputs=[
                "phase_start_state_services_statement",
                "phase_start_state_services_signature",
                "state_provider_receipt",
                "state_provider_signature",
                "state_verification_receipt",
                "state_verification_signature",
                "state_services",
                "state_services_operations_signature",
            ],
            policy_roles=[
                "state-services:provider",
                "state-services:verifier",
                "approval:approver/Operations",
            ],
            acknowledgement=target_id,
        ),
        _phase(
            "high_availability",
            risk_class="DISRUPTIVE_TARGET",
            depends_on=[
                "execution_authorization",
                "application_readiness",
                "network",
                "state_services",
            ],
            tools=["start_ga_execution_phase.py", "collect_ga_target_ha.py"],
            outputs=[
                "phase_start_high_availability_statement",
                "phase_start_high_availability_signature",
                "high_availability",
            ],
            acknowledgement=target_id,
        ),
        _phase(
            "security_assessment",
            risk_class="INDEPENDENT_REVIEW",
            depends_on=["application_readiness", "tls", "network"],
            tools=[
                "Security-signed assessment engagement",
                "external assessment/signature",
                "collect_ga_independent_security.py",
            ],
            outputs=[
                "security_assessment_engagement",
                "security_assessment_engagement_signature",
                "security_assessment_report",
                "security_assessment_signature",
                "security_assessment_pdf",
                "security_assessment",
            ],
            policy_roles=["approval:approver/Security", *assessor_roles],
        ),
        _phase(
            "preapproval_assembly",
            risk_class="READ_ONLY_ASSEMBLY",
            depends_on=["execution_authorization", "release_provenance", *evidence_phases],
            tools=["close_ga_execution_campaign.py"],
            outputs=[
                "preapproval_authorization",
                "preapproval_assembly_receipt",
                "execution_closure",
            ],
        ),
    ]


def _validate_phase_graph(
    phases: Sequence[dict[str, Any]],
    artifacts: dict[str, str],
    topology_receipt: dict[str, Any],
) -> None:
    phase_ids = [str(phase.get("phase_id", "")) for phase in phases]
    if not phase_ids or len(phase_ids) != len(set(phase_ids)):
        raise ValueError("execution phases must have unique IDs")
    available_roles = {
        f"{policy_name}:{role}"
        for policy_name, policy in topology_receipt.get("policies", {}).items()
        if isinstance(policy, dict) and isinstance(policy.get("roles"), dict)
        for role in policy["roles"]
    }
    seen_phases: set[str] = set()
    planned_outputs: list[str] = []
    acknowledgement_risks = {
        "READ_ONLY_EXTERNAL_VANTAGE",
        "READ_ONLY_AND_POLICY_PROBES",
        "MUTATING_TARGET",
        "DESTRUCTIVE_NON_PRODUCTION",
        "DISRUPTIVE_TARGET",
        "PROVIDER_FAILOVER_OBSERVATION",
    }
    for phase in phases:
        phase_id = str(phase["phase_id"])
        dependencies = phase.get("depends_on")
        outputs = phase.get("outputs")
        roles = phase.get("authorized_policy_roles")
        tools = phase.get("tools")
        if not (
            phase.get("status") == "PENDING_EXTERNAL_EVIDENCE"
            and isinstance(dependencies, list)
            and len(dependencies) == len(set(dependencies))
            and set(dependencies).issubset(seen_phases)
            and isinstance(outputs, list)
            and outputs
            and len(outputs) == len(set(outputs))
            and set(outputs).issubset(artifacts)
            and isinstance(roles, list)
            and set(roles).issubset(available_roles)
            and isinstance(tools, list)
            and tools
            and all(_meaningful(tool) for tool in tools)
        ):
            raise ValueError(f"execution phase graph is invalid at {phase_id}")
        if phase.get("risk_class") in acknowledgement_risks and not _meaningful(phase.get("required_acknowledgement")):
            raise ValueError(f"execution phase {phase_id} lacks an exact safety acknowledgement")
        if phase.get("risk_class") in acknowledgement_risks and "execution_authorization" not in dependencies:
            raise ValueError(f"execution phase {phase_id} is not dual-authorization gated")
        planned_outputs.extend(outputs)
        seen_phases.add(phase_id)
    if len(planned_outputs) != len(set(planned_outputs)) or set(planned_outputs) != set(artifacts):
        raise ValueError("execution phases must produce every planned artifact exactly once")


def prepare(
    request_path: Path,
    topology_receipt_path: Path,
    assembly_request_output: Path,
    *,
    now: datetime | None = None,
    require_fresh_evidence_root: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    request_path = request_path.resolve()
    topology_receipt_path = topology_receipt_path.resolve()
    request_digest = _sha256(request_path)
    topology_receipt_digest = _sha256(topology_receipt_path)
    request = _load_object(request_path, "execution campaign request")
    if set(request) != REQUEST_KEYS or request.get("schema_version") != REQUEST_SCHEMA_VERSION:
        raise ValueError(f"request must use exact schema {REQUEST_SCHEMA_VERSION}")
    release = _validate_release(request.get("release"))
    target = _validate_target(request.get("target"))
    execution, evidence_root, starts_at, expires_at = _validate_execution(
        request.get("execution"),
        target_id=str(target["target_id"]),
        current=current,
        require_fresh_evidence_root=require_fresh_evidence_root,
    )
    topology_receipt, topology_manifest_path = _verified_topology_receipt(topology_receipt_path)
    if _sha256(topology_receipt_path) != topology_receipt_digest:
        raise ValueError("trust topology receipt changed during campaign preparation")

    artifacts = _artifact_paths(evidence_root)
    policies = topology_receipt.get("policies")
    approval_policy = policies.get("approval") if isinstance(policies, dict) else None
    approval_roles = approval_policy.get("roles") if isinstance(approval_policy, dict) else None
    assessor_roles = (
        [f"approval:{role}" for role in sorted(approval_roles) if role.startswith("security-assessor/")]
        if isinstance(approval_roles, dict)
        else []
    )
    if not assessor_roles:
        raise ValueError("trust topology receipt has no independent security assessor role")
    assembly_request = {
        "schema_version": PREAPPROVAL_REQUEST_SCHEMA_VERSION,
        "release": {
            **release,
            "provenance_evidence_path": artifacts["release_provenance"],
        },
        "target": target,
        "evidence": {
            name: artifacts[name]
            for name in (
                "application_readiness",
                "tls",
                "secrets",
                "network",
                "alerting",
                "recovery",
                "capacity",
                "high_availability",
                "security_assessment",
            )
        },
    }
    assembly_request_payload = _json_payload(assembly_request)
    assembly_request_digest = hashlib.sha256(assembly_request_payload).hexdigest()
    campaign_basis = {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "request_sha256": request_digest,
        "trust_topology_receipt_sha256": topology_receipt_digest,
        "release": release,
        "target": target,
        "execution": execution,
        "assembly_request_sha256": assembly_request_digest,
    }
    campaign_id = (
        "gaexec_"
        + hashlib.sha256(
            json.dumps(
                campaign_basis,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
    )
    if CAMPAIGN_ID_RE.fullmatch(campaign_id) is None:
        raise ValueError("could not derive execution campaign ID")
    phases = _phases(target, execution, assessor_roles=assessor_roles)
    _validate_phase_graph(phases, artifacts, topology_receipt)
    plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "status": "PLANNED_EXTERNAL_EXECUTION",
        "authorization_boundary": "does_not_authorize_GA_or_target_mutation",
        "campaign_id": campaign_id,
        "created_at": current.isoformat(),
        "window_starts_at": starts_at.isoformat(),
        "window_expires_at": expires_at.isoformat(),
        "request": {"path": str(request_path), "sha256": request_digest},
        "trust_topology": {
            "receipt": {
                "path": str(topology_receipt_path),
                "sha256": topology_receipt_digest,
            },
            "manifest": {
                "path": str(topology_manifest_path),
                "sha256": topology_receipt["manifest"]["sha256"],
            },
            "identity_count": topology_receipt["separation"]["identity_count"],
            "public_key_count": topology_receipt["separation"]["public_key_count"],
        },
        "release": release,
        "target": target,
        "execution": {
            **execution,
            "evidence_root": str(evidence_root),
        },
        "artifacts": artifacts,
        "phases": phases,
        "preapproval_assembly_request": {
            "path": str(assembly_request_output.resolve()),
            "sha256": assembly_request_digest,
        },
        "next_action": "release_authority_reviews_plan_before_external_execution",
    }
    if _sha256(request_path) != request_digest:
        raise ValueError("execution campaign request changed during preparation")
    return plan, assembly_request


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise ValueError(f"output already exists: {path}") from exc
        temporary.unlink()
    finally:
        temporary.unlink(missing_ok=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--trust-topology-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--assembly-request-output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        for path, label in (
            (args.request, "campaign request"),
            (args.trust_topology_receipt, "trust topology receipt"),
        ):
            if not path.is_file():
                raise ValueError(f"{label} does not exist: {path}")
        outputs = {args.output.resolve(), args.assembly_request_output.resolve()}
        if len(outputs) != 2:
            raise ValueError("campaign and assembly-request outputs must be distinct")
        inputs = {args.request.resolve(), args.trust_topology_receipt.resolve()}
        if outputs.intersection(inputs):
            raise ValueError("an output must not overwrite an input")
        if any(path.exists() for path in outputs):
            raise ValueError("an output already exists; campaign artifacts are immutable")
        raw_request = _load_object(args.request, "execution campaign request")
        execution = raw_request.get("execution")
        raw_root = execution.get("evidence_root") if isinstance(execution, dict) else None
        if isinstance(raw_root, str) and raw_root.strip():
            evidence_root = Path(raw_root).expanduser()
            if evidence_root.is_absolute():
                evidence_root = evidence_root.resolve()
                if any(output == evidence_root or evidence_root in output.parents for output in outputs):
                    raise ValueError("campaign metadata outputs must be outside the fresh evidence root")
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        plan, assembly_request = prepare(
            args.request,
            args.trust_topology_receipt,
            args.assembly_request_output,
        )
        assembly_payload = _json_payload(assembly_request)
        plan_payload = _json_payload(plan)
        _atomic_write(args.assembly_request_output, assembly_payload)
        try:
            _atomic_write(args.output, plan_payload)
            persisted_plan = _load_object(args.output, "persisted execution campaign")
            persisted_request = _load_object(args.assembly_request_output, "persisted preapproval request")
            reverified_plan, reverified_request = prepare(
                args.request,
                args.trust_topology_receipt,
                args.assembly_request_output,
                now=_parse_time(plan["created_at"], "campaign created_at"),
            )
            if (
                persisted_plan != plan
                or persisted_request != assembly_request
                or reverified_plan != plan
                or reverified_request != assembly_request
            ):
                raise ValueError("persisted execution campaign did not independently re-verify")
        except Exception:
            args.output.unlink(missing_ok=True)
            raise
    except (OSError, UnicodeError, ValueError) as exc:
        args.output.unlink(missing_ok=True)
        args.assembly_request_output.unlink(missing_ok=True)
        print(f"GA execution campaign preparation failed: {exc}", file=sys.stderr)
        return 3
    print(plan_payload.decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
