from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.collect_ga_target_ha as target_ha
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


def _network_report() -> dict:
    def scan(host: str, port: str, *, open_ports: list[int]) -> dict:
        states = {str(value): "open" for value in open_ports}
        if not open_ports and port.isdigit():
            states[port] = "filtered"
        address = host if port != "1-65535" else "203.0.113.20"
        port_xml = "".join(
            f"<port protocol='tcp' portid='{value}'><state state='{state}'/></port>"
            for value, state in states.items()
        )
        raw_xml = (
            "<nmaprun version='7.98'><host><status state='up'/>"
            f"<address addr='{address}'/><ports>{port_xml}</ports></host>"
            "<runstats><finished elapsed='1.0'/></runstats></nmaprun>"
        )
        return {
            "host": host,
            "requested_ports": port,
            "host_state": "up",
            "addresses": [address],
            "port_states": states,
            "open_tcp_ports": open_ports,
            "nmap_version": "7.98",
            "elapsed_seconds": 1.0,
            "raw_nmap_xml": raw_xml,
            "xml_sha256": hashlib.sha256(raw_xml.encode()).hexdigest(),
        }

    def connection(namespace: str, source: str, host: str, port: int, connected: bool) -> dict:
        return {
            "source_namespace": namespace,
            "source": source,
            "destination_host": host,
            "destination_port": port,
            "connected": connected,
            "kubectl_exit_code": 0 if connected else 1,
        }

    policies = []
    for name in sorted(target_ha.NETWORK_REQUIRED_POLICIES):
        spec = {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]}
        policies.append(
            {
                "name": name,
                "uid": f"uid-{name}",
                "resource_version": "10",
                "generation": 1,
                "spec": spec,
                "spec_sha256": hashlib.sha256(
                    json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
            }
        )

    return {
        "schema_version": target_ha.NETWORK_SCHEMA_VERSION,
        **_binding(),
        "status": "PASS",
        "passed": True,
        "observed_at": "2026-08-06T00:02:00Z",
        "provider_observed_at": "2026-08-06T00:00:00Z",
        "verification_observed_at": "2026-08-06T00:01:00Z",
        "exercise_id": "state-ha-20260806",
        "external_scan": {
            "transport": "nmap TCP scan from acknowledged external vantage",
            "scanner_id": "external-scanner-01",
            "scanner_source_ip": "8.8.8.8",
            "public_ingress": scan("duckdock.example.com", "1-65535", open_ports=[443]),
            "private_data_services": {
                "database": scan("10.0.0.10", "3306", open_ports=[]),
                "redis": scan("10.0.0.11", "6379", open_ports=[]),
                "object_store_direct": scan("10.0.0.12", "9000", open_ports=[]),
            },
            "discovered_tcp_ports": [443],
            "private_data_services_unreachable": True,
            "passed": True,
        },
        "policy_tests": {
            "cni": {
                "namespace": "kube-system",
                "name": "cilium",
                "uid": "uid-cilium",
                "resource_version": "20",
                "generation": 2,
                "desired": 3,
                "ready": 3,
                "images": [f"quay.io/cilium/cilium@sha256:{'d' * 64}"],
            },
            "probe_identities": {
                "ingress": {
                    "namespace": "ingress-probes",
                    "namespace_uid": "uid-ingress-probes",
                    "namespace_resource_version": "30",
                    "namespace_labels": {"duckdock.io/ingress": "true"},
                    "pod": "probe",
                    "pod_uid": "uid-ingress-probe",
                    "pod_resource_version": "31",
                },
                "monitoring": {
                    "namespace": "monitoring-probes",
                    "namespace_uid": "uid-monitoring-probes",
                    "namespace_resource_version": "32",
                    "namespace_labels": {"duckdock.io/monitoring": "true"},
                    "pod": "probe",
                    "pod_uid": "uid-monitoring-probe",
                    "pod_resource_version": "33",
                },
                "untrusted": {
                    "namespace": "untrusted-probes",
                    "namespace_uid": "uid-untrusted-probes",
                    "namespace_resource_version": "34",
                    "namespace_labels": {},
                    "pod": "probe",
                    "pod_uid": "uid-untrusted-probe",
                    "pod_resource_version": "35",
                },
            },
            "network_policies": policies,
            "required_policies_present": True,
            "policy_snapshots_valid": True,
            "broad_world_egress_absent": True,
            "ingress_tests": {
                "trusted_frontend_allowed": connection(
                    "ingress-probes", "pod/probe", "frontend.duckdock.svc.cluster.local", 8080, True
                ),
                "trusted_backend_allowed": connection(
                    "monitoring-probes", "pod/probe", "backend.duckdock.svc.cluster.local", 8801, True
                ),
                "untrusted_frontend_denied": connection(
                    "untrusted-probes", "pod/probe", "frontend.duckdock.svc.cluster.local", 8080, False
                ),
                "untrusted_backend_denied": connection(
                    "untrusted-probes", "pod/probe", "backend.duckdock.svc.cluster.local", 8801, False
                ),
            },
            "egress_tests": {
                "untrusted_control_destination_reachable": connection(
                    "untrusted-probes", "pod/probe", "control.example.com", 443, True
                ),
                "approved_destination_allowed": connection(
                    "duckdock", "deployment/backend", "mysql.internal", 3306, True
                ),
                "unapproved_destination_denied": connection(
                    "duckdock", "deployment/backend", "control.example.com", 443, False
                ),
            },
            "default_deny_ingress_exercised": True,
            "unapproved_egress_denied": True,
            "approved_egress_allowed": True,
            "private_data_services_unreachable_externally": True,
            "passed": True,
        },
    }


def _state_report() -> dict:
    return {
        "schema_version": target_ha.STATE_SCHEMA_VERSION,
        **_binding(),
        "status": "PASS",
        "passed": True,
        "observed_at": "2026-08-06T00:02:00Z",
        "managed_mysql_ha": True,
        "managed_redis_ha": True,
        "object_store_ha": True,
        "rwx_repository_storage_ha": True,
        "failover_exercised": True,
        "data_integrity_passed": True,
        "services": {
            name: {
                "provider": f"Managed {name}",
                "service_instance_id": f"instance-{name}",
                "topology_id": f"topology-{name}",
                "fault_domain_count": 2,
                "failover_event_id": f"event-{name}",
                "source_fault_domain": "zone-a",
                "destination_fault_domain": "zone-b",
                "ha_enabled": True,
                "automatic_failover": True,
                "failover_exercised": True,
                "data_integrity_passed": True,
                "read_probe_passed": True,
                "write_probe_passed": True,
                "started_at": "2026-08-05T23:58:00Z",
                "recovered_at": "2026-08-06T00:00:00Z",
                "verified_at": "2026-08-06T00:01:00Z",
                "provider_event_sha256": hashlib.sha256(
                    f"provider-{name}".encode()
                ).hexdigest(),
                "verification_log_sha256": hashlib.sha256(
                    f"verification-{name}".encode()
                ).hexdigest(),
            }
            for name in target_ha.STATE_SERVICES
        },
        "state_services_policy": {"policy_id": "state-policy"},
        "provider_receipt": {
            "signed_evidence": {"signer_identity": "provider@example.com"}
        },
        "verification_receipt": {
            "signed_evidence": {"signer_identity": "verifier@example.com"}
        },
    }


def _node(name: str, zone: str) -> dict:
    return {
        "metadata": {"name": name, "labels": {"topology.kubernetes.io/zone": zone}},
        "spec": {},
        "status": {"conditions": [{"type": "Ready", "status": "True"}]},
    }


def _pod(component: str, suffix: str, node: str) -> dict:
    return {
        "metadata": {
            "name": f"{component}-{suffix}",
            "labels": {"app.kubernetes.io/component": component},
        },
        "spec": {"nodeName": node},
        "status": {"conditions": [{"type": "Ready", "status": "True"}]},
    }


def _pods(layout: dict[str, list[str]]) -> dict:
    return {
        "items": [
            _pod(component, str(index), node)
            for component, nodes in layout.items()
            for index, node in enumerate(nodes, start=1)
        ]
    }


def _deployments() -> dict:
    images = {
        "backend": BACKEND_IMAGE,
        "frontend": FRONTEND_IMAGE,
        "worker": BACKEND_IMAGE,
        "beat": BACKEND_IMAGE,
    }
    replicas = {"backend": 3, "frontend": 3, "worker": 3, "beat": 1}
    return {
        "items": [
            {
                "metadata": {"name": component},
                "spec": {
                    "replicas": replicas[component],
                    "template": {"spec": {"containers": [{"image": image}]}},
                },
                "status": {"readyReplicas": replicas[component]},
            }
            for component, image in images.items()
        ]
    }


class FakeKubectl:
    def __init__(self, *, fail_drain: bool = False) -> None:
        self.fail_drain = fail_drain
        self.commands: list[tuple[tuple[str, ...], bool]] = []
        self.pod_reads = 0
        self.node_reads = 0
        self.nodes = {
            "items": [
                _node("node-a", "zone-a"),
                _node("node-b", "zone-b"),
                _node("node-c", "zone-b"),
                _node("node-d", "zone-b"),
            ]
        }
        self.before = _pods(
            {
                "backend": ["node-a", "node-b", "node-c"],
                "frontend": ["node-a", "node-b", "node-c"],
                "worker": ["node-a", "node-b", "node-c"],
                "beat": ["node-a"],
            }
        )
        self.after = _pods(
            {
                "backend": ["node-b", "node-c", "node-d"],
                "frontend": ["node-b", "node-c", "node-d"],
                "worker": ["node-b", "node-c", "node-d"],
                "beat": ["node-b"],
            }
        )
        self.restored = _pods(
            {
                "backend": ["node-a", "node-b", "node-c"],
                "frontend": ["node-a", "node-b", "node-c"],
                "worker": ["node-a", "node-b", "node-c"],
                "beat": ["node-b"],
            }
        )

    def text(
        self,
        arguments,
        *,
        namespaced: bool = False,
        timeout: float = 600,
        check: bool = True,
    ) -> str:
        del timeout, check
        command = tuple(arguments)
        self.commands.append((command, namespaced))
        if command[:2] == ("config", "get-contexts"):
            return "customer-production-admin"
        if command[:2] == ("auth", "can-i"):
            return "yes"
        if command and command[0] == "drain" and self.fail_drain:
            raise RuntimeError("simulated drain failure")
        return ""

    def json(self, arguments, *, namespaced: bool = False) -> dict:
        del namespaced
        command = tuple(arguments)
        if command == ("get", "nodes"):
            self.node_reads += 1
            return self.nodes
        if command == ("get", "deployments"):
            return _deployments()
        if command == ("get", "pods"):
            self.pod_reads += 1
            return (self.before, self.after, self.restored)[self.pod_reads - 1]
        if command == ("get", "poddisruptionbudgets"):
            return {"items": [{"metadata": {"name": "backend"}, "spec": {"minAvailable": 2}}]}
        if command == ("get", "networkpolicies"):
            return {"items": [{"metadata": {"name": "default-deny"}}]}
        if command == ("version",):
            return {"serverVersion": {"gitVersion": "v1.34.0"}}
        raise AssertionError(f"unexpected kubectl JSON command: {command}")


def _args(tmp_path: Path) -> SimpleNamespace:
    network = tmp_path / "network.json"
    state = tmp_path / "state.json"
    signature = tmp_path / "state.sig"
    policy = tmp_path / "policy.json"
    for path in (network, state, signature, policy):
        path.write_text("{}\n", encoding="utf-8")
    return SimpleNamespace(
        context="customer-production-admin",
        namespace="duckdock",
        target_environment="customer-production",
        source_commit=COMMIT,
        backend_image=BACKEND_IMAGE,
        frontend_image=FRONTEND_IMAGE,
        base_url="https://duckdock.example.com",
        drain_zone="zone-a",
        network_evidence=network,
        state_services_evidence=state,
        state_services_signature=signature,
        state_services_signer_identity="operations@example.com",
        approval_policy=policy,
        ca_file=None,
        probe_interval=0.001,
        probe_timeout=1.0,
        grace_period=1,
        recovery_timeout=5,
        rebalance_timeout=5,
        release_binding=_binding(),
    )


def _stub_external_evidence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    allowed_signers = tmp_path / "allowed_signers"
    allowed_signers.write_text("operations@example.com ssh-ed25519 AAAA\n", encoding="utf-8")
    monkeypatch.setattr(target_ha, "validate_network_evidence", lambda *_args: _network_report())
    monkeypatch.setattr(target_ha, "validate_state_services_report", lambda *_args: _state_report())
    monkeypatch.setattr(
        target_ha,
        "load_approval_policy",
        lambda *_args: ({"policy_id": "release-authority"}, allowed_signers, "f" * 64),
    )
    monkeypatch.setattr(target_ha, "verify_state_services_signature", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(target_ha, "_http_probe", lambda *_args, **_kwargs: (True, 200, None))


def test_parse_args_requires_exact_disruption_acknowledgement(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        target_ha.parse_args(
            [
                "--context",
                "prod",
                "--target-environment",
                "customer-production",
                "--acknowledge-target-disruption",
                "another-target",
                "--source-commit",
                COMMIT,
                "--backend-image",
                BACKEND_IMAGE,
                "--frontend-image",
                FRONTEND_IMAGE,
                "--base-url",
                "https://duckdock.example.com",
                "--drain-zone",
                "zone-a",
                "--network-evidence",
                str(tmp_path / "network.json"),
                "--state-services-evidence",
                str(tmp_path / "state.json"),
                "--state-services-signature",
                str(tmp_path / "state.sig"),
                "--state-services-signer-identity",
                "operations@example.com",
                "--approval-policy",
                str(tmp_path / "policy.json"),
                "--output",
                str(tmp_path / "ha.json"),
            ]
        )


def test_network_and_state_receipts_are_content_validated(tmp_path: Path) -> None:
    network_path = tmp_path / "network.json"
    network_path.write_text(json.dumps(_network_report()), encoding="utf-8")
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(_state_report()), encoding="utf-8")

    assert target_ha.validate_network_evidence(network_path, _binding())["passed"] is True
    assert target_ha.validate_state_services_report(state_path, _binding())["passed"] is True

    network = _network_report()
    network["policy_tests"]["unapproved_egress_denied"] = False
    network_path.write_text(json.dumps(network), encoding="utf-8")
    with pytest.raises(ValueError, match="network evidence"):
        target_ha.validate_network_evidence(network_path, _binding())

    state = _state_report()
    state["services"]["mysql"]["failover_event_id"] = ""
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match="mysql receipt"):
        target_ha.validate_state_services_report(state_path, _binding())


def test_state_services_report_must_be_signed_by_policy_operations_identity(tmp_path: Path) -> None:
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen is required for state-services signature verification")
    key = tmp_path / "operations_key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
        check=True,
    )
    identity = "operations@example.com"
    allowed_signers = tmp_path / "allowed_signers"
    allowed_signers.write_text(
        f"{identity} {key.with_suffix('.pub').read_text(encoding='utf-8').strip()}\n",
        encoding="utf-8",
    )
    policy = {
        "schema_version": target_ha.APPROVAL_POLICY_SCHEMA_VERSION,
        "policy_id": "release-authority",
        "organization": "DuckDock Test Release Authority",
        "allowed_signers_path": str(allowed_signers),
        "allowed_signers_sha256": hashlib.sha256(allowed_signers.read_bytes()).hexdigest(),
        "roles": {
            "Product": ["product@example.com"],
            "Architecture": ["architecture@example.com"],
            "Security": ["security@example.com"],
            "Operations": [identity],
        },
        "independent_security_assessors": {
            "Independent Security Lab": ["assessor@independent-security.example"]
        },
    }
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy) + "\n", encoding="utf-8")
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(_state_report()) + "\n", encoding="utf-8")
    subprocess.run(
        [
            "ssh-keygen",
            "-q",
            "-Y",
            "sign",
            "-f",
            str(key),
            "-n",
            "duckdock-ha-state-services",
            str(state_path),
        ],
        check=True,
    )
    signature = Path(f"{state_path}.sig")

    _, trust_path, _ = target_ha.load_approval_policy(policy_path, identity)
    target_ha.verify_state_services_signature(
        state_path,
        signature,
        identity=identity,
        allowed_signers=trust_path,
    )

    state_path.write_text(json.dumps({**_state_report(), "passed": False}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid state-services"):
        target_ha.verify_state_services_signature(
            state_path,
            signature,
            identity=identity,
            allowed_signers=trust_path,
        )


def test_target_zone_disruption_collects_v2_evidence_and_restores_nodes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_external_evidence(monkeypatch, tmp_path)
    fake = FakeKubectl()

    report = target_ha.execute(_args(tmp_path), kubectl=fake)

    assert report["schema_version"] == target_ha.SCHEMA_VERSION
    assert report["status"] == "PASS"
    assert report["fault_injection"]["drained_nodes"] == ["node-a"]
    assert report["fault_injection"]["beat_original_node"] == "node-a"
    assert report["fault_injection"]["beat_recovery_node"] == "node-b"
    assert report["availability_probe"]["sample_count"] >= 5
    assert report["availability_probe"]["failure_count"] == 0
    assert report["cleanup"] == {"restored_nodes": ["node-a"], "errors": [], "passed": True}
    commands = [command for command, _ in fake.commands]
    assert ("taint", "node", "node-a", target_ha.TAINT, "--overwrite") in commands
    assert ("taint", "node", "node-a", f"{target_ha.TAINT_KEY}-") in commands
    assert ("uncordon", "node-a") in commands


def test_failed_drain_still_removes_taint_and_uncordons_node(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_external_evidence(monkeypatch, tmp_path)
    fake = FakeKubectl(fail_drain=True)

    report = target_ha.execute(_args(tmp_path), kubectl=fake)

    assert report["status"] == "BLOCK"
    assert "simulated drain failure" in report["exercise_error"]
    commands = [command for command, _ in fake.commands]
    assert ("taint", "node", "node-a", f"{target_ha.TAINT_KEY}-") in commands
    assert ("uncordon", "node-a") in commands
