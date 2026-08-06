#!/usr/bin/env python3
"""Validate a DuckDock 2.0 target-environment GA authorization bundle.

The in-product GA readiness endpoint proves application-level integrity. This
gate deliberately adds the deployment, independent-security, capacity,
recovery, HA, on-call and four-role authorization evidence needed to call a
specific immutable release production GA.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import math
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

try:
    from scripts.ga_path_resolution import ga_file_resolution_override
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_path_resolution import ga_file_resolution_override

try:
    from scripts.ga_approval_campaign import validate_campaign_freeze
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_approval_campaign import validate_campaign_freeze

try:
    from scripts.ga_capacity_evidence import (
        CAPACITY_EVIDENCE_SCHEMA_VERSION as CAPACITY_SCHEMA_VERSION,
        CAPACITY_POLICY_SCHEMA_VERSION,
        CLEANUP_RECEIPT_SIGNATURE_NAMESPACE as CAPACITY_CLEANUP_SIGNATURE_NAMESPACE,
        GROWTH_RECEIPT_SIGNATURE_NAMESPACE as CAPACITY_GROWTH_SIGNATURE_NAMESPACE,
        LOAD_REPORT_SIGNATURE_NAMESPACE as CAPACITY_LOAD_SIGNATURE_NAMESPACE,
        REQUIRED_COUNTERS as CAPACITY_REQUIRED_COUNTERS,
        validate_cleanup_receipt as validate_capacity_cleanup_receipt,
        validate_growth_receipt as validate_capacity_growth_receipt,
        validate_load_report as validate_capacity_load_report,
    )
    from scripts.ga_security_assessment import (
        ASSESSMENT_EVIDENCE_SCHEMA_VERSION as SECURITY_EVIDENCE_SCHEMA_VERSION,
        ASSESSMENT_SIGNATURE_NAMESPACE as SECURITY_ASSESSMENT_SIGNATURE_NAMESPACE,
        validate_assessment_report,
    )
    from scripts.ga_network_evidence import (
        NETWORK_EVIDENCE_SCHEMA_VERSION,
        NETWORK_POLICY_SCHEMA_VERSION,
        NETWORK_SIGNATURE_NAMESPACE,
        validate_network_probe_envelope,
    )
    from scripts.ga_release_provenance import (
        BUILD_SIGNATURE_NAMESPACE as RELEASE_BUILD_SIGNATURE_NAMESPACE,
        PROVENANCE_EVIDENCE_SCHEMA_VERSION as RELEASE_PROVENANCE_EVIDENCE_SCHEMA_VERSION,
        PROVENANCE_POLICY_SCHEMA_VERSION as RELEASE_PROVENANCE_POLICY_SCHEMA_VERSION,
        REQUIRED_ARTIFACTS as RELEASE_PROVENANCE_REQUIRED_ARTIFACTS,
        validate_build_report as validate_release_build_report,
    )
    from scripts.ga_state_services_evidence import (
        OPERATIONS_SIGNATURE_NAMESPACE as STATE_OPERATIONS_SIGNATURE_NAMESPACE,
        PROVIDER_SIGNATURE_NAMESPACE as STATE_PROVIDER_SIGNATURE_NAMESPACE,
        REQUIRED_SERVICES as STATE_REQUIRED_SERVICES,
        STATE_EVIDENCE_SCHEMA_VERSION,
        STATE_POLICY_SCHEMA_VERSION,
        VERIFICATION_SIGNATURE_NAMESPACE as STATE_VERIFICATION_SIGNATURE_NAMESPACE,
        build_service_projection as build_state_service_projection,
        validate_provider_receipt as validate_state_provider_receipt,
        validate_verification_receipt as validate_state_verification_receipt,
    )
    from scripts.ga_tls_evidence import (
        TLS_EVIDENCE_SCHEMA_VERSION,
        TLS_POLICY_SCHEMA_VERSION,
        TLS_SIGNATURE_NAMESPACE,
        validate_tls_probe_report,
    )
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_capacity_evidence import (
        CAPACITY_EVIDENCE_SCHEMA_VERSION as CAPACITY_SCHEMA_VERSION,
        CAPACITY_POLICY_SCHEMA_VERSION,
        CLEANUP_RECEIPT_SIGNATURE_NAMESPACE as CAPACITY_CLEANUP_SIGNATURE_NAMESPACE,
        GROWTH_RECEIPT_SIGNATURE_NAMESPACE as CAPACITY_GROWTH_SIGNATURE_NAMESPACE,
        LOAD_REPORT_SIGNATURE_NAMESPACE as CAPACITY_LOAD_SIGNATURE_NAMESPACE,
        REQUIRED_COUNTERS as CAPACITY_REQUIRED_COUNTERS,
        validate_cleanup_receipt as validate_capacity_cleanup_receipt,
        validate_growth_receipt as validate_capacity_growth_receipt,
        validate_load_report as validate_capacity_load_report,
    )
    from ga_security_assessment import (
        ASSESSMENT_EVIDENCE_SCHEMA_VERSION as SECURITY_EVIDENCE_SCHEMA_VERSION,
        ASSESSMENT_SIGNATURE_NAMESPACE as SECURITY_ASSESSMENT_SIGNATURE_NAMESPACE,
        validate_assessment_report,
    )
    from ga_network_evidence import (
        NETWORK_EVIDENCE_SCHEMA_VERSION,
        NETWORK_POLICY_SCHEMA_VERSION,
        NETWORK_SIGNATURE_NAMESPACE,
        validate_network_probe_envelope,
    )
    from ga_release_provenance import (
        BUILD_SIGNATURE_NAMESPACE as RELEASE_BUILD_SIGNATURE_NAMESPACE,
        PROVENANCE_EVIDENCE_SCHEMA_VERSION as RELEASE_PROVENANCE_EVIDENCE_SCHEMA_VERSION,
        PROVENANCE_POLICY_SCHEMA_VERSION as RELEASE_PROVENANCE_POLICY_SCHEMA_VERSION,
        REQUIRED_ARTIFACTS as RELEASE_PROVENANCE_REQUIRED_ARTIFACTS,
        validate_build_report as validate_release_build_report,
    )
    from ga_state_services_evidence import (
        OPERATIONS_SIGNATURE_NAMESPACE as STATE_OPERATIONS_SIGNATURE_NAMESPACE,
        PROVIDER_SIGNATURE_NAMESPACE as STATE_PROVIDER_SIGNATURE_NAMESPACE,
        REQUIRED_SERVICES as STATE_REQUIRED_SERVICES,
        STATE_EVIDENCE_SCHEMA_VERSION,
        STATE_POLICY_SCHEMA_VERSION,
        VERIFICATION_SIGNATURE_NAMESPACE as STATE_VERIFICATION_SIGNATURE_NAMESPACE,
        build_service_projection as build_state_service_projection,
        validate_provider_receipt as validate_state_provider_receipt,
        validate_verification_receipt as validate_state_verification_receipt,
    )
    from ga_tls_evidence import (
        TLS_EVIDENCE_SCHEMA_VERSION,
        TLS_POLICY_SCHEMA_VERSION,
        TLS_SIGNATURE_NAMESPACE,
        validate_tls_probe_report,
    )


SCHEMA_VERSION = "duckdock-ga-production-authorization-v2"
APPROVAL_POLICY_SCHEMA_VERSION = "duckdock-ga-approval-policy-v2"
APPROVAL_STATEMENT_SCHEMA_VERSION = "duckdock-ga-approval-statement-v2"
ALERTING_SCHEMA_VERSION = "duckdock-ga-alerting-evidence-v2"
ALERTING_POLICY_SCHEMA_VERSION = "duckdock-ga-alerting-trust-policy-v1"
ALERT_DELIVERY_SCHEMA_VERSION = "duckdock-ga-alert-delivery-receipt-v1"
ONCALL_ACK_SCHEMA_VERSION = "duckdock-ga-oncall-acknowledgement-v1"
ALERT_DELIVERY_SIGNATURE_NAMESPACE = "duckdock-alert-delivery-receipt"
ONCALL_ACK_SIGNATURE_NAMESPACE = "duckdock-oncall-acknowledgement"
SECRETS_SCHEMA_VERSION = "duckdock-ga-secrets-evidence-v2"
SECRETS_POLICY_SCHEMA_VERSION = "duckdock-ga-secrets-trust-policy-v1"
SECRET_ROTATION_SCHEMA_VERSION = "duckdock-ga-secret-rotation-receipt-v1"
SECRET_VERIFICATION_SCHEMA_VERSION = "duckdock-ga-secret-verification-receipt-v1"
SECRET_ROTATION_SIGNATURE_NAMESPACE = "duckdock-secret-rotation-receipt"
SECRET_VERIFICATION_SIGNATURE_NAMESPACE = "duckdock-secret-verification-receipt"
SECRET_WORKLOAD_COMPONENTS = ("backend", "worker", "beat")
REQUIRED_SECRET_CLASSES = ("application-signing", "database", "object-store")
RECOVERY_SCHEMA_VERSION = "duckdock-ga-recovery-evidence-v2"
RECOVERY_POLICY_SCHEMA_VERSION = "duckdock-ga-recovery-trust-policy-v1"
BACKUP_MEDIA_SCHEMA_VERSION = "duckdock-ga-backup-media-receipt-v1"
RESTORE_EXECUTION_SCHEMA_VERSION = "duckdock-ga-restore-execution-receipt-v1"
RECOVERY_VERIFICATION_SCHEMA_VERSION = "duckdock-ga-recovery-verification-receipt-v1"
BACKUP_MEDIA_SIGNATURE_NAMESPACE = "duckdock-backup-media-receipt"
RESTORE_EXECUTION_SIGNATURE_NAMESPACE = "duckdock-restore-execution-receipt"
RECOVERY_VERIFICATION_SIGNATURE_NAMESPACE = "duckdock-recovery-verification-receipt"
RECOVERY_REQUIRED_ARTIFACTS = ("minio.tar.gz.age", "mysql.sql.gz.age", "repos.tar.gz.age")
RECOVERY_REQUIRED_STAGES = ("mysql", "repositories", "object-store")
RECOVERY_ENVIRONMENT_CLASSES = {"recovery", "staging"}
RECOVERY_MINIMUM_RETENTION = timedelta(days=30)
REQUIRED_CONTROLS = {
    "application_readiness",
    "tls",
    "secrets",
    "network",
    "alerting",
    "recovery",
    "capacity",
    "high_availability",
    "security_assessment",
}
REQUIRED_APPROVAL_ROLES = {"Product", "Architecture", "Security", "Operations"}
FOUNDATION_CHECK_KEYS = {
    "release_version",
    "release_commit",
    "backend_image",
    "frontend_image",
    "contract_digest",
    "release_provenance",
    "target_identity",
    "approval_policy",
    "approval_trust_store",
}
APPROVAL_CHECK_KEYS = {
    "approval_roles",
    "approval_four_eyes",
    "approval_campaign",
    "approval_Product",
    "approval_Architecture",
    "approval_Security",
    "approval_Operations",
}
PLACEHOLDER_MARKERS = ("__CHANGE_ME", "example.invalid", "<", ">")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
CAMPAIGN_ID_RE = re.compile(r"^gac_[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
REQUIRED_NETWORK_POLICIES = {
    "default-deny",
    "allow-dns",
    "frontend-ingress-and-backend",
    "backend-from-frontend",
    "controlled-external-egress",
}


@dataclass(frozen=True, slots=True)
class Check:
    key: str
    owner: str
    passed: bool
    observed: str
    expected: str
    detail: str


class Gate:
    def __init__(self) -> None:
        self.checks: list[Check] = []

    def add(
        self,
        key: str,
        *,
        owner: str,
        passed: bool,
        observed: Any,
        expected: str,
        detail: str,
    ) -> None:
        self.checks.append(
            Check(
                key=key,
                owner=owner,
                passed=bool(passed),
                observed=str(observed),
                expected=expected,
                detail=detail,
            )
        )


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read authorization JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("authorization root must be an object")
    return value


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def lint_authorization(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if document.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    for key in ("release", "target", "controls", "approval_policy"):
        if not isinstance(document.get(key), dict):
            errors.append(f"{key} must be an object")
    if not isinstance(document.get("approvals"), list):
        errors.append("approvals must be an array")
    else:
        for index, approval in enumerate(document["approvals"]):
            if not isinstance(approval, dict):
                errors.append(f"approvals[{index}] must be an object")
            elif "allowed_signers_path" in approval:
                errors.append(
                    f"approvals[{index}].allowed_signers_path is forbidden; "
                    "use the release-authority approval policy"
                )
    controls = document.get("controls")
    if isinstance(controls, dict):
        missing = REQUIRED_CONTROLS - set(controls)
        extra = set(controls) - REQUIRED_CONTROLS
        if missing:
            errors.append(f"controls missing: {', '.join(sorted(missing))}")
        if extra:
            errors.append(f"unknown controls: {', '.join(sorted(extra))}")
        for name, control in controls.items():
            if not isinstance(control, dict):
                errors.append(f"controls.{name} must be an object")
                continue
            if not isinstance(control.get("evidence"), dict):
                errors.append(f"controls.{name}.evidence must be an object")
    return errors


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _resolve_file(raw: Any, authorization_path: Path) -> Path | None:
    if not isinstance(raw, str) or not raw or any(marker in raw for marker in PLACEHOLDER_MARKERS):
        return None
    handled, overridden = ga_file_resolution_override(raw)
    if handled:
        return overridden
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    adjacent = (authorization_path.parent / path).resolve()
    if adjacent.exists():
        return adjacent
    return (Path.cwd() / path).resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _evidence_json(
    control: dict[str, Any],
    authorization_path: Path,
) -> dict[str, Any] | None:
    evidence = control.get("evidence")
    if not isinstance(evidence, dict):
        return None
    path = _resolve_file(evidence.get("path"), authorization_path)
    if path is None or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _origin(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _meaningful_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not any(marker in value for marker in PLACEHOLDER_MARKERS)


def _control_observed_at(control: dict[str, Any]) -> datetime | None:
    evidence = control.get("evidence")
    return _parse_time(evidence.get("observed_at")) if isinstance(evidence, dict) else None


def _report_release_target_binding(
    report: dict[str, Any] | None,
    *,
    schema_version: str,
    status: str,
    control: dict[str, Any],
    target: dict[str, Any],
    release: dict[str, Any],
) -> bool:
    if not isinstance(report, dict):
        return False
    images = report.get("images")
    observed_at = _control_observed_at(control)
    return (
        report.get("schema_version") == schema_version
        and report.get("scope") == "target-production"
        and report.get("status") == status
        and report.get("passed") is True
        and report.get("target_environment") == target.get("target_id")
        and report.get("source_commit") == release.get("git_commit")
        and observed_at is not None
        and _parse_time(report.get("observed_at")) == observed_at
        and isinstance(images, dict)
        and isinstance(images.get("backend"), dict)
        and isinstance(images.get("frontend"), dict)
        and images["backend"].get("name") == release.get("backend_image")
        and images["frontend"].get("name") == release.get("frontend_image")
    )


def _contains_secret_material_key(value: Any) -> bool:
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
        return any(str(key).lower() in forbidden or _contains_secret_material_key(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_secret_material_key(item) for item in value)
    return False


def _ordered_report_times(
    *values: Any,
    no_later_than: datetime | None = None,
) -> bool:
    parsed = [_parse_time(value) for value in values]
    if any(value is None for value in parsed):
        return False
    concrete = [value for value in parsed if value is not None]
    return concrete == sorted(concrete) and (no_later_than is None or concrete[-1] <= no_later_than)


def _ha_component(report: dict[str, Any], snapshot: str, component: str) -> dict[str, Any]:
    snapshots = report.get(snapshot)
    if not isinstance(snapshots, dict):
        return {}
    value = snapshots.get(component)
    return value if isinstance(value, dict) else {}


def _ha_ready_replicas(report: dict[str, Any], snapshot: str, component: str) -> int:
    try:
        return int(_ha_component(report, snapshot, component).get("ready_replicas", -1))
    except (TypeError, ValueError):
        return -1


def _ha_ready_zones(report: dict[str, Any], snapshot: str, component: str) -> set[str]:
    pods = _ha_component(report, snapshot, component).get("pods")
    if not isinstance(pods, list):
        return set()
    return {
        str(item.get("zone"))
        for item in pods
        if isinstance(item, dict) and item.get("ready") is True and item.get("zone")
    }


def _ha_ready_nodes(report: dict[str, Any], snapshot: str, component: str) -> set[str]:
    pods = _ha_component(report, snapshot, component).get("pods")
    if not isinstance(pods, list):
        return set()
    return {
        str(item.get("node"))
        for item in pods
        if isinstance(item, dict) and item.get("ready") is True and item.get("node")
    }


def _ha_snapshot_consistent(report: dict[str, Any], snapshot: str, component: str) -> bool:
    value = _ha_component(report, snapshot, component)
    pods = value.get("pods")
    if not isinstance(pods, list):
        return False
    ready_pods = [item for item in pods if isinstance(item, dict) and item.get("ready") is True]
    identities = [str(item.get("pod", "")) for item in ready_pods]
    return (
        _ha_ready_replicas(report, snapshot, component) == len(ready_pods)
        and bool(identities)
        and all(identities)
        and len(set(identities)) == len(identities)
        and all(item.get("node") and item.get("zone") for item in ready_pods)
    )


def _evidence_check(
    gate: Gate,
    *,
    key: str,
    owner: str,
    control: dict[str, Any],
    authorization_path: Path,
    now: datetime,
    maximum_age: timedelta,
) -> tuple[bool, datetime | None]:
    evidence = control.get("evidence")
    if not isinstance(evidence, dict):
        gate.add(
            f"{key}_evidence",
            owner=owner,
            passed=False,
            observed="missing",
            expected="existing, digest-bound evidence file",
            detail="Every production assertion must reference retained evidence.",
        )
        return False, None
    path = _resolve_file(evidence.get("path"), authorization_path)
    expected_digest = str(evidence.get("sha256", ""))
    observed_at = _parse_time(evidence.get("observed_at"))
    exists = path is not None and path.is_file()
    actual_digest = _sha256(path) if exists else "missing"
    digest_ok = bool(DIGEST_RE.fullmatch(expected_digest)) and actual_digest == expected_digest
    fresh = observed_at is not None and timedelta(0) <= now - observed_at <= maximum_age
    passed = exists and digest_ok and fresh
    gate.add(
        f"{key}_evidence",
        owner=owner,
        passed=passed,
        observed=f"path={path or 'missing'}, digest={actual_digest}, observed_at={observed_at}",
        expected=f"file digest match; age <= {maximum_age.days}d",
        detail="Evidence is content-addressed and freshness bounded; JSON claims alone are insufficient.",
    )
    return passed, observed_at


def _https_url(value: Any) -> bool:
    if not isinstance(value, str) or any(marker in value for marker in PLACEHOLDER_MARKERS):
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.hostname) and parsed.hostname not in {"localhost", "127.0.0.1"}


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _literal_ip(value: Any, *, globally_routable: bool = False) -> bool:
    if not isinstance(value, str):
        return False
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_global if globally_routable else True


def _nmap_xml_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, str):
        return None
    try:
        root = ET.fromstring(value)
    except ET.ParseError:
        return None
    host_node = root.find("host")
    status_node = host_node.find("status") if host_node is not None else None
    finished = root.find("./runstats/finished")
    if host_node is None or status_node is None or finished is None:
        return None
    addresses = sorted(
        node.get("addr", "")
        for node in host_node.findall("address")
        if node.get("addr") and node.get("addrtype") in {None, "ipv4", "ipv6"}
    )
    port_states: dict[str, str] = {}
    for port_node in host_node.findall("./ports/port"):
        state_node = port_node.find("state")
        port_id = port_node.get("portid")
        if state_node is not None and port_id and port_id.isdigit():
            port_states[port_id] = state_node.get("state", "unknown")
    try:
        elapsed = float(finished.get("elapsed", ""))
    except ValueError:
        return None
    return {
        "host_state": status_node.get("state", "unknown"),
        "addresses": addresses,
        "port_states": port_states,
        "nmap_version": root.get("version", "unknown"),
        "elapsed_seconds": elapsed,
    }


def _network_connection_result(value: Any, *, connected: bool, port: int) -> bool:
    if not isinstance(value, dict):
        return False
    exit_code = value.get("kubectl_exit_code")
    destination_port = value.get("destination_port")
    return (
        _meaningful_string(value.get("source_namespace"))
        and _meaningful_string(value.get("source"))
        and _meaningful_string(value.get("destination_host"))
        and isinstance(destination_port, int)
        and not isinstance(destination_port, bool)
        and destination_port == port
        and value.get("connected") is connected
        and isinstance(exit_code, int)
        and not isinstance(exit_code, bool)
        and ((exit_code == 0) if connected else (exit_code != 0))
    )


def _network_probe_identities_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {"ingress", "monitoring", "untrusted"}:
        return False
    namespaces: list[str] = []
    for role, item in value.items():
        if not isinstance(item, dict):
            return False
        namespace = item.get("namespace")
        labels = item.get("namespace_labels")
        if not (
            _meaningful_string(namespace)
            and _meaningful_string(item.get("namespace_uid"))
            and _meaningful_string(item.get("namespace_resource_version"))
            and isinstance(labels, dict)
            and _meaningful_string(item.get("pod"))
            and _meaningful_string(item.get("pod_uid"))
            and _meaningful_string(item.get("pod_resource_version"))
        ):
            return False
        namespaces.append(namespace)
        if role == "ingress" and labels.get("duckdock.io/ingress") != "true":
            return False
        if role == "monitoring" and labels.get("duckdock.io/monitoring") != "true":
            return False
        if role == "untrusted" and (
            labels.get("duckdock.io/ingress") == "true"
            or labels.get("duckdock.io/monitoring") == "true"
        ):
            return False
    return len(namespaces) == len(set(namespaces))


def _network_policy_snapshots_valid(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    names = [item.get("name") for item in value if isinstance(item, dict)]
    if (
        len(names) != len(value)
        or not all(_meaningful_string(name) for name in names)
        or len(names) != len(set(names))
        or not REQUIRED_NETWORK_POLICIES.issubset(set(names))
    ):
        return False
    for item in value:
        spec = item.get("spec")
        generation = item.get("generation")
        if not (
            _meaningful_string(item.get("uid"))
            and _meaningful_string(item.get("resource_version"))
            and isinstance(generation, int)
            and not isinstance(generation, bool)
            and generation >= 1
            and isinstance(spec, dict)
            and item.get("spec_sha256") == _canonical_digest(spec)
        ):
            return False
        for rule in spec.get("egress", []) or []:
            if not isinstance(rule, dict):
                return False
            for target_entry in rule.get("to", []) or []:
                ip_block = target_entry.get("ipBlock") if isinstance(target_entry, dict) else None
                if isinstance(ip_block, dict) and ip_block.get("cidr") in {"0.0.0.0/0", "::/0"}:
                    return False
    return True


def _nmap_scan_valid(
    value: Any,
    *,
    requested_ports: str,
    expected_open_ports: list[int],
    expected_host: str | None = None,
) -> bool:
    if not isinstance(value, dict):
        return False
    addresses = value.get("addresses")
    states = value.get("port_states")
    elapsed = value.get("elapsed_seconds")
    raw_xml = value.get("raw_nmap_xml")
    xml_summary = _nmap_xml_summary(raw_xml)
    single_port_state_valid = True
    if requested_ports.isdigit():
        observed_state = states.get(requested_ports) if isinstance(states, dict) else None
        single_port_state_valid = isinstance(observed_state, str) and observed_state != "open"
    return (
        value.get("requested_ports") == requested_ports
        and value.get("host_state") == "up"
        and (expected_host is None or value.get("host") == expected_host)
        and isinstance(addresses, list)
        and bool(addresses)
        and all(_literal_ip(address) for address in addresses)
        and isinstance(states, dict)
        and value.get("open_tcp_ports") == expected_open_ports
        and all(str(port) in states and states[str(port)] == "open" for port in expected_open_ports)
        and (bool(expected_open_ports) or single_port_state_valid)
        and _meaningful_string(value.get("nmap_version"))
        and isinstance(elapsed, (int, float))
        and not isinstance(elapsed, bool)
        and elapsed >= 0
        and isinstance(raw_xml, str)
        and hashlib.sha256(raw_xml.encode("utf-8")).hexdigest()
        == value.get("xml_sha256")
        and bool(DIGEST_RE.fullmatch(str(value.get("xml_sha256", ""))))
        and isinstance(xml_summary, dict)
        and xml_summary["host_state"] == value.get("host_state")
        and xml_summary["addresses"] == addresses
        and xml_summary["port_states"] == states
        and xml_summary["nmap_version"] == value.get("nmap_version")
        and xml_summary["elapsed_seconds"] == elapsed
    )


def _release_digest(
    release: dict[str, Any],
    target: dict[str, Any],
    controls: dict[str, Any],
    approval_policy: dict[str, Any],
) -> str:
    evidence_digests = {
        key: value.get("evidence", {}).get("sha256") if isinstance(value, dict) else None
        for key, value in sorted(controls.items())
    }
    canonical = json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "release": release,
            "target": target,
            "evidence_digests": evidence_digests,
            "approval_policy": approval_policy,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _allowed_signer_bindings(path: Path) -> tuple[dict[str, set[str]] | None, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return None, str(exc)
    bindings: dict[str, set[str]] = {}
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 3:
            return None, f"line {line_number} is not an OpenSSH allowed-signers entry"
        key_index = next(
            (
                index
                for index, field in enumerate(fields[1:], start=1)
                if field.startswith(("ssh-", "ecdsa-", "sk-"))
            ),
            None,
        )
        if key_index is None or key_index + 1 >= len(fields):
            return None, f"line {line_number} does not contain an OpenSSH public key"
        key_material = f"{fields[key_index]} {fields[key_index + 1]}"
        for principal in fields[0].split(","):
            if (
                not _meaningful_string(principal)
                or any(character in principal for character in "*?!")
            ):
                return None, f"line {line_number} must use exact, non-placeholder principals"
            bindings.setdefault(principal, set()).add(key_material)
    if not bindings:
        return None, "no signer principals found"
    key_owners: dict[str, set[str]] = {}
    for principal, keys in bindings.items():
        for key in keys:
            key_owners.setdefault(key, set()).add(principal)
    shared_keys = {
        key: sorted(owners)
        for key, owners in key_owners.items()
        if len(owners) > 1
    }
    if shared_keys:
        return None, f"public keys reused across identities={list(shared_keys.values())}"
    return bindings, f"principals={sorted(bindings)}, unique_keys={len(key_owners)}"


def _verify_ssh_payload(
    *,
    identity: str,
    allowed_signers: Path,
    signature: Path,
    namespace: str,
    payload: bytes,
) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            [
                "ssh-keygen",
                "-Y",
                "verify",
                "-f",
                str(allowed_signers),
                "-I",
                identity,
                "-n",
                namespace,
                "-s",
                str(signature),
            ],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except OSError as exc:
        return False, str(exc)
    return completed.returncode == 0, completed.stdout.decode("utf-8", errors="replace").strip()


def _tls_policy_context(
    reference: Any,
    *,
    authorization_path: Path,
    probe: Any,
) -> tuple[bool, dict[str, Any], Path | None, set[str], str]:
    if not isinstance(reference, dict) or not isinstance(probe, dict):
        return False, {}, None, set(), "missing TLS policy reference or probe identity"
    policy_path = _resolve_file(reference.get("path"), authorization_path)
    trust_path_from_reference = _resolve_file(
        reference.get("allowed_signers_path"), authorization_path
    )
    policy: dict[str, Any] = {}
    if policy_path is not None and policy_path.is_file():
        try:
            loaded = json.loads(policy_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            loaded = None
        if isinstance(loaded, dict):
            policy = loaded
    expected_policy_keys = {
        "schema_version",
        "policy_id",
        "organization",
        "allowed_signers_path",
        "allowed_signers_sha256",
        "probe_operator_identities",
        "approved_probe_ids",
        "approved_vantage_ids",
        "approved_vantage_classes",
        "approved_source_cidrs",
    }
    expected_reference_keys = {
        "path",
        "sha256",
        "policy_id",
        "allowed_signers_path",
        "allowed_signers_sha256",
    }

    def exact_values(value: Any) -> set[str]:
        if not (
            isinstance(value, list)
            and bool(value)
            and all(_meaningful_string(item) for item in value)
            and len(value) == len(set(value))
        ):
            return set()
        return {str(item) for item in value}

    identities = exact_values(policy.get("probe_operator_identities"))
    probe_ids = exact_values(policy.get("approved_probe_ids"))
    vantage_ids = exact_values(policy.get("approved_vantage_ids"))
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    raw_networks = policy.get("approved_source_cidrs")
    if isinstance(raw_networks, list) and raw_networks and len(raw_networks) == len(set(raw_networks)):
        try:
            networks = [ipaddress.ip_network(str(item), strict=True) for item in raw_networks]
        except ValueError:
            networks = []
    networks_valid = bool(networks) and all(network.network_address.is_global for network in networks)
    try:
        source_address = ipaddress.ip_address(str(probe.get("source_ip", "")))
    except ValueError:
        source_address = None
    policy_digest = (
        _sha256(policy_path) if policy_path is not None and policy_path.is_file() else "missing"
    )
    policy_trust_path = (
        _resolve_file(policy.get("allowed_signers_path"), policy_path)
        if policy_path is not None
        else None
    )
    trust_digest = (
        _sha256(policy_trust_path)
        if policy_trust_path is not None and policy_trust_path.is_file()
        else "missing"
    )
    signer_bindings: dict[str, set[str]] | None = None
    signer_detail = "missing trust store"
    if policy_trust_path is not None and policy_trust_path.is_file():
        signer_bindings, signer_detail = _allowed_signer_bindings(policy_trust_path)
    valid = (
        set(reference) == expected_reference_keys
        and bool(DIGEST_RE.fullmatch(str(reference.get("sha256", ""))))
        and policy_digest == reference.get("sha256")
        and set(policy) == expected_policy_keys
        and policy.get("schema_version") == TLS_POLICY_SCHEMA_VERSION
        and _meaningful_string(policy.get("policy_id"))
        and policy.get("policy_id") == reference.get("policy_id")
        and _meaningful_string(policy.get("organization"))
        and bool(identities)
        and probe.get("probe_id") in probe_ids
        and probe.get("vantage_id") in vantage_ids
        and policy.get("approved_vantage_classes") == ["external-internet"]
        and probe.get("vantage_class") == "external-internet"
        and source_address is not None
        and source_address.is_global
        and networks_valid
        and any(source_address in network for network in networks)
        and policy_trust_path is not None
        and trust_path_from_reference == policy_trust_path
        and bool(DIGEST_RE.fullmatch(str(reference.get("allowed_signers_sha256", ""))))
        and trust_digest == reference.get("allowed_signers_sha256")
        and trust_digest == policy.get("allowed_signers_sha256")
        and signer_bindings is not None
        and set(signer_bindings) == identities
    )
    detail = (
        f"policy={policy_path or 'missing'}, digest={policy_digest}, "
        f"trust={policy_trust_path or 'missing'}, trust_digest={trust_digest}, {signer_detail}"
    )
    return valid, policy, policy_trust_path, identities, detail


def _network_policy_context(
    reference: Any,
    *,
    authorization_path: Path,
    probe: Any,
    policy_tests: Any,
) -> tuple[bool, Path | None, set[str], str]:
    if not (
        isinstance(reference, dict)
        and isinstance(probe, dict)
        and isinstance(policy_tests, dict)
    ):
        return False, None, set(), "missing network policy, probe, or cluster identity"
    policy_path = _resolve_file(reference.get("path"), authorization_path)
    trust_path_from_reference = _resolve_file(
        reference.get("allowed_signers_path"), authorization_path
    )
    policy: dict[str, Any] = {}
    if policy_path is not None and policy_path.is_file():
        try:
            loaded = json.loads(policy_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            loaded = None
        if isinstance(loaded, dict):
            policy = loaded
    expected_policy_keys = {
        "schema_version",
        "policy_id",
        "organization",
        "allowed_signers_path",
        "allowed_signers_sha256",
        "probe_operator_identities",
        "approved_probe_ids",
        "approved_vantage_ids",
        "approved_vantage_classes",
        "approved_source_cidrs",
        "approved_cluster_contexts",
        "approved_namespaces",
        "approved_cni_daemonsets",
    }
    expected_reference_keys = {
        "path",
        "sha256",
        "policy_id",
        "allowed_signers_path",
        "allowed_signers_sha256",
    }

    def exact_values(value: Any) -> set[str]:
        if not (
            isinstance(value, list)
            and value
            and all(_meaningful_string(item) for item in value)
            and len(value) == len(set(value))
        ):
            return set()
        return {str(item) for item in value}

    identities = exact_values(policy.get("probe_operator_identities"))
    probe_ids = exact_values(policy.get("approved_probe_ids"))
    vantage_ids = exact_values(policy.get("approved_vantage_ids"))
    contexts = exact_values(policy.get("approved_cluster_contexts"))
    namespaces = exact_values(policy.get("approved_namespaces"))
    cni_daemonsets = exact_values(policy.get("approved_cni_daemonsets"))
    raw_networks = policy.get("approved_source_cidrs")
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    if isinstance(raw_networks, list) and raw_networks and len(raw_networks) == len(set(raw_networks)):
        try:
            networks = [ipaddress.ip_network(str(item), strict=True) for item in raw_networks]
        except ValueError:
            networks = []
    networks_valid = bool(networks) and all(network.network_address.is_global for network in networks)
    try:
        source_address = ipaddress.ip_address(str(probe.get("source_ip", "")))
    except ValueError:
        source_address = None
    cni = policy_tests.get("cni") if isinstance(policy_tests.get("cni"), dict) else {}
    cni_identity = f"{cni.get('namespace')}/{cni.get('name')}"
    policy_digest = (
        _sha256(policy_path) if policy_path is not None and policy_path.is_file() else "missing"
    )
    policy_trust_path = (
        _resolve_file(policy.get("allowed_signers_path"), policy_path)
        if policy_path is not None
        else None
    )
    trust_digest = (
        _sha256(policy_trust_path)
        if policy_trust_path is not None and policy_trust_path.is_file()
        else "missing"
    )
    signer_bindings: dict[str, set[str]] | None = None
    signer_detail = "missing trust store"
    if policy_trust_path is not None and policy_trust_path.is_file():
        signer_bindings, signer_detail = _allowed_signer_bindings(policy_trust_path)
    valid = (
        set(reference) == expected_reference_keys
        and bool(DIGEST_RE.fullmatch(str(reference.get("sha256", ""))))
        and policy_digest == reference.get("sha256")
        and set(policy) == expected_policy_keys
        and policy.get("schema_version") == NETWORK_POLICY_SCHEMA_VERSION
        and _meaningful_string(policy.get("policy_id"))
        and policy.get("policy_id") == reference.get("policy_id")
        and _meaningful_string(policy.get("organization"))
        and bool(identities)
        and probe.get("probe_id") in probe_ids
        and probe.get("vantage_id") in vantage_ids
        and policy.get("approved_vantage_classes") == ["external-internet"]
        and probe.get("vantage_class") == "external-internet"
        and source_address is not None
        and source_address.is_global
        and networks_valid
        and any(source_address in network for network in networks)
        and policy_tests.get("cluster_context") in contexts
        and policy_tests.get("namespace") in namespaces
        and cni_identity in cni_daemonsets
        and policy_trust_path is not None
        and trust_path_from_reference == policy_trust_path
        and bool(DIGEST_RE.fullmatch(str(reference.get("allowed_signers_sha256", ""))))
        and trust_digest == reference.get("allowed_signers_sha256")
        and trust_digest == policy.get("allowed_signers_sha256")
        and signer_bindings is not None
        and set(signer_bindings) == identities
    )
    detail = (
        f"policy={policy_path or 'missing'}, digest={policy_digest}, "
        f"trust={policy_trust_path or 'missing'}, trust_digest={trust_digest}, {signer_detail}"
    )
    return valid, policy_trust_path, identities, detail


def _state_services_policy_context(
    reference: Any,
    *,
    authorization_path: Path,
) -> tuple[
    bool,
    Path | None,
    set[str],
    set[str],
    dict[str, set[str]],
    str,
]:
    if not isinstance(reference, dict):
        return False, None, set(), set(), {}, "missing state-services trust policy"
    policy_path = _resolve_file(reference.get("path"), authorization_path)
    trust_path_from_reference = _resolve_file(
        reference.get("allowed_signers_path"), authorization_path
    )
    policy: dict[str, Any] = {}
    if policy_path is not None and policy_path.is_file():
        try:
            loaded = json.loads(policy_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            loaded = None
        if isinstance(loaded, dict):
            policy = loaded
    expected_policy_keys = {
        "schema_version",
        "policy_id",
        "organization",
        "allowed_signers_path",
        "allowed_signers_sha256",
        "provider_identities",
        "verifier_identities",
        "required_services",
        "approved_providers",
    }
    expected_reference_keys = {
        "path",
        "sha256",
        "policy_id",
        "allowed_signers_path",
        "allowed_signers_sha256",
    }

    def exact_values(value: Any) -> set[str]:
        if not (
            isinstance(value, list)
            and value
            and all(_meaningful_string(item) for item in value)
            and len(value) == len(set(value))
        ):
            return set()
        return {str(item) for item in value}

    provider_identities = exact_values(policy.get("provider_identities"))
    verifier_identities = exact_values(policy.get("verifier_identities"))
    approved_providers: dict[str, set[str]] = {}
    raw_providers = policy.get("approved_providers")
    if isinstance(raw_providers, dict) and set(raw_providers) == set(STATE_REQUIRED_SERVICES):
        approved_providers = {
            name: exact_values(values) for name, values in raw_providers.items()
        }
    providers_valid = (
        set(approved_providers) == set(STATE_REQUIRED_SERVICES)
        and all(approved_providers.values())
    )
    policy_digest = (
        _sha256(policy_path) if policy_path is not None and policy_path.is_file() else "missing"
    )
    policy_trust_path = (
        _resolve_file(policy.get("allowed_signers_path"), policy_path)
        if policy_path is not None
        else None
    )
    trust_digest = (
        _sha256(policy_trust_path)
        if policy_trust_path is not None and policy_trust_path.is_file()
        else "missing"
    )
    signer_bindings: dict[str, set[str]] | None = None
    signer_detail = "missing trust store"
    if policy_trust_path is not None and policy_trust_path.is_file():
        signer_bindings, signer_detail = _allowed_signer_bindings(policy_trust_path)
    valid = (
        set(reference) == expected_reference_keys
        and bool(DIGEST_RE.fullmatch(str(reference.get("sha256", ""))))
        and policy_digest == reference.get("sha256")
        and set(policy) == expected_policy_keys
        and policy.get("schema_version") == STATE_POLICY_SCHEMA_VERSION
        and _meaningful_string(policy.get("policy_id"))
        and policy.get("policy_id") == reference.get("policy_id")
        and _meaningful_string(policy.get("organization"))
        and provider_identities
        and verifier_identities
        and not provider_identities.intersection(verifier_identities)
        and policy.get("required_services") == list(STATE_REQUIRED_SERVICES)
        and providers_valid
        and policy_trust_path is not None
        and trust_path_from_reference == policy_trust_path
        and bool(DIGEST_RE.fullmatch(str(reference.get("allowed_signers_sha256", ""))))
        and trust_digest == reference.get("allowed_signers_sha256")
        and trust_digest == policy.get("allowed_signers_sha256")
        and signer_bindings is not None
        and set(signer_bindings) == provider_identities.union(verifier_identities)
    )
    detail = (
        f"policy={policy_path or 'missing'}, digest={policy_digest}, "
        f"trust={policy_trust_path or 'missing'}, trust_digest={trust_digest}, {signer_detail}"
    )
    return (
        valid,
        policy_trust_path,
        provider_identities,
        verifier_identities,
        approved_providers,
        detail,
    )


def _release_provenance_policy_context(
    reference: Any,
    *,
    authorization_path: Path,
) -> tuple[
    bool,
    Path | None,
    set[str],
    set[str],
    set[str],
    set[str],
    dict[str, set[str]] | None,
    str,
]:
    if not isinstance(reference, dict):
        return False, None, set(), set(), set(), set(), None, "missing provenance policy"
    policy_path = _resolve_file(reference.get("path"), authorization_path)
    trust_path_from_reference = _resolve_file(
        reference.get("allowed_signers_path"), authorization_path
    )
    policy: dict[str, Any] = {}
    if policy_path is not None and policy_path.is_file():
        try:
            loaded = json.loads(policy_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            loaded = None
        if isinstance(loaded, dict):
            policy = loaded

    def exact_values(value: Any) -> set[str]:
        if not (
            isinstance(value, list)
            and value
            and all(_meaningful_string(item) for item in value)
            and len(value) == len(set(value))
        ):
            return set()
        return {str(item) for item in value}

    expected_policy_keys = {
        "schema_version",
        "policy_id",
        "organization",
        "allowed_signers_path",
        "allowed_signers_sha256",
        "builder_identities",
        "approved_source_repositories",
        "approved_builder_ids",
        "approved_workflow_refs",
        "required_artifacts",
    }
    expected_reference_keys = {
        "path",
        "sha256",
        "policy_id",
        "allowed_signers_path",
        "allowed_signers_sha256",
    }
    identities = exact_values(policy.get("builder_identities"))
    repositories = exact_values(policy.get("approved_source_repositories"))
    builder_ids = exact_values(policy.get("approved_builder_ids"))
    workflows = exact_values(policy.get("approved_workflow_refs"))
    policy_digest = (
        _sha256(policy_path) if policy_path is not None and policy_path.is_file() else "missing"
    )
    policy_trust_path = (
        _resolve_file(policy.get("allowed_signers_path"), policy_path)
        if policy_path is not None
        else None
    )
    trust_digest = (
        _sha256(policy_trust_path)
        if policy_trust_path is not None and policy_trust_path.is_file()
        else "missing"
    )
    bindings: dict[str, set[str]] | None = None
    signer_detail = "missing trust store"
    if policy_trust_path is not None and policy_trust_path.is_file():
        bindings, signer_detail = _allowed_signer_bindings(policy_trust_path)
    valid = (
        set(reference) == expected_reference_keys
        and bool(DIGEST_RE.fullmatch(str(reference.get("sha256", ""))))
        and policy_digest == reference.get("sha256")
        and set(policy) == expected_policy_keys
        and policy.get("schema_version") == RELEASE_PROVENANCE_POLICY_SCHEMA_VERSION
        and _meaningful_string(policy.get("policy_id"))
        and policy.get("policy_id") == reference.get("policy_id")
        and _meaningful_string(policy.get("organization"))
        and bool(identities)
        and bool(repositories)
        and bool(builder_ids)
        and bool(workflows)
        and policy.get("required_artifacts")
        == list(RELEASE_PROVENANCE_REQUIRED_ARTIFACTS)
        and policy_trust_path is not None
        and trust_path_from_reference == policy_trust_path
        and bool(DIGEST_RE.fullmatch(str(reference.get("allowed_signers_sha256", ""))))
        and trust_digest == reference.get("allowed_signers_sha256")
        and trust_digest == policy.get("allowed_signers_sha256")
        and bindings is not None
        and set(bindings) == identities
    )
    detail = (
        f"policy={policy_path or 'missing'}, digest={policy_digest}, "
        f"trust={policy_trust_path or 'missing'}, trust_digest={trust_digest}, {signer_detail}"
    )
    return (
        valid,
        policy_trust_path,
        identities,
        repositories,
        builder_ids,
        workflows,
        bindings,
        detail,
    )


def _approval_statement(approval: dict[str, Any], release_digest: str) -> bytes:
    return (
        json.dumps(
            {
                "schema_version": APPROVAL_STATEMENT_SCHEMA_VERSION,
                "authorization_schema_version": SCHEMA_VERSION,
                "release_digest": release_digest,
                "campaign_id": approval.get("campaign_id"),
                "campaign_freeze_sha256": approval.get("campaign_freeze_sha256"),
                "role": approval.get("role"),
                "identity": approval.get("identity"),
                "decision": approval.get("decision"),
                "approved_at": approval.get("approved_at"),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")


def _verify_ssh_signature(
    *,
    identity: str,
    allowed_signers: Path,
    signature: Path,
    approval: dict[str, Any],
    release_digest: str,
) -> tuple[bool, str]:
    return _verify_ssh_payload(
        identity=identity,
        allowed_signers=allowed_signers,
        signature=signature,
        namespace="duckdock-ga",
        payload=_approval_statement(approval, release_digest),
    )


def _verify_signed_evidence_file(
    signed_file: dict[str, Any],
    *,
    authorization_path: Path,
    namespace: str,
) -> tuple[bool, str]:
    path = _resolve_file(signed_file.get("path"), authorization_path)
    allowed_signers = _resolve_file(signed_file.get("allowed_signers_path"), authorization_path)
    signature = _resolve_file(signed_file.get("signature_path"), authorization_path)
    identity = signed_file.get("signer_identity")
    expected_digest = str(signed_file.get("sha256") or signed_file.get("manifest_sha256") or "")
    expected_trust_digest = str(signed_file.get("allowed_signers_sha256") or "")
    exists = path is not None and path.is_file()
    actual_digest = _sha256(path) if exists else "missing"
    actual_trust_digest = (
        _sha256(allowed_signers)
        if allowed_signers is not None and allowed_signers.is_file()
        else "missing"
    )
    digest_ok = bool(DIGEST_RE.fullmatch(expected_digest)) and actual_digest == expected_digest
    trust_digest_ok = (
        bool(DIGEST_RE.fullmatch(expected_trust_digest))
        and actual_trust_digest == expected_trust_digest
    )
    artifacts_ok = (
        _meaningful_string(identity)
        and allowed_signers is not None
        and allowed_signers.is_file()
        and trust_digest_ok
        and signature is not None
        and signature.is_file()
    )
    signature_ok = False
    signature_detail = "missing signer identity or signature artifacts"
    if exists and artifacts_ok:
        signature_ok, signature_detail = _verify_ssh_payload(
            identity=identity,
            allowed_signers=allowed_signers,
            signature=signature,
            namespace=namespace,
            payload=path.read_bytes(),
        )
    return (
        exists and digest_ok and artifacts_ok and signature_ok,
        f"path={path or 'missing'}, digest={actual_digest}, trust_digest={actual_trust_digest}, "
        f"signer={identity or 'missing'}, signature={signature_detail}",
    )


def _verify_policy_signed_evidence_file(
    signed_file: dict[str, Any],
    *,
    authorization_path: Path,
    namespace: str,
    allowed_signers: Path | None,
    allowed_identities: set[str],
    approval_policy_reference: dict[str, Any],
    trust_store_ok: bool,
) -> tuple[bool, str, dict[str, Any] | None]:
    path = _resolve_file(signed_file.get("path"), authorization_path)
    signature = _resolve_file(signed_file.get("signature_path"), authorization_path)
    identity = signed_file.get("signer_identity")
    expected_digest = str(signed_file.get("sha256", ""))
    exists = path is not None and path.is_file()
    actual_digest = _sha256(path) if exists else "missing"
    digest_ok = bool(DIGEST_RE.fullmatch(expected_digest)) and actual_digest == expected_digest
    policy_ok = (
        signed_file.get("approval_policy_id") == approval_policy_reference.get("policy_id")
        and signed_file.get("approval_policy_sha256") == approval_policy_reference.get("sha256")
    )
    signer_ok = _meaningful_string(identity) and identity in allowed_identities
    signature_ok = False
    signature_detail = "missing or unauthorized policy-bound signature artifacts"
    if (
        exists
        and digest_ok
        and policy_ok
        and signer_ok
        and trust_store_ok
        and allowed_signers is not None
        and signature is not None
        and signature.is_file()
    ):
        signature_ok, signature_detail = _verify_ssh_payload(
            identity=str(identity),
            allowed_signers=allowed_signers,
            signature=signature,
            namespace=namespace,
            payload=path.read_bytes(),
        )
    report: dict[str, Any] | None = None
    if exists:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            value = None
        report = value if isinstance(value, dict) else None
    return (
        exists and digest_ok and policy_ok and signer_ok and signature_ok and report is not None,
        (
            f"path={path or 'missing'}, digest={actual_digest}, signer={identity or 'missing'}, "
            f"policy={policy_ok}, signature={signature_detail}"
        ),
        report,
    )


def _alerting_policy_context(
    reference: Any,
    *,
    authorization_path: Path,
    schedule: Any,
) -> tuple[bool, dict[str, Any], Path | None, set[str], set[str], str]:
    if not isinstance(reference, dict):
        return False, {}, None, set(), set(), "missing alerting trust policy reference"
    policy_path = _resolve_file(reference.get("path"), authorization_path)
    trust_path_from_reference = _resolve_file(
        reference.get("allowed_signers_path"), authorization_path
    )
    policy: dict[str, Any] = {}
    if policy_path is not None and policy_path.is_file():
        try:
            loaded = json.loads(policy_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            loaded = None
        if isinstance(loaded, dict):
            policy = loaded
    expected_policy_keys = {
        "schema_version",
        "policy_id",
        "organization",
        "allowed_signers_path",
        "allowed_signers_sha256",
        "delivery_identities",
        "oncall_schedules",
    }
    expected_reference_keys = {
        "path",
        "sha256",
        "policy_id",
        "allowed_signers_path",
        "allowed_signers_sha256",
    }
    delivery_raw = policy.get("delivery_identities")
    schedules_raw = policy.get("oncall_schedules")
    schedules_valid = isinstance(schedules_raw, dict) and bool(schedules_raw) and all(
        _meaningful_string(name)
        and isinstance(identities, list)
        and bool(identities)
        and all(_meaningful_string(identity) for identity in identities)
        and len(identities) == len(set(identities))
        for name, identities in schedules_raw.items()
    )
    oncall_identities = (
        {str(identity) for identities in schedules_raw.values() for identity in identities}
        if schedules_valid
        else set()
    )
    delivery_identities = (
        {str(identity) for identity in delivery_raw}
        if isinstance(delivery_raw, list)
        and delivery_raw
        and all(_meaningful_string(identity) for identity in delivery_raw)
        and len(delivery_raw) == len(set(delivery_raw))
        else set()
    )
    selected_oncall = (
        {str(identity) for identity in schedules_raw.get(schedule, [])}
        if schedules_valid and isinstance(schedule, str) and schedule in schedules_raw
        else set()
    )
    policy_digest = _sha256(policy_path) if policy_path is not None and policy_path.is_file() else "missing"
    policy_trust_path = (
        _resolve_file(policy.get("allowed_signers_path"), policy_path)
        if policy_path is not None
        else None
    )
    trust_digest = (
        _sha256(policy_trust_path)
        if policy_trust_path is not None and policy_trust_path.is_file()
        else "missing"
    )
    signer_bindings: dict[str, set[str]] | None = None
    signer_detail = "missing trust store"
    if policy_trust_path is not None and policy_trust_path.is_file():
        signer_bindings, signer_detail = _allowed_signer_bindings(policy_trust_path)
    configured_identities = delivery_identities.union(oncall_identities)
    valid = (
        set(reference) == expected_reference_keys
        and bool(DIGEST_RE.fullmatch(str(reference.get("sha256", ""))))
        and policy_digest == reference.get("sha256")
        and set(policy) == expected_policy_keys
        and policy.get("schema_version") == ALERTING_POLICY_SCHEMA_VERSION
        and _meaningful_string(policy.get("policy_id"))
        and policy.get("policy_id") == reference.get("policy_id")
        and _meaningful_string(policy.get("organization"))
        and bool(delivery_identities)
        and schedules_valid
        and bool(selected_oncall)
        and not delivery_identities.intersection(oncall_identities)
        and policy_trust_path is not None
        and trust_path_from_reference == policy_trust_path
        and bool(DIGEST_RE.fullmatch(str(reference.get("allowed_signers_sha256", ""))))
        and trust_digest == reference.get("allowed_signers_sha256")
        and trust_digest == policy.get("allowed_signers_sha256")
        and signer_bindings is not None
        and set(signer_bindings) == configured_identities
    )
    detail = (
        f"policy={policy_path or 'missing'}, digest={policy_digest}, "
        f"trust={policy_trust_path or 'missing'}, trust_digest={trust_digest}, {signer_detail}"
    )
    return valid, policy, policy_trust_path, delivery_identities, selected_oncall, detail


def _verified_embedded_receipt(
    embedded: Any,
    *,
    authorization_path: Path,
    allowed_signers: Path | None,
    allowed_identities: set[str],
    namespace: str,
) -> tuple[bool, dict[str, Any] | None, str]:
    if not isinstance(embedded, dict):
        return False, None, "missing embedded signed receipt"
    signed_evidence = embedded.get("signed_evidence")
    if not isinstance(signed_evidence, dict) or set(signed_evidence) != {
        "path",
        "sha256",
        "signature_path",
        "signer_identity",
    }:
        return False, None, "missing signed receipt reference"
    path = _resolve_file(signed_evidence.get("path"), authorization_path)
    signature = _resolve_file(signed_evidence.get("signature_path"), authorization_path)
    identity = signed_evidence.get("signer_identity")
    expected_digest = str(signed_evidence.get("sha256", ""))
    payload: bytes | None = None
    if path is not None and path.is_file():
        try:
            payload = path.read_bytes()
        except OSError:
            payload = None
    exists = payload is not None
    actual_digest = hashlib.sha256(payload).hexdigest() if payload is not None else "missing"
    digest_ok = bool(DIGEST_RE.fullmatch(expected_digest)) and actual_digest == expected_digest
    signer_ok = _meaningful_string(identity) and identity in allowed_identities
    signature_ok = False
    signature_detail = "missing or unauthorized signature artifacts"
    if (
        payload is not None
        and digest_ok
        and signer_ok
        and allowed_signers is not None
        and allowed_signers.is_file()
        and signature is not None
        and signature.is_file()
    ):
        signature_ok, signature_detail = _verify_ssh_payload(
            identity=str(identity),
            allowed_signers=allowed_signers,
            signature=signature,
            namespace=namespace,
            payload=payload,
        )
    raw: dict[str, Any] | None = None
    if payload is not None:
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            value = None
        raw = value if isinstance(value, dict) else None
    embedded_raw = {key: value for key, value in embedded.items() if key != "signed_evidence"}
    projection_ok = raw is not None and raw == embedded_raw
    valid = exists and digest_ok and signer_ok and signature_ok and projection_ok
    return (
        valid,
        raw,
        (
            f"path={path or 'missing'}, digest={actual_digest}, signer={identity or 'missing'}, "
            f"projection={projection_ok}, signature={signature_detail}"
        ),
    )


def _secrets_policy_context(
    reference: Any,
    *,
    authorization_path: Path,
    provider: Any,
) -> tuple[bool, Path | None, set[str], set[str], str]:
    if not isinstance(reference, dict):
        return False, None, set(), set(), "missing secrets trust policy reference"
    policy_path = _resolve_file(reference.get("path"), authorization_path)
    trust_path_from_reference = _resolve_file(
        reference.get("allowed_signers_path"), authorization_path
    )
    policy: dict[str, Any] = {}
    if policy_path is not None and policy_path.is_file():
        try:
            loaded = json.loads(policy_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            loaded = None
        if isinstance(loaded, dict):
            policy = loaded
    expected_policy_keys = {
        "schema_version",
        "policy_id",
        "organization",
        "allowed_signers_path",
        "allowed_signers_sha256",
        "approved_providers",
        "provider_identities",
        "verifier_identities",
        "required_secret_classes",
        "workload_components",
    }
    expected_reference_keys = {
        "path",
        "sha256",
        "policy_id",
        "allowed_signers_path",
        "allowed_signers_sha256",
    }

    def exact_identity_set(value: Any) -> set[str]:
        if not (
            isinstance(value, list)
            and bool(value)
            and all(_meaningful_string(item) for item in value)
            and len(value) == len(set(value))
        ):
            return set()
        return {str(item) for item in value}

    provider_identities = exact_identity_set(policy.get("provider_identities"))
    verifier_identities = exact_identity_set(policy.get("verifier_identities"))
    providers = policy.get("approved_providers")
    providers_valid = (
        isinstance(providers, list)
        and bool(providers)
        and all(_meaningful_string(item) for item in providers)
        and len(providers) == len(set(providers))
        and provider in providers
    )
    policy_digest = (
        _sha256(policy_path) if policy_path is not None and policy_path.is_file() else "missing"
    )
    policy_trust_path = (
        _resolve_file(policy.get("allowed_signers_path"), policy_path)
        if policy_path is not None
        else None
    )
    trust_digest = (
        _sha256(policy_trust_path)
        if policy_trust_path is not None and policy_trust_path.is_file()
        else "missing"
    )
    signer_bindings: dict[str, set[str]] | None = None
    signer_detail = "missing trust store"
    if policy_trust_path is not None and policy_trust_path.is_file():
        signer_bindings, signer_detail = _allowed_signer_bindings(policy_trust_path)
    configured_identities = provider_identities.union(verifier_identities)
    valid = (
        set(reference) == expected_reference_keys
        and bool(DIGEST_RE.fullmatch(str(reference.get("sha256", ""))))
        and policy_digest == reference.get("sha256")
        and set(policy) == expected_policy_keys
        and policy.get("schema_version") == SECRETS_POLICY_SCHEMA_VERSION
        and _meaningful_string(policy.get("policy_id"))
        and policy.get("policy_id") == reference.get("policy_id")
        and _meaningful_string(policy.get("organization"))
        and providers_valid
        and bool(provider_identities)
        and bool(verifier_identities)
        and not provider_identities.intersection(verifier_identities)
        and policy.get("required_secret_classes") == list(REQUIRED_SECRET_CLASSES)
        and policy.get("workload_components") == list(SECRET_WORKLOAD_COMPONENTS)
        and policy_trust_path is not None
        and trust_path_from_reference == policy_trust_path
        and bool(DIGEST_RE.fullmatch(str(reference.get("allowed_signers_sha256", ""))))
        and trust_digest == reference.get("allowed_signers_sha256")
        and trust_digest == policy.get("allowed_signers_sha256")
        and signer_bindings is not None
        and set(signer_bindings) == configured_identities
    )
    detail = (
        f"policy={policy_path or 'missing'}, digest={policy_digest}, "
        f"trust={policy_trust_path or 'missing'}, trust_digest={trust_digest}, {signer_detail}"
    )
    return (
        valid,
        policy_trust_path,
        provider_identities,
        verifier_identities,
        detail,
    )


def _secret_class_rows_valid(value: Any, *, verification: bool) -> bool:
    if not isinstance(value, list) or len(value) != len(REQUIRED_SECRET_CLASSES):
        return False
    if [item.get("name") for item in value if isinstance(item, dict)] != list(
        REQUIRED_SECRET_CLASSES
    ):
        return False
    receipt_ids: list[str] = []
    audit_event_ids: list[str] = []
    for item in value:
        if not isinstance(item, dict) or _contains_secret_material_key(item):
            return False
        if verification:
            if set(item) != {
                "name",
                "probe_receipt_id",
                "tested_at",
                "previous_version_rejected",
                "new_version_accepted",
                "audit_event_id",
            }:
                return False
            receipt_id = item.get("probe_receipt_id")
            audit_event_id = item.get("audit_event_id")
            if not (
                _meaningful_string(receipt_id)
                and _meaningful_string(audit_event_id)
                and _parse_time(item.get("tested_at")) is not None
                and item.get("previous_version_rejected") is True
                and item.get("new_version_accepted") is True
            ):
                return False
        else:
            if set(item) != {
                "name",
                "previous_version_id",
                "new_version_id",
                "provider_receipt_id",
                "audit_event_id",
                "rotated_at",
                "old_version_disabled_at",
            }:
                return False
            receipt_id = item.get("provider_receipt_id")
            audit_event_id = item.get("audit_event_id")
            previous = item.get("previous_version_id")
            current = item.get("new_version_id")
            rotated = _parse_time(item.get("rotated_at"))
            disabled = _parse_time(item.get("old_version_disabled_at"))
            if not (
                _meaningful_string(receipt_id)
                and _meaningful_string(previous)
                and _meaningful_string(current)
                and previous != current
                and _meaningful_string(audit_event_id)
                and rotated is not None
                and disabled is not None
                and rotated <= disabled
            ):
                return False
        receipt_ids.append(str(receipt_id))
        audit_event_ids.append(str(audit_event_id))
    return len(receipt_ids) == len(set(receipt_ids)) and len(audit_event_ids) == len(
        set(audit_event_ids)
    )


def _secret_rotation_receipt_valid(
    value: Any,
    *,
    exercise_id: Any,
    target_environment: Any,
    provider: Any,
    secret_name: Any,
) -> bool:
    expected_keys = {
        "schema_version",
        "exercise_id",
        "target_environment",
        "provider",
        "secret_name",
        "started_at",
        "completed_at",
        "encrypted_at_rest",
        "access_audit_enabled",
        "credentials_external_to_evidence",
        "secret_classes",
    }
    if not isinstance(value, dict):
        return False
    started = _parse_time(value.get("started_at"))
    completed = _parse_time(value.get("completed_at"))
    rows = value.get("secret_classes")
    row_times = [
        parsed
        for item in rows or []
        if isinstance(item, dict)
        for parsed in (
            _parse_time(item.get("rotated_at")),
            _parse_time(item.get("old_version_disabled_at")),
        )
    ]
    return (
        set(value) == expected_keys
        and value.get("schema_version") == SECRET_ROTATION_SCHEMA_VERSION
        and value.get("exercise_id") == exercise_id
        and value.get("target_environment") == target_environment
        and value.get("provider") == provider
        and value.get("secret_name") == secret_name
        and value.get("encrypted_at_rest") is True
        and value.get("access_audit_enabled") is True
        and value.get("credentials_external_to_evidence") is True
        and started is not None
        and completed is not None
        and started <= completed
        and _secret_class_rows_valid(rows, verification=False)
        and len(row_times) == len(REQUIRED_SECRET_CLASSES) * 2
        and all(parsed is not None and started <= parsed <= completed for parsed in row_times)
        and not _contains_secret_material_key(value)
    )


def _secret_verification_receipt_valid(
    value: Any,
    *,
    exercise_id: Any,
    target_environment: Any,
    secret_name: Any,
) -> bool:
    expected_keys = {
        "schema_version",
        "exercise_id",
        "target_environment",
        "secret_name",
        "started_at",
        "completed_at",
        "secret_classes",
        "all_passed",
    }
    if not isinstance(value, dict):
        return False
    started = _parse_time(value.get("started_at"))
    completed = _parse_time(value.get("completed_at"))
    rows = value.get("secret_classes")
    tested = [
        _parse_time(item.get("tested_at"))
        for item in rows or []
        if isinstance(item, dict)
    ]
    return (
        set(value) == expected_keys
        and value.get("schema_version") == SECRET_VERIFICATION_SCHEMA_VERSION
        and value.get("exercise_id") == exercise_id
        and value.get("target_environment") == target_environment
        and value.get("secret_name") == secret_name
        and value.get("all_passed") is True
        and started is not None
        and completed is not None
        and started <= completed
        and _secret_class_rows_valid(rows, verification=True)
        and len(tested) == len(REQUIRED_SECRET_CLASSES)
        and all(parsed is not None and started <= parsed <= completed for parsed in tested)
        and not _contains_secret_material_key(value)
    )


def _secret_deployment_valid(value: Any, *, name: str) -> bool:
    expected_keys = {
        "name",
        "uid",
        "resource_version",
        "generation",
        "desired_replicas",
        "observed_generation",
        "updated_replicas",
        "ready_replicas",
        "available_replicas",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        return False
    desired = value.get("desired_replicas")
    generation = value.get("generation")
    return (
        value.get("name") == name
        and _meaningful_string(value.get("uid"))
        and _meaningful_string(value.get("resource_version"))
        and isinstance(generation, int)
        and not isinstance(generation, bool)
        and generation >= 1
        and isinstance(desired, int)
        and not isinstance(desired, bool)
        and desired >= 1
        and value.get("observed_generation") == generation
        and value.get("updated_replicas") == desired
        and value.get("ready_replicas") == desired
        and value.get("available_replicas") == desired
    )


def _secret_pods_valid(value: Any, *, desired: int) -> bool:
    expected_keys = {
        "name",
        "uid",
        "resource_version",
        "creation_timestamp",
        "deletion_timestamp",
        "phase",
        "ready",
    }
    return (
        isinstance(value, list)
        and len(value) == desired
        and all(
            isinstance(item, dict)
            and set(item) == expected_keys
            and _meaningful_string(item.get("name"))
            and _meaningful_string(item.get("uid"))
            and _meaningful_string(item.get("resource_version"))
            and _parse_time(item.get("creation_timestamp")) is not None
            and item.get("deletion_timestamp") is None
            and item.get("phase") == "Running"
            and item.get("ready") is True
            for item in value
        )
        and len({item["name"] for item in value}) == len(value)
        and len({item["uid"] for item in value}) == len(value)
    )


def _secret_kubernetes_snapshot_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {"captured_at", "secret", "workloads"}:
        return False
    secret = value.get("secret")
    workloads = value.get("workloads")
    if not (
        _parse_time(value.get("captured_at")) is not None
        and isinstance(secret, dict)
        and set(secret) == {"name", "uid", "resource_version", "creation_timestamp"}
        and _meaningful_string(secret.get("name"))
        and _meaningful_string(secret.get("uid"))
        and _meaningful_string(secret.get("resource_version"))
        and _parse_time(secret.get("creation_timestamp")) is not None
        and isinstance(workloads, dict)
        and set(workloads) == set(SECRET_WORKLOAD_COMPONENTS)
    ):
        return False
    for component in SECRET_WORKLOAD_COMPONENTS:
        workload = workloads[component]
        if not isinstance(workload, dict) or set(workload) != {"deployment", "pods"}:
            return False
        deployment = workload.get("deployment")
        if not _secret_deployment_valid(deployment, name=component):
            return False
        if not _secret_pods_valid(
            workload.get("pods"), desired=deployment["desired_replicas"]
        ):
            return False
    return True


def _secret_kubernetes_rotation_valid(value: Any, *, secret_name: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "metadata_only",
        "secret_data_read",
        "before",
        "after",
        "verification",
    }:
        return False
    before = value.get("before")
    after = value.get("after")
    verification = value.get("verification")
    if not (
        value.get("metadata_only") is True
        and value.get("secret_data_read") is False
        and _secret_kubernetes_snapshot_valid(before)
        and _secret_kubernetes_snapshot_valid(after)
        and isinstance(verification, dict)
        and set(verification)
        == {"secret_uid_unchanged", "secret_resource_version_changed", "components"}
        and _parse_time(before["captured_at"]) <= _parse_time(after["captured_at"])
    ):
        return False
    before_secret = before["secret"]
    after_secret = after["secret"]
    secret_uid_unchanged = (
        before_secret["name"] == secret_name
        and after_secret["name"] == secret_name
        and before_secret["uid"] == after_secret["uid"]
        and before_secret["creation_timestamp"] == after_secret["creation_timestamp"]
    )
    resource_version_changed = (
        before_secret["resource_version"] != after_secret["resource_version"]
    )
    components = verification.get("components")
    if not isinstance(components, dict) or set(components) != set(SECRET_WORKLOAD_COMPONENTS):
        return False
    for component in SECRET_WORKLOAD_COMPONENTS:
        old_workload = before["workloads"][component]
        new_workload = after["workloads"][component]
        old_deployment = old_workload["deployment"]
        new_deployment = new_workload["deployment"]
        old_uids = {item["uid"] for item in old_workload["pods"]}
        new_uids = {item["uid"] for item in new_workload["pods"]}
        expected = {
            "deployment_uid_unchanged": old_deployment["uid"] == new_deployment["uid"],
            "deployment_generation_advanced": new_deployment["generation"]
            > old_deployment["generation"],
            "all_old_pods_replaced": bool(old_uids)
            and bool(new_uids)
            and old_uids.isdisjoint(new_uids),
            "fully_ready": True,
        }
        if (
            not isinstance(components[component], dict)
            or components[component] != expected
            or new_deployment["resource_version"] == old_deployment["resource_version"]
            or not all(expected.values())
        ):
            return False
    return (
        verification.get("secret_uid_unchanged") is secret_uid_unchanged
        and verification.get("secret_resource_version_changed")
        is resource_version_changed
        and secret_uid_unchanged
        and resource_version_changed
    )


def _safe_offsite_uri(value: Any) -> bool:
    if not _meaningful_string(value):
        return False
    parsed = urlparse(value)
    return (
        parsed.scheme == "s3"
        and bool(parsed.netloc)
        and bool(parsed.path.strip("/"))
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and "@" not in value
    )


def _recovery_policy_context(
    reference: Any,
    *,
    authorization_path: Path,
    storage_provider: Any,
) -> tuple[bool, Path | None, set[str], set[str], set[str], str]:
    if not isinstance(reference, dict):
        return False, None, set(), set(), set(), "missing recovery trust policy reference"
    policy_path = _resolve_file(reference.get("path"), authorization_path)
    trust_path_from_reference = _resolve_file(
        reference.get("allowed_signers_path"), authorization_path
    )
    policy: dict[str, Any] = {}
    if policy_path is not None and policy_path.is_file():
        try:
            loaded = json.loads(policy_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            loaded = None
        if isinstance(loaded, dict):
            policy = loaded
    expected_policy_keys = {
        "schema_version",
        "policy_id",
        "organization",
        "allowed_signers_path",
        "allowed_signers_sha256",
        "approved_storage_providers",
        "storage_identities",
        "restore_executor_identities",
        "verification_identities",
        "required_artifacts",
        "required_restore_stages",
        "minimum_retention_days",
    }
    expected_reference_keys = {
        "path",
        "sha256",
        "policy_id",
        "allowed_signers_path",
        "allowed_signers_sha256",
    }

    def exact_identity_set(value: Any) -> set[str]:
        if not (
            isinstance(value, list)
            and bool(value)
            and all(_meaningful_string(item) for item in value)
            and len(value) == len(set(value))
        ):
            return set()
        return {str(item) for item in value}

    storage_identities = exact_identity_set(policy.get("storage_identities"))
    restore_identities = exact_identity_set(policy.get("restore_executor_identities"))
    verification_identities = exact_identity_set(policy.get("verification_identities"))
    identity_sets = (storage_identities, restore_identities, verification_identities)
    identities_disjoint = all(
        not identity_sets[left].intersection(identity_sets[right])
        for left in range(len(identity_sets))
        for right in range(left + 1, len(identity_sets))
    )
    providers = policy.get("approved_storage_providers")
    providers_valid = (
        isinstance(providers, list)
        and bool(providers)
        and all(_meaningful_string(item) for item in providers)
        and len(providers) == len(set(providers))
        and storage_provider in providers
    )
    policy_digest = (
        _sha256(policy_path) if policy_path is not None and policy_path.is_file() else "missing"
    )
    policy_trust_path = (
        _resolve_file(policy.get("allowed_signers_path"), policy_path)
        if policy_path is not None
        else None
    )
    trust_digest = (
        _sha256(policy_trust_path)
        if policy_trust_path is not None and policy_trust_path.is_file()
        else "missing"
    )
    signer_bindings: dict[str, set[str]] | None = None
    signer_detail = "missing trust store"
    if policy_trust_path is not None and policy_trust_path.is_file():
        signer_bindings, signer_detail = _allowed_signer_bindings(policy_trust_path)
    configured_identities = set().union(*identity_sets)
    valid = (
        set(reference) == expected_reference_keys
        and bool(DIGEST_RE.fullmatch(str(reference.get("sha256", ""))))
        and policy_digest == reference.get("sha256")
        and set(policy) == expected_policy_keys
        and policy.get("schema_version") == RECOVERY_POLICY_SCHEMA_VERSION
        and _meaningful_string(policy.get("policy_id"))
        and policy.get("policy_id") == reference.get("policy_id")
        and _meaningful_string(policy.get("organization"))
        and providers_valid
        and all(identity_sets)
        and identities_disjoint
        and policy.get("required_artifacts") == list(RECOVERY_REQUIRED_ARTIFACTS)
        and policy.get("required_restore_stages") == list(RECOVERY_REQUIRED_STAGES)
        and policy.get("minimum_retention_days") == RECOVERY_MINIMUM_RETENTION.days
        and policy_trust_path is not None
        and trust_path_from_reference == policy_trust_path
        and bool(DIGEST_RE.fullmatch(str(reference.get("allowed_signers_sha256", ""))))
        and trust_digest == reference.get("allowed_signers_sha256")
        and trust_digest == policy.get("allowed_signers_sha256")
        and signer_bindings is not None
        and set(signer_bindings) == configured_identities
    )
    detail = (
        f"policy={policy_path or 'missing'}, digest={policy_digest}, "
        f"trust={policy_trust_path or 'missing'}, trust_digest={trust_digest}, {signer_detail}"
    )
    return (
        valid,
        policy_trust_path,
        storage_identities,
        restore_identities,
        verification_identities,
        detail,
    )


def _capacity_policy_context(
    reference: Any,
    *,
    authorization_path: Path,
    database_provider: Any,
) -> tuple[
    bool,
    dict[str, Any],
    Path | None,
    set[str],
    set[str],
    set[str],
    str,
]:
    if not isinstance(reference, dict):
        return False, {}, None, set(), set(), set(), "missing capacity policy reference"
    policy_path = _resolve_file(reference.get("path"), authorization_path)
    trust_path_from_reference = _resolve_file(
        reference.get("allowed_signers_path"), authorization_path
    )
    policy: dict[str, Any] = {}
    if policy_path is not None and policy_path.is_file():
        try:
            loaded = json.loads(policy_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            loaded = None
        if isinstance(loaded, dict):
            policy = loaded
    expected_policy_keys = {
        "schema_version",
        "policy_id",
        "organization",
        "allowed_signers_path",
        "allowed_signers_sha256",
        "approved_database_providers",
        "load_executor_identities",
        "storage_observer_identities",
        "cleanup_verifier_identities",
        "required_counters",
        "minimum_database_growth_bytes",
        "maximum_pending_outbox_events",
        "maximum_replica_lag_seconds",
    }
    expected_reference_keys = {
        "path",
        "sha256",
        "policy_id",
        "allowed_signers_path",
        "allowed_signers_sha256",
    }

    def exact_identity_set(value: Any) -> set[str]:
        if not (
            isinstance(value, list)
            and bool(value)
            and all(_meaningful_string(item) for item in value)
            and len(value) == len(set(value))
        ):
            return set()
        return {str(item) for item in value}

    load_identities = exact_identity_set(policy.get("load_executor_identities"))
    storage_identities = exact_identity_set(policy.get("storage_observer_identities"))
    cleanup_identities = exact_identity_set(policy.get("cleanup_verifier_identities"))
    identity_sets = (load_identities, storage_identities, cleanup_identities)
    identities_disjoint = all(
        not identity_sets[left].intersection(identity_sets[right])
        for left in range(len(identity_sets))
        for right in range(left + 1, len(identity_sets))
    )
    providers = policy.get("approved_database_providers")
    providers_valid = (
        isinstance(providers, list)
        and bool(providers)
        and all(_meaningful_string(provider) for provider in providers)
        and len(providers) == len(set(providers))
        and database_provider in providers
    )
    thresholds_valid = (
        isinstance(policy.get("minimum_database_growth_bytes"), int)
        and not isinstance(policy.get("minimum_database_growth_bytes"), bool)
        and policy["minimum_database_growth_bytes"] > 0
        and isinstance(policy.get("maximum_pending_outbox_events"), int)
        and not isinstance(policy.get("maximum_pending_outbox_events"), bool)
        and policy["maximum_pending_outbox_events"] >= 0
        and isinstance(policy.get("maximum_replica_lag_seconds"), (int, float))
        and not isinstance(policy.get("maximum_replica_lag_seconds"), bool)
        and math.isfinite(float(policy["maximum_replica_lag_seconds"]))
        and float(policy["maximum_replica_lag_seconds"]) >= 0
    )
    policy_digest = (
        _sha256(policy_path) if policy_path is not None and policy_path.is_file() else "missing"
    )
    policy_trust_path = (
        _resolve_file(policy.get("allowed_signers_path"), policy_path)
        if policy_path is not None
        else None
    )
    trust_digest = (
        _sha256(policy_trust_path)
        if policy_trust_path is not None and policy_trust_path.is_file()
        else "missing"
    )
    signer_bindings: dict[str, set[str]] | None = None
    signer_detail = "missing trust store"
    if policy_trust_path is not None and policy_trust_path.is_file():
        signer_bindings, signer_detail = _allowed_signer_bindings(policy_trust_path)
    configured_identities = set().union(*identity_sets)
    valid = (
        set(reference) == expected_reference_keys
        and bool(DIGEST_RE.fullmatch(str(reference.get("sha256", ""))))
        and policy_digest == reference.get("sha256")
        and set(policy) == expected_policy_keys
        and policy.get("schema_version") == CAPACITY_POLICY_SCHEMA_VERSION
        and _meaningful_string(policy.get("policy_id"))
        and policy.get("policy_id") == reference.get("policy_id")
        and _meaningful_string(policy.get("organization"))
        and providers_valid
        and all(identity_sets)
        and identities_disjoint
        and policy.get("required_counters") == list(CAPACITY_REQUIRED_COUNTERS)
        and thresholds_valid
        and policy_trust_path is not None
        and trust_path_from_reference == policy_trust_path
        and bool(DIGEST_RE.fullmatch(str(reference.get("allowed_signers_sha256", ""))))
        and trust_digest == reference.get("allowed_signers_sha256")
        and trust_digest == policy.get("allowed_signers_sha256")
        and signer_bindings is not None
        and set(signer_bindings) == configured_identities
    )
    detail = (
        f"policy={policy_path or 'missing'}, digest={policy_digest}, "
        f"trust={policy_trust_path or 'missing'}, trust_digest={trust_digest}, {signer_detail}"
    )
    return (
        valid,
        policy,
        policy_trust_path,
        load_identities,
        storage_identities,
        cleanup_identities,
        detail,
    )


def _backup_media_receipt_valid(
    value: Any,
    *,
    exercise_id: Any,
    target_environment: Any,
    storage_provider: Any,
    manifest_sha256: Any,
) -> bool:
    expected_keys = {
        "schema_version",
        "exercise_id",
        "target_environment",
        "storage_provider",
        "backup_manifest_sha256",
        "backup_set_id",
        "remote_uri",
        "recovery_point_at",
        "artifact_versions",
        "object_lock",
        "completed_at",
    }
    if not isinstance(value, dict):
        return False
    completed = _parse_time(value.get("completed_at"))
    recovery_point = _parse_time(value.get("recovery_point_at"))
    artifacts = value.get("artifact_versions")
    artifact_names = [
        item.get("name") for item in artifacts or [] if isinstance(item, dict)
    ]
    version_ids = [
        item.get("version_id") for item in artifacts or [] if isinstance(item, dict)
    ]
    artifact_retention = [
        _parse_time(item.get("retained_until"))
        for item in artifacts or []
        if isinstance(item, dict)
    ]
    lock = value.get("object_lock")
    lock_observed = _parse_time(lock.get("observed_at")) if isinstance(lock, dict) else None
    retention_until = (
        _parse_time(lock.get("retention_until")) if isinstance(lock, dict) else None
    )
    return (
        set(value) == expected_keys
        and value.get("schema_version") == BACKUP_MEDIA_SCHEMA_VERSION
        and value.get("exercise_id") == exercise_id
        and value.get("target_environment") == target_environment
        and value.get("storage_provider") == storage_provider
        and value.get("backup_manifest_sha256") == manifest_sha256
        and _meaningful_string(value.get("backup_set_id"))
        and _safe_offsite_uri(value.get("remote_uri"))
        and recovery_point is not None
        and completed is not None
        and recovery_point <= completed
        and isinstance(artifacts, list)
        and len(artifacts) == len(RECOVERY_REQUIRED_ARTIFACTS)
        and artifact_names == list(RECOVERY_REQUIRED_ARTIFACTS)
        and len(version_ids) == len(set(version_ids))
        and all(
            isinstance(item, dict)
            and set(item)
            == {"name", "version_id", "etag", "sha256", "size_bytes", "retained_until"}
            and _meaningful_string(item.get("version_id"))
            and _meaningful_string(item.get("etag"))
            and bool(DIGEST_RE.fullmatch(str(item.get("sha256", ""))))
            and isinstance(item.get("size_bytes"), int)
            and not isinstance(item.get("size_bytes"), bool)
            and item["size_bytes"] > 0
            for item in artifacts
        )
        and len(artifact_retention) == len(RECOVERY_REQUIRED_ARTIFACTS)
        and all(item is not None for item in artifact_retention)
        and isinstance(lock, dict)
        and set(lock)
        == {"enabled", "mode", "retention_until", "provider_receipt_id", "observed_at"}
        and lock.get("enabled") is True
        and lock.get("mode") in {"COMPLIANCE", "GOVERNANCE"}
        and _meaningful_string(lock.get("provider_receipt_id"))
        and lock_observed is not None
        and retention_until is not None
        and lock_observed <= completed
        and retention_until >= completed + RECOVERY_MINIMUM_RETENTION
        and all(item >= retention_until for item in artifact_retention if item is not None)
        and not _contains_secret_material_key(value)
    )


def _restore_execution_receipt_valid(
    value: Any,
    *,
    exercise_id: Any,
    production_target_environment: Any,
    restore_target_environment: Any,
    manifest_sha256: Any,
) -> bool:
    expected_keys = {
        "schema_version",
        "exercise_id",
        "production_target_environment",
        "restore_target_environment",
        "environment_class",
        "backup_manifest_sha256",
        "restore_receipt_id",
        "destructive_restore",
        "production_data_overwrite",
        "failure_injected_at",
        "started_at",
        "completed_at",
        "stages",
    }
    if not isinstance(value, dict):
        return False
    failure_at = _parse_time(value.get("failure_injected_at"))
    started = _parse_time(value.get("started_at"))
    completed = _parse_time(value.get("completed_at"))
    stages = value.get("stages")
    stage_names = [item.get("name") for item in stages or [] if isinstance(item, dict)]
    stage_times = [
        _parse_time(item.get("completed_at"))
        for item in stages or []
        if isinstance(item, dict)
    ]
    command_ids = [
        item.get("command_id") for item in stages or [] if isinstance(item, dict)
    ]
    return (
        set(value) == expected_keys
        and value.get("schema_version") == RESTORE_EXECUTION_SCHEMA_VERSION
        and value.get("exercise_id") == exercise_id
        and value.get("production_target_environment") == production_target_environment
        and value.get("restore_target_environment") == restore_target_environment
        and restore_target_environment != production_target_environment
        and value.get("environment_class") in RECOVERY_ENVIRONMENT_CLASSES
        and value.get("backup_manifest_sha256") == manifest_sha256
        and _meaningful_string(value.get("restore_receipt_id"))
        and value.get("destructive_restore") is True
        and value.get("production_data_overwrite") is False
        and failure_at is not None
        and started is not None
        and completed is not None
        and failure_at <= started <= completed
        and isinstance(stages, list)
        and len(stages) == len(RECOVERY_REQUIRED_STAGES)
        and stage_names == list(RECOVERY_REQUIRED_STAGES)
        and len(command_ids) == len(set(command_ids))
        and all(
            isinstance(item, dict)
            and set(item) == {"name", "command_id", "exit_code", "log_sha256", "completed_at"}
            and _meaningful_string(item.get("command_id"))
            and item.get("exit_code") == 0
            and bool(DIGEST_RE.fullmatch(str(item.get("log_sha256", ""))))
            for item in stages
        )
        and len(stage_times) == len(RECOVERY_REQUIRED_STAGES)
        and all(item is not None and started <= item <= completed for item in stage_times)
        and stage_times == sorted(stage_times)
        and not _contains_secret_material_key(value)
    )


def _recovery_verification_receipt_valid(
    value: Any,
    *,
    exercise_id: Any,
    restore_target_environment: Any,
    manifest_sha256: Any,
) -> bool:
    expected_keys = {
        "schema_version",
        "exercise_id",
        "restore_target_environment",
        "environment_class",
        "backup_manifest_sha256",
        "verification_receipt_id",
        "started_at",
        "completed_at",
        "mysql",
        "object_store",
        "git",
        "service_readiness",
        "passed",
    }
    if not isinstance(value, dict):
        return False
    started = _parse_time(value.get("started_at"))
    completed = _parse_time(value.get("completed_at"))
    mysql = value.get("mysql")
    object_store = value.get("object_store")
    git = value.get("git")
    readiness = value.get("service_readiness")
    return (
        set(value) == expected_keys
        and value.get("schema_version") == RECOVERY_VERIFICATION_SCHEMA_VERSION
        and value.get("exercise_id") == exercise_id
        and value.get("restore_target_environment") == restore_target_environment
        and value.get("environment_class") in RECOVERY_ENVIRONMENT_CLASSES
        and value.get("backup_manifest_sha256") == manifest_sha256
        and _meaningful_string(value.get("verification_receipt_id"))
        and started is not None
        and completed is not None
        and started <= completed
        and isinstance(mysql, dict)
        and set(mysql) == {"rows_verified", "dataset_sha256"}
        and isinstance(mysql.get("rows_verified"), int)
        and not isinstance(mysql.get("rows_verified"), bool)
        and mysql["rows_verified"] > 0
        and bool(DIGEST_RE.fullmatch(str(mysql.get("dataset_sha256", ""))))
        and isinstance(object_store, dict)
        and set(object_store) == {"objects_verified", "inventory_sha256"}
        and isinstance(object_store.get("objects_verified"), int)
        and not isinstance(object_store.get("objects_verified"), bool)
        and object_store["objects_verified"] > 0
        and bool(DIGEST_RE.fullmatch(str(object_store.get("inventory_sha256", ""))))
        and isinstance(git, dict)
        and set(git) == {"repositories_verified", "refs_sha256"}
        and isinstance(git.get("repositories_verified"), int)
        and not isinstance(git.get("repositories_verified"), bool)
        and git["repositories_verified"] > 0
        and bool(DIGEST_RE.fullmatch(str(git.get("refs_sha256", ""))))
        and isinstance(readiness, dict)
        and set(readiness) == {"https_probe_passed", "background_worker_ready"}
        and readiness.get("https_probe_passed") is True
        and readiness.get("background_worker_ready") is True
        and value.get("passed") is True
        and not _contains_secret_material_key(value)
    )


def _alertmanager_api_observation(
    value: Any,
    *,
    active: bool | None = None,
    alert_name: Any = None,
) -> bool:
    if not isinstance(value, dict):
        return False
    status = value.get("http_status")
    response_bytes = value.get("response_bytes")
    raw_response = value.get("raw_response_body")
    raw_request = value.get("raw_request_body")
    base_valid = (
        _parse_time(value.get("observed_at")) is not None
        and isinstance(status, int)
        and not isinstance(status, bool)
        and 200 <= status < 300
        and isinstance(response_bytes, int)
        and not isinstance(response_bytes, bool)
        and response_bytes >= 0
        and isinstance(raw_response, str)
        and len(raw_response.encode("utf-8")) == response_bytes
        and hashlib.sha256(raw_response.encode("utf-8")).hexdigest()
        == value.get("response_sha256")
        and isinstance(raw_request, str)
        and hashlib.sha256(raw_request.encode("utf-8")).hexdigest()
        == value.get("request_sha256")
        and bool(DIGEST_RE.fullmatch(str(value.get("response_sha256", ""))))
        and bool(DIGEST_RE.fullmatch(str(value.get("request_sha256", ""))))
    )
    if active is None:
        return base_valid
    try:
        response_payload = json.loads(raw_response) if isinstance(raw_response, str) else None
    except json.JSONDecodeError:
        return False
    if not isinstance(response_payload, list) or not _meaningful_string(alert_name):
        return False
    computed_matches = sum(
        1
        for item in response_payload
        if isinstance(item, dict)
        and isinstance(item.get("labels"), dict)
        and item["labels"].get("alertname") == alert_name
    )
    matching = value.get("matching_alerts")
    return (
        base_valid
        and raw_request == ""
        and isinstance(matching, int)
        and not isinstance(matching, bool)
        and matching == computed_matches
        and ((matching > 0) if active else (matching == 0))
        and value.get("label_selector_sha256")
        == _canonical_digest({"alertname": alert_name})
        and value.get("expected_active") is active
        and value.get("passed") is True
    )


def _alertmanager_post_observation(
    value: Any,
    *,
    labels: Any,
    starts_at: datetime | None,
    ends_at: datetime | None,
) -> bool:
    if not (
        _alertmanager_api_observation(value)
        and isinstance(value, dict)
        and isinstance(labels, dict)
        and starts_at is not None
        and ends_at is not None
    ):
        return False
    payload = [
        {
            "labels": labels,
            "annotations": {"summary": "DuckDock GA signed on-call delivery exercise"},
            "startsAt": starts_at.isoformat(),
            "endsAt": ends_at.isoformat(),
        }
    ]
    raw_request = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    try:
        response_payload = json.loads(value.get("raw_response_body")) if value.get("raw_response_body") else None
    except (TypeError, json.JSONDecodeError):
        return False
    return (
        value.get("request_payload") == payload
        and value.get("raw_request_body") == raw_request
        and value.get("request_sha256")
        == hashlib.sha256(raw_request.encode("utf-8")).hexdigest()
        and (response_payload is None or isinstance(response_payload, (dict, list)))
    )


def _alert_delivery_receipt_valid(
    value: Any,
    *,
    exercise_id: Any,
    target_environment: Any,
    alert_name: Any,
    schedule: Any,
    event: str,
) -> bool:
    expected_keys = {
        "schema_version",
        "exercise_id",
        "target_environment",
        "alert_name",
        "event",
        "receipt_id",
        "deliveries",
        "schedule",
        "delivered",
        "delivered_at",
    }
    deliveries = value.get("deliveries") if isinstance(value, dict) else None
    delivered_at = _parse_time(value.get("delivered_at")) if isinstance(value, dict) else None
    delivery_times = (
        [_parse_time(item.get("delivered_at")) for item in deliveries]
        if isinstance(deliveries, list) and all(isinstance(item, dict) for item in deliveries)
        else []
    )
    deliveries_valid = (
        isinstance(deliveries, list)
        and len(deliveries) >= 2
        and all(
            isinstance(item, dict)
            and set(item) == {"channel", "receiver", "provider_receipt_id", "delivered_at"}
            and _meaningful_string(item.get("channel"))
            and _meaningful_string(item.get("receiver"))
            and _meaningful_string(item.get("provider_receipt_id"))
            for item in deliveries
        )
        and all(item is not None for item in delivery_times)
        and len({item["channel"] for item in deliveries}) == len(deliveries)
        and len({item["receiver"] for item in deliveries}) == len(deliveries)
        and len({item["provider_receipt_id"] for item in deliveries}) == len(deliveries)
        and delivered_at is not None
        and max(item for item in delivery_times if item is not None) == delivered_at
    )
    return (
        isinstance(value, dict)
        and set(value) == expected_keys
        and value.get("schema_version") == ALERT_DELIVERY_SCHEMA_VERSION
        and value.get("exercise_id") == exercise_id
        and value.get("target_environment") == target_environment
        and value.get("alert_name") == alert_name
        and value.get("schedule") == schedule
        and value.get("event") == event
        and _meaningful_string(value.get("receipt_id"))
        and deliveries_valid
        and value.get("delivered") is True
        and _parse_time(value.get("delivered_at")) is not None
    )


def _oncall_acknowledgement_valid(
    value: Any,
    *,
    exercise_id: Any,
    target_environment: Any,
    alert_name: Any,
    schedule: Any,
    signer_identity: Any,
) -> bool:
    expected_keys = {
        "schema_version",
        "exercise_id",
        "target_environment",
        "alert_name",
        "receipt_id",
        "schedule",
        "acknowledged",
        "acknowledged_by",
        "acknowledged_at",
    }
    return (
        isinstance(value, dict)
        and set(value) == expected_keys
        and value.get("schema_version") == ONCALL_ACK_SCHEMA_VERSION
        and value.get("exercise_id") == exercise_id
        and value.get("target_environment") == target_environment
        and value.get("alert_name") == alert_name
        and value.get("schedule") == schedule
        and value.get("acknowledged_by") == signer_identity
        and _meaningful_string(value.get("receipt_id"))
        and value.get("acknowledged") is True
        and _parse_time(value.get("acknowledged_at")) is not None
    )


def _signed_backup_manifest(
    signed_file: dict[str, Any],
    *,
    authorization_path: Path,
) -> dict[str, Any] | None:
    path = _resolve_file(signed_file.get("path"), authorization_path)
    if path is None or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _positive_int(value: Any) -> bool:
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return False


def _backup_manifest_matches_release(
    manifest: dict[str, Any] | None,
    *,
    release_commit: str,
) -> bool:
    if not isinstance(manifest, dict):
        return False
    expected_names = {"mysql.sql.gz.age", "repos.tar.gz.age", "minio.tar.gz.age"}
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != expected_names:
        return False
    return (
        set(manifest)
        == {"schema_version", "created_at", "timestamp", "release_commit", "encryption", "artifacts"}
        and manifest.get("schema_version") == "duckdock-secure-backup-v1"
        and manifest.get("release_commit") == release_commit
        and manifest.get("encryption") == "age-x25519"
        and _parse_time(manifest.get("created_at")) is not None
        and isinstance(manifest.get("timestamp"), str)
        and bool(re.fullmatch(r"[0-9]{8}-[0-9]{6}", manifest["timestamp"]))
        and all(
            isinstance(artifacts.get(name), dict)
            and set(artifacts[name])
            == {
                "encrypted_sha256",
                "encrypted_size_bytes",
                "plaintext_sha256",
                "plaintext_size_bytes",
            }
            and bool(DIGEST_RE.fullmatch(str(artifacts[name].get("encrypted_sha256", ""))))
            and bool(DIGEST_RE.fullmatch(str(artifacts[name].get("plaintext_sha256", ""))))
            and isinstance(artifacts[name].get("encrypted_size_bytes"), int)
            and not isinstance(artifacts[name].get("encrypted_size_bytes"), bool)
            and artifacts[name]["encrypted_size_bytes"] > 0
            and isinstance(artifacts[name].get("plaintext_size_bytes"), int)
            and not isinstance(artifacts[name].get("plaintext_size_bytes"), bool)
            and artifacts[name]["plaintext_size_bytes"] > 0
            for name in expected_names
        )
        and not _contains_secret_material_key(manifest)
    )


def evaluate(
    document: dict[str, Any],
    *,
    authorization_path: Path,
    approval_policy_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    lint_errors = lint_authorization(document)
    if lint_errors:
        raise ValueError("; ".join(lint_errors))
    current = now or datetime.now(timezone.utc)
    gate = Gate()
    release = _object(document["release"], "release")
    target = _object(document["target"], "target")
    controls = _object(document["controls"], "controls")
    approval_policy_reference = _object(document["approval_policy"], "approval_policy")

    version = str(release.get("version", ""))
    gate.add(
        "release_version",
        owner="Product",
        passed=version == "2.0.0",
        observed=version or "missing",
        expected="2.0.0",
        detail="RC identifiers cannot be used for final GA authorization.",
    )
    commit = str(release.get("git_commit", ""))
    gate.add(
        "release_commit",
        owner="Architecture",
        passed=bool(COMMIT_RE.fullmatch(commit)),
        observed=commit or "missing",
        expected="immutable 40-character Git commit",
        detail="All evidence and approvals bind to one source revision.",
    )
    for image_key in ("backend_image", "frontend_image"):
        image = str(release.get(image_key, ""))
        gate.add(
            image_key,
            owner="Security",
            passed=bool(IMAGE_RE.fullmatch(image)),
            observed=image or "missing",
            expected="registry/repository@sha256:<64 hex>",
            detail="Tags are mutable and cannot identify a GA production artifact.",
        )
    contract_digest = str(release.get("contract_digest", ""))
    gate.add(
        "contract_digest",
        owner="Architecture",
        passed=bool(DIGEST_RE.fullmatch(contract_digest)),
        observed=contract_digest or "missing",
        expected="64-character SHA-256",
        detail="The authorization binds the frozen v2 API contract.",
    )

    fault_domains = target.get("fault_domains")
    target_mode = target.get("deployment_mode")
    target_ok = (
        target.get("environment") == "production"
        and target_mode == "kubernetes-ha"
        and isinstance(target.get("target_id"), str)
        and not any(marker in str(target.get("target_id")) for marker in PLACEHOLDER_MARKERS)
        and isinstance(fault_domains, list)
        and len({str(item) for item in fault_domains if str(item).strip()}) >= 2
        and _https_url(target.get("public_base_url"))
        and _https_url(target.get("object_store_url"))
    )
    gate.add(
        "target_identity",
        owner="Architecture",
        passed=target_ok,
        observed=f"target_id={target.get('target_id')}, environment={target.get('environment')}, mode={target_mode}, fault_domains={fault_domains}",
        expected="named production kubernetes-ha target; >=2 fault domains; HTTPS app/object URLs",
        detail="Authorization is target-specific and cannot be copied from local Compose evidence.",
    )

    policy_path = approval_policy_path.resolve() if approval_policy_path is not None else None
    policy: dict[str, Any] = {}
    policy_load_detail = "missing --approval-policy"
    if policy_path is not None and policy_path.is_file():
        try:
            loaded_policy = json.loads(policy_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            policy_load_detail = str(exc)
        else:
            if isinstance(loaded_policy, dict):
                policy = loaded_policy
                policy_load_detail = "loaded"
            else:
                policy_load_detail = "policy root is not an object"

    expected_policy_keys = {
        "schema_version",
        "policy_id",
        "organization",
        "allowed_signers_path",
        "allowed_signers_sha256",
        "roles",
        "independent_security_assessors",
    }
    expected_reference_keys = {"policy_id", "sha256"}
    policy_roles_raw = policy.get("roles")
    role_identities: dict[str, set[str]] = {}
    roles_valid = isinstance(policy_roles_raw, dict) and set(policy_roles_raw) == REQUIRED_APPROVAL_ROLES
    if roles_valid:
        for role in REQUIRED_APPROVAL_ROLES:
            raw_identities = policy_roles_raw.get(role)
            if (
                not isinstance(raw_identities, list)
                or not raw_identities
                or not all(_meaningful_string(identity) for identity in raw_identities)
            ):
                roles_valid = False
                break
            identities_for_role = {str(identity) for identity in raw_identities}
            if len(identities_for_role) != len(raw_identities):
                roles_valid = False
                break
            role_identities[role] = identities_for_role
    configured_role_identities = set().union(*role_identities.values()) if role_identities else set()
    identities_are_role_exclusive = (
        roles_valid
        and len(configured_role_identities) == sum(len(items) for items in role_identities.values())
    )
    assessor_mappings_raw = policy.get("independent_security_assessors")
    assessor_provider_identities: dict[str, set[str]] = {}
    assessors_valid = isinstance(assessor_mappings_raw, dict) and bool(assessor_mappings_raw)
    if assessors_valid:
        for provider, raw_identities in assessor_mappings_raw.items():
            if (
                not _meaningful_string(provider)
                or not isinstance(raw_identities, list)
                or not raw_identities
                or not all(_meaningful_string(identity) for identity in raw_identities)
            ):
                assessors_valid = False
                break
            identities_for_provider = {str(identity) for identity in raw_identities}
            if len(identities_for_provider) != len(raw_identities):
                assessors_valid = False
                break
            assessor_provider_identities[str(provider)] = identities_for_provider
    configured_assessor_identities = (
        set().union(*assessor_provider_identities.values())
        if assessor_provider_identities
        else set()
    )
    assessors_are_exclusive = (
        assessors_valid
        and len(configured_assessor_identities)
        == sum(len(items) for items in assessor_provider_identities.values())
        and not configured_role_identities.intersection(configured_assessor_identities)
    )
    configured_identities = configured_role_identities.union(configured_assessor_identities)
    expected_policy_digest = str(approval_policy_reference.get("sha256", ""))
    actual_policy_digest = _sha256(policy_path) if policy_path is not None and policy_path.is_file() else "missing"
    approval_policy_ok = (
        set(approval_policy_reference) == expected_reference_keys
        and bool(DIGEST_RE.fullmatch(expected_policy_digest))
        and actual_policy_digest == expected_policy_digest
        and set(policy) == expected_policy_keys
        and policy.get("schema_version") == APPROVAL_POLICY_SCHEMA_VERSION
        and _meaningful_string(policy.get("policy_id"))
        and policy.get("policy_id") == approval_policy_reference.get("policy_id")
        and _meaningful_string(policy.get("organization"))
        and roles_valid
        and identities_are_role_exclusive
        and assessors_valid
        and assessors_are_exclusive
    )
    gate.add(
        "approval_policy",
        owner="Security",
        passed=approval_policy_ok,
        observed=(
            f"path={policy_path or 'missing'}, digest={actual_policy_digest}, "
            f"policy_id={policy.get('policy_id', 'missing')}, load={policy_load_detail}"
        ),
        expected=(
            f"out-of-band {APPROVAL_POLICY_SCHEMA_VERSION}; digest={expected_policy_digest or 'missing'}; "
            "four non-overlapping approval roles plus independent assessor mappings"
        ),
        detail="The release authority, not an individual approver, must provision the content-addressed role policy.",
    )

    allowed_signers = (
        _resolve_file(policy.get("allowed_signers_path"), policy_path)
        if policy_path is not None
        else None
    )
    expected_trust_digest = str(policy.get("allowed_signers_sha256", ""))
    actual_trust_digest = (
        _sha256(allowed_signers) if allowed_signers is not None and allowed_signers.is_file() else "missing"
    )
    signer_bindings: dict[str, set[str]] | None = None
    principals_detail = "missing trust store"
    if allowed_signers is not None and allowed_signers.is_file():
        signer_bindings, principals_detail = _allowed_signer_bindings(allowed_signers)
    trust_store_ok = (
        approval_policy_ok
        and bool(DIGEST_RE.fullmatch(expected_trust_digest))
        and actual_trust_digest == expected_trust_digest
        and signer_bindings is not None
        and set(signer_bindings) == configured_identities
    )
    gate.add(
        "approval_trust_store",
        owner="Security",
        passed=trust_store_ok,
        observed=(
            f"path={allowed_signers or 'missing'}, digest={actual_trust_digest}, {principals_detail}"
        ),
        expected=(
            "policy-bound shared allowed-signers digest with exactly the configured role and "
            "independent-assessor identities"
        ),
        detail=(
            "Per-approval and per-assessment trust stores are forbidden; every signature must "
            "chain to this one release-authority trust store."
        ),
    )

    provenance_reference = release.get("provenance")
    provenance_path = (
        _resolve_file(provenance_reference.get("path"), authorization_path)
        if isinstance(provenance_reference, dict)
        else None
    )
    provenance_expected_digest = (
        str(provenance_reference.get("sha256", ""))
        if isinstance(provenance_reference, dict)
        else ""
    )
    provenance_actual_digest = (
        _sha256(provenance_path)
        if provenance_path is not None and provenance_path.is_file()
        else "missing"
    )
    provenance_report: dict[str, Any] = {}
    if provenance_path is not None and provenance_path.is_file():
        try:
            loaded_provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            loaded_provenance = None
        if isinstance(loaded_provenance, dict):
            provenance_report = loaded_provenance
    provenance_observed_at = _parse_time(
        provenance_reference.get("observed_at")
        if isinstance(provenance_reference, dict)
        else None
    )
    provenance_reference_ok = (
        isinstance(provenance_reference, dict)
        and set(provenance_reference) == {"path", "sha256", "observed_at"}
        and bool(DIGEST_RE.fullmatch(provenance_expected_digest))
        and provenance_actual_digest == provenance_expected_digest
        and provenance_observed_at is not None
        and timedelta(0) <= current - provenance_observed_at <= timedelta(days=30)
        and _parse_time(provenance_report.get("observed_at")) == provenance_observed_at
    )
    (
        provenance_policy_ok,
        provenance_trust_store,
        build_identities,
        approved_source_repositories,
        approved_builder_ids,
        approved_workflow_refs,
        build_signer_bindings,
        provenance_policy_detail,
    ) = _release_provenance_policy_context(
        provenance_report.get("provenance_policy"),
        authorization_path=authorization_path,
    )
    signed_build = provenance_report.get("signed_build_report")
    build_signature_ok, raw_build_report, build_signature_detail = (
        _verified_embedded_receipt(
            signed_build,
            authorization_path=authorization_path,
            allowed_signers=provenance_trust_store if provenance_policy_ok else None,
            allowed_identities=build_identities,
            namespace=RELEASE_BUILD_SIGNATURE_NAMESPACE,
        )
    )
    raw_build_path = (
        _resolve_file(signed_build.get("signed_evidence", {}).get("path"), authorization_path)
        if isinstance(signed_build, dict)
        and isinstance(signed_build.get("signed_evidence"), dict)
        else None
    )
    build_derived: dict[str, Any] = {}
    build_validation_detail = "signed build report unavailable"
    try:
        if not (provenance_policy_ok and build_signature_ok and raw_build_path is not None):
            raise ValueError(build_validation_detail)
        source_repository = str(raw_build_report.get("source_repository", ""))
        if source_repository not in approved_source_repositories:
            raise ValueError("unapproved source repository")
        build_derived = validate_release_build_report(
            raw_build_report,
            report_path=raw_build_path,
            git_commit=commit,
            backend_image=str(release.get("backend_image", "")),
            frontend_image=str(release.get("frontend_image", "")),
            contract_digest=contract_digest,
            source_repository=source_repository,
            approved_builder_ids=approved_builder_ids,
            approved_workflow_refs=approved_workflow_refs,
            now=current,
        )
        if current - build_derived["built_at"] > timedelta(days=30):
            raise ValueError("signed release build is older than 30 days")
    except (TypeError, ValueError) as exc:
        build_validation_detail = str(exc)
    else:
        build_validation_detail = "tag, source, SLSA, SPDX and vulnerability reports validated"
    build_signer_identity = (
        signed_build.get("signed_evidence", {}).get("signer_identity")
        if isinstance(signed_build, dict)
        and isinstance(signed_build.get("signed_evidence"), dict)
        else None
    )
    approval_key_material = set().union(*signer_bindings.values()) if signer_bindings else set()
    build_key_material = (
        set().union(*build_signer_bindings.values()) if build_signer_bindings else set()
    )
    build_roles_separated = (
        build_identities.isdisjoint(configured_identities)
        and approval_key_material.isdisjoint(build_key_material)
    )
    expected_provenance_keys = {
        "schema_version",
        "release_version",
        "git_ref",
        "git_commit",
        "contract_digest",
        "observed_at",
        "built_at",
        "source_repository",
        "source_tree_sha256",
        "source_archive",
        "builder",
        "artifacts",
        "provenance_policy",
        "signed_build_report",
    }
    provenance_projection_ok = (
        bool(build_derived)
        and set(provenance_report) == expected_provenance_keys
        and provenance_report.get("schema_version")
        == RELEASE_PROVENANCE_EVIDENCE_SCHEMA_VERSION
        and provenance_report.get("release_version") == version
        and provenance_report.get("git_ref") == build_derived.get("git_ref")
        and provenance_report.get("git_commit") == commit
        and provenance_report.get("contract_digest") == contract_digest
        and provenance_report.get("built_at")
        == raw_build_report.get("build_finished_at")
        and provenance_report.get("source_repository")
        == build_derived.get("source_repository")
        and provenance_report.get("source_tree_sha256")
        == build_derived.get("source_tree_sha256")
        and provenance_report.get("source_archive") == build_derived.get("source_archive")
        and provenance_report.get("builder") == build_derived.get("builder")
        and provenance_report.get("artifacts") == build_derived.get("artifacts")
        and not _contains_secret_material_key(provenance_report)
    )
    release_provenance_ok = (
        provenance_reference_ok
        and provenance_policy_ok
        and build_signature_ok
        and build_signer_identity in build_identities
        and build_roles_separated
        and provenance_projection_ok
    )
    gate.add(
        "release_provenance",
        owner="Security",
        passed=release_provenance_ok,
        observed=(
            f"evidence={provenance_path or 'missing'}@{provenance_actual_digest}, "
            f"builder_signer={build_signer_identity or 'missing'}, "
            f"roles_separated={build_roles_separated}, validation={build_validation_detail}"
        ),
        expected=(
            "fresh content-addressed release evidence; approved distinct builder signature; "
            "signed v2.0.0 tag/source; exact image SLSA + SPDX SBOM + zero open Critical/High"
        ),
        detail=(
            f"policy=({provenance_policy_detail}); signature=({build_signature_detail}); "
            "the GA release digest includes this evidence reference, so later replacement invalidates approvals"
        ),
    )

    evidence_times: list[datetime] = (
        [provenance_observed_at] if provenance_observed_at is not None else []
    )
    evidence_owners = {
        "application_readiness": "Architecture",
        "tls": "Security",
        "secrets": "Security",
        "network": "Security",
        "alerting": "Operations",
        "recovery": "Operations",
        "capacity": "Architecture",
        "high_availability": "Architecture",
        "security_assessment": "Security",
    }
    evidence_ages = {
        "application_readiness": timedelta(days=7),
        "tls": timedelta(days=7),
        "secrets": timedelta(days=30),
        "network": timedelta(days=30),
        "alerting": timedelta(days=30),
        "recovery": timedelta(days=30),
        "capacity": timedelta(days=30),
        "high_availability": timedelta(days=30),
        "security_assessment": timedelta(days=90),
    }
    for key in sorted(REQUIRED_CONTROLS):
        _, observed_at = _evidence_check(
            gate,
            key=key,
            owner=evidence_owners[key],
            control=_object(controls[key], f"controls.{key}"),
            authorization_path=authorization_path,
            now=current,
            maximum_age=evidence_ages[key],
        )
        if observed_at is not None:
            evidence_times.append(observed_at)

    app = controls["application_readiness"]
    app_report = _evidence_json(app, authorization_path)
    app_observed_at = _control_observed_at(app)
    app_checked_at = _parse_time(app_report.get("checked_at") if isinstance(app_report, dict) else None)
    app_report_matches = (
        _report_release_target_binding(
            app_report,
            schema_version="duckdock-ga-target-readiness-v1",
            status="READY",
            control=app,
            target=target,
            release=release,
        )
        and app_report.get("passed") is True
        and app_report.get("profile_version") == "duckdock-2-ga-readiness-v1"
        and app_report.get("transport") == "network HTTPS against target"
        and _origin(app_report.get("base_url")) == str(target.get("public_base_url", "")).rstrip("/")
        and app_checked_at is not None
        and app_observed_at is not None
        and timedelta(0) <= app_observed_at - app_checked_at <= timedelta(minutes=5)
        and app_report.get("status") == app.get("status")
        and int(app_report.get("pass_count", -1)) == int(app.get("pass_count", -2))
        and int(app_report.get("block_count", -1)) == int(app.get("block_count", -2))
        and app_report.get("contract_version") == app.get("contract_version")
        and app_report.get("contract_digest") == app.get("contract_digest")
        and app_report.get("current_db_revision") == app.get("database_revision")
        and app_report.get("expected_db_revision") == app.get("expected_database_revision")
        and isinstance(app_report.get("checks"), list)
        and all(isinstance(item, dict) and item.get("status") == "PASS" for item in app_report["checks"])
    )
    gate.add(
        "application_readiness",
        owner="Architecture",
        passed=(
            app.get("status") == "READY"
            and app.get("block_count") == 0
            and int(app.get("pass_count", 0)) >= 14
            and app.get("contract_version") == version
            and app.get("contract_digest") == contract_digest
            and app.get("database_revision") == app.get("expected_database_revision")
            and app_report_matches
        ),
        observed=(
            f"status={app.get('status')}, pass={app.get('pass_count')}, block={app.get('block_count')}, "
            f"contract={app.get('contract_version')}@{app.get('contract_digest')}, "
            f"db={app.get('database_revision')}"
        ),
        expected="READY; >=14 pass; 0 block; final release contract digest/version; DB at expected revision",
        detail="The HTTPS collector report must bind the exact target, commit and images, be captured within five minutes of the readiness evaluation, and match every imported claim; it does not replace target authorization.",
    )

    tls = controls["tls"]
    negotiated = tls.get("negotiated_protocols")
    tls_report = _evidence_json(tls, authorization_path)
    tls_probe = tls_report.get("probe") if isinstance(tls_report, dict) else None
    (
        tls_policy_ok,
        _,
        tls_trust_store,
        tls_probe_identities,
        tls_policy_detail,
    ) = _tls_policy_context(
        tls_report.get("tls_policy") if isinstance(tls_report, dict) else None,
        authorization_path=authorization_path,
        probe=tls_probe,
    )
    tls_signed_probe = (
        tls_report.get("signed_probe") if isinstance(tls_report, dict) else None
    )
    tls_signature_ok, raw_tls_report, tls_signature_detail = _verified_embedded_receipt(
        tls_signed_probe,
        authorization_path=authorization_path,
        allowed_signers=tls_trust_store if tls_policy_ok else None,
        allowed_identities=tls_probe_identities,
        namespace=TLS_SIGNATURE_NAMESPACE,
    )
    tls_derived: dict[str, Any] = {}
    tls_validation_detail = "signed TLS probe unavailable"
    try:
        if not raw_tls_report:
            raise ValueError(tls_validation_detail)
        tls_derived = validate_tls_probe_report(
            raw_tls_report,
            target_environment=str(target.get("target_id")),
            source_commit=str(release.get("git_commit")),
            backend_image=str(release.get("backend_image")),
            frontend_image=str(release.get("frontend_image")),
            application_url=str(tls_report.get("application_url", "")),
            object_store_url=str(tls_report.get("object_store_url", "")),
            exercise_id=str(tls_report.get("exercise_id", "")),
            now=current,
        )
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        tls_validation_detail = str(exc)
    else:
        tls_validation_detail = "signed external TLS observations validated"
    tls_observed_at = _parse_time(
        tls_report.get("observed_at") if isinstance(tls_report, dict) else None
    )
    tls_probe_observed_at = _parse_time(
        tls_report.get("probe_observed_at") if isinstance(tls_report, dict) else None
    )
    tls_report_matches_target = (
        isinstance(tls_report, dict)
        and set(tls_report)
        == {
            "schema_version",
            "scope",
            "target_environment",
            "source_commit",
            "images",
            "status",
            "passed",
            "observed_at",
            "probe_observed_at",
            "exercise_id",
            "probe",
            "application_url",
            "object_store_url",
            "negotiated_protocols",
            "certificate_days_remaining",
            "hostname_verified",
            "legacy_protocols_rejected",
            "hsts_max_age_seconds",
            "endpoints",
            "tls_policy",
            "signed_probe",
        }
        and
        _report_release_target_binding(
            tls_report,
            schema_version=TLS_EVIDENCE_SCHEMA_VERSION,
            status="PASS",
            control=tls,
            target=target,
            release=release,
        )
        and tls_report.get("passed") is True
        and tls_policy_ok
        and tls_signature_ok
        and bool(tls_derived)
        and tls_report.get("exercise_id") == raw_tls_report.get("exercise_id")
        and tls_report.get("probe") == tls_derived.get("probe")
        and tls_report.get("probe_observed_at") == raw_tls_report.get("observed_at")
        and tls_probe_observed_at == tls_derived.get("observed_at")
        and tls_observed_at is not None
        and tls_probe_observed_at is not None
        and timedelta(0) <= tls_observed_at - tls_probe_observed_at <= timedelta(minutes=5)
        and _origin(tls_report.get("application_url")) == str(target.get("public_base_url", "")).rstrip("/")
        and _origin(tls_report.get("object_store_url")) == str(target.get("object_store_url", "")).rstrip("/")
        and tls_report.get("negotiated_protocols") == tls_derived.get("negotiated_protocols")
        and tls_report.get("legacy_protocols_rejected")
        == tls_derived.get("legacy_protocols_rejected")
        and tls_report.get("certificate_days_remaining")
        == tls_derived.get("certificate_days_remaining")
        and tls_report.get("hostname_verified") is tls_derived.get("hostname_verified")
        and tls_report.get("hsts_max_age_seconds") == tls_derived.get("hsts_max_age_seconds")
        and tls_report.get("endpoints") == raw_tls_report.get("endpoints")
        and tls_report.get("negotiated_protocols") == negotiated
        and tls_report.get("legacy_protocols_rejected") == tls.get("legacy_protocols_rejected")
        and tls_report.get("certificate_days_remaining") == tls.get("certificate_days_remaining")
        and tls_report.get("hostname_verified") is tls.get("hostname_verified")
        and tls_report.get("hsts_max_age_seconds") == tls.get("hsts_max_age_seconds")
        and not _contains_secret_material_key(tls_report)
    )
    gate.add(
        "tls_probe_signature",
        owner="Security",
        passed=tls_policy_ok and tls_signature_ok and bool(tls_derived),
        observed=(
            f"probe={tls_probe}, validation={tls_validation_detail}, "
            f"signature={tls_signature_detail}"
        ),
        expected=(
            "release-authority-approved external probe identity, source CIDR and signed raw "
            "TLS observations"
        ),
        detail=(
            "The v3 gate reopens the signed probe, validates its exact external vantage policy, "
            f"certificate fingerprints, handshakes, HSTS and legacy OpenSSL output. {tls_policy_detail}"
        ),
    )
    tls_ok = (
        tls.get("status") == "PASS"
        and isinstance(negotiated, list)
        and set(negotiated).issubset({"TLSv1.2", "TLSv1.3"})
        and "TLSv1.2" in negotiated
        and tls.get("legacy_protocols_rejected") == ["TLSv1", "TLSv1.1"]
        and int(tls.get("certificate_days_remaining", -1)) >= 30
        and int(tls.get("hsts_max_age_seconds", 0)) >= 31_536_000
        and tls.get("hostname_verified") is True
        and tls_report_matches_target
    )
    gate.add(
        "tls",
        owner="Security",
        passed=tls_ok,
        observed=f"status={tls.get('status')}, protocols={negotiated}, cert_days={tls.get('certificate_days_remaining')}",
        expected="TLS 1.2/1.3 only; hostname valid; cert >=30d; HSTS >=1y",
        detail="The content-addressed probe JSON must match both actual public target origins and every claimed TLS metric.",
    )

    secrets = controls["secrets"]
    secrets_report = _evidence_json(secrets, authorization_path)
    secret_store = (
        secrets_report.get("secret_store")
        if isinstance(secrets_report, dict) and isinstance(secrets_report.get("secret_store"), dict)
        else {}
    )
    secret_rotation = (
        secrets_report.get("rotation")
        if isinstance(secrets_report, dict) and isinstance(secrets_report.get("rotation"), dict)
        else {}
    )
    secret_classes = secret_rotation.get("secret_classes")
    secrets_policy_ok, secrets_trust_store, provider_identities, verifier_identities, secrets_policy_detail = (
        _secrets_policy_context(
            secrets_report.get("secrets_policy") if isinstance(secrets_report, dict) else None,
            authorization_path=authorization_path,
            provider=secrets.get("provider"),
        )
    )
    rotation_embedded = (
        secrets_report.get("rotation_receipt") if isinstance(secrets_report, dict) else None
    )
    verification_embedded = (
        secrets_report.get("verification_receipt") if isinstance(secrets_report, dict) else None
    )
    rotation_signature_ok, rotation_receipt, rotation_signature_detail = (
        _verified_embedded_receipt(
            rotation_embedded,
            authorization_path=authorization_path,
            allowed_signers=secrets_trust_store,
            allowed_identities=provider_identities,
            namespace=SECRET_ROTATION_SIGNATURE_NAMESPACE,
        )
    )
    verification_signature_ok, verification_receipt, verification_signature_detail = (
        _verified_embedded_receipt(
            verification_embedded,
            authorization_path=authorization_path,
            allowed_signers=secrets_trust_store,
            allowed_identities=verifier_identities,
            namespace=SECRET_VERIFICATION_SIGNATURE_NAMESPACE,
        )
    )
    exercise_id = secret_rotation.get("exercise_id")
    secret_name = secret_rotation.get("secret_name")
    rotation_receipt_valid = _secret_rotation_receipt_valid(
        rotation_receipt,
        exercise_id=exercise_id,
        target_environment=target.get("target_id"),
        provider=secrets.get("provider"),
        secret_name=secret_name,
    )
    verification_receipt_valid = _secret_verification_receipt_valid(
        verification_receipt,
        exercise_id=exercise_id,
        target_environment=target.get("target_id"),
        secret_name=secret_name,
    )
    rotation_signer = (
        rotation_embedded.get("signed_evidence", {}).get("signer_identity")
        if isinstance(rotation_embedded, dict)
        and isinstance(rotation_embedded.get("signed_evidence"), dict)
        else None
    )
    verification_signer = (
        verification_embedded.get("signed_evidence", {}).get("signer_identity")
        if isinstance(verification_embedded, dict)
        and isinstance(verification_embedded.get("signed_evidence"), dict)
        else None
    )
    report_observed = (
        _parse_time(secrets_report.get("observed_at")) if isinstance(secrets_report, dict) else None
    )
    exercise_started = _parse_time(secret_rotation.get("started_at"))
    exercise_completed = _parse_time(secret_rotation.get("completed_at"))
    provider_started = (
        _parse_time(rotation_receipt.get("started_at"))
        if isinstance(rotation_receipt, dict)
        else None
    )
    provider_completed = (
        _parse_time(rotation_receipt.get("completed_at"))
        if isinstance(rotation_receipt, dict)
        else None
    )
    verifier_started = (
        _parse_time(verification_receipt.get("started_at"))
        if isinstance(verification_receipt, dict)
        else None
    )
    verifier_completed = (
        _parse_time(verification_receipt.get("completed_at"))
        if isinstance(verification_receipt, dict)
        else None
    )
    kubernetes_observation = (
        secrets_report.get("kubernetes_observation")
        if isinstance(secrets_report, dict)
        else None
    )
    before_captured = (
        _parse_time(kubernetes_observation.get("before", {}).get("captured_at"))
        if isinstance(kubernetes_observation, dict)
        and isinstance(kubernetes_observation.get("before"), dict)
        else None
    )
    after_captured = (
        _parse_time(kubernetes_observation.get("after", {}).get("captured_at"))
        if isinstance(kubernetes_observation, dict)
        and isinstance(kubernetes_observation.get("after"), dict)
        else None
    )
    secrets_timeline = (
        all(
            value is not None
            for value in (
                before_captured,
                exercise_started,
                provider_started,
                provider_completed,
                verifier_started,
                verifier_completed,
                after_captured,
                exercise_completed,
                report_observed,
            )
        )
        and before_captured <= exercise_started
        and exercise_started - timedelta(minutes=5) <= provider_started
        and provider_started <= provider_completed <= verifier_started <= verifier_completed
        and verifier_completed <= after_captured <= exercise_completed <= report_observed
    )
    secrets_report_matches = (
        _report_release_target_binding(
            secrets_report,
            schema_version=SECRETS_SCHEMA_VERSION,
            status="PASS",
            control=secrets,
            target=target,
            release=release,
        )
        and secrets_report.get("provider") == secrets.get("provider")
        and secrets_report.get("plaintext_env_persisted") is secrets.get("plaintext_env_persisted")
        and secrets_report.get("rotation_tested") is secrets.get("rotation_tested")
        and secret_store.get("encrypted_at_rest") is True
        and secret_store.get("access_audit_enabled") is True
        and secret_store.get("credentials_external_to_evidence") is True
        and set(secret_store)
        == {"encrypted_at_rest", "access_audit_enabled", "credentials_external_to_evidence"}
        and set(secret_rotation)
        == {
            "exercise_id",
            "secret_name",
            "secret_classes",
            "started_at",
            "completed_at",
            "old_credentials_rejected",
            "new_credentials_accepted",
            "workloads_reloaded",
            "audit_event_recorded",
        }
        and _meaningful_string(exercise_id)
        and _meaningful_string(secret_name)
        and secret_rotation.get("old_credentials_rejected") is True
        and secret_rotation.get("new_credentials_accepted") is True
        and secret_rotation.get("workloads_reloaded") is True
        and secret_rotation.get("audit_event_recorded") is True
        and not _contains_secret_material_key(secrets_report)
        and secret_classes == list(REQUIRED_SECRET_CLASSES)
        and secrets_policy_ok
        and rotation_signature_ok
        and verification_signature_ok
        and rotation_signer != verification_signer
        and rotation_receipt_valid
        and verification_receipt_valid
        and isinstance(rotation_receipt, dict)
        and secret_store.get("encrypted_at_rest")
        is rotation_receipt.get("encrypted_at_rest")
        and secret_store.get("access_audit_enabled")
        is rotation_receipt.get("access_audit_enabled")
        and secret_store.get("credentials_external_to_evidence")
        is rotation_receipt.get("credentials_external_to_evidence")
        and _secret_kubernetes_rotation_valid(
            kubernetes_observation,
            secret_name=secret_name,
        )
        and secrets_timeline
    )
    secrets_ok = (
        secrets.get("status") == "PASS"
        and secrets.get("plaintext_env_persisted") is False
        and secrets.get("rotation_tested") is True
        and secrets.get("provider") in {"SOPS/age", "External Secrets", "Vault", "cloud-secret-manager"}
        and secrets_report_matches
    )
    gate.add(
        "secrets",
        owner="Security",
        passed=secrets_ok,
        observed=f"status={secrets.get('status')}, provider={secrets.get('provider')}, rotation={secrets.get('rotation_tested')}",
        expected="approved secret manager; no persisted plaintext env; rotation tested",
        detail=(
            "The target collector must retain metadata-only Kubernetes before/after snapshots and "
            "separately signed provider/verifier receipts without credential values. "
            f"policy=({secrets_policy_detail}); rotation=({rotation_signature_detail}); "
            f"verification=({verification_signature_detail})"
        ),
    )

    network = controls["network"]
    network_evidence_report = _evidence_json(network, authorization_path)
    network_probe = (
        network_evidence_report.get("probe")
        if isinstance(network_evidence_report, dict)
        else None
    )
    network_evidence_policy_tests = (
        network_evidence_report.get("policy_tests")
        if isinstance(network_evidence_report, dict)
        else None
    )
    (
        network_policy_ok,
        network_trust_store,
        network_probe_identities,
        network_policy_detail,
    ) = _network_policy_context(
        network_evidence_report.get("network_policy")
        if isinstance(network_evidence_report, dict)
        else None,
        authorization_path=authorization_path,
        probe=network_probe,
        policy_tests=network_evidence_policy_tests,
    )
    network_signed_probe = (
        network_evidence_report.get("signed_probe")
        if isinstance(network_evidence_report, dict)
        else None
    )
    network_signature_ok, network_report, network_signature_detail = (
        _verified_embedded_receipt(
            network_signed_probe,
            authorization_path=authorization_path,
            allowed_signers=network_trust_store if network_policy_ok else None,
            allowed_identities=network_probe_identities,
            namespace=NETWORK_SIGNATURE_NAMESPACE,
        )
    )
    network_raw_binding = False
    network_validation_detail = "signed network probe unavailable"
    try:
        if not isinstance(network_report, dict):
            raise ValueError(network_validation_detail)
        validate_network_probe_envelope(
            network_report,
            target_environment=str(target.get("target_id")),
            source_commit=str(release.get("git_commit")),
            backend_image=str(release.get("backend_image")),
            frontend_image=str(release.get("frontend_image")),
            exercise_id=str(network_evidence_report.get("exercise_id", ""))
            if isinstance(network_evidence_report, dict)
            else "",
            now=current,
        )
    except (TypeError, ValueError) as exc:
        network_validation_detail = str(exc)
    else:
        network_raw_binding = True
        network_validation_detail = "signed external network probe envelope validated"
    external_scan = (
        network_report.get("external_scan")
        if isinstance(network_report, dict) and isinstance(network_report.get("external_scan"), dict)
        else {}
    )
    policy_tests = (
        network_report.get("policy_tests")
        if isinstance(network_report, dict) and isinstance(network_report.get("policy_tests"), dict)
        else {}
    )
    public_scan = external_scan.get("public_ingress") if isinstance(external_scan.get("public_ingress"), dict) else {}
    private_scans = (
        external_scan.get("private_data_services")
        if isinstance(external_scan.get("private_data_services"), dict)
        else {}
    )
    cni = policy_tests.get("cni") if isinstance(policy_tests.get("cni"), dict) else {}
    probe_identities = policy_tests.get("probe_identities")
    ingress_tests = (
        policy_tests.get("ingress_tests")
        if isinstance(policy_tests.get("ingress_tests"), dict)
        else {}
    )
    egress_tests = (
        policy_tests.get("egress_tests")
        if isinstance(policy_tests.get("egress_tests"), dict)
        else {}
    )
    network_claims_match = isinstance(network_report, dict) and all(
        network_report.get(key) == network.get(key)
        for key in (
            "public_tcp_ports",
            "database_public",
            "redis_public",
            "object_store_direct_public",
            "default_deny_ingress",
            "egress_allowlist_enforced",
        )
    )
    public_hostname = urlparse(str(target.get("public_base_url", ""))).hostname
    public_scan_valid = bool(public_hostname) and _nmap_scan_valid(
        public_scan,
        requested_ports="1-65535",
        expected_open_ports=[443],
        expected_host=public_hostname,
    )
    private_scan_ports = {
        "database": 3306,
        "redis": 6379,
        "object_store_direct": 9000,
    }
    private_scans_valid = set(private_scans) == set(private_scan_ports) and all(
        isinstance(private_scans.get(name), dict)
        and _literal_ip(private_scans[name].get("host"))
        and _nmap_scan_valid(
            private_scans[name],
            requested_ports=str(port),
            expected_open_ports=[],
        )
        for name, port in private_scan_ports.items()
    )
    cni_images = cni.get("images")
    cni_desired = cni.get("desired")
    cni_ready = cni.get("ready")
    cni_valid = (
        _meaningful_string(cni.get("namespace"))
        and _meaningful_string(cni.get("name"))
        and _meaningful_string(cni.get("uid"))
        and _meaningful_string(cni.get("resource_version"))
        and isinstance(cni.get("generation"), int)
        and not isinstance(cni.get("generation"), bool)
        and cni.get("generation") >= 1
        and isinstance(cni_desired, int)
        and not isinstance(cni_desired, bool)
        and cni_desired > 0
        and cni_ready == cni_desired
        and isinstance(cni_images, list)
        and bool(cni_images)
        and all(isinstance(image, str) and bool(IMAGE_RE.fullmatch(image)) for image in cni_images)
    )
    cni_identity = (
        f"{cni.get('namespace')}/{cni.get('name')} {' '.join(cni_images)}"
        if cni_valid
        else "invalid"
    )
    probe_identities_valid = _network_probe_identities_valid(probe_identities)
    ingress_probe = probe_identities.get("ingress", {}) if isinstance(probe_identities, dict) else {}
    monitoring_probe = probe_identities.get("monitoring", {}) if isinstance(probe_identities, dict) else {}
    untrusted_probe = probe_identities.get("untrusted", {}) if isinstance(probe_identities, dict) else {}
    target_namespace = policy_tests.get("namespace")
    frontend_service = f"frontend.{target_namespace}.svc.cluster.local"
    backend_service = f"backend.{target_namespace}.svc.cluster.local"
    ingress_paths_valid = (
        probe_identities_valid
        and set(ingress_tests)
        == {
            "trusted_frontend_allowed",
            "trusted_backend_allowed",
            "untrusted_frontend_denied",
            "untrusted_backend_denied",
        }
        and _network_connection_result(
            ingress_tests.get("trusted_frontend_allowed"), connected=True, port=8080
        )
        and ingress_tests["trusted_frontend_allowed"].get("source_namespace") == ingress_probe.get("namespace")
        and ingress_tests["trusted_frontend_allowed"].get("source") == f"pod/{ingress_probe.get('pod')}"
        and ingress_tests["trusted_frontend_allowed"].get("destination_host") == frontend_service
        and _network_connection_result(
            ingress_tests.get("trusted_backend_allowed"), connected=True, port=8801
        )
        and ingress_tests["trusted_backend_allowed"].get("source_namespace") == monitoring_probe.get("namespace")
        and ingress_tests["trusted_backend_allowed"].get("source") == f"pod/{monitoring_probe.get('pod')}"
        and ingress_tests["trusted_backend_allowed"].get("destination_host") == backend_service
        and _network_connection_result(
            ingress_tests.get("untrusted_frontend_denied"), connected=False, port=8080
        )
        and ingress_tests["untrusted_frontend_denied"].get("source_namespace") == untrusted_probe.get("namespace")
        and ingress_tests["untrusted_frontend_denied"].get("source") == f"pod/{untrusted_probe.get('pod')}"
        and ingress_tests["untrusted_frontend_denied"].get("destination_host") == frontend_service
        and _network_connection_result(
            ingress_tests.get("untrusted_backend_denied"), connected=False, port=8801
        )
        and ingress_tests["untrusted_backend_denied"].get("source_namespace") == untrusted_probe.get("namespace")
        and ingress_tests["untrusted_backend_denied"].get("source") == f"pod/{untrusted_probe.get('pod')}"
        and ingress_tests["untrusted_backend_denied"].get("destination_host") == backend_service
    )
    approved_egress = (
        egress_tests.get("approved_destination_allowed")
        if isinstance(egress_tests.get("approved_destination_allowed"), dict)
        else {}
    )
    control_egress = (
        egress_tests.get("untrusted_control_destination_reachable")
        if isinstance(egress_tests.get("untrusted_control_destination_reachable"), dict)
        else {}
    )
    denied_egress = (
        egress_tests.get("unapproved_destination_denied")
        if isinstance(egress_tests.get("unapproved_destination_denied"), dict)
        else {}
    )
    approved_port = approved_egress.get("destination_port")
    unapproved_port = denied_egress.get("destination_port")
    egress_paths_valid = (
        set(egress_tests)
        == {
            "untrusted_control_destination_reachable",
            "approved_destination_allowed",
            "unapproved_destination_denied",
        }
        and isinstance(approved_port, int)
        and not isinstance(approved_port, bool)
        and 1 <= approved_port <= 65535
        and isinstance(unapproved_port, int)
        and not isinstance(unapproved_port, bool)
        and 1 <= unapproved_port <= 65535
        and _network_connection_result(approved_egress, connected=True, port=approved_port)
        and approved_egress.get("source_namespace") == target_namespace
        and approved_egress.get("source") == "deployment/backend"
        and _network_connection_result(control_egress, connected=True, port=unapproved_port)
        and control_egress.get("source_namespace") == untrusted_probe.get("namespace")
        and control_egress.get("source") == f"pod/{untrusted_probe.get('pod')}"
        and _network_connection_result(denied_egress, connected=False, port=unapproved_port)
        and denied_egress.get("source_namespace") == target_namespace
        and denied_egress.get("source") == "deployment/backend"
        and control_egress.get("destination_host") == denied_egress.get("destination_host")
        and control_egress.get("destination_port") == denied_egress.get("destination_port")
        and (approved_egress.get("destination_host"), approved_port)
        != (denied_egress.get("destination_host"), unapproved_port)
    )
    network_evidence_observed_at = _parse_time(
        network_evidence_report.get("observed_at")
        if isinstance(network_evidence_report, dict)
        else None
    )
    network_probe_observed_at = _parse_time(
        network_evidence_report.get("probe_observed_at")
        if isinstance(network_evidence_report, dict)
        else None
    )
    network_projection_keys = (
        "public_tcp_ports",
        "database_public",
        "redis_public",
        "object_store_direct_public",
        "default_deny_ingress",
        "egress_allowlist_enforced",
        "enforced_by",
        "external_scan",
        "policy_tests",
    )
    network_evidence_matches = (
        isinstance(network_evidence_report, dict)
        and isinstance(network_report, dict)
        and set(network_evidence_report)
        == {
            "schema_version",
            "scope",
            "target_environment",
            "source_commit",
            "images",
            "status",
            "passed",
            "observed_at",
            "probe_observed_at",
            "exercise_id",
            "probe",
            "public_tcp_ports",
            "database_public",
            "redis_public",
            "object_store_direct_public",
            "default_deny_ingress",
            "egress_allowlist_enforced",
            "enforced_by",
            "external_scan",
            "policy_tests",
            "network_policy",
            "signed_probe",
        }
        and _report_release_target_binding(
            network_evidence_report,
            schema_version=NETWORK_EVIDENCE_SCHEMA_VERSION,
            status="PASS",
            control=network,
            target=target,
            release=release,
        )
        and network_evidence_report.get("passed") is True
        and network_policy_ok
        and network_signature_ok
        and network_raw_binding
        and network_evidence_report.get("exercise_id") == network_report.get("exercise_id")
        and network_evidence_report.get("probe") == network_report.get("probe")
        and network_evidence_report.get("probe_observed_at") == network_report.get("observed_at")
        and network_probe_observed_at is not None
        and network_evidence_observed_at is not None
        and timedelta(0)
        <= network_evidence_observed_at - network_probe_observed_at
        <= timedelta(minutes=5)
        and all(
            network_evidence_report.get(key) == network_report.get(key)
            for key in network_projection_keys
        )
        and not _contains_secret_material_key(network_evidence_report)
    )
    network_report_matches = (
        network_raw_binding
        and network_evidence_matches
        and network_claims_match
        and network_report.get("passed") is True
        and network_report.get("enforced_by") == cni_identity
        and external_scan.get("transport") == "nmap TCP scan from acknowledged external vantage"
        and _meaningful_string(external_scan.get("scanner_id"))
        and _literal_ip(external_scan.get("scanner_source_ip"), globally_routable=True)
        and public_scan_valid
        and private_scans_valid
        and external_scan.get("discovered_tcp_ports") == [443]
        and external_scan.get("private_data_services_unreachable") is True
        and external_scan.get("passed") is True
        and _meaningful_string(policy_tests.get("cluster_context"))
        and _meaningful_string(target_namespace)
        and cni_valid
        and probe_identities_valid
        and _network_policy_snapshots_valid(policy_tests.get("network_policies"))
        and policy_tests.get("required_policies_present") is True
        and policy_tests.get("policy_snapshots_valid") is True
        and policy_tests.get("broad_world_egress_absent") is True
        and ingress_paths_valid
        and egress_paths_valid
        and policy_tests.get("default_deny_ingress_exercised") is True
        and policy_tests.get("unapproved_egress_denied") is True
        and policy_tests.get("approved_egress_allowed") is True
        and policy_tests.get("private_data_services_unreachable_externally") is True
        and policy_tests.get("passed") is True
    )
    network_ok = (
        network.get("status") == "PASS"
        and network.get("public_tcp_ports") == [443]
        and network.get("database_public") is False
        and network.get("redis_public") is False
        and network.get("object_store_direct_public") is False
        and network.get("default_deny_ingress") is True
        and network.get("egress_allowlist_enforced") is True
        and network_report_matches
    )
    gate.add(
        "network_probe_signature",
        owner="Security",
        passed=network_policy_ok and network_signature_ok and network_raw_binding,
        observed=(
            f"probe={network_probe}, validation={network_validation_detail}, "
            f"signature={network_signature_detail}"
        ),
        expected=(
            "release-authority-approved external probe, source CIDR, target cluster and signed "
            "raw network observations"
        ),
        detail=(
            "The v3 gate reopens the signed probe before recomputing raw nmap XML, CNI identity, "
            f"NetworkPolicies and ingress/egress paths. {network_policy_detail}"
        ),
    )
    gate.add(
        "network",
        owner="Security",
        passed=network_ok,
        observed=f"status={network.get('status')}, public_ports={network.get('public_tcp_ports')}, egress={network.get('egress_allowlist_enforced')}",
        expected="only 443 public; data stores private; default deny; target egress allowlist",
        detail="The release-bound target report must include an outside TCP scan plus exercised ingress and egress policy paths; YAML declarations alone do not pass.",
    )

    alerting = controls["alerting"]
    alerting_report = _evidence_json(alerting, authorization_path)
    alerting_schedule = alerting.get("oncall_schedule")
    alert_exercise = (
        alerting_report.get("alert_exercise")
        if isinstance(alerting_report, dict)
        and isinstance(alerting_report.get("alert_exercise"), dict)
        else {}
    )
    alerting_policy_ok, _, alerting_trust_store, delivery_identities, oncall_identities, alerting_policy_detail = (
        _alerting_policy_context(
            alerting_report.get("alerting_policy") if isinstance(alerting_report, dict) else None,
            authorization_path=authorization_path,
            schedule=alerting_schedule,
        )
    )
    firing_embedded = alerting_report.get("firing_receipt") if isinstance(alerting_report, dict) else None
    resolved_embedded = alerting_report.get("resolved_receipt") if isinstance(alerting_report, dict) else None
    acknowledgement_embedded = (
        alerting_report.get("oncall_acknowledgement") if isinstance(alerting_report, dict) else None
    )
    firing_signature_ok, firing_receipt, firing_signature_detail = _verified_embedded_receipt(
        firing_embedded,
        authorization_path=authorization_path,
        allowed_signers=alerting_trust_store,
        allowed_identities=delivery_identities,
        namespace=ALERT_DELIVERY_SIGNATURE_NAMESPACE,
    )
    resolved_signature_ok, resolved_receipt, resolved_signature_detail = _verified_embedded_receipt(
        resolved_embedded,
        authorization_path=authorization_path,
        allowed_signers=alerting_trust_store,
        allowed_identities=delivery_identities,
        namespace=ALERT_DELIVERY_SIGNATURE_NAMESPACE,
    )
    acknowledgement_signature_ok, acknowledgement, acknowledgement_signature_detail = _verified_embedded_receipt(
        acknowledgement_embedded,
        authorization_path=authorization_path,
        allowed_signers=alerting_trust_store,
        allowed_identities=oncall_identities,
        namespace=ONCALL_ACK_SIGNATURE_NAMESPACE,
    )
    exercise_id = alert_exercise.get("exercise_id")
    alert_name = alert_exercise.get("alert_name")
    labels = alert_exercise.get("labels")
    firing_signer = (
        firing_embedded.get("signed_evidence", {}).get("signer_identity")
        if isinstance(firing_embedded, dict)
        and isinstance(firing_embedded.get("signed_evidence"), dict)
        else None
    )
    resolved_signer = (
        resolved_embedded.get("signed_evidence", {}).get("signer_identity")
        if isinstance(resolved_embedded, dict)
        and isinstance(resolved_embedded.get("signed_evidence"), dict)
        else None
    )
    acknowledgement_signer = (
        acknowledgement_embedded.get("signed_evidence", {}).get("signer_identity")
        if isinstance(acknowledgement_embedded, dict)
        and isinstance(acknowledgement_embedded.get("signed_evidence"), dict)
        else None
    )
    firing_receipt_valid = _alert_delivery_receipt_valid(
        firing_receipt,
        exercise_id=exercise_id,
        target_environment=target.get("target_id"),
        alert_name=alert_name,
        schedule=alerting_schedule,
        event="firing",
    )
    resolved_receipt_valid = _alert_delivery_receipt_valid(
        resolved_receipt,
        exercise_id=exercise_id,
        target_environment=target.get("target_id"),
        alert_name=alert_name,
        schedule=alerting_schedule,
        event="resolved",
    )
    acknowledgement_valid = _oncall_acknowledgement_valid(
        acknowledgement,
        exercise_id=exercise_id,
        target_environment=target.get("target_id"),
        alert_name=alert_name,
        schedule=alerting_schedule,
        signer_identity=acknowledgement_signer,
    )
    firing_api = alert_exercise.get("firing_api")
    active_observation = alert_exercise.get("active_observation")
    resolved_api = alert_exercise.get("resolved_api")
    inactive_observation = alert_exercise.get("inactive_observation")
    exercise_started = _parse_time(alert_exercise.get("started_at"))
    firing_expires = _parse_time(alert_exercise.get("firing_expires_at"))
    resolve_requested = _parse_time(alert_exercise.get("resolve_requested_at"))
    report_observed = _parse_time(alerting_report.get("observed_at")) if isinstance(alerting_report, dict) else None
    firing_delivered = _parse_time(firing_receipt.get("delivered_at")) if isinstance(firing_receipt, dict) else None
    acknowledged_at = _parse_time(acknowledgement.get("acknowledged_at")) if isinstance(acknowledgement, dict) else None
    resolved_delivered = _parse_time(resolved_receipt.get("delivered_at")) if isinstance(resolved_receipt, dict) else None
    firing_api_at = _parse_time(firing_api.get("observed_at")) if isinstance(firing_api, dict) else None
    active_at = _parse_time(active_observation.get("observed_at")) if isinstance(active_observation, dict) else None
    resolved_api_at = _parse_time(resolved_api.get("observed_at")) if isinstance(resolved_api, dict) else None
    inactive_at = _parse_time(inactive_observation.get("observed_at")) if isinstance(inactive_observation, dict) else None
    firing_target_times = (
        [_parse_time(item.get("delivered_at")) for item in firing_receipt["deliveries"]]
        if isinstance(firing_receipt, dict)
        and isinstance(firing_receipt.get("deliveries"), list)
        and all(isinstance(item, dict) for item in firing_receipt["deliveries"])
        else []
    )
    resolved_target_times = (
        [_parse_time(item.get("delivered_at")) for item in resolved_receipt["deliveries"]]
        if isinstance(resolved_receipt, dict)
        and isinstance(resolved_receipt.get("deliveries"), list)
        and all(isinstance(item, dict) for item in resolved_receipt["deliveries"])
        else []
    )
    timeline_values = (
        exercise_started,
        firing_expires,
        firing_api_at,
        active_at,
        firing_delivered,
        acknowledged_at,
        resolve_requested,
        resolved_api_at,
        inactive_at,
        resolved_delivered,
        report_observed,
    )
    timeline_complete = all(value is not None for value in timeline_values)
    alerting_timeline_valid = (
        timeline_complete
        and exercise_started <= firing_api_at <= active_at <= report_observed
        and len(firing_target_times) >= 2
        and all(
            item is not None and exercise_started <= item <= firing_delivered
            for item in firing_target_times
        )
        and exercise_started <= firing_delivered <= acknowledged_at <= resolve_requested
        and resolve_requested < firing_expires
        and resolve_requested <= resolved_api_at <= inactive_at <= report_observed
        and len(resolved_target_times) >= 2
        and all(
            item is not None and resolve_requested <= item <= resolved_delivered
            for item in resolved_target_times
        )
        and resolve_requested <= resolved_delivered <= report_observed
    )
    receipt_id_values = [
        item.get("receipt_id")
        for item in (firing_receipt, acknowledgement, resolved_receipt)
        if isinstance(item, dict)
    ]
    receipt_ids_unique = (
        len(receipt_id_values) == 3
        and all(isinstance(value, str) for value in receipt_id_values)
        and len(set(receipt_id_values)) == 3
    )
    firing_targets = (
        {(item.get("channel"), item.get("receiver")) for item in firing_receipt.get("deliveries", [])}
        if isinstance(firing_receipt, dict)
        and isinstance(firing_receipt.get("deliveries"), list)
        and all(
            isinstance(item, dict)
            and isinstance(item.get("channel"), str)
            and isinstance(item.get("receiver"), str)
            for item in firing_receipt["deliveries"]
        )
        else set()
    )
    resolved_targets = (
        {(item.get("channel"), item.get("receiver")) for item in resolved_receipt.get("deliveries", [])}
        if isinstance(resolved_receipt, dict)
        and isinstance(resolved_receipt.get("deliveries"), list)
        and all(
            isinstance(item, dict)
            and isinstance(item.get("channel"), str)
            and isinstance(item.get("receiver"), str)
            for item in resolved_receipt["deliveries"]
        )
        else set()
    )
    firing_provider_ids = (
        {item.get("provider_receipt_id") for item in firing_receipt.get("deliveries", [])}
        if isinstance(firing_receipt, dict)
        and isinstance(firing_receipt.get("deliveries"), list)
        and all(
            isinstance(item, dict) and isinstance(item.get("provider_receipt_id"), str)
            for item in firing_receipt["deliveries"]
        )
        else set()
    )
    resolved_provider_ids = (
        {item.get("provider_receipt_id") for item in resolved_receipt.get("deliveries", [])}
        if isinstance(resolved_receipt, dict)
        and isinstance(resolved_receipt.get("deliveries"), list)
        and all(
            isinstance(item, dict) and isinstance(item.get("provider_receipt_id"), str)
            for item in resolved_receipt["deliveries"]
        )
        else set()
    )
    alerting_report_matches = (
        _report_release_target_binding(
            alerting_report,
            schema_version=ALERTING_SCHEMA_VERSION,
            status="PASS",
            control=alerting,
            target=target,
            release=release,
        )
        and alerting_report.get("test_notification_delivered") is alerting.get("test_notification_delivered")
        and alerting_report.get("resolved_notification_delivered") is alerting.get("resolved_notification_delivered")
        and alerting_report.get("oncall_schedule") == alerting_schedule
        and alerting_policy_ok
        and firing_signature_ok
        and resolved_signature_ok
        and acknowledgement_signature_ok
        and firing_receipt_valid
        and resolved_receipt_valid
        and acknowledgement_valid
        and receipt_ids_unique
        and len(firing_targets) >= 2
        and firing_targets == resolved_targets
        and len(firing_provider_ids) >= 2
        and len(resolved_provider_ids) >= 2
        and not firing_provider_ids.intersection(resolved_provider_ids)
        and firing_signer == resolved_signer
        and firing_signer != acknowledgement_signer
        and _meaningful_string(exercise_id)
        and alert_name == f"DuckDockGA_{exercise_id}"
        and isinstance(labels, dict)
        and labels
        == {
            "alertname": alert_name,
            "severity": "critical",
            "duckdock_target": target.get("target_id"),
            "duckdock_exercise": exercise_id,
        }
        and _https_url(alert_exercise.get("alertmanager_url"))
        and _alertmanager_post_observation(
            firing_api,
            labels=labels,
            starts_at=exercise_started,
            ends_at=firing_expires,
        )
        and _alertmanager_api_observation(
            active_observation,
            active=True,
            alert_name=alert_name,
        )
        and _alertmanager_post_observation(
            resolved_api,
            labels=labels,
            starts_at=exercise_started,
            ends_at=resolve_requested,
        )
        and _alertmanager_api_observation(
            inactive_observation,
            active=False,
            alert_name=alert_name,
        )
        and firing_api.get("request_sha256") != resolved_api.get("request_sha256")
        and alerting_timeline_valid
    )
    alerting_ok = (
        alerting.get("status") == "PASS"
        and alerting.get("test_notification_delivered") is True
        and alerting.get("resolved_notification_delivered") is True
        and isinstance(alerting.get("oncall_schedule"), str)
        and not any(marker in str(alerting.get("oncall_schedule")) for marker in PLACEHOLDER_MARKERS)
        and alerting_report_matches
    )
    gate.add(
        "alerting",
        owner="Operations",
        passed=alerting_ok,
        observed=f"status={alerting.get('status')}, fired={alerting.get('test_notification_delivered')}, resolved={alerting.get('resolved_notification_delivered')}",
        expected="firing + resolved notifications delivered to a named on-call schedule",
        detail=(
            "The target collector must actively observe the same Alertmanager alert firing/resolving and retain "
            "separately signed delivery/on-call receipts under an exact, content-addressed trust policy. "
            f"policy=({alerting_policy_detail}); firing=({firing_signature_detail}); "
            f"ack=({acknowledgement_signature_detail}); resolved=({resolved_signature_detail})"
        ),
    )

    recovery = controls["recovery"]
    target_rpo = int(target.get("maximum_rpo_seconds", 900))
    target_rto = int(target.get("maximum_rto_seconds", 14_400))
    recovery_report = _evidence_json(recovery, authorization_path)
    backup_report = (
        recovery_report.get("backup")
        if isinstance(recovery_report, dict) and isinstance(recovery_report.get("backup"), dict)
        else {}
    )
    signed_backup_ok, signed_backup_detail = _verify_signed_evidence_file(
        backup_report,
        authorization_path=authorization_path,
        namespace="duckdock-backup",
    )
    backup_manifest = _signed_backup_manifest(
        backup_report,
        authorization_path=authorization_path,
    )
    backup_manifest_ok = _backup_manifest_matches_release(
        backup_manifest,
        release_commit=commit,
    )
    gate.add(
        "recovery_backup_signature",
        owner="Operations",
        passed=signed_backup_ok and backup_manifest_ok,
        observed=signed_backup_detail,
        expected="release-bound duckdock-secure-backup-v1 manifest with valid duckdock-backup OpenSSH signature",
        detail="The gate verifies the retained manifest bytes, signer identity, encrypted/plaintext artifact metadata and exact release commit; a signature_verified boolean is not evidence.",
    )
    storage_provider = (
        recovery_report.get("storage_provider") if isinstance(recovery_report, dict) else None
    )
    (
        recovery_policy_ok,
        recovery_trust_store,
        storage_identities,
        restore_identities,
        verification_identities,
        recovery_policy_detail,
    ) = _recovery_policy_context(
        recovery_report.get("recovery_policy") if isinstance(recovery_report, dict) else None,
        authorization_path=authorization_path,
        storage_provider=storage_provider,
    )
    media_embedded = (
        recovery_report.get("media_receipt") if isinstance(recovery_report, dict) else None
    )
    restore_embedded = (
        recovery_report.get("restore_receipt") if isinstance(recovery_report, dict) else None
    )
    verification_embedded = (
        recovery_report.get("verification_receipt")
        if isinstance(recovery_report, dict)
        else None
    )
    media_signature_ok, media_receipt, media_signature_detail = _verified_embedded_receipt(
        media_embedded,
        authorization_path=authorization_path,
        allowed_signers=recovery_trust_store,
        allowed_identities=storage_identities,
        namespace=BACKUP_MEDIA_SIGNATURE_NAMESPACE,
    )
    restore_signature_ok, restore_receipt, restore_signature_detail = (
        _verified_embedded_receipt(
            restore_embedded,
            authorization_path=authorization_path,
            allowed_signers=recovery_trust_store,
            allowed_identities=restore_identities,
            namespace=RESTORE_EXECUTION_SIGNATURE_NAMESPACE,
        )
    )
    verification_signature_ok, verification_receipt, verification_signature_detail = (
        _verified_embedded_receipt(
            verification_embedded,
            authorization_path=authorization_path,
            allowed_signers=recovery_trust_store,
            allowed_identities=verification_identities,
            namespace=RECOVERY_VERIFICATION_SIGNATURE_NAMESPACE,
        )
    )
    exercise = (
        recovery_report.get("exercise")
        if isinstance(recovery_report, dict) and isinstance(recovery_report.get("exercise"), dict)
        else {}
    )
    exercise_id = exercise.get("exercise_id")
    restore_target_environment = exercise.get("restore_target_environment")
    manifest_digest = backup_report.get("manifest_sha256")
    media_receipt_valid = _backup_media_receipt_valid(
        media_receipt,
        exercise_id=exercise_id,
        target_environment=target.get("target_id"),
        storage_provider=storage_provider,
        manifest_sha256=manifest_digest,
    )
    restore_receipt_valid = _restore_execution_receipt_valid(
        restore_receipt,
        exercise_id=exercise_id,
        production_target_environment=target.get("target_id"),
        restore_target_environment=restore_target_environment,
        manifest_sha256=manifest_digest,
    )
    verification_receipt_valid = _recovery_verification_receipt_valid(
        verification_receipt,
        exercise_id=exercise_id,
        restore_target_environment=restore_target_environment,
        manifest_sha256=manifest_digest,
    )
    role_signers = [
        embedded.get("signed_evidence", {}).get("signer_identity")
        if isinstance(embedded, dict)
        and isinstance(embedded.get("signed_evidence"), dict)
        else None
        for embedded in (media_embedded, restore_embedded, verification_embedded)
    ]
    role_signers_distinct = (
        all(_meaningful_string(identity) for identity in role_signers)
        and len(set(role_signers)) == 3
    )
    manifest_created = (
        _parse_time(backup_manifest.get("created_at"))
        if isinstance(backup_manifest, dict)
        else None
    )
    recovery_point = (
        _parse_time(media_receipt.get("recovery_point_at"))
        if isinstance(media_receipt, dict)
        else None
    )
    media_completed = (
        _parse_time(media_receipt.get("completed_at"))
        if isinstance(media_receipt, dict)
        else None
    )
    failure_at = (
        _parse_time(restore_receipt.get("failure_injected_at"))
        if isinstance(restore_receipt, dict)
        else None
    )
    restore_started = (
        _parse_time(restore_receipt.get("started_at"))
        if isinstance(restore_receipt, dict)
        else None
    )
    restore_completed = (
        _parse_time(restore_receipt.get("completed_at"))
        if isinstance(restore_receipt, dict)
        else None
    )
    verification_started = (
        _parse_time(verification_receipt.get("started_at"))
        if isinstance(verification_receipt, dict)
        else None
    )
    verification_completed = (
        _parse_time(verification_receipt.get("completed_at"))
        if isinstance(verification_receipt, dict)
        else None
    )
    exercise_started = _parse_time(exercise.get("started_at"))
    exercise_completed = _parse_time(exercise.get("completed_at"))
    report_observed = (
        _parse_time(recovery_report.get("observed_at"))
        if isinstance(recovery_report, dict)
        else None
    )
    recovery_timeline = (
        all(
            value is not None
            for value in (
                recovery_point,
                manifest_created,
                media_completed,
                failure_at,
                restore_started,
                restore_completed,
                verification_started,
                verification_completed,
                exercise_started,
                exercise_completed,
                report_observed,
            )
        )
        and recovery_point <= manifest_created <= media_completed
        and recovery_point <= failure_at <= restore_started <= restore_completed
        and restore_completed <= verification_started <= verification_completed
        and exercise_started - timedelta(minutes=5) <= media_completed
        and exercise_started - timedelta(minutes=5) <= restore_completed
        and exercise_started - timedelta(minutes=5) <= verification_completed
        and verification_completed <= exercise_completed <= report_observed
    )
    measured_rpo = (
        math.ceil((failure_at - recovery_point).total_seconds())
        if failure_at is not None and recovery_point is not None
        else -1
    )
    measured_rto = (
        math.ceil((verification_completed - failure_at).total_seconds())
        if verification_completed is not None and failure_at is not None
        else -1
    )
    mysql_rows = (
        verification_receipt.get("mysql", {}).get("rows_verified")
        if isinstance(verification_receipt, dict)
        and isinstance(verification_receipt.get("mysql"), dict)
        else None
    )
    objects_verified = (
        verification_receipt.get("object_store", {}).get("objects_verified")
        if isinstance(verification_receipt, dict)
        and isinstance(verification_receipt.get("object_store"), dict)
        else None
    )
    git_repository_count = (
        verification_receipt.get("git", {}).get("repositories_verified")
        if isinstance(verification_receipt, dict)
        and isinstance(verification_receipt.get("git"), dict)
        else None
    )
    media_artifacts = (
        {
            item.get("name"): item
            for item in media_receipt.get("artifact_versions", [])
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        if isinstance(media_receipt, dict)
        and isinstance(media_receipt.get("artifact_versions"), list)
        else {}
    )
    manifest_artifacts = (
        backup_manifest.get("artifacts")
        if isinstance(backup_manifest, dict)
        and isinstance(backup_manifest.get("artifacts"), dict)
        else {}
    )
    offsite_artifacts_match = (
        set(media_artifacts) == set(RECOVERY_REQUIRED_ARTIFACTS)
        and set(manifest_artifacts) == set(RECOVERY_REQUIRED_ARTIFACTS)
        and all(
            media_artifacts[name].get("sha256")
            == manifest_artifacts[name].get("encrypted_sha256")
            and media_artifacts[name].get("size_bytes")
            == manifest_artifacts[name].get("encrypted_size_bytes")
            for name in RECOVERY_REQUIRED_ARTIFACTS
        )
    )
    recovery_claims_match = isinstance(recovery_report, dict) and all(
        recovery_report.get(key) == recovery.get(key)
        for key in (
            "offsite_media",
            "encrypted",
            "immutable_or_object_locked",
            "rpo_seconds",
            "rto_seconds",
            "mysql_rows_verified",
            "objects_verified",
            "git_repositories_verified",
        )
    )
    recovery_report_matches = (
        _report_release_target_binding(
            recovery_report,
            schema_version=RECOVERY_SCHEMA_VERSION,
            status="PASSED",
            control=recovery,
            target=target,
            release=release,
        )
        and recovery_claims_match
        and backup_report.get("manifest_schema_version") == "duckdock-secure-backup-v1"
        and bool(DIGEST_RE.fullmatch(str(backup_report.get("manifest_sha256", ""))))
        and signed_backup_ok
        and backup_manifest_ok
        and backup_report.get("decryption_key_external") is True
        and _meaningful_string(storage_provider)
        and recovery_policy_ok
        and media_signature_ok
        and restore_signature_ok
        and verification_signature_ok
        and role_signers_distinct
        and media_receipt_valid
        and restore_receipt_valid
        and verification_receipt_valid
        and offsite_artifacts_match
        and _meaningful_string(exercise_id)
        and exercise.get("production_target_environment") == target.get("target_id")
        and _meaningful_string(restore_target_environment)
        and restore_target_environment != target.get("target_id")
        and exercise.get("maximum_rpo_seconds") == target_rpo
        and exercise.get("maximum_rto_seconds") == target_rto
        and recovery_timeline
        and recovery_report.get("rpo_seconds") == measured_rpo
        and recovery_report.get("rto_seconds") == measured_rto
        and recovery_report.get("mysql_rows_verified") == mysql_rows
        and recovery_report.get("objects_verified") == objects_verified
        and recovery_report.get("git_repositories_verified")
        is (isinstance(git_repository_count, int) and git_repository_count > 0)
        and not _contains_secret_material_key(recovery_report)
    )
    recovery_ok = (
        recovery.get("status") == "PASSED"
        and recovery.get("offsite_media") is True
        and recovery.get("encrypted") is True
        and recovery.get("immutable_or_object_locked") is True
        and int(recovery.get("rpo_seconds", target_rpo + 1)) <= target_rpo
        and int(recovery.get("rto_seconds", target_rto + 1)) <= target_rto
        and int(recovery.get("mysql_rows_verified", 0)) > 0
        and int(recovery.get("objects_verified", 0)) > 0
        and recovery.get("git_repositories_verified") is True
        and recovery_report_matches
    )
    gate.add(
        "recovery",
        owner="Operations",
        passed=recovery_ok,
        observed=f"status={recovery.get('status')}, RPO={recovery.get('rpo_seconds')}, RTO={recovery.get('rto_seconds')}",
        expected=f"offsite encrypted immutable restore; RPO<={target_rpo}s; RTO<={target_rto}s; DB/object/Git verified",
        detail=(
            "The v2 collector must retain the signed backup manifest plus separate storage, restore "
            "executor and independent verification receipts. RPO/RTO and offsite artifact identity "
            "are recomputed from raw evidence. "
            f"policy=({recovery_policy_detail}); media=({media_signature_detail}); "
            f"restore=({restore_signature_detail}); verification=({verification_signature_detail})"
        ),
    )

    capacity = controls["capacity"]
    minimum_sustained_rps = float(target.get("minimum_sustained_rps", 50))
    capacity_report = _evidence_json(capacity, authorization_path)
    database_provider = (
        capacity_report.get("database_provider") if isinstance(capacity_report, dict) else None
    )
    capacity_policy_reference = (
        capacity_report.get("capacity_policy")
        if isinstance(capacity_report, dict)
        and isinstance(capacity_report.get("capacity_policy"), dict)
        else {}
    )
    (
        capacity_policy_ok,
        capacity_policy,
        capacity_trust_store,
        load_identities,
        storage_identities,
        cleanup_identities,
        capacity_policy_detail,
    ) = _capacity_policy_context(
        capacity_policy_reference,
        authorization_path=authorization_path,
        database_provider=database_provider,
    )
    load_embedded = capacity_report.get("load_report") if isinstance(capacity_report, dict) else None
    growth_embedded = (
        capacity_report.get("growth_receipt") if isinstance(capacity_report, dict) else None
    )
    cleanup_embedded = (
        capacity_report.get("cleanup_receipt") if isinstance(capacity_report, dict) else None
    )
    load_signature_ok, raw_load_report, load_signature_detail = _verified_embedded_receipt(
        load_embedded,
        authorization_path=authorization_path,
        allowed_signers=capacity_trust_store if capacity_policy_ok else None,
        allowed_identities=load_identities,
        namespace=CAPACITY_LOAD_SIGNATURE_NAMESPACE,
    )
    growth_signature_ok, raw_growth_receipt, growth_signature_detail = (
        _verified_embedded_receipt(
            growth_embedded,
            authorization_path=authorization_path,
            allowed_signers=capacity_trust_store if capacity_policy_ok else None,
            allowed_identities=storage_identities,
            namespace=CAPACITY_GROWTH_SIGNATURE_NAMESPACE,
        )
    )
    cleanup_signature_ok, raw_cleanup_receipt, cleanup_signature_detail = (
        _verified_embedded_receipt(
            cleanup_embedded,
            authorization_path=authorization_path,
            allowed_signers=capacity_trust_store if capacity_policy_ok else None,
            allowed_identities=cleanup_identities,
            namespace=CAPACITY_CLEANUP_SIGNATURE_NAMESPACE,
        )
    )
    capacity_signers = [
        embedded.get("signed_evidence", {}).get("signer_identity")
        if isinstance(embedded, dict) and isinstance(embedded.get("signed_evidence"), dict)
        else None
        for embedded in (load_embedded, growth_embedded, cleanup_embedded)
    ]
    capacity_signers_distinct = (
        all(_meaningful_string(identity) for identity in capacity_signers)
        and len(set(capacity_signers)) == 3
    )
    exercise = (
        capacity_report.get("exercise")
        if isinstance(capacity_report, dict) and isinstance(capacity_report.get("exercise"), dict)
        else {}
    )
    requirements = (
        capacity_report.get("requirements")
        if isinstance(capacity_report, dict)
        and isinstance(capacity_report.get("requirements"), dict)
        else {}
    )
    data_growth = (
        capacity_report.get("data_growth")
        if isinstance(capacity_report, dict) and isinstance(capacity_report.get("data_growth"), dict)
        else {}
    )
    derived_load: dict[str, Any] = {}
    derived_growth: dict[str, Any] = {}
    derived_cleanup: dict[str, Any] = {}
    capacity_validation_detail = "signed capacity receipts unavailable"
    try:
        if not (raw_load_report and raw_growth_receipt and raw_cleanup_receipt):
            raise ValueError(capacity_validation_detail)
        derived_load = validate_capacity_load_report(
            raw_load_report,
            target_environment=str(target.get("target_id")),
            source_commit=str(release.get("git_commit")),
            backend_image=str(release.get("backend_image")),
            frontend_image=str(release.get("frontend_image")),
            base_url=str(target.get("public_base_url")),
            namespace_id=int(capacity_report.get("namespace_id", 0)),
            exercise_id=str(exercise.get("exercise_id", "")),
            now=current,
        )
        derived_growth = validate_capacity_growth_receipt(
            raw_growth_receipt,
            load=derived_load,
            target_environment=str(target.get("target_id")),
            source_commit=str(release.get("git_commit")),
            backend_image=str(release.get("backend_image")),
            frontend_image=str(release.get("frontend_image")),
            namespace_id=int(capacity_report.get("namespace_id", 0)),
            exercise_id=str(exercise.get("exercise_id", "")),
            database_provider=str(database_provider),
        )
        derived_cleanup = validate_capacity_cleanup_receipt(
            raw_cleanup_receipt,
            load=derived_load,
            growth=derived_growth,
            target_environment=str(target.get("target_id")),
            source_commit=str(release.get("git_commit")),
            backend_image=str(release.get("backend_image")),
            frontend_image=str(release.get("frontend_image")),
            namespace_id=int(capacity_report.get("namespace_id", 0)),
            exercise_id=str(exercise.get("exercise_id", "")),
        )
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        capacity_validation_detail = str(exc)
    else:
        capacity_validation_detail = "load, data growth and cleanup receipts validated"
    growth_passed = (
        bool(derived_growth)
        and derived_growth.get("counts_monotonic") is True
        and derived_growth.get("agent_runs_delta") == derived_load.get("materialized_runs")
        and derived_growth.get("tagged_agent_runs") == derived_load.get("materialized_runs")
        and int(derived_growth.get("audit_logs_delta", -1))
        >= int(derived_load.get("materialized_runs", 0))
        and int(derived_growth.get("outbox_events_delta", -1))
        >= int(derived_load.get("materialized_runs", 0))
        and int(derived_growth.get("database_growth_bytes", -1))
        >= int(capacity_policy.get("minimum_database_growth_bytes", 0))
        and int(derived_growth.get("pending_outbox_events", 1))
        <= int(capacity_policy.get("maximum_pending_outbox_events", -1))
        and float(derived_growth.get("replica_lag_seconds", 1e12))
        <= float(capacity_policy.get("maximum_replica_lag_seconds", -1))
    )
    requirement_numeric_fields = {
        "minimum_sustained_seconds",
        "minimum_sustained_rps",
        "minimum_materialized_runs",
        "maximum_error_rate",
        "maximum_write_p95_ms",
        "maximum_timeline_p95_ms",
    }
    requirements_types_valid = all(
        isinstance(requirements.get(field), (int, float))
        and not isinstance(requirements.get(field), bool)
        and math.isfinite(float(requirements[field]))
        for field in requirement_numeric_fields
    )
    requirements_valid = (
        set(requirements)
        == requirement_numeric_fields
        and requirements_types_valid
        and float(requirements.get("minimum_sustained_seconds", 0)) >= 900
        and float(requirements.get("minimum_sustained_rps", 0)) >= minimum_sustained_rps
        and int(requirements.get("minimum_materialized_runs", 0)) >= 50_000
        and float(requirements.get("maximum_error_rate", 1)) <= 0.001
        and float(requirements.get("maximum_write_p95_ms", 1e12)) <= 1_000
        and float(requirements.get("maximum_timeline_p95_ms", 1e12)) <= 250
    )
    expected_growth_projection = (
        {
            "agent_runs_delta": derived_growth.get("agent_runs_delta"),
            "audit_logs_delta": derived_growth.get("audit_logs_delta"),
            "outbox_events_delta": derived_growth.get("outbox_events_delta"),
            "tagged_agent_runs": derived_growth.get("tagged_agent_runs"),
            "database_growth_bytes": derived_growth.get("database_growth_bytes"),
            "pending_outbox_events": derived_growth.get("pending_outbox_events"),
            "replica_lag_seconds": derived_growth.get("replica_lag_seconds"),
            "counts_monotonic": derived_growth.get("counts_monotonic"),
        }
        if derived_growth
        else {}
    )
    report_matches_target = (
        isinstance(capacity_report, dict)
        and set(capacity_report)
        == {
            "schema_version",
            "scope",
            "target_environment",
            "source_commit",
            "images",
            "status",
            "passed",
            "observed_at",
            "base_url",
            "namespace_id",
            "database_provider",
            "transport",
            "exercise",
            "requirements",
            "phases",
            "offered_runs",
            "materialized_runs",
            "failure_count",
            "error_rate",
            "post_growth_timeline_query",
            "data_growth",
            "cleanup_verified",
            "capacity_policy",
            "load_report",
            "growth_receipt",
            "cleanup_receipt",
        }
        and _report_release_target_binding(
            capacity_report,
            schema_version=CAPACITY_SCHEMA_VERSION,
            status="PASSED",
            control=capacity,
            target=target,
            release=release,
        )
        and capacity_report.get("transport") == "network HTTPS against target"
        and str(capacity_report.get("base_url", "")).rstrip("/")
        == str(target.get("public_base_url", "")).rstrip("/")
        and capacity_report.get("database_provider") == capacity.get("database_provider")
        and capacity_policy_ok
        and load_signature_ok
        and growth_signature_ok
        and cleanup_signature_ok
        and capacity_signers_distinct
        and bool(derived_load)
        and bool(derived_growth)
        and bool(derived_cleanup)
        and requirements_valid
        and exercise
        == {
            "exercise_id": raw_load_report.get("exercise_id"),
            "run_tag": raw_load_report.get("run_tag"),
            "started_at": raw_load_report.get("started_at"),
            "finished_at": raw_load_report.get("finished_at"),
            "completed_at": capacity_report.get("observed_at"),
        }
        and _parse_time(exercise.get("completed_at")) is not None
        and derived_cleanup.get("completed_at") <= _parse_time(exercise.get("completed_at"))
        and capacity_report.get("phases") == raw_load_report.get("phases")
        and capacity_report.get("offered_runs") == derived_load.get("offered_runs")
        and capacity_report.get("materialized_runs") == derived_load.get("materialized_runs")
        and capacity_report.get("failure_count") == raw_load_report.get("failure_count")
        and capacity_report.get("error_rate") == derived_load.get("error_rate")
        and capacity_report.get("post_growth_timeline_query")
        == raw_load_report.get("post_growth_timeline_query")
        and data_growth == expected_growth_projection
        and capacity_report.get("cleanup_verified") is derived_cleanup.get("passed")
        and growth_passed
        and derived_cleanup.get("passed") is True
        and not _contains_secret_material_key(capacity_report)
    )
    capacity_numeric_fields = (
        "sustained_seconds",
        "sustained_rps",
        "materialized_runs",
        "error_rate",
        "write_p95_ms",
        "timeline_p95_ms",
        "agent_runs_delta",
        "audit_logs_delta",
        "outbox_events_delta",
        "database_growth_bytes",
    )
    capacity_types_valid = all(
        isinstance(capacity.get(field), (int, float))
        and not isinstance(capacity.get(field), bool)
        and math.isfinite(float(capacity[field]))
        for field in capacity_numeric_fields
    )
    capacity_claims_match = (
        capacity.get("sustained_seconds") == derived_load.get("sustained_seconds")
        and capacity.get("sustained_rps") == derived_load.get("sustained_rps")
        and capacity.get("materialized_runs") == derived_load.get("materialized_runs")
        and capacity.get("error_rate") == derived_load.get("error_rate")
        and capacity.get("write_p95_ms") == derived_load.get("write_p95_ms")
        and capacity.get("timeline_p95_ms") == derived_load.get("timeline_p95_ms")
        and capacity.get("post_growth_query_passed") is True
        and capacity.get("agent_runs_delta") == derived_growth.get("agent_runs_delta")
        and capacity.get("audit_logs_delta") == derived_growth.get("audit_logs_delta")
        and capacity.get("outbox_events_delta") == derived_growth.get("outbox_events_delta")
        and capacity.get("database_growth_bytes")
        == derived_growth.get("database_growth_bytes")
        and capacity.get("cleanup_verified") is derived_cleanup.get("passed")
    )
    capacity_ok = (
        capacity.get("status") == "PASSED"
        and capacity_types_valid
        and float(capacity.get("sustained_seconds", 0)) >= 900
        and float(capacity.get("sustained_rps", 0)) >= minimum_sustained_rps
        and float(capacity.get("materialized_runs", 0)) >= 50_000
        and float(capacity.get("error_rate", 1)) <= 0.001
        and float(capacity.get("write_p95_ms", 1e12)) <= 1_000
        and float(capacity.get("timeline_p95_ms", 1e12)) <= 250
        and capacity.get("post_growth_query_passed") is True
        and capacity.get("cleanup_verified") is True
        and capacity_claims_match
        and report_matches_target
    )
    gate.add(
        "capacity",
        owner="Architecture",
        passed=capacity_ok,
        observed=(
            f"status={capacity.get('status')}, duration={capacity.get('sustained_seconds')}s, "
            f"rps={capacity.get('sustained_rps')}, runs={capacity.get('materialized_runs')}, "
            f"errors={capacity.get('error_rate')}, DB_growth={capacity.get('database_growth_bytes')}, "
            f"cleanup={capacity.get('cleanup_verified')}"
        ),
        expected=(
            f">=900s at >={minimum_sustained_rps} rps; >=50k reconciled AgentRun/Audit/Outbox rows; "
            "signed DB byte growth/lag/backlog; error<=0.1%; write p95<=1s; query p95<=250ms; "
            "namespace deletion and both credentials revoked"
        ),
        detail=(
            "The v3 evidence must re-verify three role-separated signatures and recompute load, "
            "database growth and cleanup from raw files. Local/v2 HTTP summaries cannot authorize "
            f"production. Validation={capacity_validation_detail}; policy=({capacity_policy_detail}); "
            f"load=({load_signature_detail}); growth=({growth_signature_detail}); "
            f"cleanup=({cleanup_signature_detail})"
        ),
    )

    ha = controls["high_availability"]
    replica_counts = ha.get("replica_counts")
    ha_report = _evidence_json(ha, authorization_path)
    report_counts = ha_report.get("replica_counts") if isinstance(ha_report, dict) else None
    report_domains_raw = ha_report.get("fault_domains") if isinstance(ha_report, dict) else None
    report_domains = (
        {str(item) for item in report_domains_raw if str(item).strip()}
        if isinstance(report_domains_raw, list)
        else set()
    )
    target_domains = (
        {str(item) for item in fault_domains if str(item).strip()} if isinstance(fault_domains, list) else set()
    )
    report_fault = ha_report.get("fault_injection") if isinstance(ha_report, dict) else None
    report_probe = ha_report.get("availability_probe") if isinstance(ha_report, dict) else None
    report_network = ha_report.get("network_policy") if isinstance(ha_report, dict) else None
    report_state = ha_report.get("state_services") if isinstance(ha_report, dict) else None
    report_cleanup = ha_report.get("cleanup") if isinstance(ha_report, dict) else None
    evidence_observed_at = _parse_time(
        (ha.get("evidence") or {}).get("observed_at") if isinstance(ha.get("evidence"), dict) else None
    )
    expected_counts = (
        {name: int(replica_counts.get(name, 0)) for name in ("backend", "frontend", "worker", "beat")}
        if isinstance(replica_counts, dict)
        else {}
    )
    report_counts_match = isinstance(report_counts, dict) and all(
        int(report_counts.get(name, -1)) == expected_counts.get(name)
        for name in ("backend", "frontend", "worker", "beat")
    )
    before_ready = all(
        _ha_snapshot_consistent(ha_report or {}, "before", name)
        and _ha_ready_replicas(ha_report or {}, "before", name) >= expected_counts.get(name, 1)
        for name in ("backend", "frontend", "worker", "beat")
    )
    drain_ready = all(
        _ha_snapshot_consistent(ha_report or {}, "after_zone_drain", name)
        and _ha_ready_replicas(ha_report or {}, "after_zone_drain", name) >= expected_counts.get(name, 1)
        for name in ("backend", "frontend", "worker", "beat")
    )
    restored_ready_and_spread = all(
        _ha_snapshot_consistent(ha_report or {}, "after_zone_return_and_rolling_rebalance", name)
        and _ha_ready_replicas(ha_report or {}, "after_zone_return_and_rolling_rebalance", name)
        >= expected_counts.get(name, 1)
        and target_domains.issubset(_ha_ready_zones(ha_report or {}, "after_zone_return_and_rolling_rebalance", name))
        for name in ("backend", "frontend", "worker")
    )
    before_spread = all(
        target_domains.issubset(_ha_ready_zones(ha_report or {}, "before", name))
        for name in ("backend", "frontend", "worker")
    )
    drained_zone_absent = isinstance(report_fault, dict) and all(
        report_fault.get("drained_zone") not in _ha_ready_zones(ha_report or {}, "after_zone_drain", name)
        and report_fault.get("drained_node") not in _ha_ready_nodes(ha_report or {}, "after_zone_drain", name)
        for name in ("backend", "frontend", "worker", "beat")
    )
    drained_nodes_raw = report_fault.get("drained_nodes") if isinstance(report_fault, dict) else None
    drained_nodes = (
        {str(item) for item in drained_nodes_raw if _meaningful_string(item)}
        if isinstance(drained_nodes_raw, list)
        else set()
    )
    cluster_nodes = ha_report.get("cluster_nodes") if isinstance(ha_report, dict) else None
    cluster_node_names = (
        [item.get("node") for item in cluster_nodes if isinstance(item, dict)]
        if isinstance(cluster_nodes, list)
        else []
    )
    eligible_drained_nodes = (
        {
            str(item.get("node"))
            for item in cluster_nodes
            if isinstance(item, dict)
            and item.get("zone") == report_fault.get("drained_zone")
            and item.get("ready") is True
            and item.get("schedulable") is True
            and item.get("control_plane") is False
            and _meaningful_string(item.get("node"))
        }
        if isinstance(cluster_nodes, list) and isinstance(report_fault, dict)
        else set()
    )
    surviving_eligible_nodes = (
        [
            item
            for item in cluster_nodes
            if isinstance(item, dict)
            and item.get("zone") != report_fault.get("drained_zone")
            and item.get("ready") is True
            and item.get("schedulable") is True
            and item.get("control_plane") is False
        ]
        if isinstance(cluster_nodes, list) and isinstance(report_fault, dict)
        else []
    )
    snapshot_nodes = set().union(
        *(
            _ha_ready_nodes(ha_report or {}, snapshot, component)
            for snapshot in ("before", "after_zone_drain", "after_zone_return_and_rolling_rebalance")
            for component in ("backend", "frontend", "worker", "beat")
        )
    )
    cluster_inventory_ok = (
        isinstance(cluster_nodes, list)
        and bool(cluster_nodes)
        and all(isinstance(name, str) and bool(name) for name in cluster_node_names)
        and len(cluster_node_names) == len(set(cluster_node_names))
        and drained_nodes == eligible_drained_nodes
        and len(surviving_eligible_nodes) >= 3
        and snapshot_nodes.issubset(set(cluster_node_names))
        and report_domains.issubset(
            {
                str(item.get("zone"))
                for item in cluster_nodes
                if isinstance(item, dict)
                and item.get("ready") is True
                and item.get("schedulable") is True
                and item.get("control_plane") is False
                and _meaningful_string(item.get("zone"))
            }
        )
    )
    all_drained_nodes_absent = bool(drained_nodes) and all(
        not (drained_nodes & _ha_ready_nodes(ha_report or {}, "after_zone_drain", name))
        for name in ("backend", "frontend", "worker", "beat")
    )
    probe_samples = report_probe.get("samples") if isinstance(report_probe, dict) else None
    probe_sample_times = (
        [_parse_time(item.get("observed_at")) for item in probe_samples if isinstance(item, dict)]
        if isinstance(probe_samples, list)
        else []
    )
    probe_samples_ok = (
        isinstance(probe_samples, list)
        and len(probe_samples) >= 5
        and len(probe_samples) == int(report_probe.get("sample_count", -1))
        and len(probe_sample_times) == len(probe_samples)
        and all(value is not None for value in probe_sample_times)
        and probe_sample_times == sorted(probe_sample_times)
        and all(
            isinstance(item, dict)
            and item.get("passed") is True
            and int(item.get("status_code", -1)) == 200
            for item in probe_samples
        )
    )
    network_reference = report_network.get("evidence") if isinstance(report_network, dict) else None
    network_control_evidence = controls["network"].get("evidence")
    network_evidence_bound = (
        isinstance(network_reference, dict)
        and isinstance(network_control_evidence, dict)
        and network_reference.get("sha256") == network_control_evidence.get("sha256")
        and bool(DIGEST_RE.fullmatch(str(network_reference.get("sha256", ""))))
        and (network_path := _resolve_file(network_reference.get("path"), authorization_path)) is not None
        and network_path.is_file()
        and _sha256(network_path) == network_reference.get("sha256")
    )
    state_reference = report_state.get("evidence") if isinstance(report_state, dict) else {}
    state_signature_ok, state_signature_detail, state_evidence_report = _verify_policy_signed_evidence_file(
        state_reference if isinstance(state_reference, dict) else {},
        authorization_path=authorization_path,
        namespace=STATE_OPERATIONS_SIGNATURE_NAMESPACE,
        allowed_signers=allowed_signers,
        allowed_identities=role_identities.get("Operations", set()),
        approval_policy_reference=approval_policy_reference,
        trust_store_ok=trust_store_ok,
    )
    gate.add(
        "high_availability_state_services_signature",
        owner="Operations",
        passed=state_signature_ok,
        observed=state_signature_detail,
        expected="Operations policy identity signature over independently verified state-services evidence",
        detail="Operations attests the assembled wrapper; provider and verifier signatures are checked separately.",
    )
    (
        state_policy_ok,
        state_trust_store,
        state_provider_identities,
        state_verifier_identities,
        state_approved_providers,
        state_policy_detail,
    ) = _state_services_policy_context(
        state_evidence_report.get("state_services_policy")
        if isinstance(state_evidence_report, dict)
        else None,
        authorization_path=authorization_path,
    )
    state_signer_bindings: dict[str, set[str]] | None = None
    if state_trust_store is not None and state_trust_store.is_file():
        state_signer_bindings, _ = _allowed_signer_bindings(state_trust_store)
    approval_key_material = (
        set().union(*signer_bindings.values()) if signer_bindings else set()
    )
    state_key_material = (
        set().union(*state_signer_bindings.values()) if state_signer_bindings else set()
    )
    state_roles_separated = (
        state_provider_identities.isdisjoint(configured_identities)
        and state_verifier_identities.isdisjoint(configured_identities)
        and approval_key_material.isdisjoint(state_key_material)
    )
    provider_embedded = (
        state_evidence_report.get("provider_receipt")
        if isinstance(state_evidence_report, dict)
        else None
    )
    verification_embedded = (
        state_evidence_report.get("verification_receipt")
        if isinstance(state_evidence_report, dict)
        else None
    )
    provider_signature_ok, provider_receipt, provider_signature_detail = (
        _verified_embedded_receipt(
            provider_embedded,
            authorization_path=authorization_path,
            allowed_signers=state_trust_store if state_policy_ok else None,
            allowed_identities=state_provider_identities,
            namespace=STATE_PROVIDER_SIGNATURE_NAMESPACE,
        )
    )
    verifier_signature_ok, verification_receipt, verifier_signature_detail = (
        _verified_embedded_receipt(
            verification_embedded,
            authorization_path=authorization_path,
            allowed_signers=state_trust_store if state_policy_ok else None,
            allowed_identities=state_verifier_identities,
            namespace=STATE_VERIFICATION_SIGNATURE_NAMESPACE,
        )
    )
    state_provider_signer = (
        provider_embedded.get("signed_evidence", {}).get("signer_identity")
        if isinstance(provider_embedded, dict)
        and isinstance(provider_embedded.get("signed_evidence"), dict)
        else None
    )
    state_verifier_signer = (
        verification_embedded.get("signed_evidence", {}).get("signer_identity")
        if isinstance(verification_embedded, dict)
        and isinstance(verification_embedded.get("signed_evidence"), dict)
        else None
    )
    state_provider_derived: dict[str, Any] = {}
    state_verification_derived: dict[str, Any] = {}
    state_receipt_validation_detail = "signed provider/verifier receipts unavailable"
    try:
        if not (provider_signature_ok and verifier_signature_ok):
            raise ValueError(state_receipt_validation_detail)
        state_provider_derived = validate_state_provider_receipt(
            provider_receipt,
            target_environment=str(target.get("target_id")),
            source_commit=str(release.get("git_commit")),
            backend_image=str(release.get("backend_image")),
            frontend_image=str(release.get("frontend_image")),
            exercise_id=str(state_evidence_report.get("exercise_id", "")),
            approved_providers=state_approved_providers,
            now=current,
        )
        state_verification_derived = validate_state_verification_receipt(
            verification_receipt,
            target_environment=str(target.get("target_id")),
            source_commit=str(release.get("git_commit")),
            backend_image=str(release.get("backend_image")),
            frontend_image=str(release.get("frontend_image")),
            exercise_id=str(state_evidence_report.get("exercise_id", "")),
            provider_services=state_provider_derived["services"],
            now=current,
        )
    except (TypeError, ValueError) as exc:
        state_receipt_validation_detail = str(exc)
    else:
        state_receipt_validation_detail = "provider failover and independent integrity receipts validated"
    state_receipts_ok = (
        state_policy_ok
        and state_roles_separated
        and provider_signature_ok
        and verifier_signature_ok
        and state_provider_signer != state_verifier_signer
        and bool(state_provider_derived)
        and bool(state_verification_derived)
    )
    gate.add(
        "high_availability_state_services_independent_verification",
        owner="Architecture",
        passed=state_receipts_ok,
        observed=(
            f"provider={state_provider_signer}, verifier={state_verifier_signer}, "
            f"validation={state_receipt_validation_detail}"
        ),
        expected="distinct approved provider and verifier signatures over failover and data-integrity receipts",
        detail=(
            f"provider_signature=({provider_signature_detail}); "
            f"verifier_signature=({verifier_signature_detail}); roles_separated={state_roles_separated}; "
            f"policy=({state_policy_detail})"
        ),
    )
    state_services = (
        state_evidence_report.get("services") if isinstance(state_evidence_report, dict) else None
    )
    state_evidence_observed_at = _parse_time(
        state_evidence_report.get("observed_at") if isinstance(state_evidence_report, dict) else None
    )
    state_provider_observed_at = _parse_time(
        state_evidence_report.get("provider_observed_at")
        if isinstance(state_evidence_report, dict)
        else None
    )
    state_verification_observed_at = _parse_time(
        state_evidence_report.get("verification_observed_at")
        if isinstance(state_evidence_report, dict)
        else None
    )
    state_service_projection = (
        build_state_service_projection(
            state_provider_derived["services"],
            state_verification_derived["services"],
        )
        if state_receipts_ok
        else {}
    )
    state_evidence_matches = (
        state_signature_ok
        and state_receipts_ok
        and isinstance(state_evidence_report, dict)
        and set(state_evidence_report)
        == {
            "schema_version",
            "scope",
            "target_environment",
            "source_commit",
            "images",
            "status",
            "passed",
            "observed_at",
            "provider_observed_at",
            "verification_observed_at",
            "exercise_id",
            "managed_mysql_ha",
            "managed_redis_ha",
            "object_store_ha",
            "rwx_repository_storage_ha",
            "failover_exercised",
            "data_integrity_passed",
            "services",
            "state_services_policy",
            "provider_receipt",
            "verification_receipt",
        }
        and state_evidence_report.get("schema_version") == STATE_EVIDENCE_SCHEMA_VERSION
        and state_evidence_report.get("scope") == "target-production"
        and state_evidence_report.get("status") == "PASS"
        and state_evidence_report.get("passed") is True
        and state_evidence_report.get("target_environment") == target.get("target_id")
        and state_evidence_report.get("source_commit") == commit
        and isinstance(state_evidence_report.get("images"), dict)
        and isinstance(state_evidence_report["images"].get("backend"), dict)
        and isinstance(state_evidence_report["images"].get("frontend"), dict)
        and state_evidence_report["images"]["backend"].get("name") == release.get("backend_image")
        and state_evidence_report["images"]["frontend"].get("name") == release.get("frontend_image")
        and state_evidence_observed_at is not None
        and state_provider_observed_at == state_provider_derived.get("observed_at")
        and state_verification_observed_at == state_verification_derived.get("observed_at")
        and state_provider_observed_at is not None
        and state_verification_observed_at is not None
        and state_provider_observed_at
        <= state_verification_observed_at
        <= state_evidence_observed_at
        and state_verification_observed_at - state_provider_observed_at <= timedelta(hours=4)
        and state_evidence_observed_at - state_verification_observed_at <= timedelta(minutes=5)
        and evidence_observed_at is not None
        and timedelta(0) <= evidence_observed_at - state_evidence_observed_at <= timedelta(days=30)
        and isinstance(state_services, dict)
        and isinstance(report_state, dict)
        and state_services == state_service_projection
        and provider_receipt is not None
        and verification_receipt is not None
        and state_evidence_report.get("provider_observed_at")
        == provider_receipt.get("observed_at")
        and state_evidence_report.get("verification_observed_at")
        == verification_receipt.get("observed_at")
        and not _contains_secret_material_key(state_evidence_report)
        and all(
            state_evidence_report.get(key) is True and report_state.get(key) is True
            for key in (
                "managed_mysql_ha",
                "managed_redis_ha",
                "object_store_ha",
                "rwx_repository_storage_ha",
                "failover_exercised",
                "data_integrity_passed",
            )
        )
    )
    cleanup_ok = (
        isinstance(report_cleanup, dict)
        and report_cleanup.get("passed") is True
        and report_cleanup.get("errors") == []
        and isinstance(report_cleanup.get("restored_nodes"), list)
        and drained_nodes.issubset({str(item) for item in report_cleanup["restored_nodes"]})
    )
    report_matches_target = (
        isinstance(ha_report, dict)
        and ha_report.get("schema_version") == "duckdock-kubernetes-ha-failover-v2"
        and ha_report.get("scope") == "target-production"
        and ha_report.get("status") == "PASS"
        and ha_report.get("passed") is True
        and ha_report.get("target_environment") == target.get("target_id")
        and ha_report.get("source_commit") == commit
        and _parse_time(ha_report.get("observed_at")) == evidence_observed_at
        and isinstance(ha_report.get("images"), dict)
        and isinstance(ha_report["images"].get("backend"), dict)
        and isinstance(ha_report["images"].get("frontend"), dict)
        and ha_report["images"]["backend"].get("name") == release.get("backend_image")
        and ha_report["images"]["frontend"].get("name") == release.get("frontend_image")
        and target_domains == report_domains
        and len(report_domains) >= 2
        and int(ha_report.get("fault_domains_exercised", 0)) == len(report_domains)
        and int(ha_report.get("fault_domains_exercised", 0)) == int(ha.get("fault_domains_exercised", -1))
        and report_counts_match
        and before_ready
        and before_spread
        and drain_ready
        and drained_zone_absent
        and all_drained_nodes_absent
        and cluster_inventory_ok
        and restored_ready_and_spread
        and isinstance(report_fault, dict)
        and report_fault.get("drained_zone") in target_domains
        and isinstance(report_fault.get("drained_node"), str)
        and bool(report_fault.get("drained_node"))
        and report_fault.get("node_failover_passed") is True
        and report_fault.get("zone_failover_passed") is True
        and report_fault.get("beat_recovery_passed") is True
        and report_fault.get("beat_original_node") != report_fault.get("beat_recovery_node")
        and int(report_fault.get("recovery_seconds", target_rto + 1)) <= target_rto
        and isinstance(report_probe, dict)
        and report_probe.get("transport") == "network HTTPS against target"
        and str(report_probe.get("base_url", "")).rstrip("/") == str(target.get("public_base_url", "")).rstrip("/")
        and report_probe.get("passed") is True
        and int(report_probe.get("sample_count", 0)) >= 5
        and int(report_probe.get("failure_count", -1)) == 0
        and probe_samples_ok
        and isinstance(report_network, dict)
        and report_network.get("enforcement_exercised") is True
        and report_network.get("passed") is True
        and network_evidence_bound
        and isinstance(report_state, dict)
        and report_state.get("managed_mysql_ha") is True
        and report_state.get("managed_redis_ha") is True
        and report_state.get("object_store_ha") is True
        and report_state.get("rwx_repository_storage_ha") is True
        and report_state.get("failover_exercised") is True
        and report_state.get("data_integrity_passed") is True
        and state_evidence_matches
        and cleanup_ok
        and _meaningful_string(ha_report.get("cluster_context"))
        and _meaningful_string(ha_report.get("namespace"))
        and ha_report.get("exercise_error") is None
    )
    ha_ok = (
        ha.get("status") == "PASS"
        and isinstance(replica_counts, dict)
        and all(int(replica_counts.get(name, 0)) >= 3 for name in ("backend", "frontend", "worker"))
        and int(ha.get("fault_domains_exercised", 0)) >= 2
        and ha.get("managed_mysql_ha") is True
        and ha.get("managed_redis_ha") is True
        and ha.get("object_store_ha") is True
        and ha.get("rwx_repository_storage_ha") is True
        and ha.get("node_failover_passed") is True
        and ha.get("zone_failover_passed") is True
        and ha.get("beat_recovery_passed") is True
        and report_matches_target
    )
    gate.add(
        "high_availability",
        owner="Architecture",
        passed=ha_ok,
        observed=(
            f"status={ha.get('status')}, replicas={replica_counts}, "
            f"zones={ha.get('fault_domains_exercised')}, "
            f"report_scope={ha_report.get('scope') if isinstance(ha_report, dict) else 'missing'}, "
            f"before={before_ready}/{before_spread}, drain={drain_ready}/{drained_zone_absent}/"
            f"{all_drained_nodes_absent}, inventory={cluster_inventory_ok}, restored={restored_ready_and_spread}, "
            f"probe={probe_samples_ok}, network={network_evidence_bound}, "
            f"state={state_evidence_matches}, cleanup={cleanup_ok}"
        ),
        expected="3x stateless replicas; 2+ zones; HA state stores/RWX; node+zone+beat failover passed",
        detail="The content-addressed report must bind the exact target, commit and images; prove target HTTPS continuity, node/zone/Beat failover, restored spread, enforced policy and state-service integrity. Local kind evidence cannot authorize production.",
    )

    security = controls["security_assessment"]
    security_report = _evidence_json(security, authorization_path)
    assessment_projection = (
        security_report.get("assessment")
        if isinstance(security_report, dict)
        and isinstance(security_report.get("assessment"), dict)
        else {}
    )
    findings_projection = (
        security_report.get("findings")
        if isinstance(security_report, dict)
        and isinstance(security_report.get("findings"), dict)
        else {}
    )
    release_authority = (
        security_report.get("release_authority")
        if isinstance(security_report, dict)
        and isinstance(security_report.get("release_authority"), dict)
        else {}
    )
    signed_assessment = (
        security_report.get("signed_assessment")
        if isinstance(security_report, dict)
        and isinstance(security_report.get("signed_assessment"), dict)
        else {}
    )
    security_provider = security.get("provider")
    approved_assessor_identities = (
        assessor_provider_identities.get(str(security_provider), set())
        if isinstance(security_provider, str)
        else set()
    )
    signed_assessment_ok, raw_assessment, signed_assessment_detail = (
        _verified_embedded_receipt(
            signed_assessment,
            authorization_path=authorization_path,
            allowed_signers=allowed_signers if trust_store_ok else None,
            allowed_identities=approved_assessor_identities,
            namespace=SECURITY_ASSESSMENT_SIGNATURE_NAMESPACE,
        )
    )
    signed_evidence = (
        signed_assessment.get("signed_evidence")
        if isinstance(signed_assessment, dict)
        and isinstance(signed_assessment.get("signed_evidence"), dict)
        else {}
    )
    assessor_identity = signed_evidence.get("signer_identity")
    assessment_path = _resolve_file(signed_evidence.get("path"), authorization_path)
    derived_assessment: dict[str, Any] = {}
    assessment_validation_detail = "signed raw assessment unavailable"
    if raw_assessment is not None and assessment_path is not None:
        try:
            derived_assessment = validate_assessment_report(
                raw_assessment,
                assessment_path=assessment_path,
                expected_provider=str(security_provider),
                expected_assessor_identity=str(assessor_identity),
                target_environment=str(target.get("target_id")),
                source_commit=str(release.get("git_commit")),
                backend_image=str(release.get("backend_image")),
                frontend_image=str(release.get("frontend_image")),
                contract_digest=str(release.get("contract_digest")),
                now=current,
            )
        except (OSError, UnicodeError, ValueError) as exc:
            assessment_validation_detail = str(exc)
        else:
            assessment_validation_detail = "raw assessment and report artifact validated"
    release_authority_ok = (
        set(release_authority)
        == {
            "policy_id",
            "policy_sha256",
            "allowed_signers_path",
            "allowed_signers_sha256",
        }
        and release_authority.get("policy_id") == approval_policy_reference.get("policy_id")
        and release_authority.get("policy_sha256") == approval_policy_reference.get("sha256")
        and _resolve_file(release_authority.get("allowed_signers_path"), authorization_path)
        == allowed_signers
        and release_authority.get("allowed_signers_sha256") == actual_trust_digest
        and approval_policy_ok
        and trust_store_ok
    )
    gate.add(
        "security_assessment_signature",
        owner="Security",
        passed=signed_assessment_ok and release_authority_ok,
        observed=(
            f"provider={security_provider}, approved_identities={sorted(approved_assessor_identities)}, "
            f"release_authority={release_authority_ok}, {signed_assessment_detail}"
        ),
        expected=(
            "raw assessor JSON signed by a provider identity pre-authorized in the out-of-band "
            "release-authority policy"
        ),
        detail=(
            "The evidence cannot choose its own allowed-signers file. The assessor identity and key "
            "must be distinct from every internal approver and chain to the shared policy trust store."
        ),
    )
    expected_assessment_projection = (
        {
            "assessment_id": derived_assessment.get("assessment_id"),
            "scope": derived_assessment.get("scope"),
            "methodologies": derived_assessment.get("methodologies"),
            "started_at": raw_assessment.get("started_at"),
            "completed_at": raw_assessment.get("completed_at"),
        }
        if isinstance(raw_assessment, dict) and derived_assessment
        else {}
    )
    expected_findings_projection = (
        {
            "total_findings": derived_assessment.get("total_findings"),
            "open_by_severity": derived_assessment.get("open_by_severity"),
            "closed_by_severity": derived_assessment.get("closed_by_severity"),
            "critical_high_retest_completed": derived_assessment.get(
                "critical_high_retest_completed"
            ),
        }
        if derived_assessment
        else {}
    )
    security_report_matches = (
        isinstance(security_report, dict)
        and set(security_report)
        == {
            "schema_version",
            "scope",
            "status",
            "passed",
            "observed_at",
            "target_environment",
            "source_commit",
            "images",
            "contract_digest",
            "independent",
            "provider",
            "open_critical",
            "open_high",
            "assessment",
            "findings",
            "release_authority",
            "signed_assessment",
        }
        and _report_release_target_binding(
            security_report,
            schema_version=SECURITY_EVIDENCE_SCHEMA_VERSION,
            status="PASS",
            control=security,
            target=target,
            release=release,
        )
        and security_report.get("independent") is security.get("independent")
        and security_report.get("provider") == security.get("provider")
        and isinstance(security_report.get("open_critical"), int)
        and not isinstance(security_report.get("open_critical"), bool)
        and isinstance(security_report.get("open_high"), int)
        and not isinstance(security_report.get("open_high"), bool)
        and security_report.get("open_critical") == security.get("open_critical")
        and security_report.get("open_high") == security.get("open_high")
        and security_report.get("contract_digest") == release.get("contract_digest")
        and assessment_projection == expected_assessment_projection
        and findings_projection == expected_findings_projection
        and security_report.get("open_critical") == derived_assessment.get("open_critical")
        and security_report.get("open_high") == derived_assessment.get("open_high")
        and _ordered_report_times(
            raw_assessment.get("started_at") if isinstance(raw_assessment, dict) else None,
            raw_assessment.get("completed_at") if isinstance(raw_assessment, dict) else None,
            no_later_than=_control_observed_at(security),
        )
        and signed_assessment_ok
        and release_authority_ok
        and bool(derived_assessment)
        and not _contains_secret_material_key(security_report)
    )
    security_ok = (
        security.get("status") == "PASS"
        and security.get("independent") is True
        and isinstance(security.get("provider"), str)
        and not any(marker in str(security.get("provider")) for marker in PLACEHOLDER_MARKERS)
        and isinstance(security.get("open_critical"), int)
        and not isinstance(security.get("open_critical"), bool)
        and security.get("open_critical") == 0
        and isinstance(security.get("open_high"), int)
        and not isinstance(security.get("open_high"), bool)
        and security.get("open_high") == 0
        and security.get("source_commit") == commit
        and security.get("backend_image") == release.get("backend_image")
        and security.get("frontend_image") == release.get("frontend_image")
        and security_report_matches
        and signed_assessment_ok
        and derived_assessment.get("critical_high_retest_completed") is True
    )
    gate.add(
        "security_assessment",
        owner="Security",
        passed=security_ok,
        observed=f"status={security.get('status')}, independent={security.get('independent')}, C={security.get('open_critical')}, H={security.get('open_high')}",
        expected="independent scoped assessment; 0 open critical/high; exact commit and image digests",
        detail=(
            "The signed raw assessor JSON and linked PDF must bind the exact target, contract, "
            "commit and images. Finding counts, Critical/High closure and projections are recomputed "
            f"instead of trusted from wrapper fields. Validation={assessment_validation_detail}."
        ),
    )

    release_digest = _release_digest(release, target, controls, approval_policy_reference)
    approvals = _list(document["approvals"], "approvals")
    approval_roles = [item.get("role") for item in approvals if isinstance(item, dict)]
    identities = [item.get("identity") for item in approvals if isinstance(item, dict)]
    gate.add(
        "approval_roles",
        owner="Product",
        passed=set(approval_roles) == REQUIRED_APPROVAL_ROLES and len(approvals) == 4,
        observed=approval_roles,
        expected=", ".join(sorted(REQUIRED_APPROVAL_ROLES)),
        detail="Exactly one approval is required from every accountable function.",
    )
    gate.add(
        "approval_four_eyes",
        owner="Security",
        passed=len(identities) == 4 and len({str(item) for item in identities}) == 4,
        observed=identities,
        expected="four distinct signer identities",
        detail="One person cannot satisfy multiple organizational approvals.",
    )
    campaign_ids = [
        item.get("campaign_id") for item in approvals if isinstance(item, dict)
    ]
    campaign_freeze_digests = [
        item.get("campaign_freeze_sha256")
        for item in approvals
        if isinstance(item, dict)
    ]
    campaign_reference = document.get("approval_campaign")
    campaign_reference_shape_ok = (
        isinstance(campaign_reference, dict)
        and set(campaign_reference) == {"path", "sha256", "campaign_id"}
    )
    campaign_freeze_path = (
        _resolve_file(campaign_reference.get("path"), authorization_path)
        if isinstance(campaign_reference, dict)
        else None
    )
    campaign_reference_digest = (
        _sha256(campaign_freeze_path)
        if campaign_freeze_path is not None and campaign_freeze_path.is_file()
        else "missing"
    )
    campaign_validation: dict[str, Any] | None = None
    campaign_validation_detail = "missing or invalid campaign freeze reference"
    base_matches = False
    policy_path_matches = False
    if (
        campaign_reference_shape_ok
        and campaign_reference_digest == campaign_reference.get("sha256")
        and campaign_freeze_path is not None
    ):
        try:
            campaign_receipt = _load_json(campaign_freeze_path)
            receipt_authorization = _object(
                campaign_receipt.get("authorization"),
                "approval campaign authorization binding",
            )
            receipt_policy = _object(
                campaign_receipt.get("approval_policy"),
                "approval campaign policy binding",
            )
            frozen_base_path = _resolve_file(
                receipt_authorization.get("path"), authorization_path
            )
            frozen_policy_path = _resolve_file(receipt_policy.get("path"), authorization_path)
            if frozen_base_path is None or frozen_policy_path is None:
                raise ValueError("campaign freeze bound files cannot be resolved")
            campaign_validation = validate_campaign_freeze(
                campaign_freeze_path,
                authorization_path=frozen_base_path,
                approval_policy_path=frozen_policy_path,
                now=current,
                require_active=False,
                enforce_recorded_paths=False,
            )
            frozen_base = _load_json(frozen_base_path)
            expected_base = dict(document)
            expected_base.pop("approval_campaign", None)
            expected_base["approvals"] = []
            base_matches = frozen_base == expected_base and frozen_base.get("approvals") == []
            policy_path_matches = (
                approval_policy_path is not None
                and frozen_policy_path.resolve() == approval_policy_path.resolve()
            )
            campaign_validation_detail = (
                f"freeze={campaign_freeze_path}, base_matches={base_matches}, "
                f"policy_path_matches={policy_path_matches}"
            )
        except (OSError, UnicodeError, ValueError) as exc:
            campaign_validation_detail = str(exc)
    approval_times = [
        _parse_time(item.get("approved_at"))
        for item in approvals
        if isinstance(item, dict)
    ]
    campaign_times_ok = (
        campaign_validation is not None
        and len(approval_times) == len(approvals)
        and all(
            approved_at is not None
            and approved_at >= campaign_validation["frozen_at"]
            and approved_at <= campaign_validation["approvals_expire_at"]
            for approved_at in approval_times
        )
    )
    campaign_binding_ok = (
        bool(approvals)
        and len(campaign_ids) == len(approvals)
        and len(campaign_freeze_digests) == len(approvals)
        and len({str(item) for item in campaign_ids}) == 1
        and len({str(item) for item in campaign_freeze_digests}) == 1
        and all(CAMPAIGN_ID_RE.fullmatch(str(item)) for item in campaign_ids)
        and all(DIGEST_RE.fullmatch(str(item)) for item in campaign_freeze_digests)
        and campaign_validation is not None
        and campaign_reference.get("campaign_id") == campaign_validation["campaign_id"]
        and campaign_reference.get("sha256")
        == campaign_validation["campaign_freeze_sha256"]
        and set(campaign_ids) == {campaign_validation["campaign_id"]}
        and set(campaign_freeze_digests)
        == {campaign_validation["campaign_freeze_sha256"]}
        and base_matches
        and policy_path_matches
        and campaign_times_ok
    )
    gate.add(
        "approval_campaign",
        owner="Operations",
        passed=campaign_binding_ok,
        observed=(
            f"campaign_ids={campaign_ids}, campaign_freeze_sha256={campaign_freeze_digests}, "
            f"{campaign_validation_detail}, approval_times_in_window={campaign_times_ok}"
        ),
        expected="one immutable campaign id and freeze receipt digest shared by every approval",
        detail=(
            "The signed campaign fields prevent approvals from separate collection windows from "
            "being mixed even when they concern the same release digest."
        ),
    )
    latest_evidence = max(evidence_times, default=None)
    for role in sorted(REQUIRED_APPROVAL_ROLES):
        matches = [item for item in approvals if isinstance(item, dict) and item.get("role") == role]
        approval = matches[0] if len(matches) == 1 else {}
        identity = str(approval.get("identity", ""))
        approved_at = _parse_time(approval.get("approved_at"))
        signature = _resolve_file(approval.get("signature_path"), authorization_path)
        role_authorized = identity in role_identities.get(role, set())
        signature_ok = False
        signature_detail = "missing signature artifacts"
        if (
            trust_store_ok
            and allowed_signers is not None
            and signature is not None
            and signature.is_file()
            and identity
        ):
            signature_ok, signature_detail = _verify_ssh_signature(
                identity=identity,
                allowed_signers=allowed_signers,
                signature=signature,
                approval=approval,
                release_digest=release_digest,
            )
        approval_ok = (
            approval.get("decision") == "APPROVED"
            and approval.get("signed_digest") == release_digest
            and CAMPAIGN_ID_RE.fullmatch(str(approval.get("campaign_id", ""))) is not None
            and DIGEST_RE.fullmatch(str(approval.get("campaign_freeze_sha256", ""))) is not None
            and approved_at is not None
            and latest_evidence is not None
            and approved_at >= latest_evidence
            and approved_at <= current
            and role_authorized
            and signature_ok
        )
        gate.add(
            f"approval_{role}",
            owner=role,
            passed=approval_ok,
            observed=(
                f"identity={identity or 'missing'}, role_authorized={role_authorized}, "
                f"campaign_id={approval.get('campaign_id', 'missing')}, "
                f"approved_at={approved_at}, signature={signature_detail}"
            ),
            expected=(
                f"policy-authorized {role} identity; immutable campaign; APPROVED after latest evidence; "
                f"OpenSSH signature over {release_digest}"
            ),
            detail="Approvals are bound to the release, target, every evidence digest and the out-of-band role policy.",
        )

    failed = {check.key for check in gate.checks if not check.passed}
    failed_foundation = failed.intersection(FOUNDATION_CHECK_KEYS)
    failed_approvals = failed.intersection(APPROVAL_CHECK_KEYS)
    failed_evidence = failed - FOUNDATION_CHECK_KEYS - APPROVAL_CHECK_KEYS
    foundation_ready = not failed_foundation
    evidence_ready_for_approval = foundation_ready and not failed_evidence
    approvals_complete = evidence_ready_for_approval and not failed_approvals
    if approvals_complete:
        status = "GA_AUTHORIZED"
        campaign_stage = "AUTHORIZED"
        next_action = "archive_authorized_bundle"
    elif evidence_ready_for_approval:
        status = "AWAITING_EXTERNAL_APPROVALS"
        campaign_stage = "APPROVAL_COLLECTION"
        next_action = "collect_organizational_approvals"
    elif foundation_ready:
        status = "BLOCKED"
        campaign_stage = "EVIDENCE_COLLECTION"
        next_action = "collect_or_replace_external_evidence"
    else:
        status = "BLOCKED"
        campaign_stage = "FOUNDATION"
        next_action = "repair_release_foundation"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "campaign_stage": campaign_stage,
        "foundation_ready": foundation_ready,
        "evidence_ready_for_approval": evidence_ready_for_approval,
        "approvals_complete": approvals_complete,
        "failed_foundation_checks": sorted(failed_foundation),
        "failed_evidence_checks": sorted(failed_evidence),
        "failed_approval_checks": sorted(failed_approvals),
        "next_action": next_action,
        "release_digest": release_digest,
        "checked_at": current.isoformat(),
        "pass_count": sum(check.passed for check in gate.checks),
        "block_count": sum(not check.passed for check in gate.checks),
        "checks": [{**asdict(check), "status": "PASS" if check.passed else "BLOCK"} for check in gate.checks],
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("authorization", type=Path)
    parser.add_argument(
        "--approval-policy",
        type=Path,
        help="release-authority managed approval policy (required for evaluation)",
    )
    parser.add_argument("--lint", action="store_true", help="validate structure only; placeholders are allowed")
    parser.add_argument("--output", type=Path, help="write the JSON evaluation report")
    outcome = parser.add_mutually_exclusive_group()
    outcome.add_argument(
        "--require-evidence-ready",
        action="store_true",
        help="return zero only after all non-approval checks pass",
    )
    outcome.add_argument(
        "--allow-blocked",
        action="store_true",
        help="return zero after emitting any structurally valid non-GA report",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        document = _load_json(args.authorization)
        lint_errors = lint_authorization(document)
        if lint_errors:
            raise ValueError("; ".join(lint_errors))
        if args.lint:
            print(json.dumps({"ok": True, "schema_version": SCHEMA_VERSION}, sort_keys=True))
            return 0
        result = evaluate(
            document,
            authorization_path=args.authorization.resolve(),
            approval_policy_path=args.approval_policy,
        )
    except ValueError as exc:
        print(f"GA authorization error: {exc}", file=sys.stderr)
        return 3
    payload = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    if (
        result["status"] == "GA_AUTHORIZED"
        or (args.require_evidence_ready and result["evidence_ready_for_approval"] is True)
        or args.allow_blocked
    ):
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
