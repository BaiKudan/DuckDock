from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.collect_ga_target_network as target_network
from scripts.ga_network_evidence import validate_network_probe_envelope
from scripts.ga_release_identity import build_release_binding


COMMIT = "a" * 40
BACKEND_IMAGE = f"registry.example.com/duckdock/backend@sha256:{'b' * 64}"
FRONTEND_IMAGE = f"registry.example.com/duckdock/frontend@sha256:{'c' * 64}"


def _binding() -> dict:
    return build_release_binding(
        scope="target-production",
        target_environment="customer-production",
        source_commit=COMMIT,
        backend_image=BACKEND_IMAGE,
        frontend_image=FRONTEND_IMAGE,
    )


def _scan(
    host: str,
    ports: str,
    port_states: dict[int, str],
    *,
    address: str | None = None,
) -> target_network.ScanResult:
    scan_address = address or host
    port_xml = "".join(
        f"<port protocol='tcp' portid='{port}'><state state='{state}'/></port>"
        for port, state in sorted(port_states.items())
    )
    raw_xml = (
        "<nmaprun version='7.98'><host><status state='up'/>"
        f"<address addr='{scan_address}'/><ports>{port_xml}</ports></host>"
        "<runstats><finished elapsed='1.0'/></runstats></nmaprun>"
    )
    return target_network.ScanResult(
        host=host,
        requested_ports=ports,
        host_state="up",
        addresses=[scan_address],
        port_states=port_states,
        nmap_version="7.98",
        elapsed_seconds=1.0,
        raw_nmap_xml=raw_xml,
        xml_sha256=hashlib.sha256(raw_xml.encode()).hexdigest(),
    )


class FakeScanner:
    def scan(self, host: str, ports: str, *, open_only: bool) -> target_network.ScanResult:
        del open_only
        if ports == "1-65535":
            return _scan(host, ports, {443: "open"}, address="203.0.113.20")
        return _scan(host, ports, {int(ports): "filtered"})


def _policy(name: str, *, broad: bool = False) -> dict:
    spec: dict = {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]}
    if name == "controlled-external-egress":
        spec["egress"] = [
            {
                "to": [
                    {
                        "ipBlock": {
                            "cidr": "0.0.0.0/0" if broad else "10.0.0.0/8",
                        }
                    }
                ],
                "ports": [{"protocol": "TCP", "port": 443}],
            }
        ]
    return {
        "metadata": {
            "name": name,
            "uid": f"uid-{name}",
            "resourceVersion": "10",
            "generation": 2,
        },
        "spec": spec,
    }


class FakeKubectl:
    def __init__(
        self,
        *,
        broad_egress: bool = False,
        control_reachable: bool = True,
        mutable_cni_image: bool = False,
    ) -> None:
        self.broad_egress = broad_egress
        self.control_reachable = control_reachable
        self.mutable_cni_image = mutable_cni_image
        self.probes: list[dict] = []

    def text(self, arguments, *, namespace: str | None = None, timeout: float = 60) -> str:
        del namespace, timeout
        command = tuple(arguments)
        if command[:2] == ("config", "get-contexts"):
            return "customer-production-admin"
        if command[:2] == ("auth", "can-i"):
            return "yes"
        raise AssertionError(f"unexpected kubectl text command: {command}")

    def json(self, arguments, *, namespace: str | None = None) -> dict:
        command = tuple(arguments)
        if command[:2] == ("get", "namespace"):
            labels = {
                "ingress-probes": {"duckdock.io/ingress": "true"},
                "monitoring-probes": {"duckdock.io/monitoring": "true"},
                "untrusted-probes": {},
            }[command[2]]
            return {
                "metadata": {
                    "name": command[2],
                    "uid": f"uid-{command[2]}",
                    "resourceVersion": "30",
                    "labels": labels,
                }
            }
        if command[:2] == ("get", "pod"):
            return {
                "metadata": {
                    "name": command[2],
                    "uid": f"uid-{namespace}-{command[2]}",
                    "resourceVersion": "31",
                },
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            }
        if command == ("get", "networkpolicies"):
            return {
                "items": [
                    _policy(name, broad=self.broad_egress)
                    for name in sorted(target_network.REQUIRED_NETWORK_POLICIES)
                ]
            }
        if command[:2] == ("get", "daemonset"):
            return {
                "metadata": {
                    "name": command[2],
                    "uid": "uid-cilium",
                    "resourceVersion": "20",
                    "generation": 3,
                },
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "image": (
                                        "quay.io/cilium/cilium:latest"
                                        if self.mutable_cni_image
                                        else "quay.io/cilium/cilium@sha256:" + "d" * 64
                                    )
                                }
                            ]
                        }
                    }
                },
                "status": {"desiredNumberScheduled": 5, "numberReady": 5},
            }
        raise AssertionError(f"unexpected kubectl JSON command: {command} namespace={namespace}")

    def connection_probe(
        self,
        *,
        namespace: str,
        source: str,
        host: str,
        port: int,
        timeout: float,
    ) -> dict:
        del timeout
        connected = False
        if namespace == "ingress-probes" and host.startswith("frontend.") and port == 8080:
            connected = True
        elif namespace == "monitoring-probes" and host.startswith("backend.") and port == 8801:
            connected = True
        elif namespace == "untrusted-probes" and host == "egress-control.example.com":
            connected = self.control_reachable
        elif namespace == "duckdock" and host == "mysql.internal.example.com" and port == 3306:
            connected = True
        result = {
            "source_namespace": namespace,
            "source": source,
            "destination_host": host,
            "destination_port": port,
            "connected": connected,
            "kubectl_exit_code": 0 if connected else 1,
            "result": "connected" if connected else "denied-or-unreachable",
        }
        self.probes.append(result)
        return result


def _args(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        context="customer-production-admin",
        namespace="duckdock",
        target_environment="customer-production",
        source_commit=COMMIT,
        backend_image=BACKEND_IMAGE,
        frontend_image=FRONTEND_IMAGE,
        base_url="https://duckdock.example.com",
        public_host="duckdock.example.com",
        exercise_id="network-ga-20260806",
        scanner_id="external-scanner-hz-01",
        vantage_id="internet-hangzhou-01",
        scanner_source_ip="8.8.8.8",
        database_address="10.0.1.10",
        database_port=3306,
        redis_address="10.0.1.11",
        redis_port=6379,
        object_store_direct_address="10.0.1.12",
        object_store_direct_port=9000,
        trusted_probe_namespace="ingress-probes",
        trusted_probe_pod="probe",
        monitoring_probe_namespace="monitoring-probes",
        monitoring_probe_pod="probe",
        untrusted_probe_namespace="untrusted-probes",
        untrusted_probe_pod="probe",
        approved_egress_host="mysql.internal.example.com",
        approved_egress_port=3306,
        unapproved_egress_host="egress-control.example.com",
        unapproved_egress_port=443,
        cni_daemonset_namespace="kube-system",
        cni_daemonset_name="cilium",
        scan_timeout=30,
        connection_timeout=1.0,
        output=tmp_path / "network.json",
        release_binding=_binding(),
    )


def test_nmap_xml_is_parsed_into_content_addressed_port_states(monkeypatch: pytest.MonkeyPatch) -> None:
    xml = b"""<?xml version='1.0'?>
<nmaprun version='7.98'>
  <host><status state='up'/><address addr='203.0.113.20' addrtype='ipv4'/>
  <address addr='00:11:22:33:44:55' addrtype='mac'/><ports>
    <port protocol='tcp' portid='443'><state state='open'/></port>
    <port protocol='tcp' portid='9000'><state state='filtered'/></port>
  </ports></host>
  <runstats><finished elapsed='1.25'/></runstats>
</nmaprun>"""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=xml, stderr=b""),
    )

    result = target_network.NmapScanner(host_timeout_seconds=30).scan(
        "203.0.113.20",
        "443,9000",
        open_only=False,
    )

    assert result.open_ports == [443]
    assert result.addresses == ["203.0.113.20"]
    assert result.port_states == {443: "open", 9000: "filtered"}
    assert result.elapsed_seconds == 1.25
    assert len(result.xml_sha256) == 64


def test_target_network_collector_proves_external_and_cni_paths(tmp_path: Path) -> None:
    report = target_network.collect(
        _args(tmp_path),
        scanner=FakeScanner(),
        kubectl=FakeKubectl(),
    )

    assert report["schema_version"] == target_network.SCHEMA_VERSION
    assert report["status"] == "PASS"
    assert report["public_tcp_ports"] == [443]
    assert report["external_scan"]["private_data_services_unreachable"] is True
    assert report["policy_tests"]["default_deny_ingress_exercised"] is True
    assert report["policy_tests"]["approved_egress_allowed"] is True
    assert report["policy_tests"]["unapproved_egress_denied"] is True
    assert report["policy_tests"]["broad_world_egress_absent"] is True
    assert report["policy_tests"]["probe_identities"]["untrusted"]["namespace_labels"] == {}
    validate_network_probe_envelope(
        report,
        target_environment="customer-production",
        source_commit=COMMIT,
        backend_image=BACKEND_IMAGE,
        frontend_image=FRONTEND_IMAGE,
        exercise_id="network-ga-20260806",
    )


def test_broad_world_egress_policy_blocks_network_evidence(tmp_path: Path) -> None:
    report = target_network.collect(
        _args(tmp_path),
        scanner=FakeScanner(),
        kubectl=FakeKubectl(broad_egress=True),
    )

    assert report["status"] == "BLOCK"
    assert report["egress_allowlist_enforced"] is False
    assert report["policy_tests"]["broad_world_egress_absent"] is False


def test_unapproved_egress_denial_needs_reachable_control_destination(tmp_path: Path) -> None:
    report = target_network.collect(
        _args(tmp_path),
        scanner=FakeScanner(),
        kubectl=FakeKubectl(control_reachable=False),
    )

    assert report["status"] == "BLOCK"
    assert report["policy_tests"]["unapproved_egress_denied"] is False


def test_mutable_cni_identity_is_rejected_before_pass_report(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="immutable runtime identity"):
        target_network.collect(
            _args(tmp_path),
            scanner=FakeScanner(),
            kubectl=FakeKubectl(mutable_cni_image=True),
        )


def test_private_scan_without_observed_port_state_blocks_evidence(tmp_path: Path) -> None:
    class MissingPortStateScanner(FakeScanner):
        def scan(self, host: str, ports: str, *, open_only: bool) -> target_network.ScanResult:
            result = super().scan(host, ports, open_only=open_only)
            if ports == "6379":
                return target_network.ScanResult(
                    host=result.host,
                    requested_ports=result.requested_ports,
                    host_state=result.host_state,
                    addresses=result.addresses,
                    port_states={},
                    nmap_version=result.nmap_version,
                    elapsed_seconds=result.elapsed_seconds,
                    raw_nmap_xml=result.raw_nmap_xml,
                    xml_sha256=result.xml_sha256,
                )
            return result

    report = target_network.collect(
        _args(tmp_path),
        scanner=MissingPortStateScanner(),
        kubectl=FakeKubectl(),
    )

    assert report["status"] == "BLOCK"
    assert report["external_scan"]["private_data_services_unreachable"] is False


def test_parse_args_requires_exact_external_vantage_acknowledgement(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        target_network.parse_args(
            [
                "--context",
                "prod",
                "--target-environment",
                "customer-production",
                "--acknowledge-external-vantage",
                "another-target",
                "--source-commit",
                COMMIT,
                "--backend-image",
                BACKEND_IMAGE,
                "--frontend-image",
                FRONTEND_IMAGE,
                "--base-url",
                "https://duckdock.example.com",
                "--exercise-id",
                "network-ga-20260806",
                "--scanner-id",
                "scanner",
                "--vantage-id",
                "internet-hangzhou-01",
                "--scanner-source-ip",
                "8.8.8.8",
                "--database-address",
                "10.0.0.10",
                "--redis-address",
                "10.0.0.11",
                "--object-store-direct-address",
                "10.0.0.12",
                "--trusted-probe-namespace",
                "ingress-probes",
                "--trusted-probe-pod",
                "probe",
                "--monitoring-probe-namespace",
                "monitoring-probes",
                "--monitoring-probe-pod",
                "probe",
                "--untrusted-probe-namespace",
                "untrusted-probes",
                "--untrusted-probe-pod",
                "probe",
                "--approved-egress-host",
                "mysql.internal",
                "--approved-egress-port",
                "3306",
                "--unapproved-egress-host",
                "control.example.com",
                "--unapproved-egress-port",
                "443",
                "--cni-daemonset-name",
                "cilium",
                "--output",
                str(tmp_path / "network.json"),
            ]
        )
