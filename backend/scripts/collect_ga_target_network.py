#!/usr/bin/env python3
"""Probe external exposure and Kubernetes CNI enforcement for a signed GA report."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

try:
    from scripts.ga_network_evidence import (
        EXERCISE_RE,
        NETWORK_RAW_SCHEMA_VERSION,
        contains_secret_material_key,
    )
    from scripts.ga_release_identity import build_release_binding
    from scripts.ga_execution_phase_start import verify_runtime_entry
    from scripts.ga_target_cluster_access import verify_live_target_cluster_access
except ModuleNotFoundError:  # direct `python backend/scripts/...` execution
    from ga_network_evidence import (
        EXERCISE_RE,
        NETWORK_RAW_SCHEMA_VERSION,
        contains_secret_material_key,
    )
    from ga_release_identity import build_release_binding
    from ga_execution_phase_start import verify_runtime_entry
    from ga_target_cluster_access import verify_live_target_cluster_access


SCHEMA_VERSION = NETWORK_RAW_SCHEMA_VERSION
IMAGE_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_NETWORK_POLICIES = {
    "default-deny",
    "allow-dns",
    "frontend-ingress-and-backend",
    "backend-from-frontend",
    "controlled-external-egress",
}
CONNECT_SCRIPT = """
import socket
import sys
host, port, timeout = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
with socket.create_connection((host, port), timeout=timeout):
    pass
""".strip()


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _meaningful(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "__CHANGE_ME" not in value


def require_safe_base_url(raw: str) -> tuple[str, str]:
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
    return f"https://{parsed.netloc}", parsed.hostname


def require_ip(raw: str, label: str, *, globally_routable: bool = False) -> str:
    try:
        address = ipaddress.ip_address(raw)
    except ValueError as exc:
        raise ValueError(f"{label} must be a literal IP address") from exc
    if globally_routable and not address.is_global:
        raise ValueError(f"{label} must be a globally routable external address")
    return str(address)


@dataclass(frozen=True, slots=True)
class ScanResult:
    host: str
    requested_ports: str
    host_state: str
    addresses: list[str]
    port_states: dict[int, str]
    nmap_version: str
    elapsed_seconds: float
    raw_nmap_xml: str
    xml_sha256: str

    @property
    def open_ports(self) -> list[int]:
        return sorted(port for port, state in self.port_states.items() if state == "open")

    def as_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "requested_ports": self.requested_ports,
            "host_state": self.host_state,
            "addresses": self.addresses,
            "port_states": {str(port): state for port, state in sorted(self.port_states.items())},
            "open_tcp_ports": self.open_ports,
            "nmap_version": self.nmap_version,
            "elapsed_seconds": self.elapsed_seconds,
            "raw_nmap_xml": self.raw_nmap_xml,
            "xml_sha256": self.xml_sha256,
        }


def _nmap_xml_summary(raw_xml: str) -> dict[str, Any] | None:
    try:
        root = ET.fromstring(raw_xml)
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
    port_states: dict[int, str] = {}
    for port_node in host_node.findall("./ports/port"):
        state_node = port_node.find("state")
        port_id = port_node.get("portid")
        if state_node is not None and port_id and port_id.isdigit():
            port_states[int(port_id)] = state_node.get("state", "unknown")
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


def _scan_identity_valid(result: ScanResult) -> bool:
    try:
        addresses_valid = bool(result.addresses) and all(
            str(ipaddress.ip_address(address)) == address for address in result.addresses
        )
    except ValueError:
        addresses_valid = False
    xml_summary = _nmap_xml_summary(result.raw_nmap_xml)
    return (
        result.host_state == "up"
        and addresses_valid
        and _meaningful(result.nmap_version)
        and result.nmap_version != "unknown"
        and result.elapsed_seconds >= 0
        and _meaningful(result.raw_nmap_xml)
        and hashlib.sha256(result.raw_nmap_xml.encode("utf-8")).hexdigest()
        == result.xml_sha256
        and bool(DIGEST_RE.fullmatch(result.xml_sha256))
        and isinstance(xml_summary, dict)
        and xml_summary["host_state"] == result.host_state
        and xml_summary["addresses"] == result.addresses
        and xml_summary["port_states"] == result.port_states
        and xml_summary["nmap_version"] == result.nmap_version
        and xml_summary["elapsed_seconds"] == result.elapsed_seconds
    )


class NmapScanner:
    def __init__(self, *, host_timeout_seconds: int) -> None:
        self.host_timeout_seconds = host_timeout_seconds

    def scan(self, host: str, ports: str, *, open_only: bool) -> ScanResult:
        command = [
            "nmap",
            "-Pn",
            "-n",
            "--reason",
            "--host-timeout",
            f"{self.host_timeout_seconds}s",
            "-p",
            ports,
            "-oX",
            "-",
        ]
        if open_only:
            command.append("--open")
        command.append(host)
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.host_timeout_seconds + 60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"cannot execute nmap for {host}: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"nmap failed for {host}: {detail}")
        xml_bytes = completed.stdout
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as exc:
            raise RuntimeError(f"nmap returned invalid XML for {host}: {exc}") from exc
        host_node = root.find("host")
        status_node = host_node.find("status") if host_node is not None else None
        addresses = (
            sorted(
                node.get("addr", "")
                for node in host_node.findall("address")
                if node.get("addr") and node.get("addrtype") in {None, "ipv4", "ipv6"}
            )
            if host_node is not None
            else []
        )
        port_states: dict[int, str] = {}
        if host_node is not None:
            for port_node in host_node.findall("./ports/port"):
                state_node = port_node.find("state")
                port_id = port_node.get("portid")
                if state_node is not None and port_id and port_id.isdigit():
                    port_states[int(port_id)] = state_node.get("state", "unknown")
        finished = root.find("./runstats/finished")
        return ScanResult(
            host=host,
            requested_ports=ports,
            host_state=status_node.get("state", "unknown") if status_node is not None else "unknown",
            addresses=addresses,
            port_states=port_states,
            nmap_version=root.get("version", "unknown"),
            elapsed_seconds=float(finished.get("elapsed", "0")) if finished is not None else 0.0,
            raw_nmap_xml=xml_bytes.decode("utf-8"),
            xml_sha256=hashlib.sha256(xml_bytes).hexdigest(),
        )


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    output: str


class Kubectl:
    def __init__(self, context: str, namespace: str) -> None:
        self.context = context
        self.namespace = namespace

    def run(
        self,
        arguments: Sequence[str],
        *,
        namespace: str | None = None,
        timeout: float = 60,
        check: bool = True,
    ) -> CommandResult:
        command = ["kubectl", "--context", self.context]
        if namespace is not None:
            command.extend(["--namespace", namespace])
        command.extend(arguments)
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
                text=True,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"cannot execute kubectl: {exc}") from exc
        result = CommandResult(completed.returncode, completed.stdout.strip())
        if check and result.returncode != 0:
            raise RuntimeError(f"kubectl failed ({' '.join(arguments)}): {result.output}")
        return result

    def text(
        self,
        arguments: Sequence[str],
        *,
        namespace: str | None = None,
        timeout: float = 60,
    ) -> str:
        return self.run(arguments, namespace=namespace, timeout=timeout).output

    def json(
        self,
        arguments: Sequence[str],
        *,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        value = json.loads(self.text([*arguments, "-o", "json"], namespace=namespace))
        if not isinstance(value, dict):
            raise RuntimeError("kubectl JSON root is not an object")
        return value

    def connection_probe(
        self,
        *,
        namespace: str,
        source: str,
        host: str,
        port: int,
        timeout: float,
    ) -> dict[str, Any]:
        result = self.run(
            [
                "exec",
                source,
                "--",
                "python3",
                "-c",
                CONNECT_SCRIPT,
                host,
                str(port),
                str(timeout),
            ],
            namespace=namespace,
            timeout=timeout + 15,
            check=False,
        )
        return {
            "source_namespace": namespace,
            "source": source,
            "destination_host": host,
            "destination_port": port,
            "connected": result.returncode == 0,
            "kubectl_exit_code": result.returncode,
            "result": "connected" if result.returncode == 0 else "denied-or-unreachable",
        }


def _pod_ready(document: dict[str, Any]) -> bool:
    return any(
        condition.get("type") == "Ready" and condition.get("status") == "True"
        for condition in document.get("status", {}).get("conditions", [])
    )


def _validate_probe_pod(
    kubectl: Kubectl,
    *,
    namespace: str,
    pod: str,
    trusted_label: str | None,
) -> dict[str, Any]:
    namespace_object = kubectl.json(["get", "namespace", namespace])
    namespace_metadata = namespace_object.get("metadata", {})
    labels = namespace_metadata.get("labels", {})
    if not (
        isinstance(labels, dict)
        and _meaningful(namespace_metadata.get("uid"))
        and _meaningful(namespace_metadata.get("resourceVersion"))
    ):
        raise ValueError(f"probe namespace {namespace} lacks auditable identity metadata")
    if trusted_label is not None:
        if labels.get(trusted_label) != "true":
            raise ValueError(f"trusted probe namespace lacks {trusted_label}=true")
    elif labels.get("duckdock.io/ingress") == "true" or labels.get("duckdock.io/monitoring") == "true":
        raise ValueError("untrusted probe namespace carries a trusted DuckDock namespace label")
    pod_object = kubectl.json(["get", "pod", pod], namespace=namespace)
    if not _pod_ready(pod_object):
        raise ValueError(f"probe pod {namespace}/{pod} is not Ready")
    pod_metadata = pod_object.get("metadata", {})
    if not (
        _meaningful(pod_metadata.get("uid"))
        and _meaningful(pod_metadata.get("resourceVersion"))
    ):
        raise ValueError(f"probe pod {namespace}/{pod} lacks auditable identity metadata")
    return {
        "namespace": namespace,
        "namespace_uid": namespace_metadata.get("uid"),
        "namespace_resource_version": namespace_metadata.get("resourceVersion"),
        "namespace_labels": labels,
        "pod": pod,
        "pod_uid": pod_metadata.get("uid"),
        "pod_resource_version": pod_metadata.get("resourceVersion"),
    }


def _policy_snapshot(document: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    rows = []
    broad_egress = False
    for item in document.get("items", []):
        if not isinstance(item, dict):
            continue
        name = item.get("metadata", {}).get("name")
        spec = item.get("spec")
        if not isinstance(name, str) or not isinstance(spec, dict):
            continue
        for rule in spec.get("egress", []) or []:
            if not isinstance(rule, dict):
                continue
            for target in rule.get("to", []) or []:
                ip_block = target.get("ipBlock") if isinstance(target, dict) else None
                if isinstance(ip_block, dict) and ip_block.get("cidr") in {"0.0.0.0/0", "::/0"}:
                    broad_egress = True
        rows.append(
            {
                "name": name,
                "uid": item.get("metadata", {}).get("uid"),
                "resource_version": item.get("metadata", {}).get("resourceVersion"),
                "generation": item.get("metadata", {}).get("generation"),
                "spec_sha256": _sha256_json(spec),
                "spec": spec,
            }
        )
    return sorted(rows, key=lambda value: value["name"]), broad_egress


def _cni_snapshot(document: dict[str, Any], namespace: str, name: str) -> dict[str, Any]:
    status = document.get("status", {})
    desired = int(status.get("desiredNumberScheduled", 0))
    ready = int(status.get("numberReady", 0))
    containers = document.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    images = sorted(
        container.get("image")
        for container in containers
        if isinstance(container, dict) and _meaningful(container.get("image"))
    )
    metadata = document.get("metadata", {})
    if (
        desired <= 0
        or ready != desired
        or not images
        or not all(IMAGE_RE.fullmatch(image) for image in images)
        or not _meaningful(metadata.get("uid"))
        or not _meaningful(metadata.get("resourceVersion"))
        or not isinstance(metadata.get("generation"), int)
        or isinstance(metadata.get("generation"), bool)
        or metadata.get("generation") < 1
    ):
        raise ValueError("CNI daemonset is not fully ready or has no immutable runtime identity")
    return {
        "namespace": namespace,
        "name": name,
        "uid": metadata.get("uid"),
        "resource_version": metadata.get("resourceVersion"),
        "generation": metadata.get("generation"),
        "desired": desired,
        "ready": ready,
        "images": images,
    }


def collect(
    args: argparse.Namespace,
    *,
    scanner: NmapScanner | None = None,
    kubectl: Kubectl | None = None,
) -> dict[str, Any]:
    network_scanner = scanner or NmapScanner(host_timeout_seconds=args.scan_timeout)
    cluster = kubectl or Kubectl(args.context, args.namespace)
    if cluster.text(["config", "get-contexts", args.context, "-o", "name"]) != args.context:
        raise ValueError("requested kube context does not exist exactly")
    if cluster.text(["auth", "can-i", "get", "networkpolicies"], namespace=args.namespace) != "yes":
        raise ValueError("kube identity cannot read target NetworkPolicies")
    for probe_namespace in {
        args.namespace,
        args.trusted_probe_namespace,
        args.monitoring_probe_namespace,
        args.untrusted_probe_namespace,
    }:
        if cluster.text(["auth", "can-i", "create", "pods/exec"], namespace=probe_namespace) != "yes":
            raise ValueError(f"kube identity cannot execute network probes in {probe_namespace}")

    probe_identities = {
        "ingress": _validate_probe_pod(
            cluster,
            namespace=args.trusted_probe_namespace,
            pod=args.trusted_probe_pod,
            trusted_label="duckdock.io/ingress",
        ),
        "monitoring": _validate_probe_pod(
            cluster,
            namespace=args.monitoring_probe_namespace,
            pod=args.monitoring_probe_pod,
            trusted_label="duckdock.io/monitoring",
        ),
        "untrusted": _validate_probe_pod(
            cluster,
            namespace=args.untrusted_probe_namespace,
            pod=args.untrusted_probe_pod,
            trusted_label=None,
        ),
    }

    policies_document = cluster.json(["get", "networkpolicies"], namespace=args.namespace)
    policy_snapshots, broad_egress = _policy_snapshot(policies_document)
    policy_names = [item["name"] for item in policy_snapshots]
    policy_snapshots_valid = len(policy_names) == len(set(policy_names)) and all(
        _meaningful(item.get("uid"))
        and _meaningful(item.get("resource_version"))
        and isinstance(item.get("generation"), int)
        and not isinstance(item.get("generation"), bool)
        and item.get("generation") >= 1
        for item in policy_snapshots
    )
    policies_present = (
        REQUIRED_NETWORK_POLICIES.issubset(set(policy_names)) and policy_snapshots_valid
    )
    cni_document = cluster.json(
        ["get", "daemonset", args.cni_daemonset_name],
        namespace=args.cni_daemonset_namespace,
    )
    cni = _cni_snapshot(cni_document, args.cni_daemonset_namespace, args.cni_daemonset_name)

    public_scan = network_scanner.scan(args.public_host, "1-65535", open_only=True)
    data_scans = {
        "database": network_scanner.scan(args.database_address, str(args.database_port), open_only=False),
        "redis": network_scanner.scan(args.redis_address, str(args.redis_port), open_only=False),
        "object_store_direct": network_scanner.scan(
            args.object_store_direct_address,
            str(args.object_store_direct_port),
            open_only=False,
        ),
    }
    public_scan_ok = _scan_identity_valid(public_scan) and public_scan.open_ports == [443]
    private_scans_ok = all(
        _scan_identity_valid(result)
        and not result.open_ports
        and result.port_states.get(int(result.requested_ports)) not in {None, "open"}
        for result in data_scans.values()
    )

    frontend_service = f"frontend.{args.namespace}.svc.cluster.local"
    backend_service = f"backend.{args.namespace}.svc.cluster.local"
    ingress_tests = {
        "trusted_frontend_allowed": cluster.connection_probe(
            namespace=args.trusted_probe_namespace,
            source=f"pod/{args.trusted_probe_pod}",
            host=frontend_service,
            port=8080,
            timeout=args.connection_timeout,
        ),
        "trusted_backend_allowed": cluster.connection_probe(
            namespace=args.monitoring_probe_namespace,
            source=f"pod/{args.monitoring_probe_pod}",
            host=backend_service,
            port=8801,
            timeout=args.connection_timeout,
        ),
        "untrusted_frontend_denied": cluster.connection_probe(
            namespace=args.untrusted_probe_namespace,
            source=f"pod/{args.untrusted_probe_pod}",
            host=frontend_service,
            port=8080,
            timeout=args.connection_timeout,
        ),
        "untrusted_backend_denied": cluster.connection_probe(
            namespace=args.untrusted_probe_namespace,
            source=f"pod/{args.untrusted_probe_pod}",
            host=backend_service,
            port=8801,
            timeout=args.connection_timeout,
        ),
    }
    egress_tests = {
        "untrusted_control_destination_reachable": cluster.connection_probe(
            namespace=args.untrusted_probe_namespace,
            source=f"pod/{args.untrusted_probe_pod}",
            host=args.unapproved_egress_host,
            port=args.unapproved_egress_port,
            timeout=args.connection_timeout,
        ),
        "approved_destination_allowed": cluster.connection_probe(
            namespace=args.namespace,
            source="deployment/backend",
            host=args.approved_egress_host,
            port=args.approved_egress_port,
            timeout=args.connection_timeout,
        ),
        "unapproved_destination_denied": cluster.connection_probe(
            namespace=args.namespace,
            source="deployment/backend",
            host=args.unapproved_egress_host,
            port=args.unapproved_egress_port,
            timeout=args.connection_timeout,
        ),
    }
    default_deny_ingress_exercised = (
        ingress_tests["trusted_frontend_allowed"]["connected"] is True
        and ingress_tests["trusted_backend_allowed"]["connected"] is True
        and ingress_tests["untrusted_frontend_denied"]["connected"] is False
        and ingress_tests["untrusted_backend_denied"]["connected"] is False
    )
    approved_egress_allowed = egress_tests["approved_destination_allowed"]["connected"] is True
    unapproved_egress_denied = (
        egress_tests["untrusted_control_destination_reachable"]["connected"] is True
        and egress_tests["unapproved_destination_denied"]["connected"] is False
    )
    policy_passed = (
        policies_present
        and not broad_egress
        and default_deny_ingress_exercised
        and approved_egress_allowed
        and unapproved_egress_denied
    )
    passed = public_scan_ok and private_scans_ok and policy_passed
    report = {
        "schema_version": SCHEMA_VERSION,
        **args.release_binding,
        "status": "PASS" if passed else "BLOCK",
        "passed": passed,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "exercise_id": args.exercise_id,
        "probe": {
            "probe_id": args.scanner_id,
            "vantage_id": args.vantage_id,
            "vantage_class": "external-internet",
            "source_ip": args.scanner_source_ip,
        },
        "public_tcp_ports": public_scan.open_ports,
        "database_public": bool(data_scans["database"].open_ports),
        "redis_public": bool(data_scans["redis"].open_ports),
        "object_store_direct_public": bool(data_scans["object_store_direct"].open_ports),
        "default_deny_ingress": default_deny_ingress_exercised,
        "egress_allowlist_enforced": approved_egress_allowed and unapproved_egress_denied and not broad_egress,
        "enforced_by": f"{cni['namespace']}/{cni['name']} {' '.join(cni['images'])}",
        "external_scan": {
            "transport": "nmap TCP scan from acknowledged external vantage",
            "scanner_id": args.scanner_id,
            "scanner_source_ip": args.scanner_source_ip,
            "public_ingress": public_scan.as_dict(),
            "private_data_services": {name: result.as_dict() for name, result in data_scans.items()},
            "discovered_tcp_ports": public_scan.open_ports,
            "private_data_services_unreachable": private_scans_ok,
            "passed": public_scan_ok and private_scans_ok,
        },
        "policy_tests": {
            "cluster_context": args.context,
            "namespace": args.namespace,
            "cni": cni,
            "probe_identities": probe_identities,
            "network_policies": policy_snapshots,
            "required_policies_present": policies_present,
            "policy_snapshots_valid": policy_snapshots_valid,
            "broad_world_egress_absent": not broad_egress,
            "ingress_tests": ingress_tests,
            "egress_tests": egress_tests,
            "default_deny_ingress_exercised": default_deny_ingress_exercised,
            "unapproved_egress_denied": unapproved_egress_denied,
            "approved_egress_allowed": approved_egress_allowed,
            "private_data_services_unreachable_externally": private_scans_ok,
            "passed": policy_passed,
        },
    }
    if contains_secret_material_key(report):
        raise ValueError("network probe contains a forbidden credential field")
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True)
    parser.add_argument("--namespace", default="duckdock")
    parser.add_argument("--target-environment", required=True)
    parser.add_argument("--execution-campaign", type=Path, required=True)
    parser.add_argument("--phase-action-id", required=True)
    parser.add_argument("--acknowledge-external-vantage", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--backend-image", required=True)
    parser.add_argument("--frontend-image", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--exercise-id", required=True)
    parser.add_argument("--scanner-id", required=True)
    parser.add_argument("--vantage-id", required=True)
    parser.add_argument("--scanner-source-ip", required=True)
    parser.add_argument("--database-address", required=True)
    parser.add_argument("--database-port", type=int, default=3306)
    parser.add_argument("--redis-address", required=True)
    parser.add_argument("--redis-port", type=int, default=6379)
    parser.add_argument("--object-store-direct-address", required=True)
    parser.add_argument("--object-store-direct-port", type=int, default=9000)
    parser.add_argument("--trusted-probe-namespace", required=True)
    parser.add_argument("--trusted-probe-pod", required=True)
    parser.add_argument("--monitoring-probe-namespace", required=True)
    parser.add_argument("--monitoring-probe-pod", required=True)
    parser.add_argument("--untrusted-probe-namespace", required=True)
    parser.add_argument("--untrusted-probe-pod", required=True)
    parser.add_argument("--approved-egress-host", required=True)
    parser.add_argument("--approved-egress-port", type=int, required=True)
    parser.add_argument("--unapproved-egress-host", required=True)
    parser.add_argument("--unapproved-egress-port", type=int, required=True)
    parser.add_argument("--cni-daemonset-namespace", default="kube-system")
    parser.add_argument("--cni-daemonset-name", required=True)
    parser.add_argument("--scan-timeout", type=int, default=900)
    parser.add_argument("--connection-timeout", type=float, default=5.0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite-output", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.acknowledge_external_vantage != args.target_environment:
            raise ValueError("--acknowledge-external-vantage must exactly equal target environment")
        if not EXERCISE_RE.fullmatch(args.exercise_id):
            raise ValueError("exercise ID must be 8-64 safe characters")
        if not all(
            _meaningful(value)
            for value in (
                args.context,
                args.namespace,
                args.scanner_id,
                args.vantage_id,
                args.trusted_probe_namespace,
                args.trusted_probe_pod,
                args.monitoring_probe_namespace,
                args.monitoring_probe_pod,
                args.untrusted_probe_namespace,
                args.untrusted_probe_pod,
                args.approved_egress_host,
                args.unapproved_egress_host,
                args.cni_daemonset_name,
            )
        ):
            raise ValueError("context, probe, scanner, egress and CNI identifiers must be non-placeholder")
        if len(
            {
                args.trusted_probe_namespace,
                args.monitoring_probe_namespace,
                args.untrusted_probe_namespace,
            }
        ) != 3:
            raise ValueError("ingress, monitoring and untrusted probes must use different namespaces")
        if min(
            args.database_port,
            args.redis_port,
            args.object_store_direct_port,
            args.approved_egress_port,
            args.unapproved_egress_port,
            args.scan_timeout,
            args.connection_timeout,
        ) <= 0:
            raise ValueError("ports and timeouts must be positive")
        if any(
            port > 65535
            for port in (
                args.database_port,
                args.redis_port,
                args.object_store_direct_port,
                args.approved_egress_port,
                args.unapproved_egress_port,
            )
        ):
            raise ValueError("ports must be <= 65535")
        if args.output.exists() and not args.overwrite_output:
            raise ValueError("output already exists; choose a new evidence path or pass --overwrite-output")
        args.base_url, args.public_host = require_safe_base_url(args.base_url)
        args.scanner_source_ip = require_ip(
            args.scanner_source_ip,
            "scanner source IP",
            globally_routable=True,
        )
        args.database_address = require_ip(args.database_address, "database address")
        args.redis_address = require_ip(args.redis_address, "Redis address")
        args.object_store_direct_address = require_ip(
            args.object_store_direct_address,
            "object-store direct address",
        )
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
            phase_id="network",
            action_id=args.phase_action_id,
            release_binding=args.release_binding,
            target_environment=args.target_environment,
            kubernetes_context=args.context,
            namespace=args.namespace,
        )
        verify_live_target_cluster_access(
            args.execution_campaign,
            phase_id="network",
            operational_scope={
                "context": args.context,
                "namespace": args.namespace,
                "trusted_probe_namespace": args.trusted_probe_namespace,
                "trusted_probe_pod": args.trusted_probe_pod,
                "monitoring_probe_namespace": args.monitoring_probe_namespace,
                "monitoring_probe_pod": args.monitoring_probe_pod,
                "untrusted_probe_namespace": args.untrusted_probe_namespace,
                "untrusted_probe_pod": args.untrusted_probe_pod,
                "cni_daemonset_namespace": args.cni_daemonset_namespace,
                "cni_daemonset_name": args.cni_daemonset_name,
            },
        )
        report = collect(args)
    except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Target network probe failed: {exc}", file=sys.stderr)
        return 3
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
