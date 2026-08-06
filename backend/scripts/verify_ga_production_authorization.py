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
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse


SCHEMA_VERSION = "duckdock-ga-production-authorization-v2"
APPROVAL_POLICY_SCHEMA_VERSION = "duckdock-ga-approval-policy-v1"
APPROVAL_STATEMENT_SCHEMA_VERSION = "duckdock-ga-approval-statement-v1"
ALERTING_SCHEMA_VERSION = "duckdock-ga-alerting-evidence-v2"
ALERTING_POLICY_SCHEMA_VERSION = "duckdock-ga-alerting-trust-policy-v1"
ALERT_DELIVERY_SCHEMA_VERSION = "duckdock-ga-alert-delivery-receipt-v1"
ONCALL_ACK_SCHEMA_VERSION = "duckdock-ga-oncall-acknowledgement-v1"
ALERT_DELIVERY_SIGNATURE_NAMESPACE = "duckdock-alert-delivery-receipt"
ONCALL_ACK_SIGNATURE_NAMESPACE = "duckdock-oncall-acknowledgement"
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
EXTERNAL_CHECK_KEYS = {
    "security_assessment",
    "security_assessment_signature",
    "approval_roles",
    "approval_four_eyes",
    "approval_policy",
    "approval_trust_store",
    "approval_Product",
    "approval_Architecture",
    "approval_Security",
    "approval_Operations",
}
PLACEHOLDER_MARKERS = ("__CHANGE_ME", "example.invalid", "<", ">")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
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
        "credential",
        "credential_value",
        "password",
        "password_value",
        "plaintext",
        "plaintext_value",
        "private_key",
        "raw_secret",
        "secret_value",
        "secret_values",
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


def _approval_statement(approval: dict[str, Any], release_digest: str) -> bytes:
    return (
        json.dumps(
            {
                "schema_version": APPROVAL_STATEMENT_SCHEMA_VERSION,
                "authorization_schema_version": SCHEMA_VERSION,
                "release_digest": release_digest,
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
    exists = path is not None and path.is_file()
    actual_digest = _sha256(path) if exists else "missing"
    digest_ok = bool(DIGEST_RE.fullmatch(expected_digest)) and actual_digest == expected_digest
    artifacts_ok = (
        _meaningful_string(identity)
        and allowed_signers is not None
        and allowed_signers.is_file()
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
        f"path={path or 'missing'}, digest={actual_digest}, signer={identity or 'missing'}, signature={signature_detail}",
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


def _verified_alerting_receipt(
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
    exists = path is not None and path.is_file()
    actual_digest = _sha256(path) if exists else "missing"
    digest_ok = bool(DIGEST_RE.fullmatch(expected_digest)) and actual_digest == expected_digest
    signer_ok = _meaningful_string(identity) and identity in allowed_identities
    signature_ok = False
    signature_detail = "missing or unauthorized signature artifacts"
    if (
        exists
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
            payload=path.read_bytes(),
        )
    raw: dict[str, Any] | None = None
    if exists:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
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
        manifest.get("schema_version") == "duckdock-secure-backup-v1"
        and manifest.get("release_commit") == release_commit
        and manifest.get("encryption") == "age-x25519"
        and _parse_time(manifest.get("created_at")) is not None
        and all(
            isinstance(artifacts.get(name), dict)
            and bool(DIGEST_RE.fullmatch(str(artifacts[name].get("encrypted_sha256", ""))))
            and bool(DIGEST_RE.fullmatch(str(artifacts[name].get("plaintext_sha256", ""))))
            and _positive_int(artifacts[name].get("encrypted_size_bytes"))
            and _positive_int(artifacts[name].get("plaintext_size_bytes"))
            for name in expected_names
        )
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
    configured_identities = set().union(*role_identities.values()) if role_identities else set()
    identities_are_role_exclusive = (
        roles_valid
        and len(configured_identities) == sum(len(items) for items in role_identities.values())
    )
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
            "exactly four non-overlapping role mappings"
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
        expected="policy-bound shared allowed-signers digest with exactly the configured role identities",
        detail="Per-approval trust stores are forbidden; every signature must chain to this one release-authority trust store.",
    )

    evidence_times: list[datetime] = []
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
    tls_report_matches_target = (
        _report_release_target_binding(
            tls_report,
            schema_version="duckdock-ga-tls-probe-v2",
            status="PASS",
            control=tls,
            target=target,
            release=release,
        )
        and tls_report.get("passed") is True
        and _origin(tls_report.get("application_url")) == str(target.get("public_base_url", "")).rstrip("/")
        and _origin(tls_report.get("object_store_url")) == str(target.get("object_store_url", "")).rstrip("/")
        and tls_report.get("negotiated_protocols") == negotiated
        and tls_report.get("legacy_protocols_rejected") == tls.get("legacy_protocols_rejected")
        and int(tls_report.get("certificate_days_remaining", -1)) == int(tls.get("certificate_days_remaining", -2))
        and tls_report.get("hostname_verified") is tls.get("hostname_verified")
        and int(tls_report.get("hsts_max_age_seconds", -1)) == int(tls.get("hsts_max_age_seconds", -2))
        and isinstance(tls_report.get("endpoints"), dict)
        and set(tls_report["endpoints"]) == {"application", "object_store"}
        and all(
            isinstance(item, dict) and item.get("passed") is True
            for item in (tls_report.get("endpoints") or {}).values()
        )
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
    secrets_report_matches = (
        _report_release_target_binding(
            secrets_report,
            schema_version="duckdock-ga-secrets-evidence-v1",
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
        and secret_rotation.get("executed") is True
        and secret_rotation.get("old_credentials_rejected") is True
        and secret_rotation.get("workloads_reloaded") is True
        and secret_rotation.get("audit_event_recorded") is True
        and not _contains_secret_material_key(secrets_report)
        and isinstance(secret_classes, list)
        and bool(secret_classes)
        and all(_meaningful_string(item) for item in secret_classes)
        and _ordered_report_times(
            secret_rotation.get("started_at"),
            secret_rotation.get("completed_at"),
            no_later_than=_control_observed_at(secrets),
        )
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
        detail="The target report must bind the release and prove audited encrypted storage, credential rejection and workload reload without retaining secret values.",
    )

    network = controls["network"]
    network_report = _evidence_json(network, authorization_path)
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
    network_report_matches = (
        _report_release_target_binding(
            network_report,
            schema_version="duckdock-ga-network-evidence-v2",
            status="PASS",
            control=network,
            target=target,
            release=release,
        )
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
    firing_signature_ok, firing_receipt, firing_signature_detail = _verified_alerting_receipt(
        firing_embedded,
        authorization_path=authorization_path,
        allowed_signers=alerting_trust_store,
        allowed_identities=delivery_identities,
        namespace=ALERT_DELIVERY_SIGNATURE_NAMESPACE,
    )
    resolved_signature_ok, resolved_receipt, resolved_signature_detail = _verified_alerting_receipt(
        resolved_embedded,
        authorization_path=authorization_path,
        allowed_signers=alerting_trust_store,
        allowed_identities=delivery_identities,
        namespace=ALERT_DELIVERY_SIGNATURE_NAMESPACE,
    )
    acknowledgement_signature_ok, acknowledgement, acknowledgement_signature_detail = _verified_alerting_receipt(
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
    restore_report = (
        recovery_report.get("restore")
        if isinstance(recovery_report, dict) and isinstance(recovery_report.get("restore"), dict)
        else {}
    )
    verification_report = (
        recovery_report.get("verification")
        if isinstance(recovery_report, dict) and isinstance(recovery_report.get("verification"), dict)
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
            schema_version="duckdock-ga-recovery-evidence-v1",
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
        and restore_report.get("destructive_restore") is True
        and _meaningful_string(restore_report.get("target_environment"))
        and restore_report.get("target_environment") != target.get("target_id")
        and restore_report.get("production_data_overwrite") is False
        and restore_report.get("integrity_digest_verified") is True
        and _ordered_report_times(
            restore_report.get("started_at"),
            restore_report.get("completed_at"),
            no_later_than=_control_observed_at(recovery),
        )
        and verification_report.get("mysql_rows_verified") == recovery.get("mysql_rows_verified")
        and verification_report.get("objects_verified") == recovery.get("objects_verified")
        and verification_report.get("git_repositories_verified") is recovery.get("git_repositories_verified")
        and verification_report.get("passed") is True
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
        detail="The release-bound report must prove a signed offsite encrypted immutable backup and a destructive non-production restore with DB/object/Git integrity checks.",
    )

    capacity = controls["capacity"]
    minimum_sustained_rps = float(target.get("minimum_sustained_rps", 50))
    capacity_report = _evidence_json(capacity, authorization_path)
    sustained_report = None
    timeline_report: dict[str, Any] = {}
    if capacity_report is not None and isinstance(capacity_report.get("phases"), list):
        sustained_report = next(
            (
                phase
                for phase in capacity_report["phases"]
                if isinstance(phase, dict) and phase.get("name") == "sustained"
            ),
            None,
        )
    if capacity_report is not None and isinstance(capacity_report.get("post_growth_timeline_query"), dict):
        timeline_report = capacity_report["post_growth_timeline_query"]
    report_matches_target = (
        _report_release_target_binding(
            capacity_report,
            schema_version="duckdock-target-capacity-gate-v2",
            status="PASSED",
            control=capacity,
            target=target,
            release=release,
        )
        and capacity_report.get("passed") is True
        and capacity_report.get("transport") == "network HTTPS against target"
        and capacity_report.get("target_environment") == target.get("target_id")
        and str(capacity_report.get("base_url", "")).rstrip("/") == str(target.get("public_base_url", "")).rstrip("/")
        and isinstance(sustained_report, dict)
        and sustained_report.get("passed") is True
        and int(sustained_report.get("duration_seconds", 0)) == int(capacity.get("sustained_seconds", -1))
        and float(sustained_report.get("target_rate", 0)) == float(capacity.get("sustained_rps", -1))
        and int(capacity_report.get("materialized_runs", -1)) == int(capacity.get("materialized_runs", -2))
        and float(capacity_report.get("error_rate", -1)) == float(capacity.get("error_rate", -2))
        and float(sustained_report.get("p95_ms", -1)) == float(capacity.get("write_p95_ms", -2))
        and float(timeline_report.get("p95_ms", -1)) == float(capacity.get("timeline_p95_ms", -2))
        and timeline_report.get("passed") is capacity.get("post_growth_query_passed")
    )
    capacity_ok = (
        capacity.get("status") == "PASSED"
        and int(capacity.get("sustained_seconds", 0)) >= 900
        and float(capacity.get("sustained_rps", 0)) >= minimum_sustained_rps
        and int(capacity.get("materialized_runs", 0)) >= 50_000
        and float(capacity.get("error_rate", 1)) <= 0.001
        and float(capacity.get("write_p95_ms", 1e12)) <= 1_000
        and float(capacity.get("timeline_p95_ms", 1e12)) <= 250
        and capacity.get("post_growth_query_passed") is True
        and report_matches_target
    )
    gate.add(
        "capacity",
        owner="Architecture",
        passed=capacity_ok,
        observed=(
            f"status={capacity.get('status')}, duration={capacity.get('sustained_seconds')}s, "
            f"rps={capacity.get('sustained_rps')}, runs={capacity.get('materialized_runs')}, "
            f"errors={capacity.get('error_rate')}"
        ),
        expected=f">=900s at >={minimum_sustained_rps} rps; >=50k rows; error<=0.1%; write p95<=1s; query p95<=250ms",
        detail="The evidence must be a passing network-HTTPS target report; local ASGI/MySQL engineering results cannot authorize production.",
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
        namespace="duckdock-ha-state-services",
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
        expected="Operations policy identity signature over a release-bound state-services failover report",
        detail="Managed MySQL/Redis/object/RWX HA cannot be established by booleans copied into the Kubernetes report.",
    )
    state_services = (
        state_evidence_report.get("services") if isinstance(state_evidence_report, dict) else None
    )
    state_evidence_observed_at = _parse_time(
        state_evidence_report.get("observed_at") if isinstance(state_evidence_report, dict) else None
    )
    state_evidence_matches = (
        state_signature_ok
        and isinstance(state_evidence_report, dict)
        and state_evidence_report.get("schema_version") == "duckdock-ga-state-services-failover-v1"
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
        and evidence_observed_at is not None
        and timedelta(0) <= evidence_observed_at - state_evidence_observed_at <= timedelta(days=30)
        and isinstance(state_services, dict)
        and isinstance(report_state, dict)
        and set(state_services) == {"mysql", "redis", "object_store", "rwx_repository_storage"}
        and all(
            isinstance(state_services.get(name), dict)
            and _meaningful_string(state_services[name].get("provider"))
            and _meaningful_string(state_services[name].get("failover_receipt_id"))
            and state_services[name].get("ha_enabled") is True
            and state_services[name].get("failover_exercised") is True
            and state_services[name].get("data_integrity_passed") is True
            and _ordered_report_times(
                state_services[name].get("started_at"),
                state_services[name].get("recovered_at"),
                no_later_than=state_evidence_observed_at,
            )
            for name in ("mysql", "redis", "object_store", "rwx_repository_storage")
        )
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
    assessment_report = (
        security_report.get("assessment")
        if isinstance(security_report, dict) and isinstance(security_report.get("assessment"), dict)
        else {}
    )
    findings_report = (
        security_report.get("findings")
        if isinstance(security_report, dict) and isinstance(security_report.get("findings"), dict)
        else {}
    )
    signed_report = (
        security_report.get("signed_report")
        if isinstance(security_report, dict) and isinstance(security_report.get("signed_report"), dict)
        else {}
    )
    assessment_scope = assessment_report.get("scope")
    methodologies = assessment_report.get("methodologies")
    assessment_scope_values = (
        set(assessment_scope)
        if isinstance(assessment_scope, list) and all(isinstance(item, str) for item in assessment_scope)
        else set()
    )
    methodology_values = (
        set(methodologies)
        if isinstance(methodologies, list) and all(isinstance(item, str) for item in methodologies)
        else set()
    )
    required_assessment_scope = {
        "application-and-api",
        "identity-and-access",
        "kubernetes-infrastructure",
        "supply-chain",
        "agent-security",
    }
    signed_report_ok, signed_report_detail = _verify_signed_evidence_file(
        signed_report,
        authorization_path=authorization_path,
        namespace="duckdock-security-assessment",
    )
    gate.add(
        "security_assessment_signature",
        owner="Security",
        passed=signed_report_ok,
        observed=signed_report_detail,
        expected="digest-matched assessor report with valid OpenSSH signature",
        detail="The independent assessor, not the DuckDock implementation team, must sign the retained report in namespace duckdock-security-assessment.",
    )
    security_report_matches = (
        _report_release_target_binding(
            security_report,
            schema_version="duckdock-ga-independent-security-evidence-v1",
            status="PASS",
            control=security,
            target=target,
            release=release,
        )
        and security_report.get("independent") is security.get("independent")
        and security_report.get("provider") == security.get("provider")
        and security_report.get("open_critical") == security.get("open_critical")
        and security_report.get("open_high") == security.get("open_high")
        and security_report.get("contract_digest") == release.get("contract_digest")
        and assessment_report.get("independence_attested") is True
        and _meaningful_string(assessment_report.get("assessment_id"))
        and isinstance(assessment_scope, list)
        and required_assessment_scope.issubset(assessment_scope_values)
        and isinstance(methodologies, list)
        and {"penetration-test", "manual-code-review"}.issubset(methodology_values)
        and _ordered_report_times(
            assessment_report.get("started_at"),
            assessment_report.get("completed_at"),
            no_later_than=_control_observed_at(security),
        )
        and findings_report.get("open_critical") == security.get("open_critical")
        and findings_report.get("open_high") == security.get("open_high")
        and findings_report.get("retest_completed") is True
        and bool(DIGEST_RE.fullmatch(str(signed_report.get("sha256", ""))))
        and _meaningful_string(signed_report.get("signer_identity"))
    )
    security_ok = (
        security.get("status") == "PASS"
        and security.get("independent") is True
        and isinstance(security.get("provider"), str)
        and not any(marker in str(security.get("provider")) for marker in PLACEHOLDER_MARKERS)
        and int(security.get("open_critical", -1)) == 0
        and int(security.get("open_high", -1)) == 0
        and security.get("source_commit") == commit
        and security.get("backend_image") == release.get("backend_image")
        and security.get("frontend_image") == release.get("frontend_image")
        and security_report_matches
        and signed_report_ok
    )
    gate.add(
        "security_assessment",
        owner="Security",
        passed=security_ok,
        observed=f"status={security.get('status')}, independent={security.get('independent')}, C={security.get('open_critical')}, H={security.get('open_high')}",
        expected="independent scoped assessment; 0 open critical/high; exact commit and image digests",
        detail="The independent signed report must bind the exact target, contract, commit and images; cover application, IAM, Kubernetes, supply-chain and agent risks; and record completed retesting.",
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
                f"approved_at={approved_at}, signature={signature_detail}"
            ),
            expected=(
                f"policy-authorized {role} identity; APPROVED after latest evidence; "
                f"OpenSSH signature over {release_digest}"
            ),
            detail="Approvals are bound to the release, target, every evidence digest and the out-of-band role policy.",
        )

    failed = {check.key for check in gate.checks if not check.passed}
    if not failed:
        status = "GA_AUTHORIZED"
    elif failed.issubset(EXTERNAL_CHECK_KEYS | {f"{key}_evidence" for key in EXTERNAL_CHECK_KEYS}):
        status = "AWAITING_EXTERNAL_APPROVALS"
    else:
        status = "BLOCKED"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
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
    parser.add_argument("--allow-blocked", action="store_true", help="return zero after emitting a non-GA report")
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
    if result["status"] == "GA_AUTHORIZED" or args.allow_blocked:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
