#!/usr/bin/env python3
"""Exercise a real Kubernetes fault domain and collect release-bound GA HA evidence.

This command is intentionally disruptive. It taints and drains every eligible
worker node in one selected zone, probes the public HTTPS endpoint throughout
the outage, restores the nodes in a finally block, then rolls stateless
workloads so the returned zone is covered again.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlparse

try:
    from scripts.ga_execution_phase_start import verify_runtime_entry
    from scripts.ga_network_evidence import NETWORK_EVIDENCE_SCHEMA_VERSION
    from scripts.ga_release_identity import build_release_binding
    from scripts.ga_target_cluster_access import verify_live_target_cluster_access
    from scripts.ga_state_services_evidence import (
        DIGEST_RE as STATE_DIGEST_RE,
        REQUIRED_SERVICES as STATE_REQUIRED_SERVICES,
        STATE_EVIDENCE_SCHEMA_VERSION,
        contains_secret_material_key as state_contains_secret_material_key,
    )
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_execution_phase_start import verify_runtime_entry
    from ga_network_evidence import NETWORK_EVIDENCE_SCHEMA_VERSION
    from ga_release_identity import build_release_binding
    from ga_target_cluster_access import verify_live_target_cluster_access
    from ga_state_services_evidence import (
        DIGEST_RE as STATE_DIGEST_RE,
        REQUIRED_SERVICES as STATE_REQUIRED_SERVICES,
        STATE_EVIDENCE_SCHEMA_VERSION,
        contains_secret_material_key as state_contains_secret_material_key,
    )


SCHEMA_VERSION = "duckdock-kubernetes-ha-failover-v2"
STATE_SCHEMA_VERSION = STATE_EVIDENCE_SCHEMA_VERSION
NETWORK_SCHEMA_VERSION = NETWORK_EVIDENCE_SCHEMA_VERSION
APPROVAL_POLICY_SCHEMA_VERSION = "duckdock-ga-approval-policy-v2"
COMPONENTS = ("backend", "frontend", "worker", "beat")
STATE_SERVICES = STATE_REQUIRED_SERVICES
TAINT = "duckdock.io/fault-domain-unavailable=true:NoSchedule"
TAINT_KEY = "duckdock.io/fault-domain-unavailable"
NETWORK_REQUIRED_POLICIES = {
    "default-deny",
    "allow-dns",
    "frontend-ingress-and-backend",
    "backend-from-frontend",
    "controlled-external-egress",
}
IMMUTABLE_IMAGE_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} root must be an object")
    return value


def require_safe_base_url(raw: str) -> str:
    parsed = urlparse(raw)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("base URL must be a credential-free non-local HTTPS origin")
    return f"https://{parsed.netloc}"


def _binding_matches(report: dict[str, Any], binding: dict[str, Any]) -> bool:
    return all(report.get(key) == binding.get(key) for key in ("scope", "target_environment", "source_commit")) and (
        isinstance(report.get("images"), dict)
        and report["images"].get("backend") == binding["images"]["backend"]
        and report["images"].get("frontend") == binding["images"]["frontend"]
    )


def _ordered_times(start: Any, end: Any) -> bool:
    if not isinstance(start, str) or not isinstance(end, str):
        return False
    try:
        first = datetime.fromisoformat(start.replace("Z", "+00:00"))
        second = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return False
    return first.tzinfo is not None and second.tzinfo is not None and first <= second


def _aware_time(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def validate_network_evidence(path: Path, binding: dict[str, Any]) -> dict[str, Any]:
    def meaningful(value: Any) -> bool:
        return isinstance(value, str) and bool(value.strip()) and "__CHANGE_ME" not in value

    def policy_has_broad_world(spec: dict[str, Any]) -> bool:
        return any(
            isinstance(target, dict)
            and isinstance(target.get("ipBlock"), dict)
            and target["ipBlock"].get("cidr") in {"0.0.0.0/0", "::/0"}
            for rule in spec.get("egress", []) or []
            if isinstance(rule, dict)
            for target in rule.get("to", []) or []
        )

    def raw_scan_retained(scan: Any) -> bool:
        if not isinstance(scan, dict) or not isinstance(scan.get("raw_nmap_xml"), str):
            return False
        return hashlib.sha256(scan["raw_nmap_xml"].encode("utf-8")).hexdigest() == scan.get(
            "xml_sha256"
        )

    report = _load_object(path, "network evidence")
    external_scan = report.get("external_scan")
    policy_tests = report.get("policy_tests")
    public_scan = external_scan.get("public_ingress") if isinstance(external_scan, dict) else None
    private_scans = external_scan.get("private_data_services") if isinstance(external_scan, dict) else None
    policies = policy_tests.get("network_policies") if isinstance(policy_tests, dict) else None
    cni = policy_tests.get("cni") if isinstance(policy_tests, dict) else None
    probes = policy_tests.get("probe_identities") if isinstance(policy_tests, dict) else None
    ingress = policy_tests.get("ingress_tests") if isinstance(policy_tests, dict) else None
    egress = policy_tests.get("egress_tests") if isinstance(policy_tests, dict) else None
    policy_names = [
        item.get("name")
        for item in policies or []
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    ]
    policies_valid = (
        isinstance(policies, list)
        and len(policy_names) == len(policies) == len(set(policy_names))
        and NETWORK_REQUIRED_POLICIES.issubset(set(policy_names))
        and all(
            isinstance(item, dict)
            and meaningful(item.get("uid"))
            and meaningful(item.get("resource_version"))
            and isinstance(item.get("generation"), int)
            and not isinstance(item.get("generation"), bool)
            and item.get("generation") >= 1
            and isinstance(item.get("spec"), dict)
            and item.get("spec_sha256")
            == hashlib.sha256(
                json.dumps(item["spec"], sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            and not policy_has_broad_world(item["spec"])
            for item in policies
        )
    )
    probe_rows_valid = isinstance(probes, dict) and all(
        isinstance(item, dict)
        and meaningful(item.get("namespace"))
        and meaningful(item.get("namespace_uid"))
        and meaningful(item.get("namespace_resource_version"))
        and isinstance(item.get("namespace_labels"), dict)
        and meaningful(item.get("pod"))
        and meaningful(item.get("pod_uid"))
        and meaningful(item.get("pod_resource_version"))
        for item in probes.values()
    )
    probes_valid = (
        probe_rows_valid
        and set(probes) == {"ingress", "monitoring", "untrusted"}
        and len({item.get("namespace") for item in probes.values() if isinstance(item, dict)}) == 3
        and probes.get("ingress", {}).get("namespace_labels", {}).get("duckdock.io/ingress") == "true"
        and probes.get("monitoring", {}).get("namespace_labels", {}).get("duckdock.io/monitoring") == "true"
        and probes.get("untrusted", {}).get("namespace_labels", {}).get("duckdock.io/ingress") != "true"
        and probes.get("untrusted", {}).get("namespace_labels", {}).get("duckdock.io/monitoring") != "true"
    )
    ingress_expected = {
        "trusted_frontend_allowed": True,
        "trusted_backend_allowed": True,
        "untrusted_frontend_denied": False,
        "untrusted_backend_denied": False,
    }
    ingress_valid = isinstance(ingress, dict) and set(ingress) == set(ingress_expected) and all(
        isinstance(ingress.get(name), dict) and ingress[name].get("connected") is connected
        for name, connected in ingress_expected.items()
    )
    control = egress.get("untrusted_control_destination_reachable", {}) if isinstance(egress, dict) else {}
    approved = egress.get("approved_destination_allowed", {}) if isinstance(egress, dict) else {}
    denied = egress.get("unapproved_destination_denied", {}) if isinstance(egress, dict) else {}
    egress_valid = (
        isinstance(egress, dict)
        and set(egress)
        == {
            "untrusted_control_destination_reachable",
            "approved_destination_allowed",
            "unapproved_destination_denied",
        }
        and isinstance(control, dict)
        and isinstance(approved, dict)
        and isinstance(denied, dict)
        and control.get("connected") is True
        and approved.get("connected") is True
        and denied.get("connected") is False
        and control.get("destination_host") == denied.get("destination_host")
        and control.get("destination_port") == denied.get("destination_port")
    )
    if not (
        report.get("schema_version") == NETWORK_SCHEMA_VERSION
        and report.get("status") == "PASS"
        and report.get("passed") is True
        and _aware_time(report.get("observed_at"))
        and _binding_matches(report, binding)
        and isinstance(external_scan, dict)
        and external_scan.get("transport") == "nmap TCP scan from acknowledged external vantage"
        and external_scan.get("passed") is True
        and external_scan.get("discovered_tcp_ports") == [443]
        and external_scan.get("private_data_services_unreachable") is True
        and isinstance(public_scan, dict)
        and raw_scan_retained(public_scan)
        and public_scan.get("requested_ports") == "1-65535"
        and public_scan.get("open_tcp_ports") == [443]
        and isinstance(private_scans, dict)
        and set(private_scans) == {"database", "redis", "object_store_direct"}
        and all(
            isinstance(item, dict)
            and raw_scan_retained(item)
            and item.get("open_tcp_ports") == []
            for item in private_scans.values()
        )
        and isinstance(policy_tests, dict)
        and policy_tests.get("passed") is True
        and isinstance(cni, dict)
        and isinstance(cni.get("desired"), int)
        and not isinstance(cni.get("desired"), bool)
        and cni.get("desired", 0) > 0
        and cni.get("ready") == cni.get("desired")
        and meaningful(cni.get("namespace"))
        and meaningful(cni.get("name"))
        and meaningful(cni.get("uid"))
        and meaningful(cni.get("resource_version"))
        and isinstance(cni.get("generation"), int)
        and not isinstance(cni.get("generation"), bool)
        and cni.get("generation") >= 1
        and isinstance(cni.get("images"), list)
        and bool(cni.get("images"))
        and all(
            isinstance(image, str) and bool(IMMUTABLE_IMAGE_RE.fullmatch(image))
            for image in cni["images"]
        )
        and policies_valid
        and probes_valid
        and ingress_valid
        and egress_valid
        and policy_tests.get("required_policies_present") is True
        and policy_tests.get("policy_snapshots_valid") is True
        and policy_tests.get("broad_world_egress_absent") is True
        and policy_tests.get("default_deny_ingress_exercised") is True
        and policy_tests.get("unapproved_egress_denied") is True
        and policy_tests.get("approved_egress_allowed") is True
        and policy_tests.get("private_data_services_unreachable_externally") is True
    ):
        raise ValueError("network evidence is not a passing release-bound target enforcement report")
    return report


def validate_state_services_report(path: Path, binding: dict[str, Any]) -> dict[str, Any]:
    report = _load_object(path, "state-services evidence")
    services = report.get("services")
    provider_receipt = report.get("provider_receipt")
    verification_receipt = report.get("verification_receipt")
    provider_signed = (
        provider_receipt.get("signed_evidence") if isinstance(provider_receipt, dict) else None
    )
    verifier_signed = (
        verification_receipt.get("signed_evidence")
        if isinstance(verification_receipt, dict)
        else None
    )
    if not (
        report.get("schema_version") == STATE_SCHEMA_VERSION
        and report.get("status") == "PASS"
        and report.get("passed") is True
        and _aware_time(report.get("observed_at"))
        and _binding_matches(report, binding)
        and report.get("managed_mysql_ha") is True
        and report.get("managed_redis_ha") is True
        and report.get("object_store_ha") is True
        and report.get("rwx_repository_storage_ha") is True
        and report.get("failover_exercised") is True
        and report.get("data_integrity_passed") is True
        and isinstance(services, dict)
        and set(services) == set(STATE_SERVICES)
        and isinstance(report.get("state_services_policy"), dict)
        and isinstance(provider_signed, dict)
        and isinstance(verifier_signed, dict)
        and provider_signed.get("signer_identity") != verifier_signed.get("signer_identity")
        and not state_contains_secret_material_key(report)
    ):
        raise ValueError("state-services evidence is not a passing release-bound target report")
    for name in STATE_SERVICES:
        service = services[name]
        if not (
            isinstance(service, dict)
            and isinstance(service.get("provider"), str)
            and bool(service["provider"].strip())
            and isinstance(service.get("service_instance_id"), str)
            and bool(service["service_instance_id"].strip())
            and isinstance(service.get("topology_id"), str)
            and bool(service["topology_id"].strip())
            and isinstance(service.get("fault_domain_count"), int)
            and not isinstance(service.get("fault_domain_count"), bool)
            and service["fault_domain_count"] >= 2
            and isinstance(service.get("failover_event_id"), str)
            and bool(service["failover_event_id"].strip())
            and isinstance(service.get("source_fault_domain"), str)
            and isinstance(service.get("destination_fault_domain"), str)
            and bool(service["source_fault_domain"].strip())
            and bool(service["destination_fault_domain"].strip())
            and service["source_fault_domain"] != service["destination_fault_domain"]
            and service.get("ha_enabled") is True
            and service.get("automatic_failover") is True
            and service.get("failover_exercised") is True
            and service.get("data_integrity_passed") is True
            and service.get("read_probe_passed") is True
            and service.get("write_probe_passed") is True
            and _ordered_times(service.get("started_at"), service.get("recovered_at"))
            and _ordered_times(service.get("recovered_at"), service.get("verified_at"))
            and _ordered_times(service.get("verified_at"), report.get("observed_at"))
            and bool(STATE_DIGEST_RE.fullmatch(str(service.get("provider_event_sha256", ""))))
            and bool(
                STATE_DIGEST_RE.fullmatch(str(service.get("verification_log_sha256", "")))
            )
        ):
            raise ValueError(f"state-services evidence has an invalid {name} receipt")
    return report


def load_approval_policy(path: Path, signer_identity: str) -> tuple[dict[str, Any], Path, str]:
    policy = _load_object(path, "approval policy")
    expected_keys = {
        "schema_version",
        "policy_id",
        "organization",
        "allowed_signers_path",
        "allowed_signers_sha256",
        "roles",
        "independent_security_assessors",
    }
    roles = policy.get("roles")
    required_roles = {"Product", "Architecture", "Security", "Operations"}
    role_lists_valid = (
        isinstance(roles, dict)
        and set(roles) == required_roles
        and all(isinstance(roles[role], list) for role in required_roles)
    )
    role_identities = (
        [identity for role in required_roles for identity in roles.get(role, [])]
        if role_lists_valid
        else []
    )
    roles_valid = (
        role_lists_valid
        and all(
            roles[role]
            and all(isinstance(identity, str) and identity.strip() for identity in roles[role])
            for role in required_roles
        )
        and len(role_identities) == len(set(role_identities))
    )
    assessors = policy.get("independent_security_assessors")
    assessors_valid = (
        isinstance(assessors, dict)
        and bool(assessors)
        and all(
            isinstance(provider, str)
            and bool(provider.strip())
            and isinstance(identities, list)
            and bool(identities)
            and all(isinstance(identity, str) and bool(identity.strip()) for identity in identities)
            and len(identities) == len(set(identities))
            for provider, identities in assessors.items()
        )
    )
    assessor_identities = (
        [identity for identities in assessors.values() for identity in identities]
        if assessors_valid
        else []
    )
    identities_exclusive = len(role_identities + assessor_identities) == len(
        set(role_identities + assessor_identities)
    )
    if not (
        set(policy) == expected_keys
        and policy.get("schema_version") == APPROVAL_POLICY_SCHEMA_VERSION
        and roles_valid
        and assessors_valid
        and identities_exclusive
        and signer_identity in roles.get("Operations", [])
    ):
        raise ValueError("state-services signer is not an Operations identity in the approval policy")
    raw_trust_path = policy.get("allowed_signers_path")
    if not isinstance(raw_trust_path, str) or not raw_trust_path:
        raise ValueError("approval policy allowed_signers_path is missing")
    trust_path = Path(raw_trust_path).expanduser()
    if not trust_path.is_absolute():
        trust_path = (path.parent / trust_path).resolve()
    if not trust_path.is_file() or _sha256(trust_path) != policy.get("allowed_signers_sha256"):
        raise ValueError("approval policy trust store is missing or digest-mismatched")
    return policy, trust_path, _sha256(path)


def verify_state_services_signature(
    report_path: Path,
    signature_path: Path,
    *,
    identity: str,
    allowed_signers: Path,
) -> None:
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
                "duckdock-ha-state-services",
                "-s",
                str(signature_path),
            ],
            input=report_path.read_bytes(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except OSError as exc:
        raise ValueError(f"cannot verify state-services signature: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stdout.decode("utf-8", errors="replace").strip()
        raise ValueError(f"invalid state-services Operations signature: {detail}")


class Kubectl:
    def __init__(self, context: str, namespace: str) -> None:
        self.context = context
        self.namespace = namespace

    def _command(self, arguments: Sequence[str], *, namespaced: bool) -> list[str]:
        command = ["kubectl", "--context", self.context]
        if namespaced:
            command.extend(["--namespace", self.namespace])
        command.extend(arguments)
        return command

    def text(
        self,
        arguments: Sequence[str],
        *,
        namespaced: bool = False,
        timeout: float = 600,
        check: bool = True,
    ) -> str:
        completed = subprocess.run(
            self._command(arguments, namespaced=namespaced),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            text=True,
        )
        if check and completed.returncode != 0:
            raise RuntimeError(
                f"kubectl failed ({' '.join(arguments)}): {completed.stdout.strip()}"
            )
        return completed.stdout.strip()

    def json(self, arguments: Sequence[str], *, namespaced: bool = False) -> dict[str, Any]:
        output = self.text([*arguments, "-o", "json"], namespaced=namespaced)
        value = json.loads(output)
        if not isinstance(value, dict):
            raise RuntimeError("kubectl JSON root is not an object")
        return value


def _node_zone(node: dict[str, Any]) -> str | None:
    labels = node.get("metadata", {}).get("labels", {})
    return labels.get("topology.kubernetes.io/zone") if isinstance(labels, dict) else None


def _node_ready(node: dict[str, Any]) -> bool:
    return any(
        condition.get("type") == "Ready" and condition.get("status") == "True"
        for condition in node.get("status", {}).get("conditions", [])
    )


def _ready_pod(pod: dict[str, Any]) -> bool:
    return pod.get("metadata", {}).get("deletionTimestamp") is None and any(
        condition.get("type") == "Ready" and condition.get("status") == "True"
        for condition in pod.get("status", {}).get("conditions", [])
    )


def component_snapshot(pods: dict[str, Any], nodes: dict[str, Any]) -> dict[str, Any]:
    node_zones = {
        item.get("metadata", {}).get("name"): _node_zone(item)
        for item in nodes.get("items", [])
        if isinstance(item, dict)
    }
    result: dict[str, Any] = {}
    for component in COMPONENTS:
        rows = []
        for pod in pods.get("items", []):
            labels = pod.get("metadata", {}).get("labels", {})
            if labels.get("app.kubernetes.io/component") != component:
                continue
            node_name = pod.get("spec", {}).get("nodeName")
            rows.append(
                {
                    "pod": pod.get("metadata", {}).get("name"),
                    "node": node_name,
                    "zone": node_zones.get(node_name),
                    "ready": _ready_pod(pod),
                }
            )
        result[component] = {
            "ready_replicas": sum(row["ready"] for row in rows),
            "pods": rows,
        }
    return result


def _ready_zones(snapshot: dict[str, Any], component: str) -> set[str]:
    return {
        str(item["zone"])
        for item in snapshot.get(component, {}).get("pods", [])
        if item.get("ready") is True and item.get("zone")
    }


def _ready_nodes(snapshot: dict[str, Any], component: str) -> set[str]:
    return {
        str(item["node"])
        for item in snapshot.get(component, {}).get("pods", [])
        if item.get("ready") is True and item.get("node")
    }


def _deployment_map(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        item.get("metadata", {}).get("name"): item
        for item in document.get("items", [])
        if isinstance(item, dict) and item.get("metadata", {}).get("name") in COMPONENTS
    }


def node_inventory(document: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for node in document.get("items", []):
        if not isinstance(node, dict):
            continue
        labels = node.get("metadata", {}).get("labels", {})
        rows.append(
            {
                "node": node.get("metadata", {}).get("name"),
                "zone": _node_zone(node),
                "ready": _node_ready(node),
                "schedulable": node.get("spec", {}).get("unschedulable") is not True,
                "control_plane": (
                    "node-role.kubernetes.io/control-plane" in labels
                    or "node-role.kubernetes.io/master" in labels
                ),
            }
        )
    return sorted(rows, key=lambda item: str(item.get("node")))


def validate_cluster_preflight(
    *,
    nodes: dict[str, Any],
    deployments: dict[str, Any],
    pods: dict[str, Any],
    drain_zone: str,
    backend_image: str,
    frontend_image: str,
) -> tuple[list[str], list[str], dict[str, Any]]:
    deployment_by_name = _deployment_map(deployments)
    if set(deployment_by_name) != set(COMPONENTS):
        raise ValueError("target namespace must contain backend/frontend/worker/beat deployments")
    expected_replicas = {"backend": 3, "frontend": 3, "worker": 3, "beat": 1}
    expected_images = {
        "backend": backend_image,
        "frontend": frontend_image,
        "worker": backend_image,
        "beat": backend_image,
    }
    for component in COMPONENTS:
        deployment = deployment_by_name[component]
        replicas = int(deployment.get("spec", {}).get("replicas", 0))
        ready = int(deployment.get("status", {}).get("readyReplicas", 0))
        containers = deployment.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        images = {container.get("image") for container in containers if isinstance(container, dict)}
        if replicas < expected_replicas[component] or ready < expected_replicas[component]:
            raise ValueError(f"{component} does not have the required ready replicas")
        if expected_images[component] not in images:
            raise ValueError(f"{component} does not run the declared immutable release image")

    ready_workers = []
    for node in nodes.get("items", []):
        labels = node.get("metadata", {}).get("labels", {})
        if not _node_ready(node) or node.get("spec", {}).get("unschedulable") is True:
            continue
        if "node-role.kubernetes.io/control-plane" in labels or "node-role.kubernetes.io/master" in labels:
            continue
        ready_workers.append(node)
    zones = sorted({zone for node in ready_workers if (zone := _node_zone(node))})
    if len(zones) < 2:
        raise ValueError("target cluster needs at least two ready worker fault domains")
    drain_nodes = sorted(
        node.get("metadata", {}).get("name")
        for node in ready_workers
        if _node_zone(node) == drain_zone
    )
    if not drain_nodes:
        raise ValueError("selected drain zone has no eligible ready worker nodes")
    surviving_nodes = [node for node in ready_workers if _node_zone(node) != drain_zone]
    if len(surviving_nodes) < 3:
        raise ValueError("at least three ready worker nodes must survive the selected zone disruption")

    before = component_snapshot(pods, nodes)
    if not all(before[name]["ready_replicas"] >= expected_replicas[name] for name in COMPONENTS):
        raise ValueError("pod snapshot does not prove all required replicas are ready")
    beat_rows = [item for item in before["beat"]["pods"] if item.get("ready")]
    if len(beat_rows) != 1 or beat_rows[0].get("zone") != drain_zone:
        raise ValueError("selected drain zone must contain the one ready Beat pod")
    report_domains = sorted(
        set().union(*(_ready_zones(before, name) for name in ("backend", "frontend", "worker")))
    )
    if drain_zone not in report_domains or len(report_domains) < 2:
        raise ValueError("stateless workloads are not spread across the selected fault domains")
    return drain_nodes, report_domains, before


@dataclass(slots=True)
class ProbeSample:
    observed_at: str
    passed: bool
    status_code: int | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "observed_at": self.observed_at,
                "passed": self.passed,
                "status_code": self.status_code,
                "error": self.error,
            }.items()
            if value is not None
        }


class ProbeRecorder:
    def __init__(self, probe: Callable[[], tuple[bool, int | None, str | None]], interval: float) -> None:
        self.probe = probe
        self.interval = interval
        self.samples: list[ProbeSample] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def sample(self) -> ProbeSample:
        passed, status_code, error = self.probe()
        value = ProbeSample(
            observed_at=datetime.now(timezone.utc).isoformat(),
            passed=passed,
            status_code=status_code,
            error=error,
        )
        self.samples.append(value)
        return value

    def start(self) -> None:
        if self._thread is not None:
            return

        def loop() -> None:
            while not self._stop.is_set():
                self.sample()
                self._stop.wait(self.interval)

        self._thread = threading.Thread(target=loop, name="duckdock-ga-ha-probe", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(5.0, self.interval * 2))


def _http_probe(base_url: str, ca_file: Path | None, timeout: float) -> tuple[bool, int | None, str | None]:
    class NoRedirect(urllib_request.HTTPRedirectHandler):
        def redirect_request(self, request, file_pointer, code, message, headers, new_url):
            del request, file_pointer, code, message, headers, new_url
            return None

    context = ssl.create_default_context(cafile=str(ca_file) if ca_file else None)
    request = urllib_request.Request(
        f"{base_url}/health",
        headers={"User-Agent": "DuckDock-GA-HA-Probe/2"},
    )
    try:
        opener = urllib_request.build_opener(urllib_request.HTTPSHandler(context=context), NoRedirect())
        with opener.open(request, timeout=timeout) as response:
            response.read(4096)
            return response.status == 200, response.status, None
    except urllib_error.HTTPError as exc:
        return False, exc.code, f"HTTPError: {exc}"
    except (urllib_error.URLError, TimeoutError, OSError) as exc:
        return False, None, f"{type(exc).__name__}: {exc}"


def _pdb_minimums(document: dict[str, Any]) -> dict[str, Any]:
    return {
        item.get("metadata", {}).get("name"): item.get("spec", {}).get("minAvailable")
        for item in document.get("items", [])
        if isinstance(item, dict)
    }


def execute(args: argparse.Namespace, *, kubectl: Kubectl | None = None) -> dict[str, Any]:
    runner = kubectl or Kubectl(args.context, args.namespace)
    validate_network_evidence(args.network_evidence, args.release_binding)
    state_report = validate_state_services_report(args.state_services_evidence, args.release_binding)
    policy, allowed_signers, policy_digest = load_approval_policy(
        args.approval_policy,
        args.state_services_signer_identity,
    )
    verify_state_services_signature(
        args.state_services_evidence,
        args.state_services_signature,
        identity=args.state_services_signer_identity,
        allowed_signers=allowed_signers,
    )

    if runner.text(["config", "get-contexts", args.context, "-o", "name"]) != args.context:
        raise ValueError("requested kube context does not exist exactly")
    for verb, resource, namespaced in (
        ("get", "pods", True),
        ("patch", "nodes", False),
        ("create", "pods/eviction", True),
    ):
        if runner.text(["auth", "can-i", verb, resource], namespaced=namespaced) != "yes":
            raise ValueError(f"kube identity cannot {verb} {resource}")

    nodes = runner.json(["get", "nodes"])
    deployments = runner.json(["get", "deployments"], namespaced=True)
    pods = runner.json(["get", "pods"], namespaced=True)
    drain_nodes, fault_domains, before = validate_cluster_preflight(
        nodes=nodes,
        deployments=deployments,
        pods=pods,
        drain_zone=args.drain_zone,
        backend_image=args.backend_image,
        frontend_image=args.frontend_image,
    )
    pdb = runner.json(["get", "poddisruptionbudgets"], namespaced=True)
    network_policies = runner.json(["get", "networkpolicies"], namespaced=True)
    server_version = runner.json(["version"])
    beat_original_node = next(
        item["node"] for item in before["beat"]["pods"] if item.get("ready") is True
    )

    recorder = ProbeRecorder(
        lambda: _http_probe(args.base_url, args.ca_file, args.probe_timeout),
        args.probe_interval,
    )
    if not recorder.sample().passed:
        raise ValueError("public HTTPS health probe failed before disruption")

    mutated_nodes: list[str] = []
    cleanup_errors: list[str] = []
    exercise_error: str | None = None
    after_drain: dict[str, Any] = {}
    restored: dict[str, Any] = {}
    started_at = datetime.now(timezone.utc)
    recovered_at: datetime | None = None
    recorder.start()
    try:
        for node in drain_nodes:
            runner.text(["taint", "node", node, TAINT, "--overwrite"])
            mutated_nodes.append(node)
        for node in drain_nodes:
            runner.text(
                [
                    "drain",
                    node,
                    "--ignore-daemonsets",
                    "--delete-emptydir-data",
                    "--force",
                    f"--grace-period={args.grace_period}",
                    f"--timeout={args.recovery_timeout}s",
                ],
                timeout=args.recovery_timeout + 60,
            )
        for component in COMPONENTS:
            runner.text(
                ["rollout", "status", f"deployment/{component}", f"--timeout={args.recovery_timeout}s"],
                namespaced=True,
                timeout=args.recovery_timeout + 30,
            )
        after_drain = component_snapshot(runner.json(["get", "pods"], namespaced=True), nodes)
        recovered_at = datetime.now(timezone.utc)
        deadline = time.monotonic() + max(5.0, args.probe_interval * 6)
        while len(recorder.samples) < 5 and time.monotonic() < deadline:
            time.sleep(min(args.probe_interval, 0.1))
    except Exception as exc:  # evidence must survive a failed disruption
        exercise_error = f"{type(exc).__name__}: {exc}"
    finally:
        recorder.stop()
        for node in reversed(mutated_nodes):
            try:
                runner.text(["taint", "node", node, f"{TAINT_KEY}-"])
            except Exception as exc:
                cleanup_errors.append(f"remove taint {node}: {exc}")
            try:
                runner.text(["uncordon", node])
            except Exception as exc:
                cleanup_errors.append(f"uncordon {node}: {exc}")

    if exercise_error is None and not cleanup_errors:
        try:
            for component in ("backend", "frontend", "worker"):
                runner.text(["rollout", "restart", f"deployment/{component}"], namespaced=True)
            for component in ("backend", "frontend", "worker"):
                runner.text(
                    ["rollout", "status", f"deployment/{component}", f"--timeout={args.rebalance_timeout}s"],
                    namespaced=True,
                    timeout=args.rebalance_timeout + 30,
                )
            restored_nodes = runner.json(["get", "nodes"])
            restored = component_snapshot(
                runner.json(["get", "pods"], namespaced=True),
                restored_nodes,
            )
        except Exception as exc:
            exercise_error = f"rebalance {type(exc).__name__}: {exc}"

    samples = [sample.as_dict() for sample in recorder.samples]
    probe_failures = sum(not sample["passed"] for sample in samples)
    expected_replicas = {"backend": 3, "frontend": 3, "worker": 3, "beat": 1}
    replicas_recovered = bool(after_drain) and all(
        after_drain.get(name, {}).get("ready_replicas", 0) >= expected_replicas[name]
        for name in COMPONENTS
    )
    drained_zone_absent = bool(after_drain) and all(
        args.drain_zone not in _ready_zones(after_drain, name) for name in COMPONENTS
    )
    beat_recovery_node = next(
        (
            item.get("node")
            for item in after_drain.get("beat", {}).get("pods", [])
            if item.get("ready") is True
        ),
        None,
    )
    beat_recovered = (
        beat_recovery_node is not None
        and beat_recovery_node != beat_original_node
        and beat_recovery_node not in drain_nodes
    )
    restored_spread = bool(restored) and all(
        set(fault_domains).issubset(_ready_zones(restored, name))
        for name in ("backend", "frontend", "worker")
    )
    passed = (
        exercise_error is None
        and not cleanup_errors
        and replicas_recovered
        and drained_zone_absent
        and beat_recovered
        and restored_spread
        and len(samples) >= 5
        and probe_failures == 0
    )
    observed_at = datetime.now(timezone.utc).isoformat()
    state_summary = {
        key: state_report[key]
        for key in (
            "managed_mysql_ha",
            "managed_redis_ha",
            "object_store_ha",
            "rwx_repository_storage_ha",
            "failover_exercised",
            "data_integrity_passed",
        )
    }
    state_summary["evidence"] = {
        "path": str(args.state_services_evidence.resolve()),
        "sha256": _sha256(args.state_services_evidence),
        "signer_identity": args.state_services_signer_identity,
        "signature_path": str(args.state_services_signature.resolve()),
        "approval_policy_id": policy["policy_id"],
        "approval_policy_sha256": policy_digest,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        **args.release_binding,
        "status": "PASS" if passed else "BLOCK",
        "passed": passed,
        "observed_at": observed_at,
        "cluster_context": args.context,
        "namespace": args.namespace,
        "kubernetes_version": server_version.get("serverVersion", {}).get("gitVersion"),
        "cluster_nodes": node_inventory(nodes),
        "fault_domains": fault_domains,
        "fault_domains_exercised": len(fault_domains),
        "replica_counts": expected_replicas,
        "before": before,
        "after_zone_drain": after_drain,
        "after_zone_return_and_rolling_rebalance": restored,
        "fault_injection": {
            "drained_nodes": drain_nodes,
            "drained_node": beat_original_node,
            "drained_zone": args.drain_zone,
            "started_at": started_at.isoformat(),
            "recovered_at": recovered_at.isoformat() if recovered_at else None,
            "recovery_seconds": int((recovered_at - started_at).total_seconds()) if recovered_at else -1,
            "node_failover_passed": replicas_recovered and drained_zone_absent and probe_failures == 0,
            "zone_failover_passed": replicas_recovered and drained_zone_absent and probe_failures == 0,
            "beat_recovery_passed": beat_recovered,
            "beat_original_node": beat_original_node,
            "beat_recovery_node": beat_recovery_node,
        },
        "availability_probe": {
            "transport": "network HTTPS against target",
            "base_url": args.base_url,
            "sample_count": len(samples),
            "failure_count": probe_failures,
            "passed": len(samples) >= 5 and probe_failures == 0,
            "samples": samples,
        },
        "pdb_min_available": _pdb_minimums(pdb),
        "network_policy": {
            "objects_admitted": len(network_policies.get("items", [])),
            "enforcement_exercised": True,
            "passed": True,
            "evidence": {
                "path": str(args.network_evidence.resolve()),
                "sha256": _sha256(args.network_evidence),
            },
        },
        "state_services": state_summary,
        "cleanup": {
            "restored_nodes": mutated_nodes,
            "errors": cleanup_errors,
            "passed": not cleanup_errors,
        },
        "exercise_error": exercise_error,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True, help="exact kubeconfig context")
    parser.add_argument("--namespace", default="duckdock")
    parser.add_argument("--target-environment", required=True)
    parser.add_argument("--execution-campaign", type=Path, required=True)
    parser.add_argument("--phase-action-id", required=True)
    parser.add_argument("--acknowledge-target-disruption", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--backend-image", required=True)
    parser.add_argument("--frontend-image", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--drain-zone", required=True)
    parser.add_argument("--network-evidence", type=Path, required=True)
    parser.add_argument("--state-services-evidence", type=Path, required=True)
    parser.add_argument("--state-services-signature", type=Path, required=True)
    parser.add_argument("--state-services-signer-identity", required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument("--probe-interval", type=float, default=2.0)
    parser.add_argument("--probe-timeout", type=float, default=10.0)
    parser.add_argument("--grace-period", type=int, default=30)
    parser.add_argument("--recovery-timeout", type=int, default=600)
    parser.add_argument("--rebalance-timeout", type=int, default=600)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite-output", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.acknowledge_target_disruption != args.target_environment:
            raise ValueError("--acknowledge-target-disruption must exactly equal target environment")
        if not args.context.strip() or not args.namespace.strip() or not args.drain_zone.strip():
            raise ValueError("context, namespace and drain zone must be non-empty")
        if args.probe_interval <= 0 or args.probe_timeout <= 0:
            raise ValueError("probe interval and timeout must be positive")
        if min(args.grace_period, args.recovery_timeout, args.rebalance_timeout) <= 0:
            raise ValueError("grace/recovery/rebalance timeouts must be positive")
        if args.output.exists() and not args.overwrite_output:
            raise ValueError("output already exists; choose a new evidence path or pass --overwrite-output")
        args.base_url = require_safe_base_url(args.base_url)
        args.release_binding = build_release_binding(
            scope="target-production",
            target_environment=args.target_environment,
            source_commit=args.source_commit,
            backend_image=args.backend_image,
            frontend_image=args.frontend_image,
        )
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        verify_runtime_entry(
            args.execution_campaign,
            phase_id="high_availability",
            action_id=args.phase_action_id,
            release_binding=args.release_binding,
            target_environment=args.target_environment,
            kubernetes_context=args.context,
            namespace=args.namespace,
        )
        verify_live_target_cluster_access(
            args.execution_campaign,
            phase_id="high_availability",
            operational_scope={
                "context": args.context,
                "namespace": args.namespace,
                "drain_zone": args.drain_zone,
            },
        )
        report = execute(args)
    except (ValueError, RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(f"Target HA collection failed before a report could be completed: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("Target HA collection interrupted after cleanup", file=sys.stderr)
        return 130
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
